"""A stated review checkpoint has to survive the start request (#447).

`start_staged`'s fully-auto branch rebuilt `supervision` from the accepted plan
and overwrote whatever the caller sent. The plan is the right default -- it is
where a planner-requested checkpoint lands -- but it was also the only thing a
fully-auto run could ever have, so a person who unchecked a stage on the canvas
watched the run go straight through it, and one who checked a stage the planner
had not named got nothing.
"""

from __future__ import annotations

from pathlib import Path


def _fully_auto_branch() -> str:
    service = Path("src/ads/api/service.py").read_text(encoding="utf-8")
    start = service.index('if run_mode == "fully_auto":')
    return service[start : service.index("supervision = self._normalise_supervision(", start)]


class TestTheCallersCheckpointsAreADecision:
    def test_an_explicit_list_replaces_the_plans(self) -> None:
        branch = _fully_auto_branch()

        assert "requested_supervision = (configuration or {}).get(\"supervision\")" in branch
        assert "list(stated_checkpoints)" in branch
        assert "if isinstance(stated_checkpoints, list)" in branch

    def test_the_plan_is_still_the_default(self) -> None:
        # A run started with no supervision in the body -- every existing caller
        # -- keeps behaving exactly as it did.
        assert "else plan.checkpoint_stages" in _fully_auto_branch()

    def test_an_empty_list_means_no_optional_checkpoints_not_fall_back(self) -> None:
        # Unchecking every stage is a decision. `isinstance(..., list)` is the
        # test rather than truthiness, because `[]` and "not stated" are
        # different answers and a falsy check would collapse them.
        branch = _fully_auto_branch()
        assert "if stated_checkpoints else" not in branch
        assert "or plan.checkpoint_stages" not in branch


class TestTheSafetyFloorIsUntouched:
    def test_graph_checkpoints_are_still_unioned_in(self) -> None:
        # Those come from the saved blueprint, not from this request, so a
        # caller cannot switch off a gate the automation graph declares.
        service = Path("src/ads/api/service.py").read_text(encoding="utf-8")
        assert (
            "checkpoints = sorted(set(supervision[\"checkpoint_stages\"]) | graph_checkpoints)"
            in service
        )

    def test_manual_still_declares_every_stage_a_checkpoint(self) -> None:
        service = Path("src/ads/api/service.py").read_text(encoding="utf-8")
        manual = (
            'if run_mode == "manual":\n'
            "            checkpoints = sorted(set(checkpoints) | _PIPELINE_STAGES)"
        )
        assert manual in service
