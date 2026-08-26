"""Uploading on the Data projects page, and the silence that hid its failure.

The reported symptom was "I can't upload any data on the automation page".
Nothing errored and nothing was logged, because the handler began:

    const selected = Array.from(files); if (!selected.length || !automation) return;

`automation` is null while the record is loading and stays null forever if its
fetch failed, so choosing files did nothing at all -- no request, no spinner, no
message. The button looked alive and was not.

The fetch could fail for a reason that had no business blocking uploads:
`refreshAutomation` awaited `api.dataSources()` inside the same `Promise.all` as
the record itself, so a failure in the reuse picker -- a convenience list --
rejected the batch, left `automation` null, and disabled the page's primary
action.

These assertions read the component source. There is no DOM test library in
this project, and adding one to cover a two-line guard is the wrong trade.
"""

from __future__ import annotations

import re
from pathlib import Path

WORKSPACE = (
    Path(__file__).resolve().parents[1] / "web" / "src" / "pages" / "AutomationWorkspace.tsx"
)


def _source() -> str:
    return WORKSPACE.read_text(encoding="utf-8")


def test_upload_does_not_return_silently_when_the_record_is_missing() -> None:
    source = _source()
    assert "!selected.length || !automation) return" not in source, (
        "the silent guard is back: picking files does nothing and says nothing"
    )

    body = source[source.index("async function upload("):]
    body = body[: body.index("\n  async function ")]
    assert "setError(" in body, "a rejected upload must tell the person something"


def test_the_reuse_picker_cannot_take_uploading_down_with_it() -> None:
    """`api.dataSources()` populates an optional dropdown. It must not sit in
    the `Promise.all` that decides whether the project record loads at all."""
    source = _source()
    batch = re.search(r"await Promise\.all\(\[(.*?)\]\)", source, re.S)
    assert batch, "the record-loading batch was not found"
    assert "api.dataSources()" not in batch.group(1), (
        "a failure in the optional source list would null the automation record"
    )
    assert "api.dataSources()" in source, "the reuse picker should still be populated"


def test_upload_controls_are_disabled_until_the_record_exists() -> None:
    """Belt and braces for the same defect: if the handler cannot act, the
    control that calls it should not look clickable."""
    source = _source()
    assert source.count("disabled={!automation}") >= 2, (
        "both the empty-state upload button and the + button must be gated"
    )
