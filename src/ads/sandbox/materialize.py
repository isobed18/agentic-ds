"""Materialize process-local frames as read-only execution-backend inputs."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

from ads.sandbox.backend import ExecutionBackend

_SAFE_TABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TRACKING_FILE = ".ads_materialized_frames.json"


def _tracked_tables(root: Path) -> set[str]:
    manifest = root / _TRACKING_FILE
    if not manifest.exists():
        return set()
    try:
        raw = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Materialized-frame tracking file is unreadable.") from exc
    if not isinstance(raw, list) or not all(
        isinstance(item, str) and _SAFE_TABLE.fullmatch(item) for item in raw
    ):
        raise ValueError("Materialized-frame tracking file contains unsafe table names.")
    return set(raw)


def _write_tracking(root: Path, tables: set[str]) -> None:
    manifest = root / _TRACKING_FILE
    temporary = Path(str(manifest) + ".tmp")
    temporary.write_text(json.dumps(sorted(tables)), encoding="utf-8")
    temporary.replace(manifest)


def materialize_frame_copies(
    backend: ExecutionBackend,
    frames: dict[str, pd.DataFrame],
    *,
    replace_existing: bool = False,
) -> dict[str, str]:
    """Write atomic CSV copies and return table-to-container-path mappings.

    These files are derived inputs for an isolated runtime. The original source
    files and the in-process DataFrames are never exposed as writable mounts.
    """
    root = backend.data_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    tracked = _tracked_tables(root)
    if replace_existing:
        for table in tracked:
            target = (root / f"{table}.csv").resolve()
            if root not in target.parents:
                raise ValueError("Tracked materialized path escaped the data directory.")
            target.unlink(missing_ok=True)
        tracked.clear()
    paths: dict[str, str] = {}
    for table, frame in frames.items():
        if not _SAFE_TABLE.fullmatch(table):
            raise ValueError(
                f"Table name {table!r} cannot be materialized safely for execution."
            )
        target = (root / f"{table}.csv").resolve()
        if root not in target.parents:
            raise ValueError(f"Materialized path for {table!r} escaped the data directory.")
        temporary = Path(str(target) + ".tmp")
        frame.to_csv(temporary, index=False)
        temporary.replace(target)
        paths[table] = f"/data/{target.name}"
        tracked.add(table)
    _write_tracking(root, tracked)
    return paths


__all__ = ["materialize_frame_copies"]
