"""Promoting a table out of a PDF-only run has to re-author its plan (#471).

The plan a PDF-only run is first shown says `defer_pipeline` -- correctly, at
that moment: nothing structured has been validated yet. Promoting a table is
the act that changes it, and `_replan_after_promotion` exists to re-enter the
graph at intake so the promoted rows are profiled, joined and re-planned.

It bails out on `runtime.resume is None`, and the document-only staging branch
never built a `resume` tuple. So on exactly the run where promotion is the
*only* way to get training data, promotion answered "unavailable", the plan
was never re-authored, and the panel kept saying

    No trusted structured ML input is selected

with three tables sitting promoted underneath it. Nothing errored; nothing
advanced either.
"""

from __future__ import annotations

import time
from pathlib import Path

from pypdf import PdfWriter

from ads.api import ControlPlane
from ads.store import ArtifactStore


def _plane(tmp_path: Path) -> ControlPlane:
    sources = tmp_path / "sources"
    (sources / "demo").mkdir(parents=True)
    (sources / "demo" / "table.csv").write_text("id,target\n1,2\n", encoding="utf-8")
    return ControlPlane(
        store=ArtifactStore(tmp_path / "artifacts"),
        source_roots=(sources,),
        upload_root=tmp_path / "uploads",
    )


class _Model:
    """Enough of a structured LLM for `build_full_spec` to wire a real graph."""

    def complete(self, *args, **kwargs):  # pragma: no cover - not reached
        raise AssertionError("the replan must not depend on a model answering")


def _staged_pdf_only_run(tmp_path: Path) -> tuple[ControlPlane, str]:
    plane = _plane(tmp_path)
    pdf_path = tmp_path / "brief.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=400)
    writer.add_metadata({"/Title": "Quarterly tables"})
    with pdf_path.open("wb") as stream:
        writer.write(stream)

    uploaded = plane.upload("brief.pdf", pdf_path.read_bytes())
    blueprint = plane.default_staging_pipeline(uploaded["source_id"])
    node = next(
        item for item in blueprint["components"] if item["id"] == "understand-documents"
    )
    node["settings"]["engine"] = "text_layer"
    node["settings"]["ocr"] = "never"
    plane.llm_factory = lambda: _Model()

    staged = plane.stage_run(uploaded["source_id"], {"pipeline_blueprint": blueprint})
    run_id = staged["run_id"]
    deadline = time.time() + 15
    while plane.progress(run_id)["status"] == "staging" and time.time() < deadline:
        time.sleep(0.02)
    assert plane.progress(run_id)["status"] == "staged"
    return plane, run_id


def test_a_pdf_only_run_can_replan_after_promotion(tmp_path: Path) -> None:
    """The bail-out condition, met on the one run that depends on this path.

    Asserted at the boundary that decides it -- a resident runtime carrying a
    resume tuple -- rather than by driving a real extraction, so the test says
    why the replan was refused rather than only that some later state differed.
    """
    plane, run_id = _staged_pdf_only_run(tmp_path)

    runtime = plane._runtime_runs.get(run_id)  # noqa: SLF001

    assert runtime is not None, "the staged run must stay resident to be replanned"
    assert runtime.resume is not None, (
        "_replan_after_promotion returns 'unavailable' on a null resume, so "
        "promoting a table out of this run could never re-author its plan"
    )
    spec, _registry, state, _llm = runtime.resume
    # The replan re-enters at intake by name; a graph without that entry point
    # would fail later and much less legibly.
    assert spec.entry == "intake"
    assert state.run_id == run_id


def test_the_promotion_path_reports_a_replan_not_unavailable(tmp_path: Path) -> None:
    """`_replan_after_promotion` is what the promote endpoint answers with.

    "unavailable" is the string the deployment returned, and it is the reason
    the panel never changed. Anything else means the re-author was started.
    """
    plane, run_id = _staged_pdf_only_run(tmp_path)

    answer = plane._replan_after_promotion(run_id)  # noqa: SLF001

    assert answer != "unavailable", (
        "the run is resident and staged; refusing to replan here is the bug"
    )
    assert answer == "replanning"
