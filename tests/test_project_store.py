"""Projects persist above automations and keep an open data pool."""

from __future__ import annotations

import json

import pytest

from ads.automation import AutomationStore
from ads.projects import ProjectRevisionConflict, ProjectStore


def test_project_survives_restart_and_keeps_revision_history(tmp_path) -> None:
    root = tmp_path / "projects"
    store = ProjectStore(root)
    created = store.create("Customer retention")

    saved = store.add_source(created.project_id, source_id="upload:customers")

    assert ProjectStore(root).get(created.project_id) == saved
    assert saved.source_ids == ("upload:customers",)
    assert saved.automation_ids == ()
    revision_files = sorted((root / "revisions" / created.project_id).glob("*.json"))
    assert [path.name for path in revision_files] == ["000001.json", "000002.json"]
    assert json.loads(revision_files[0].read_text(encoding="utf-8"))["source_ids"] == []


def test_project_rejects_stale_updates_and_duplicate_sources_are_idempotent(tmp_path) -> None:
    store = ProjectStore(tmp_path / "projects")
    created = store.create("Research")
    saved = store.add_source(created.project_id, source_id="upload:research")

    assert store.add_source(created.project_id, source_id="upload:research") == saved
    with pytest.raises(ProjectRevisionConflict, match="revision changed"):
        store.update(
            created.project_id,
            expected_revision=created.revision,
            changes={"name": "Stale name"},
        )


def test_project_pool_stays_open_after_an_automation_input_freezes(tmp_path) -> None:
    """#157: output history freezes the child automation's selected input,
    never its parent's pool. Both halves are asserted so one cannot be fixed by
    weakening the other."""
    projects = ProjectStore(tmp_path / "projects")
    automations = AutomationStore(tmp_path / "automations")
    project = projects.create("Retention")
    projects.add_source(project.project_id, source_id="upload:customers")
    automation = automations.create("Churn flow")
    executed = automations.attach_execution(
        automation.automation_id,
        run_id="run-a1b2c3d4",
        source_id="upload:customers",
    )

    expanded = projects.add_source(project.project_id, source_id="upload:campaigns")
    assert expanded.source_ids == ("upload:customers", "upload:campaigns")
    with pytest.raises(ValueError, match="cannot change after its first execution"):
        automations.update(
            automation.automation_id,
            expected_revision=executed.revision,
            changes={"source_id": "upload:campaigns"},
        )


def test_project_store_deletes_definition_and_revisions(tmp_path) -> None:
    store = ProjectStore(tmp_path / "projects")
    project = store.create("Disposable")
    store.add_source(project.project_id, source_id="upload:data")

    assert store.delete(project.project_id) == {"definitions": 1, "revisions": 2}
    assert store.list() == []
    with pytest.raises(KeyError):
        store.get(project.project_id)
