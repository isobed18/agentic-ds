"""Vertical-slice tests for source selection, run launch, and progress polling."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from ads.api import ControlPlane, create_app

# Private, like `_quote` in test_intake: the budget the guard actually spends is
# imported rather than restated, so the two cannot drift apart.
from ads.api.service import _MIN_UPLOAD_NAME_BUDGET, _check_upload_path_fits
from ads.contracts import (
    IntegrationPlan,
    Metric,
    ProblemDefinition,
    SplitStrategy,
    TaskType,
    ValidationStrategy,
)
from ads.store import ArtifactStore


def _contracts() -> dict:
    return {
        "integration_plan": IntegrationPlan(
            base_table="table",
            base_grain=["id"],
            grain_description="One row per id.",
            joins=[],
        ).model_dump(mode="json"),
        "problem": ProblemDefinition(
            task_type=TaskType.REGRESSION,
            target_column="target",
            primary_metric=Metric.RMSE,
            title="Predict target",
            description="A bounded test problem.",
            confirmed_by="auto",
        ).model_dump(mode="json"),
        "validation_strategy": ValidationStrategy(
            strategy=SplitStrategy.RANDOM,
            n_folds=3,
            test_size=0.2,
            rationale="Rows are independent.",
        ).model_dump(mode="json"),
    }


def _plane(tmp_path: Path) -> ControlPlane:
    sources = tmp_path / "sources"
    (sources / "demo").mkdir(parents=True)
    (sources / "demo" / "table.csv").write_text("id,target\n1,2\n", encoding="utf-8")
    return ControlPlane(
        store=ArtifactStore(tmp_path / "artifacts"),
        source_roots=(sources,),
        upload_root=tmp_path / "uploads",
    )


def test_sources_and_uploads_are_selectable_without_path_traversal(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    assert plane.data_sources() == [{"source_id": "demo", "label": "demo"}]
    uploaded = plane.upload("new.csv", b"id,target\n2,4\n")
    assert uploaded["source_id"].startswith("upload:")
    assert {item["source_id"] for item in plane.data_sources()} == {
        "demo",
        uploaded["source_id"],
    }

    try:
        plane.source_path("../artifacts")
    except KeyError:
        pass
    else:
        raise AssertionError("source selection escaped the configured root")


def test_each_structured_file_carries_a_row_free_profile_insight(tmp_path: Path) -> None:
    """The Data tab and Intake used to receive only a file name and route.

    The summary must come from the same measured DataCards the agents are
    allowed to see: counts, keys and issue codes, never source values.
    """
    plane = _plane(tmp_path)
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with (tmp_path / "sources" / "demo" / "context.pdf").open("wb") as handle:
        writer.write(handle)

    profile = plane.source_profile("demo")
    file_summary = next(
        item for item in profile["source_files"] if item["name"] == "table.csv"
    )
    document_summary = next(
        item for item in profile["source_files"] if item["name"] == "context.pdf"
    )

    assert file_summary["rows"] == 1
    assert file_summary["tables"] == 1
    assert file_summary["schema_role"]["en"]
    assert file_summary["candidate_keys"] == []
    assert "no key candidate" in file_summary["insight"]["en"]
    assert "1 row" in file_summary["insight"]["en"]
    assert "1 satır" in file_summary["insight"]["tr"]
    assert "1,2" not in str(file_summary), "raw source values leaked into the insight"
    assert document_summary["schema_role"]["en"] == "document context"
    assert "1 page" in document_summary["insight"]["en"]
    assert "OCR or vision needed" in document_summary["insight"]["en"]

    # Project Data reads the catalog-shaped summary, not SourceProfile
    # directly. Keep the exact per-file evidence on that path too.
    catalog = plane._dataset_summary({"source_id": "demo", "label": "demo"})
    catalog_files = {item["name"]: item for item in catalog["file_summaries"]}
    assert catalog_files["table.csv"]["insight"] == file_summary["insight"]
    assert catalog_files["context.pdf"]["insight"] == document_summary["insight"]


def test_an_identical_single_file_upload_reuses_its_owned_source(tmp_path: Path) -> None:
    """The same bytes used to create a fresh directory and source every time."""
    plane = _plane(tmp_path)
    content = b"id,target\n2,4\n"

    first = plane.upload("bank.csv", content, owner="ishak-ads")
    repeated = plane.upload("bank-copy.csv", content, owner="ishak-ads")

    assert repeated["source_id"] == first["source_id"]
    assert repeated["files"] == ["bank.csv"]
    assert repeated["reused"] is True
    assert len([path for path in plane.upload_root.iterdir() if path.is_dir()]) == 1


def test_identical_content_owned_by_someone_else_is_not_disclosed(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    content = b"id,target\n2,4\n"

    first = plane.upload("bank.csv", content, owner="ishak-ads")
    other = plane.upload("bank.csv", content, owner="emre-ads")

    assert other["source_id"] != first["source_id"]


def test_a_long_file_name_is_refused_with_a_readable_message(tmp_path: Path) -> None:
    """It used to reach `write_bytes` and surface as a bare OSError.

    The limit is the application's own: nothing here asks the host whether it
    has Windows long-path support, because that switch is not this program's to
    rely on.
    """
    plane = _plane(tmp_path)

    with pytest.raises(ValueError, match="file name is too long"):
        plane.upload(f"{'a' * 300}.csv", b"id,target\n2,4\n")


def test_a_refused_name_leaves_no_upload_group_behind(tmp_path: Path) -> None:
    """The group used to be created before the name was known to be writable."""
    plane = _plane(tmp_path)
    before = plane.data_sources()

    with pytest.raises(ValueError):
        plane.upload(f"{'a' * 300}.csv", b"id,target\n2,4\n")

    assert plane.data_sources() == before, "a rejected upload created a group"
    uploads = tmp_path / "uploads"
    assert not uploads.exists() or not any(uploads.iterdir())


def test_a_name_that_fits_the_component_limit_can_still_overrun_the_path(
    tmp_path: Path,
) -> None:
    """255 bytes is legal as a name and still too long once the root is joined.

    This is the case the component check alone misses, and the one MAX_PATH is
    actually about.
    """
    plane = _plane(tmp_path)
    name = f"{'a' * 240}.csv"
    assert len(name.encode("utf-8")) <= 255

    with pytest.raises(ValueError, match="file name is too long"):
        plane.upload(name, b"id,target\n2,4\n")


def test_a_too_deep_upload_root_blames_the_root_not_the_file(tmp_path: Path) -> None:
    """Saying "file name is too long" here would send the wrong person looking.

    The helper is exercised directly rather than through `upload`: a root this
    deep cannot be created at all on a host *without* long-path support, so
    building a ControlPlane around one fails before the check is reached. The
    branch belongs to hosts that do have it enabled -- which is the case the
    guard exists for, since that setting is not this program's to depend on.
    """
    deep = tmp_path.joinpath(*["d" * 20] * 12)
    assert len(str(deep)) > 260 - _MIN_UPLOAD_NAME_BUDGET

    with pytest.raises(ValueError, match="upload directory is too deep"):
        _check_upload_path_fits(deep / "table.csv")


def test_an_ordinary_turkish_file_name_is_still_accepted(tmp_path: Path) -> None:
    """The guard must not start rejecting the names this product actually gets."""
    plane = _plane(tmp_path)

    uploaded = plane.upload("2026-yili-calisma-takvimi-excel.csv", b"id,target\n2,4\n")

    assert uploaded["files"] == ["2026-yili-calisma-takvimi-excel.csv"]


def test_pdf_only_upload_is_available_for_staging_but_not_structured_pipeline(
    tmp_path: Path,
) -> None:
    plane = _plane(tmp_path)
    pdf_path = tmp_path / "brief.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=400)
    writer.add_metadata({"/Title": "Local briefing"})
    with pdf_path.open("wb") as stream:
        writer.write(stream)

    uploaded = plane.upload("brief.pdf", pdf_path.read_bytes())
    profile = plane.source_profile(uploaded["source_id"])

    assert profile["tables"] == []
    assert profile["documents"][0]["name"] == "brief.pdf"
    assert profile["documents"][0]["understanding_status"] == "needs_ocr_or_vision"
    assert "Local briefing" in str(profile["documents"])
    assert "PDF text are omitted" in profile["privacy"]

    blueprint = plane.default_staging_pipeline(uploaded["source_id"])
    document_node = next(
        item for item in blueprint["components"] if item["id"] == "understand-documents"
    )
    document_node["settings"]["engine"] = "text_layer"
    document_node["settings"]["ocr"] = "never"
    plane.llm_factory = lambda: object()
    staged = plane.stage_run(
        uploaded["source_id"], {"pipeline_blueprint": blueprint}
    )
    workspace = plane.staging_workspace(staged["run_id"])
    enabled = {
        item["id"]
        for item in workspace["pipeline_blueprint"]["components"]
        if item["enabled"]
    }
    assert staged["status"] == "staging"
    assert "understand-documents" in enabled
    assert "default-ml-pipeline" not in enabled
    deadline = time.time() + 10
    while plane.progress(staged["run_id"])["status"] == "staging" and time.time() < deadline:
        time.sleep(0.02)
    progress = plane.progress(staged["run_id"])
    assert progress["status"] == "staged"
    event_names = [event["event"] for event in progress["events"]]
    assert "source_discovery_ready" in event_names
    assert "document_understanding_started" in event_names
    # #365: `object()` is not a structured LLM, so no plan can be proposed. The
    # run used to report `staging_analysis_ready` and carry no plan, and the
    # canvas -- which has only "pending" and "failed" for the proposal node --
    # drew that as still working. The file was accepted, nothing went red, and
    # the flow never advanced. The reason is recorded now. The status is
    # deliberately unchanged: the understanding that ran is real, and a
    # deployment with no planner configured is a configuration fact rather
    # than a failed run.
    skipped = next(
        event for event in progress["events"] if event["event"] == "staging_analysis_skipped"
    )
    assert "No planner model is available" in skipped["reason"]["en"]
    assert skipped["reason"]["tr"] != skipped["reason"]["en"]
    assert {item["name"]: item["route"] for item in profile["source_files"]} == {
        "brief.pdf": "documents"
    }
    with pytest.raises(ValueError, match="cannot be continued"):
        plane.start_staged_run(staged["run_id"])


def test_starting_a_staged_run_after_a_restart_names_the_real_cause(
    tmp_path: Path,
) -> None:
    """A restarted process reports an actionable message, not a bare "not staged".

    `_cannot_stage_error` already turns an unknown run_id into "no longer
    exists; choose the dataset again" (see test_staged_run.py). This proves
    the same actionable message reaches the harder case: a run that really
    was staged, whose disk snapshot still says so and whose `progress()`
    still reports it -- just not resident in *this* process's memory, the
    ordinary result of the process that finished staging not being the one
    serving `/start`. Without this, the mismatch between a `staged` progress
    view and a refused start reads as a product bug, not a call to restage.
    """
    plane = _plane(tmp_path)
    pdf_path = tmp_path / "brief.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=400)
    writer.add_metadata({"/Title": "Local briefing"})
    with pdf_path.open("wb") as stream:
        writer.write(stream)

    uploaded = plane.upload("brief.pdf", pdf_path.read_bytes())
    blueprint = plane.default_staging_pipeline(uploaded["source_id"])
    document_node = next(
        item for item in blueprint["components"] if item["id"] == "understand-documents"
    )
    document_node["settings"]["engine"] = "text_layer"
    document_node["settings"]["ocr"] = "never"
    plane.llm_factory = lambda: object()
    staged = plane.stage_run(uploaded["source_id"], {"pipeline_blueprint": blueprint})
    deadline = time.time() + 10
    while plane.progress(staged["run_id"])["status"] == "staging" and time.time() < deadline:
        time.sleep(0.02)
    assert plane.progress(staged["run_id"])["status"] == "staged"

    # A fresh ControlPlane over the same on-disk store/run-state, standing in
    # for the new process a restart leaves behind: an empty `_runtime_runs`,
    # the same persisted snapshot.
    restarted = ControlPlane(
        store=ArtifactStore(tmp_path / "artifacts"),
        source_roots=(tmp_path / "sources",),
        upload_root=tmp_path / "uploads",
    )
    assert restarted.progress(staged["run_id"])["status"] == "staged"

    with pytest.raises(ValueError, match="no longer exists"):
        restarted.start_staged_run(staged["run_id"])


def test_identical_document_content_reuses_extraction_across_uploads(
    tmp_path: Path, monkeypatch
) -> None:
    """#64: re-uploading byte-identical PDFs must not re-run OCR.

    The only skip-check keyed on `_source_fingerprint` (name/size/mtime), so a
    second upload — a fresh source_id with a new mtime, exactly the #53 scenario
    — never matched and paid the full extraction cost again. Two different owners
    are used so the upload dedupe does not collapse them into one source.
    """
    pdf_path = tmp_path / "brief.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=400)
    writer.add_metadata({"/Title": "Local briefing"})
    with pdf_path.open("wb") as stream:
        writer.write(stream)
    content = pdf_path.read_bytes()

    from ads.api import service as service_module

    real_extract = service_module.extract_document_directory
    calls = {"count": 0}

    def counting_extract(*args, **kwargs):
        calls["count"] += 1
        return real_extract(*args, **kwargs)

    monkeypatch.setattr(service_module, "extract_document_directory", counting_extract)

    plane = _plane(tmp_path)
    plane.llm_factory = lambda: object()

    def _stage_documents(name: str, owner: str) -> dict:
        uploaded = plane.upload(name, content, owner=owner)
        blueprint = plane.default_staging_pipeline(uploaded["source_id"])
        node = next(
            item for item in blueprint["components"] if item["id"] == "understand-documents"
        )
        node["settings"]["engine"] = "text_layer"
        node["settings"]["ocr"] = "never"
        staged = plane.stage_run(uploaded["source_id"], {"pipeline_blueprint": blueprint})
        deadline = time.time() + 10
        while (
            plane.progress(staged["run_id"])["status"] == "staging" and time.time() < deadline
        ):
            time.sleep(0.02)
        return plane.progress(staged["run_id"])

    first = _stage_documents("brief.pdf", owner="ishak-ads")
    assert first["status"] == "staged"
    assert calls["count"] == 1

    second = _stage_documents("brief.pdf", owner="emre-ads")
    assert second["status"] == "staged"
    assert calls["count"] == 1, "identical PDF content re-ran extraction instead of reusing it"
    reused = [
        event
        for event in second["events"]
        if event["event"] == "document_understanding_ready" and event.get("reused_cache")
    ]
    assert reused, "second run did not report a cache reuse"


def test_http_start_and_progress_vertical_slice(tmp_path: Path, monkeypatch) -> None:
    plane = _plane(tmp_path)
    entered = threading.Event()

    def fake_run(spec, registry, state, *, rubrics, policy, on_event):
        on_event("stage_started", {"stage": "intake"})
        entered.set()
        on_event("gate_decided", {"stage": "intake", "verdict": "auto_proceed"})
        return SimpleNamespace(status="completed", error=None)

    monkeypatch.setattr("ads.api.service.run_workflow", fake_run)
    client = TestClient(create_app(plane.store.root, plane=plane))

    assert client.get("/api/data-sources").json()[0]["source_id"] == "demo"
    response = client.post(
        "/api/runs",
        json={"source_id": "demo", **_contracts(), "candidate_limit": 1},
    )
    assert response.status_code == 200
    run_id = response.json()["run_id"]
    assert entered.wait(2)

    for _ in range(20):
        progress = client.get(f"/api/runs/{run_id}/progress").json()
        if progress["status"] == "completed":
            break
        time.sleep(0.01)
    else:
        raise AssertionError("background run did not complete")

    assert [event["event"] for event in progress["events"]] == [
        "stage_started",
        "gate_decided",
    ]
    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["progress"]["run_id"] == run_id
    assert client.get("/api/runs").json()[0]["status"] == "completed"
