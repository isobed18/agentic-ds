"""Filesystem persistence for top-level project containers."""

from __future__ import annotations

import json
import shutil
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ads.contracts.project import ProjectDefinition


class ProjectRevisionConflict(ValueError):
    """Raised when a client saves over a newer project revision."""


class ProjectStore:
    """Atomically save project state and immutable revision snapshots."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.current_root = self.root / "current"
        self.revision_root = self.root / "revisions"
        self.current_root.mkdir(parents=True, exist_ok=True)
        self.revision_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def create(self, name: str) -> ProjectDefinition:
        normalized = name.strip()
        if not normalized:
            raise ValueError("project name cannot be empty")
        definition = ProjectDefinition(
            project_id=f"project-{uuid.uuid4().hex[:12]}",
            name=normalized,
        )
        with self._lock:
            self._write(definition)
        return definition

    def list(self) -> list[ProjectDefinition]:
        with self._lock:
            records = [self._read(path) for path in self.current_root.glob("project-*.json")]
        return sorted(records, key=lambda item: item.updated_at, reverse=True)

    def get(self, project_id: str) -> ProjectDefinition:
        target = self._target(project_id)
        if not target.exists():
            raise KeyError(project_id)
        with self._lock:
            return self._read(target)

    def update(
        self,
        project_id: str,
        *,
        expected_revision: int,
        changes: dict[str, Any],
    ) -> ProjectDefinition:
        allowed = {"name", "source_ids", "automation_ids"}
        unknown = sorted(set(changes) - allowed)
        if unknown:
            raise ValueError(f"unsupported project fields: {', '.join(unknown)}")
        with self._lock:
            current = self.get(project_id)
            if current.revision != expected_revision:
                raise ProjectRevisionConflict(
                    f"project revision changed from {expected_revision} to {current.revision}"
                )
            payload = current.model_dump(mode="python")
            payload.update(changes)
            payload["revision"] = current.revision + 1
            payload["updated_at"] = datetime.now(UTC)
            saved = ProjectDefinition.model_validate(payload)
            self._write(saved)
            return saved

    def add_source(self, project_id: str, *, source_id: str) -> ProjectDefinition:
        normalized = source_id.strip()
        if not normalized:
            raise ValueError("source id cannot be empty")
        with self._lock:
            current = self.get(project_id)
            if normalized in current.source_ids:
                return current
            return self.update(
                project_id,
                expected_revision=current.revision,
                changes={"source_ids": (*current.source_ids, normalized)},
            )

    def add_automation(self, project_id: str, *, automation_id: str) -> ProjectDefinition:
        normalized = automation_id.strip()
        if not normalized:
            raise ValueError("automation id cannot be empty")
        with self._lock:
            current = self.get(project_id)
            if normalized in current.automation_ids:
                return current
            return self.update(
                project_id,
                expected_revision=current.revision,
                changes={"automation_ids": (*current.automation_ids, normalized)},
            )

    def delete(self, project_id: str) -> dict[str, int]:
        target = self._target(project_id)
        revision_dir = (self.revision_root / project_id).resolve()
        if self.revision_root not in revision_dir.parents:
            raise ValueError("invalid project revision path")
        with self._lock:
            if not target.is_file():
                raise KeyError(project_id)
            revision_count = (
                sum(1 for path in revision_dir.glob("*.json") if path.is_file())
                if revision_dir.is_dir()
                else 0
            )
            target.unlink()
            if revision_dir.is_dir():
                shutil.rmtree(revision_dir)
        return {"definitions": 1, "revisions": revision_count}

    def _target(self, project_id: str) -> Path:
        suffix = project_id.removeprefix("project-")
        if (
            not project_id.startswith("project-")
            or len(suffix) != 12
            or any(char not in "0123456789abcdef" for char in suffix)
        ):
            raise ValueError("invalid project id")
        target = (self.current_root / f"{project_id}.json").resolve()
        if self.current_root not in target.parents:
            raise ValueError("invalid project id")
        return target

    @staticmethod
    def _read(path: Path) -> ProjectDefinition:
        return ProjectDefinition.model_validate_json(path.read_text(encoding="utf-8"))

    def _write(self, definition: ProjectDefinition) -> None:
        payload = json.dumps(definition.model_dump(mode="json"), indent=2, ensure_ascii=False)
        current = self._target(definition.project_id)
        revision_dir = (self.revision_root / definition.project_id).resolve()
        if self.revision_root not in revision_dir.parents:
            raise ValueError("invalid project revision path")
        revision_dir.mkdir(parents=True, exist_ok=True)
        revision = revision_dir / f"{definition.revision:06d}.json"
        self._atomic_write(revision, payload)
        self._atomic_write(current, payload)

    @staticmethod
    def _atomic_write(target: Path, payload: str) -> None:
        temporary = target.with_suffix(f"{target.suffix}.tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(target)


__all__ = ["ProjectRevisionConflict", "ProjectStore"]
