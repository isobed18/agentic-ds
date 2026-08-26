"""Tests for the control-plane password gate.

The bar this project holds itself to is that a test must be able to fail for
the reason it claims. These are written as counter-tests where possible: for
every "this is allowed" there is a "and this specific tampering is not".
"""

from __future__ import annotations

import json
import time

import pytest

from ads.api.auth import (
    COOKIE_NAME,
    AuthConfig,
    RateLimiter,
    _b64decode,
    _b64encode,
    config_from_env,
    hash_password,
    issue_session,
    read_session,
    verify_password,
)

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from ads.api import create_app  # noqa: E402

PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def config() -> AuthConfig:
    return AuthConfig(
        username="isobed18",
        password_hash=hash_password(PASSWORD),
        secret=b"test-secret-not-used-anywhere-real",
        session_hours=1.0,
        secure_cookie=False,
        max_failures=3,
        lockout_seconds=60,
    )


# ---------------------------------------------------------------- hashing


def test_hash_verifies_and_wrong_password_does_not() -> None:
    encoded = hash_password(PASSWORD)
    assert verify_password(PASSWORD, encoded)
    assert not verify_password(PASSWORD + "x", encoded)
    assert not verify_password("", encoded)


def test_hash_is_salted() -> None:
    # Two hashes of the same password must differ, or a leaked hash file would
    # reveal which accounts share a password.
    assert hash_password(PASSWORD) != hash_password(PASSWORD)


def test_malformed_hash_fails_closed() -> None:
    for broken in ["", "not-a-hash", "scrypt$1$2$3", "bcrypt$1$8$1$AAAA$AAAA"]:
        assert not verify_password(PASSWORD, broken)


# ---------------------------------------------------------------- sessions


def test_session_round_trip(config: AuthConfig) -> None:
    token = issue_session(config)
    assert read_session(token, config) == "isobed18"


def test_tampered_payload_is_rejected(config: AuthConfig) -> None:
    """Swap the payload for a *valid* one and keep the old signature.

    The naive version of this test flipped a base64 character, which produced
    unparseable JSON -- so it passed even with the signature check removed
    entirely. The substituted payload here is well-formed and unexpired, so the
    signature is the only thing that can reject it.
    """
    token = issue_session(config)
    _, signature = token.split(".", 1)
    forged_payload = json.dumps(
        {"u": config.username, "exp": time.time() + 86400},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    forged = _b64encode(forged_payload)
    assert json.loads(_b64decode(forged))["u"] == config.username, "payload not valid"
    assert read_session(f"{forged}.{signature}", config) is None


def test_signature_from_another_secret_is_rejected(config: AuthConfig) -> None:
    other = AuthConfig(
        username=config.username,
        password_hash=config.password_hash,
        secret=b"a-different-secret",
    )
    assert read_session(issue_session(other), config) is None


def test_expired_session_is_rejected(config: AuthConfig) -> None:
    token = issue_session(config, now=time.time() - 7200)  # 1h session, 2h ago
    assert read_session(token, config) is None


def test_session_for_a_renamed_user_is_rejected(config: AuthConfig) -> None:
    token = issue_session(config)
    renamed = AuthConfig(
        username="someone-else",
        password_hash=config.password_hash,
        secret=config.secret,
    )
    assert read_session(token, renamed) is None


def test_multi_user_sessions_are_scoped_to_current_accounts() -> None:
    config = AuthConfig(
        username="gonenc-ads",
        password_hash=hash_password(PASSWORD),
        secret=b"multi-user-test-secret",
        users={
            "gonenc-ads": hash_password(PASSWORD),
            "berkin-ads": hash_password("another-password"),
        },
    )
    token = issue_session(config, username="berkin-ads")
    assert read_session(token, config) == "berkin-ads"
    removed = AuthConfig(
        username="gonenc-ads",
        password_hash=config.credentials["gonenc-ads"],
        secret=config.secret,
        users={"gonenc-ads": config.credentials["gonenc-ads"]},
    )
    assert read_session(token, removed) is None


def test_garbage_token_is_rejected(config: AuthConfig) -> None:
    for junk in ["", ".", "no-dot", "a.b.c", "!!!.???"]:
        assert read_session(junk, config) is None


# ---------------------------------------------------------------- limiter


def test_limiter_locks_out_after_max_failures() -> None:
    limiter = RateLimiter(max_failures=3, lockout_seconds=60)
    assert limiter.retry_after("1.2.3.4") == 0
    for _ in range(2):
        limiter.record_failure("1.2.3.4")
    assert limiter.retry_after("1.2.3.4") == 0, "locked out too early"
    limiter.record_failure("1.2.3.4")
    assert limiter.retry_after("1.2.3.4") > 0, "third failure did not lock"


def test_lockout_is_per_client() -> None:
    limiter = RateLimiter(max_failures=2, lockout_seconds=60)
    limiter.record_failure("1.2.3.4")
    limiter.record_failure("1.2.3.4")
    assert limiter.retry_after("1.2.3.4") > 0
    assert limiter.retry_after("5.6.7.8") == 0


def test_lockout_expires() -> None:
    limiter = RateLimiter(max_failures=1, lockout_seconds=60)
    limiter.record_failure("1.2.3.4", now=1000.0)
    assert limiter.retry_after("1.2.3.4", now=1000.0) > 0
    assert limiter.retry_after("1.2.3.4", now=1061.0) == 0


def test_success_clears_the_counter() -> None:
    limiter = RateLimiter(max_failures=2, lockout_seconds=60)
    limiter.record_failure("1.2.3.4")
    limiter.record_success("1.2.3.4")
    limiter.record_failure("1.2.3.4")
    assert limiter.retry_after("1.2.3.4") == 0, "success did not reset the count"


# ---------------------------------------------------------------- env


def test_config_is_none_without_a_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADS_AUTH_USERNAME", raising=False)
    monkeypatch.delenv("ADS_AUTH_PASSWORD_HASH", raising=False)
    assert config_from_env() is None


def test_config_is_none_with_only_half_a_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Half a credential must not be read as "no credential, run open"; it is a
    # configuration mistake, and both halves are required to switch auth on.
    monkeypatch.setenv("ADS_AUTH_USERNAME", "isobed18")
    monkeypatch.delenv("ADS_AUTH_PASSWORD_HASH", raising=False)
    assert config_from_env() is None


def test_config_reads_multiple_users(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "ADS_AUTH_USERS_JSON",
        json.dumps({"gonenc-ads": hash_password(PASSWORD), "berkin-ads": hash_password("two")}),
    )
    monkeypatch.delenv("ADS_AUTH_USERNAME", raising=False)
    monkeypatch.delenv("ADS_AUTH_PASSWORD_HASH", raising=False)
    config = config_from_env()
    assert config is not None
    assert set(config.credentials) == {"gonenc-ads", "berkin-ads"}


# ---------------------------------------------------------------- the app


@pytest.fixture
def client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("ADS_AUTH_USERNAME", "isobed18")
    monkeypatch.setenv("ADS_AUTH_PASSWORD_HASH", hash_password(PASSWORD))
    monkeypatch.setenv("ADS_AUTH_SECRET", "test-secret")
    monkeypatch.setenv("ADS_AUTH_SECURE_COOKIE", "0")
    return TestClient(create_app(tmp_path), follow_redirects=False)


def test_api_requires_a_session(client: TestClient) -> None:
    assert client.get("/api/runs").status_code == 401


def test_page_redirects_to_login(client: TestClient) -> None:
    res = client.get("/")
    assert res.status_code == 303
    assert res.headers["location"] == "/login"


def test_login_page_is_public(client: TestClient) -> None:
    res = client.get("/login")
    assert res.status_code == 200
    assert "Giriş yap" in res.text


def test_health_is_public_and_leaks_nothing(client: TestClient) -> None:
    res = client.get("/api/health")
    assert res.status_code == 200
    assert set(res.json()) == {"status", "auth"}


def test_correct_credentials_open_the_api(client: TestClient) -> None:
    res = client.post("/api/auth/login", json={"username": "isobed18", "password": PASSWORD})
    assert res.status_code == 200
    assert COOKIE_NAME in res.cookies
    assert client.get("/api/runs").status_code == 200


def test_second_configured_user_can_login(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "ADS_AUTH_USERS_JSON",
        json.dumps({"gonenc-ads": hash_password(PASSWORD), "berkin-ads": hash_password("two")}),
    )
    monkeypatch.delenv("ADS_AUTH_USERNAME", raising=False)
    monkeypatch.delenv("ADS_AUTH_PASSWORD_HASH", raising=False)
    monkeypatch.setenv("ADS_AUTH_SECRET", "test-secret")
    monkeypatch.setenv("ADS_AUTH_SECURE_COOKIE", "0")
    client = TestClient(create_app(tmp_path), follow_redirects=False)
    response = client.post(
        "/api/auth/login", json={"username": "berkin-ads", "password": "two"}
    )
    assert response.status_code == 200
    assert response.json()["username"] == "berkin-ads"
    assert client.get("/api/runs").status_code == 200


def test_wrong_password_is_rejected(client: TestClient) -> None:
    res = client.post("/api/auth/login", json={"username": "isobed18", "password": "wrong"})
    assert res.status_code == 401
    assert client.get("/api/runs").status_code == 401


def test_wrong_username_is_rejected(client: TestClient) -> None:
    res = client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    assert res.status_code == 401


def test_logout_closes_the_session(client: TestClient) -> None:
    client.post("/api/auth/login", json={"username": "isobed18", "password": PASSWORD})
    assert client.get("/api/runs").status_code == 200
    client.post("/api/auth/logout")
    assert client.get("/api/runs").status_code == 401


def test_forged_cookie_does_not_open_the_api(client: TestClient) -> None:
    """A hand-built cookie claiming a live session must not be accepted.

    The payload is deliberately complete -- right username, expiry a day out --
    so nothing but the missing signature can turn it away. An earlier version
    of this test omitted `exp` and was therefore rejected by the expiry check,
    passing even when signature verification was disabled.
    """
    payload = _b64encode(
        json.dumps(
            {"u": "isobed18", "exp": time.time() + 86400},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    client.cookies.set(COOKIE_NAME, f"{payload}.notarealsignature")
    assert client.get("/api/runs").status_code == 401


def test_repeated_failures_lock_the_endpoint(client: TestClient) -> None:
    codes = [
        client.post(
            "/api/auth/login", json={"username": "isobed18", "password": "wrong"}
        ).status_code
        for _ in range(9)
    ]
    assert 429 in codes, f"never rate limited: {codes}"
    # And the lockout must hold even for the *correct* password, or it is not a
    # lockout, only a message.
    blocked = client.post("/api/auth/login", json={"username": "isobed18", "password": PASSWORD})
    assert blocked.status_code == 429


def test_no_auth_configured_leaves_the_api_open(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The historical behaviour the loopback launcher and the rest of the suite
    # depend on. serve_public.py is what prevents this reaching the tunnel.
    for name in ("ADS_AUTH_USERNAME", "ADS_AUTH_PASSWORD_HASH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("ADS_AUTH_USERS_JSON", raising=False)
    open_client = TestClient(create_app(tmp_path))
    assert open_client.get("/api/runs").status_code == 200
