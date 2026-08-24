"""Serve the control plane for exposure through a Cloudflare tunnel.

    python scripts/serve_public.py

Differs from serve_ui.py in exactly two ways, both of which matter:

* It **refuses to start** unless a credential exists in `.auth.env`. The
  password gate is off by default in the application so that local development
  and the test suite are unaffected; this launcher is the thing that makes the
  permissive default impossible to expose by accident.
* It stays bound to loopback and says so. cloudflared connects to 127.0.0.1
  from this same host, so the server is never on the LAN and never listens on
  a routable address. The tunnel is the only way in, and the password is the
  only way through it.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".auth.env"


def load_env_file(path: Path) -> dict[str, str]:
    """Read a trivial KEY=VALUE file. Not a dotenv implementation; no quoting,
    no interpolation, no export statements -- the file is machine-written."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, default=ROOT / "data/artifacts")
    # Both default to this file's repository rather than the working directory.
    # A deployment is served from a checkout that has no data/ of its own, and
    # a relative default there means no datasets and no error to say why.
    parser.add_argument("--data", type=Path, default=ROOT / "data")
    parser.add_argument("--port", type=int, default=8077)
    parser.add_argument(
        "--insecure-cookie",
        action="store_true",
        help="Allow the session cookie over plain HTTP. Only for testing this "
        "launcher on localhost; the tunnel always terminates TLS.",
    )
    args = parser.parse_args()

    values = load_env_file(ENV_PATH)
    if not values.get("ADS_AUTH_USERNAME") or not values.get("ADS_AUTH_PASSWORD_HASH"):
        raise SystemExit(
            f"No credential found in {ENV_PATH}.\n"
            "This launcher will not expose the control plane without one. Run:\n"
            "  python scripts/set_password.py --username <name>"
        )
    os.environ.update(values)
    if args.insecure_cookie:
        os.environ["ADS_AUTH_SECURE_COOKIE"] = "0"

    sys.path.insert(0, str(ROOT / "src"))

    try:
        import uvicorn
    except ModuleNotFoundError:
        raise SystemExit(
            "uvicorn is not installed. Run:\n"
            '  python -m uv pip install --python .venv/Scripts/python.exe -e ".[api]"'
        ) from None

    from ads.api import create_app

    args.artifacts.mkdir(parents=True, exist_ok=True)
    if not args.data.is_dir():
        raise SystemExit(
            f"No data directory at {args.data.resolve()}.\n"
            "Pass --data <path> pointing at the folder that holds the datasets."
        )
    print(f"Artifacts: {args.artifacts.resolve()}")
    print(f"Data:      {args.data.resolve()}")
    print(f"User:      {values['ADS_AUTH_USERNAME']}")
    print(f"Bound to:  127.0.0.1:{args.port}  (loopback only -- not on the LAN)")
    print("Password gate: ON")

    uvicorn.run(
        create_app(args.artifacts, source_roots=[args.data]),
        host="127.0.0.1",
        port=args.port,
        # cloudflared is the only client and it is on this host, so the
        # proxy headers uvicorn would parse are Cloudflare's, not a caller's.
        forwarded_allow_ips="127.0.0.1",
    )


if __name__ == "__main__":
    main()
