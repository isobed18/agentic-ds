"""Password gate for the control plane.

The UI hands out run metadata, artifact payloads and the contents of uploaded
source tables. Bound to loopback that is nobody's problem. Reachable from the
public internet it is the entire attack surface, so this module is the only
thing standing in front of it and is deliberately small enough to read in one
sitting.

Design notes, because each one is a decision someone will want to revisit:

* No new dependencies. Password hashing is ``hashlib.scrypt`` and the session
  cookie is an HMAC over a JSON payload -- both stdlib. The project forbids
  copyleft dependencies and an auth library is not worth an exception.
* The password is never stored, and never appears in this repository. The
  server reads a *hash* from the environment; ``scripts/set_password.py``
  writes it to a gitignored file.
* Auth is **off** unless a credential is configured. That keeps loopback
  development and the test suite working untouched. The public launcher
  (``scripts/serve_public.py``) refuses to start without it, so the permissive
  default cannot leak onto the tunnel.
* Failed attempts are rate limited. A single account with a human-chosen
  password on a public endpoint is guessable at machine speed otherwise; this
  is the control that makes the difference.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from threading import Lock

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

COOKIE_NAME = "ads_session"

# scrypt cost. n=2**14 keeps a single verification near 100ms on this hardware,
# which is irrelevant for one interactive login and expensive for a guesser.
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_DKLEN = 32

_ENV_USERNAME = "ADS_AUTH_USERNAME"
_ENV_PASSWORD_HASH = "ADS_AUTH_PASSWORD_HASH"
_ENV_USERS = "ADS_AUTH_USERS_JSON"
_ENV_SECRET = "ADS_AUTH_SECRET"
_ENV_SESSION_HOURS = "ADS_AUTH_SESSION_HOURS"
_ENV_SECURE_COOKIE = "ADS_AUTH_SECURE_COOKIE"


# --------------------------------------------------------------------------
# password hashing
# --------------------------------------------------------------------------


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    """Return a self-describing scrypt hash string.

    Format: ``scrypt$<n>$<r>$<p>$<salt_b64>$<hash_b64>``. The parameters travel
    with the hash so raising the cost later does not invalidate existing ones.
    """
    salt = salt if salt is not None else secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_DKLEN,
    )
    return "$".join(
        [
            "scrypt",
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(derived).decode("ascii"),
        ]
    )


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time check of ``password`` against a :func:`hash_password` string."""
    try:
        scheme, n_raw, r_raw, p_raw, salt_b64, hash_b64 = encoded.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n_raw),
            r=int(r_raw),
            p=int(p_raw),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        # A malformed hash is a configuration error, not a login failure. Fail
        # closed rather than raising into the request handler.
        return False
    return hmac.compare_digest(derived, expected)


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthConfig:
    """Everything the gate needs. ``enabled`` is derived, never set by hand."""

    username: str
    password_hash: str
    secret: bytes
    session_hours: float = 12.0
    secure_cookie: bool = True
    max_failures: int = 8
    lockout_seconds: int = 900
    users: dict[str, str] = field(default_factory=dict)

    @property
    def credentials(self) -> dict[str, str]:
        """Configured accounts, with the legacy single account as a fallback."""
        if self.users:
            return self.users
        if self.username and self.password_hash:
            return {self.username: self.password_hash}
        return {}

    @property
    def enabled(self) -> bool:
        return bool(self.credentials)


def config_from_env() -> AuthConfig | None:
    """Build a config from the environment, or ``None`` when unconfigured.

    ``None`` means "run without a password", which is the historical behaviour
    and what the loopback launcher and the tests rely on.
    """
    users: dict[str, str] = {}
    users_raw = os.environ.get(_ENV_USERS, "").strip()
    if users_raw:
        try:
            decoded = json.loads(users_raw)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, dict):
            users = {
                str(name).strip(): str(password_hash).strip()
                for name, password_hash in decoded.items()
                if str(name).strip() and str(password_hash).strip()
            }

    username = os.environ.get(_ENV_USERNAME, "").strip()
    password_hash = os.environ.get(_ENV_PASSWORD_HASH, "").strip()
    if not users and username and password_hash:
        users = {username: password_hash}
    if not users:
        return None
    username, password_hash = next(iter(users.items()))

    secret_raw = os.environ.get(_ENV_SECRET, "").strip()
    if secret_raw:
        secret = secret_raw.encode("utf-8")
    else:
        # An ephemeral secret is valid but invalidates sessions on restart.
        # serve_public.py persists one so that is not the normal path.
        secret = secrets.token_bytes(32)

    try:
        session_hours = float(os.environ.get(_ENV_SESSION_HOURS, "12"))
    except ValueError:
        session_hours = 12.0

    secure_cookie = os.environ.get(_ENV_SECURE_COOKIE, "1").strip().lower() not in {
        "0",
        "false",
        "no",
    }

    return AuthConfig(
        username=username,
        password_hash=password_hash,
        secret=secret,
        session_hours=session_hours,
        secure_cookie=secure_cookie,
        users=users,
    )


# --------------------------------------------------------------------------
# session cookie
# --------------------------------------------------------------------------


def _sign(payload: bytes, secret: bytes) -> str:
    digest = hmac.new(secret, payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def issue_session(
    config: AuthConfig,
    *,
    username: str | None = None,
    now: float | None = None,
) -> str:
    """Mint a signed session token for one configured user."""
    now = time.time() if now is None else now
    selected = username or config.username
    if selected not in config.credentials:
        raise ValueError("cannot issue a session for an unconfigured user")
    payload = json.dumps(
        {"u": selected, "exp": now + config.session_hours * 3600},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"{_b64encode(payload)}.{_sign(payload, config.secret)}"


def read_session(token: str, config: AuthConfig, *, now: float | None = None) -> str | None:
    """Return the username carried by ``token``, or ``None`` if it is not valid.

    Signature is checked before the payload is trusted for anything, including
    its own expiry.
    """
    now = time.time() if now is None else now
    try:
        payload_b64, signature = token.split(".", 1)
        payload = _b64decode(payload_b64)
    except (ValueError, TypeError):
        return None
    if not hmac.compare_digest(_sign(payload, config.secret), signature):
        return None
    try:
        claims = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(claims, dict):
        return None
    if float(claims.get("exp", 0)) <= now:
        return None
    username = claims.get("u")
    # Removing an account invalidates its signed sessions without rotating the
    # shared signing key, so account deletion takes effect at the next request.
    if username not in config.credentials:
        return None
    return username


# --------------------------------------------------------------------------
# rate limiting
# --------------------------------------------------------------------------


@dataclass
class _Attempts:
    failures: list[float] = field(default_factory=list)
    locked_until: float = 0.0


class RateLimiter:
    """Per-client failure counter with a fixed lockout.

    Keyed by client address. Behind the tunnel every request arrives from
    localhost, so the forwarded client address is used when present -- see
    :func:`_client_key` for why that is safe here and would not be in general.
    """

    def __init__(self, *, max_failures: int, lockout_seconds: int) -> None:
        self._max = max_failures
        self._lockout = lockout_seconds
        self._by_client: dict[str, _Attempts] = {}
        self._lock = Lock()

    def retry_after(self, key: str, *, now: float | None = None) -> int:
        """Seconds the client must wait, or 0 if it may attempt a login."""
        now = time.time() if now is None else now
        with self._lock:
            record = self._by_client.get(key)
            if record is None or record.locked_until <= now:
                return 0
            return int(record.locked_until - now) + 1

    def record_failure(self, key: str, *, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            record = self._by_client.setdefault(key, _Attempts())
            window_start = now - self._lockout
            record.failures = [t for t in record.failures if t >= window_start]
            record.failures.append(now)
            if len(record.failures) >= self._max:
                record.locked_until = now + self._lockout
                record.failures.clear()

    def record_success(self, key: str) -> None:
        with self._lock:
            self._by_client.pop(key, None)


def _client_key(request: Request) -> str:
    """Identify the caller for rate-limiting purposes.

    ``CF-Connecting-IP`` is set by Cloudflare and cannot be spoofed by a client
    *through* the tunnel, because the tunnel terminates at Cloudflare and the
    header is rewritten there. It is trusted only as a rate-limit bucket, never
    for authorisation, so the worst case of a wrong value is that an attacker
    shares a bucket with someone else.
    """
    forwarded = request.headers.get("cf-connecting-ip")
    if forwarded:
        return forwarded.strip()
    if request.client is not None:
        return request.client.host
    return "unknown"


# --------------------------------------------------------------------------
# login page
# --------------------------------------------------------------------------

# Served standalone rather than through the SPA, so an unauthenticated caller
# never receives the application bundle -- the bundle names every API route and
# is not something to hand out at the door.
_LOGIN_PAGE = """<!doctype html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Agentic DS</title>
<style>
  :root {
    --ink:#0f172a; --ink-soft:#334155; --ink-mute:#64748b; --ink-faint:#94a3b8;
    --line:#e2e8f0; --line-soft:#f1f5f9;
    --surface:#ffffff; --sunken:#f8fafc;
    --brand-100:#dbeafe; --brand-500:#3b82f6; --brand-600:#2563eb; --brand-700:#1d4ed8;
    --stop-50:#fef2f2; --stop-600:#dc2626;
    --sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    --mono:ui-monospace,SFMono-Regular,"Cascadia Mono",Menlo,Consolas,monospace;
  }
  *{box-sizing:border-box}
  body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
       background:var(--sunken);color:var(--ink);font-family:var(--sans);padding:24px}
  .card{width:100%;max-width:380px;background:var(--surface);border:1px solid var(--line);
        border-radius:12px;padding:32px;
        box-shadow:0 1px 2px rgba(15,23,42,.04),0 12px 32px -16px rgba(15,23,42,.18)}
  .mark{width:34px;height:34px;border-radius:9px;background:var(--brand-600);margin-bottom:18px;
        display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:15px}
  h1{font-size:19px;font-weight:650;letter-spacing:-.01em;margin:0 0 4px}
  .sub{font-size:13.5px;color:var(--ink-mute);margin:0 0 24px;line-height:1.5}
  label{display:block;font-size:12.5px;font-weight:600;color:var(--ink-soft);margin:0 0 6px}
  .field{width:100%;border:1px solid var(--line);border-radius:8px;background:var(--surface);
         padding:9px 11px;font-size:14px;font-family:inherit;color:var(--ink);outline:none;
         transition:border-color .12s, box-shadow .12s}
  .field:focus{border-color:var(--brand-500);box-shadow:0 0 0 3px var(--brand-100)}
  .row{margin-bottom:16px}
  button{width:100%;border:0;border-radius:8px;background:var(--brand-600);color:#fff;
         padding:10px 14px;font-size:14px;font-weight:600;font-family:inherit;cursor:pointer;
         transition:background .12s}
  button:hover:not(:disabled){background:var(--brand-700)}
  button:disabled{opacity:.55;cursor:not-allowed}
  .error{display:none;background:var(--stop-50);border:1px solid #fecaca;color:var(--stop-600);
         border-radius:8px;padding:9px 12px;font-size:13px;margin-bottom:16px;line-height:1.45}
  .error.on{display:block}
  footer{margin-top:22px;padding-top:16px;border-top:1px solid var(--line-soft);
          font-family:var(--mono);font-size:11px;color:var(--ink-faint);text-align:center}
</style>
</head>
<body>
  <main class="card">
    <div class="mark">DS</div>
    <h1>Agentic Data Science</h1>
    <p class="sub">Bu kontrol paneli kurum verisi ve model çıktıları içerir.
       Devam etmek için giriş yapın.</p>

    <div class="error" id="error" role="alert"></div>

    <form id="form" autocomplete="on">
      <div class="row">
        <label for="username">Kullanıcı adı</label>
        <input class="field" id="username" name="username" autocomplete="username"
               autocapitalize="none" autocorrect="off" spellcheck="false" required autofocus>
      </div>
      <div class="row">
        <label for="password">Parola</label>
        <input class="field" id="password" name="password" type="password"
               autocomplete="current-password" required>
      </div>
      <button type="submit" id="submit">Giriş yap</button>
    </form>

    <footer>yerel · kayıt yok</footer>
  </main>

<script>
  var form = document.getElementById('form');
  var errorBox = document.getElementById('error');
  var submit = document.getElementById('submit');

  function showError(message) {
    errorBox.textContent = message;
    errorBox.className = 'error on';
  }

  form.addEventListener('submit', function (event) {
    event.preventDefault();
    errorBox.className = 'error';
    submit.disabled = true;
    submit.textContent = 'Kontrol ediliyor...';

    fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        username: document.getElementById('username').value,
        password: document.getElementById('password').value
      })
    }).then(function (response) {
      return response.json().then(function (body) {
        return { ok: response.ok, body: body };
      });
    }).then(function (result) {
      if (result.ok) {
        var next = new URLSearchParams(window.location.search).get('next');
        // Only same-origin relative paths, so a crafted ?next= cannot bounce
        // a freshly authenticated session to another host.
        window.location.href =
          next && next.charAt(0) === '/' && next.charAt(1) !== '/' ? next : '/';
        return;
      }
      showError(result.body.detail || 'Giriş yapılamadı.');
      submit.disabled = false;
      submit.textContent = 'Giriş yap';
    }).catch(function () {
      showError('Sunucuya ulaşılamadı.');
      submit.disabled = false;
      submit.textContent = 'Giriş yap';
    });
  });
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------
# wiring
# --------------------------------------------------------------------------

# Reachable without a session. Everything else -- including the SPA shell, the
# JS bundle and every API route -- requires one.
_PUBLIC_PATHS = frozenset({"/login", "/api/auth/login", "/api/health"})


def install_auth(app: FastAPI, config: AuthConfig) -> None:
    """Mount the login routes and the gate middleware on ``app``.

    Called from :func:`ads.api.create_app` only when a credential is
    configured, so an unconfigured server behaves exactly as it did before.
    """
    limiter = RateLimiter(
        max_failures=config.max_failures,
        lockout_seconds=config.lockout_seconds,
    )

    def _set_cookie(response: Response, token: str) -> None:
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=int(config.session_hours * 3600),
            httponly=True,
            samesite="lax",
            secure=config.secure_cookie,
            path="/",
        )

    @app.get("/login", response_class=HTMLResponse, include_in_schema=False)
    def login_page(request: Request) -> Response:
        # Already signed in: skip the form rather than inviting a second login.
        token = request.cookies.get(COOKIE_NAME, "")
        if token and read_session(token, config):
            return RedirectResponse("/", status_code=303)
        return HTMLResponse(_LOGIN_PAGE)

    @app.post("/api/auth/login", include_in_schema=False)
    async def login(request: Request) -> Response:
        key = _client_key(request)
        wait = limiter.retry_after(key)
        if wait:
            return JSONResponse(
                {"detail": f"Çok fazla hatalı deneme. {wait} saniye sonra tekrar deneyin."},
                status_code=429,
                headers={"Retry-After": str(wait)},
            )

        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            body = {}
        username = str(body.get("username", ""))
        password = str(body.get("password", ""))

        # Always perform one expensive password verification. Unknown names use
        # a real configured hash as a timing dummy, so account enumeration does
        # not become materially cheaper than a wrong password.
        configured_hash = config.credentials.get(username)
        password_ok = verify_password(password, configured_hash or config.password_hash)
        name_ok = configured_hash is not None
        if not (name_ok and password_ok):
            limiter.record_failure(key)
            return JSONResponse({"detail": "Kullanıcı adı veya parola hatalı."}, status_code=401)

        limiter.record_success(key)
        response = JSONResponse({"username": username})
        _set_cookie(response, issue_session(config, username=username))
        return response

    @app.post("/api/auth/logout", include_in_schema=False)
    def logout() -> Response:
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE_NAME, path="/")
        return response

    @app.get("/api/auth/session", include_in_schema=False)
    def session(request: Request) -> Response:
        token = request.cookies.get(COOKIE_NAME, "")
        username = read_session(token, config) if token else None
        return JSONResponse({"username": username, "authenticated": bool(username)})

    @app.middleware("http")
    async def require_session(request: Request, call_next):
        path = request.url.path
        if path in _PUBLIC_PATHS:
            return await call_next(request)

        token = request.cookies.get(COOKIE_NAME, "")
        session_user = read_session(token, config) if token else None
        if session_user:
            # Routes need to know *who* is asking, not merely that someone is.
            # A per-user capability (the paid DeepSeek backend spends one
            # person's key) cannot be enforced from a boolean.
            request.state.username = session_user
            return await call_next(request)

        # API callers get a status code they can act on; browsers get the form.
        # Redirecting an XHR would hand the SPA an HTML body for a JSON request.
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Oturum gerekli."}, status_code=401)
        target = "/login"
        if path != "/":
            query = f"?{request.url.query}" if request.url.query else ""
            target = f"/login?next={path}{query}"
        return RedirectResponse(target, status_code=303)
