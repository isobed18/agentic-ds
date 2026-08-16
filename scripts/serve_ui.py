"""Serve the control-plane UI.

    python scripts/serve_ui.py                 # http://127.0.0.1:8077
    python scripts/serve_ui.py --port 9000
    python scripts/serve_ui.py --host 0.0.0.0  # reachable from other devices

Binds to loopback by default. This surface exposes run metadata and artifact
payloads; on enterprise data that is not something to put on a LAN without
thinking about it, so widening the bind is an explicit choice.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, default=Path("data/artifacts"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8077)
    args = parser.parse_args()

    try:
        import uvicorn
    except ModuleNotFoundError:
        raise SystemExit(
            "uvicorn is not installed. Run:\n"
            '  python -m uv pip install --python .venv/Scripts/python.exe -e ".[api]"'
        ) from None

    from ads.api import create_app

    args.artifacts.mkdir(parents=True, exist_ok=True)
    print(f"Serving artifacts from {args.artifacts.resolve()}")
    print(f"Open http://{args.host}:{args.port}")
    uvicorn.run(create_app(args.artifacts), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
