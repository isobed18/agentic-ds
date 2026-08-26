"""Saved automations persist independently from execution snapshots."""

from __future__ import annotations

import json

import pytest

from ads.automation import AutomationRevisionConflict, AutomationStore
from ads.staging import build_default_blueprint


def test_automation_survives_store_restart_and_keeps_revision_history(tmp_path) -> None:
    root = tmp_path / "automations"
    store = AutomationStore(root)
    created = store.create("Department research analysis")
    blueprint = build_default_blueprint(has_tables=True, has_documents=True)

    saved = store.update(
        created.automation_id,
        expected_revision=created.revision,
        changes={
            "status": "saved",
            "source_id": "upload:research",
            "pipeline_blueprint": blueprint,
        },
    )

    reloaded = AutomationStore(root).get(created.automation_id)
    assert reloaded == saved
    assert reloaded.revision == 2
    assert reloaded.pipeline_blueprint == blueprint
    revision_files = sorted((root / "revisions" / created.automation_id).glob("*.json"))
    assert [path.name for path in revision_files] == ["000001.json", "000002.json"]
    assert json.loads(revision_files[0].read_text(encoding="utf-8"))["status"] == "draft"


def test_automation_rejects_stale_editor_save(tmp_path) -> None:
    store = AutomationStore(tmp_path / "automations")
    created = store.create("Research")
    store.update(
        created.automation_id,
        expected_revision=created.revision,
        changes={"name": "Renamed research"},
    )

    with pytest.raises(AutomationRevisionConflict, match="revision changed"):
        store.update(
            created.automation_id,
            expected_revision=created.revision,
            changes={"name": "Stale name"},
        )


def test_execution_history_is_attached_without_becoming_editor_state(tmp_path) -> None:
    store = AutomationStore(tmp_path / "automations")
    created = store.create("Reusable analysis")

    first = store.attach_execution(
        created.automation_id,
        run_id="run-a1b2c3d4",
        source_id="upload:data",
    )
    second = store.attach_execution(
        created.automation_id,
        run_id="run-e5f6a7b8",
        source_id="upload:data",
    )

    assert first.execution_ids == ("run-a1b2c3d4",)
    assert second.execution_ids == ("run-a1b2c3d4", "run-e5f6a7b8")
    assert second.pipeline_blueprint is None
