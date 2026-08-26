"""Filesystem persistence for reusable automation definitions."""

from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ads.contracts.automation_definition import AutomationDefinition


class AutomationRevisionConflict(ValueError):
    """Raised when a client saves over a newer editor revision."""


class AutomationStore:
    """Atomically saves current definitions and immutable revision snapshots."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.current_root = self.root / "current"
        self.revision_root = self.root / "revisions"
        self.current_root.mkdir(parents=True, exist_ok=True)
        self.revision_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def create(self, name: str) -> AutomationDefinition:
        normalized = name.strip()
        if not normalized:
            raise ValueError("automation name cannot be empty")
        definition = AutomationDefinition(
            automation_id=f"automation-{uuid.uuid4().hex[:12]}",
            name=normalized,
        )
        with self._lock:
            self._write(definition)
        return definition

    def list(self) -> list[AutomationDefinition]:
        with self._lock:
            records = [self._read(path) for path in self.current_root.glob("automation-*.json")]
        return sorted(records, key=lambda item: item.updated_at, reverse=True)

    def get(self, automation_id: str) -> AutomationDefinition:
        target = self._target(automation_id)
        if not target.exists():
            raise KeyError(automation_id)
        with self._lock:
            return self._read(target)

    def update(
        self,
        automation_id: str,
        *,
        expected_revision: int,
        changes: dict[str, Any],
    ) -> AutomationDefinition:
        allowed = {
            "name",
            "status",
            "source_id",
            "pipeline_blueprint",
            "pipeline_layout",
            "workspace_artifact_id",
            "execution_ids",
        }
        unknown = sorted(set(changes) - allowed)
        if unknown:
            raise ValueError(f"unsupported automation fields: {', '.join(unknown)}")
        with self._lock:
            current = self.get(automation_id)
            if current.revision != expected_revision:
                raise AutomationRevisionConflict(
                    f"automation revision changed from {expected_revision} to {current.revision}"
                )
            payload = current.model_dump(mode="python")
            payload.update(changes)
            payload["revision"] = current.revision + 1
            payload["updated_at"] = datetime.now(UTC)
            saved = AutomationDefinition.model_validate(payload)
            self._write(saved)
            return saved

    def attach_execution(
        self,
        automation_id: str,
        *,
        run_id: str,
        source_id: str,
    ) -> AutomationDefinition:
        with self._lock:
            current = self.get(automation_id)
            execution_ids = tuple(dict.fromkeys((*current.execution_ids, run_id)))
            return self.update(
                automation_id,
                expected_revision=current.revision,
                changes={
                    "source_id": source_id,
                    "execution_ids": execution_ids,
                },
            )

    def sync_workspace(
        self,
        automation_id: str,
        *,
        source_id: str,
        workspace_artifact_id: str,
        pipeline_blueprint: Any,
        pipeline_layout: Any,
    ) -> AutomationDefinition:
        with self._lock:
            current = self.get(automation_id)
            return self.update(
                automation_id,
                expected_revision=current.revision,
                changes={
                    "status": "saved",
                    "source_id": source_id,
                    "workspace_artifact_id": workspace_artifact_id,
                    "pipeline_blueprint": pipeline_blueprint,
                    "pipeline_layout": pipeline_layout,
                },
            )

    def _target(self, automation_id: str) -> Path:
        suffix = automation_id.removeprefix("automation-")
        if (
            not automation_id.startswith("automation-")
            or len(suffix) != 12
            or any(char not in "0123456789abcdef" for char in suffix)
        ):
            raise ValueError("invalid automation id")
        target = (self.current_root / f"{automation_id}.json").resolve()
        if self.current_root not in target.parents:
            raise ValueError("invalid automation id")
        return target

    @staticmethod
    def _read(path: Path) -> AutomationDefinition:
        return AutomationDefinition.model_validate_json(path.read_text(encoding="utf-8"))

    def _write(self, definition: AutomationDefinition) -> None:
        payload = json.dumps(definition.model_dump(mode="json"), indent=2, ensure_ascii=False)
        current = self._target(definition.automation_id)
        revision_dir = (self.revision_root / definition.automation_id).resolve()
        if self.revision_root not in revision_dir.parents:
            raise ValueError("invalid automation revision path")
        revision_dir.mkdir(parents=True, exist_ok=True)
        revision = revision_dir / f"{definition.revision:06d}.json"
        self._atomic_write(revision, payload)
        self._atomic_write(current, payload)

    @staticmethod
    def _atomic_write(target: Path, payload: str) -> None:
        temporary = target.with_suffix(f"{target.suffix}.tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(target)


__all__ = ["AutomationRevisionConflict", "AutomationStore"]
