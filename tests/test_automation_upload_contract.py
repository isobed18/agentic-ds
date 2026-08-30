"""Project-owned uploads stay visible, cancellable, and out of automations."""

from __future__ import annotations

from pathlib import Path

PAGES = Path(__file__).resolve().parents[1] / "web" / "src" / "pages"


def test_upload_lives_on_project_data_and_reports_failures() -> None:
    project = (PAGES / "ProjectWorkspace.tsx").read_text(encoding="utf-8")
    automation = (PAGES / "AutomationWorkspace.tsx").read_text(encoding="utf-8")

    assert "async function upload(" in project
    assert "api.addProjectSource(projectId, group)" in project
    assert "setError(messageOf(caught))" in project
    assert "api.upload" not in automation


def test_optional_reuse_picker_cannot_take_project_uploading_down() -> None:
    project = (PAGES / "ProjectWorkspace.tsx").read_text(encoding="utf-8")

    assert "api.dataSources().then(setSources).catch(() => setSources([]))" in project
    assert "Promise.all([api.dataSources" not in project


def test_upload_progress_precedes_disabled_project_actions() -> None:
    project = (PAGES / "ProjectWorkspace.tsx").read_text(encoding="utf-8")

    assert project.index("{progress &&") < project.index('t("Upload new files")')
    assert project.count("disabled={uploading}") >= 2
    assert "controller.current?.abort()" in project
