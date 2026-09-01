"""The guided page's stage groups, held to the workflow the runner executes.

The guided pipeline shows seven friendly groups, and each group claims a set of
established workflow stages by id. Those ids are written by hand in TypeScript
and were never checked against the Python spec, so they drifted: the "Analyze
and validate" group asked for `exploratory_analysis` (an artifact type, not a
stage) and `lineage_audit` (an id nothing produces) instead of `eda` and
`leakage_audit`.

Nothing failed loudly. Both stages still executed and still wrote artifacts --
they simply never matched the group, so their live status, stage inspection,
and artifact count were missing from the only screen that shows them. That is
the failure this file exists to prevent: a silently incomplete UI, not a crash.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ads.pipeline.workflow import build_full_spec_definition

GUIDED_PIPELINE = (
    Path(__file__).resolve().parents[1] / "web" / "src" / "components" / "mlPipelineGroups.ts"
)


def _declared_groups() -> dict[str, list[str]]:
    """Read the `GROUPS` table out of the component as `{group id: [stage ids]}`.

    Parsed from source rather than duplicated here on purpose: a copy in the
    test would keep passing after the component drifted, which is exactly the
    bug being guarded.

    The table moved out of ``GuidedPipeline.tsx`` into its own module with #214,
    when the understanding canvas and the accepted pipeline became one graph and
    both halves needed to read it.
    """
    source = GUIDED_PIPELINE.read_text(encoding="utf-8")
    block = re.search(r"const GROUPS[^=]*=\s*\[(.*?)\n\];", source, re.S)
    assert block, "GROUPS table not found -- this test needs updating with the component"

    groups: dict[str, list[str]] = {}
    for entry in re.finditer(
        r'\{\s*id:\s*"([^"]+)".*?stages:\s*\[([^\]]*)\]', block.group(1), re.S
    ):
        groups[entry.group(1)] = re.findall(r'"([^"]+)"', entry.group(2))
    assert groups, "no groups parsed out of the GROUPS table"
    return groups


def test_every_group_stage_is_a_real_workflow_stage() -> None:
    known = {stage.id for stage in build_full_spec_definition().stages}
    referenced = {stage for stages in _declared_groups().values() for stage in stages}

    unknown = sorted(referenced - known)
    assert not unknown, (
        f"guided groups reference stages the workflow does not declare: {unknown}. "
        f"Known stage ids: {sorted(known)}"
    )


@pytest.mark.parametrize("stage_id", ["eda", "leakage_audit"])
def test_analysis_group_covers_the_two_stages_it_lost(stage_id: str) -> None:
    """The exact regression: these two were named by the wrong id for long
    enough to ship, so they get their own assertion rather than relying on the
    set comparison above to notice."""
    assert stage_id in _declared_groups()["analysis"]


def test_no_established_stage_is_missing_from_every_group() -> None:
    """A stage the runner executes but no group claims is invisible to the
    person watching the run. `intake` and `schema_discovery` are the deliberate
    exceptions: they complete during staging, before this page is shown.
    """
    staged_before_this_page = {"intake", "schema_discovery"}
    known = {stage.id for stage in build_full_spec_definition().stages}
    referenced = {stage for stages in _declared_groups().values() for stage in stages}

    orphaned = sorted(known - referenced - staged_before_this_page)
    assert not orphaned, f"workflow stages no guided group displays: {orphaned}"
