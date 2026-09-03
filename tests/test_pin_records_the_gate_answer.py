"""Pinning from an unanswered gate is an answer, and is recorded as one (#464).

The issue's open question. `answer_run` -> `resume_workflow` writes a
`human_decisions` entry for every gate a person answers; `pin_problem_framing`
re-enters the graph directly and wrote nothing, so a run whose
`problem_discovery` escalation was resolved by naming a target showed an
escalation nobody replied to -- while a person had replied, in the most
specific way the product offers.
"""

from __future__ import annotations

from pathlib import Path


def _pin_source() -> str:
    service = Path("src/ads/api/service.py").read_text(encoding="utf-8")
    return service[service.index("def pin_problem_framing(") : service.index("def answer_run(")]


class TestTheDecisionIsRecorded:
    def test_a_pin_from_a_gate_writes_a_human_decision(self) -> None:
        pin = _pin_source()

        assert 'if runtime.status == "awaiting_human":' in pin
        assert 'state.blackboard.setdefault("human_decisions", []).append(' in pin

    def test_it_uses_the_shape_resume_workflow_already_writes(self) -> None:
        # A second shape for the same record would mean every reader of the
        # audit trail has to know about both.
        runner = Path("src/ads/orchestration/runner.py").read_text(encoding="utf-8")
        assert 'state.blackboard.setdefault("human_decisions", []).append(' in runner
        for field in ('"stage_id"', '"decision"', '"instructions"'):
            assert field in _pin_source()

    def test_the_decision_is_a_retry_because_that_is_what_a_pin_does(self) -> None:
        # `resume_workflow`'s vocabulary is approve / retry / abort. A pin
        # re-runs the stage under a constraint, which is `retry`; calling it
        # `approve` would say the framing was accepted, which is the opposite.
        assert '"decision": "retry",' in _pin_source()

    def test_the_constraint_travels_as_the_instruction(self) -> None:
        # An entry that records only "retry" says a person intervened without
        # saying what they decided.
        pin = _pin_source()
        assert "pinned problem framing:" in pin
        assert "target_column=" in pin
        assert "task_type=" in pin

    def test_nothing_is_recorded_for_a_run_that_was_not_at_a_gate(self) -> None:
        # A failed, interrupted or aborted run has no open escalation, so there
        # is no decision to attribute -- writing one would invent a gate.
        pin = _pin_source()
        guard = pin.index('if runtime.status == "awaiting_human":')
        append = pin.index('state.blackboard.setdefault("human_decisions", []).append(')
        assert guard < append
