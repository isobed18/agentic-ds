"""Entry point for a container platform (Railway, Fly, any PaaS).

`serve_public.py` is the tunnel launcher and is deliberately rigid about two
things: it binds loopback only, and it reads its configuration from `.auth.env`
and `.runtime.env` next to the repository. Both are correct there -- cloudflared
connects from the same host, so the server is never on the LAN, and the files
are machine-written by `set_users.py`.

Neither survives a container. The platform routes to a published port, so the
process must bind `0.0.0.0`; and the platform supplies configuration as
environment variables, so there is no file to read. Rather than loosen the
tunnel launcher and risk its loopback guarantee becoming a flag someone can
turn off by accident, this is a second entry point with its own rules.

What is kept is the property that matters: **it refuses to start without a
credential.** A container platform gives out a public URL immediately, so
booting unauthenticated would publish the control plane to the internet, which
is a worse outcome than a failed deploy.

What must be set in the platform's variables:

    ADS_AUTH_USERS_JSON   the output of scripts/set_users.py, or the legacy
                          ADS_AUTH_USERNAME + ADS_AUTH_PASSWORD_HASH pair
    ADS_AUTH_SECRET       session signing key; rotating it logs everyone out
    ADS_LLM_BACKEND       deepseek  (see the note below)
    DEEPSEEK_API          the API key
    ADS_DEEPSEEK_USERS    who may spend it, default ishak-ads
    ADS_TEAMS             optional; unset means everyone shares one team

**Backend note.** `claude_cli` cannot work here -- it drives an interactive
Claude Code session that is logged in on someone's machine, and there is no such
session in a container. `ollama` cannot work either without a model server to
talk to. `deepseek` is the backend that works on a PaaS, and it is remote paid
inference, which is worth saying out loud given the project describes itself as
local-first.

**Storage.** `--data` must be a mounted volume. A container filesystem is
discarded on every deploy, so uploads, artifacts and run state written to the
image are gone the next time anything ships.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _configured_credential() -> bool:
    """True when the environment carries an account the gate can check."""
    if os.environ.get("ADS_AUTH_USERS_JSON", "").strip():
        return True
    return bool(
        os.environ.get("ADS_AUTH_USERNAME", "").strip()
        and os.environ.get("ADS_AUTH_PASSWORD_HASH", "").strip()
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    # The platform chooses the port and passes it in; the default is only for
    # running the container locally.
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))  # noqa: S104
    parser.add_argument(
        "--data",
        type=Path,
        default=Path(os.environ.get("ADS_DATA_DIR", "/data")),
        help="Mounted volume. A container filesystem does not survive a deploy.",
    )
    args = parser.parse_args()

    if not _configured_credential():
        raise SystemExit(
            "No credential in the environment. Set ADS_AUTH_USERS_JSON (from\n"
            "scripts/set_users.py) or ADS_AUTH_USERNAME + ADS_AUTH_PASSWORD_HASH.\n"
            "Refusing to start: this platform publishes a URL immediately, so an\n"
            "unauthenticated boot would put the control plane on the internet."
        )

    if not os.environ.get("ADS_AUTH_SECRET", "").strip():
        raise SystemExit(
            "ADS_AUTH_SECRET is not set. Sessions would be signed with a key that\n"
            "changes on every restart, logging everyone out on each deploy."
        )

    backend = os.environ.get("ADS_LLM_BACKEND", "").strip().casefold()
    if backend in {"claude_cli", "ollama"}:
        raise SystemExit(
            f"ADS_LLM_BACKEND={backend} cannot work in a container: it needs a\n"
            "logged-in Claude Code session or a local model server, and neither\n"
            "exists here. Use deepseek, and note that it is remote paid inference."
        )

    sys.path.insert(0, str(ROOT / "src"))

    try:
        import uvicorn
    except ModuleNotFoundError:
        raise SystemExit('uvicorn is not installed. Build with: pip install -e ".[api]"') from None

    from ads.api import create_app

    # The volume is empty on first boot. Creating the tree is the difference
    # between a working first deploy and a crash loop nobody can read.
    data = args.data
    artifacts = data / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    (data / "uploads").mkdir(parents=True, exist_ok=True)
    (data / "logs").mkdir(parents=True, exist_ok=True)

    print(f"Data:      {data.resolve()}")
    print(f"Artifacts: {artifacts.resolve()}")
    print(f"LLM:       {backend or 'ollama (default -- will not work here)'}")
    print(f"Bound to:  {args.host}:{args.port}")
    print("Password gate: ON")

    application = create_app(artifacts, source_roots=[data])
    uvicorn.run(application, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
