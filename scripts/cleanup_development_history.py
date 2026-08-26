"""One-time cleanup of persisted development/test executions.

This script is deliberately opt-in. It never runs during application startup,
and defaults to a read-only inventory. Pass ``--execute`` only when the listed
runs are known disposable development history.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ads.api import ControlPlane
from ads.store import ArtifactStore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, default=Path("data/artifacts"))
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    artifact_root = args.artifacts.resolve()
    plane = ControlPlane(store=ArtifactStore(artifact_root))
    runs = plane.list_runs()
    print(
        json.dumps(
            {
                "artifact_root": str(artifact_root),
                "mode": "execute" if args.execute else "dry_run",
                "run_count": len(runs),
                "runs": [
                    {
                        "run_id": item.run_id,
                        "status": item.status,
                        "artifact_count": item.artifact_count,
                    }
                    for item in runs
                ],
            },
            indent=2,
        )
    )
    if not args.execute:
        return

    deleted = []
    for item in runs:
        deleted.append(plane.delete_run(item.run_id, confirmation=item.run_id))
    print(json.dumps({"deleted": deleted, "remaining": len(plane.list_runs())}, indent=2))


if __name__ == "__main__":
    main()
