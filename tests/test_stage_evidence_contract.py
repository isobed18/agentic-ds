"""#166: a stage's investigator scout runs on the same ``AgentContext`` that the
stage's downstream agent panel then validates, and admits its measured tool
evidence into it. ``_validate_tool_evidence`` rejects a context carrying
evidence from any tool the panel has not declared, so whenever the scout used a
tool the panel lacked, the run stopped dead with "received evidence from
undeclared tools" -- the defect #166 saw as the pipeline halting after data
understanding, never reaching ML data prep.

The contract that keeps the handoff working: a panel must declare every tool its
own scout can admit. This failed at problem_discovery (missing ``correlation``)
and lurked one stage later at validation_strategy (missing
``trial_validation_strategy``), which the same LLM tool choice would have hit.
"""

from __future__ import annotations

from ads.agents import (
    problem_discovery,
    problem_investigator,
    validation_investigator,
    validation_strategy,
)


def test_problem_panel_declares_every_tool_its_scout_can_admit() -> None:
    panel = problem_discovery.build_spec().allowed_tools
    scout = problem_investigator.build_spec(allow_code=False).allowed_tools
    undeclared = sorted(scout - panel)
    assert not undeclared, f"problem_discovery must declare its scout's tools: {undeclared}"


def test_validation_panel_declares_every_tool_its_scout_can_admit() -> None:
    panel = validation_strategy.build_spec().allowed_tools
    scout = validation_investigator.build_spec(allow_code=False).allowed_tools
    undeclared = sorted(scout - panel)
    assert not undeclared, f"validation_strategy must declare its scout's tools: {undeclared}"
