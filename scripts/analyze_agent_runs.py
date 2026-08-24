"""Command-line script to analyze agent behavior from repository run logs."""

from __future__ import annotations

import argparse
from pathlib import Path

from ads.reporting.agent_analytics import analyze_run_records, format_agent_analytics_report
from ads.store import ArtifactStore


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze empirical agent behavior across persisted pipeline runs."
    )
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=Path("data/artifacts"),
        help="Path to artifact store root (default: data/artifacts)",
    )
    parser.add_argument(
        "--run-state-dir",
        type=Path,
        default=Path("data/run-state"),
        help="Path to run-state snapshots directory (default: data/run-state)",
    )
    args = parser.parse_args()

    store = ArtifactStore(args.artifacts_dir)
    summary = analyze_run_records(store=store, run_state_root=args.run_state_dir)
    report = format_agent_analytics_report(summary)
    print(report)


if __name__ == "__main__":
    main()
