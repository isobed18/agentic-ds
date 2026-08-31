"""ProblemDiscoveryAgent — propose what ML problems the data can answer.

Stage 2 is CRITICAL risk: choosing the wrong target silently invalidates
everything downstream, and no later stage can detect the mistake. So this agent
never decides. It proposes ranked framings, :mod:`ads.discovery.support`
measures each one, and a human picks at an always-on gate.

The division of labour is the point:

* **LLM** — what could this data be used for, in business terms? Which columns
  make a framing plausible? What would a stakeholder actually want?
* **Python** — is it feasible? How many positives are there? Enough rows?
  Enough features? Is the target even the right shape for the task?

The agent is explicitly permitted to propose framings that later prove
unsupported. Suppressing them would hide a real finding: "you asked for fraud
detection and your data has 39 labelled cases" is exactly what the user needs
to hear.
"""

from __future__ import annotations

import pandas as pd

from ads.agents.base import AgentContext, AgentSpec, ValidationFailure, suggest_name
from ads.contracts.datacard import DataCard
from ads.contracts.gates import PermissionTier
from ads.contracts.problem import (
    METRICS_BY_TASK,
    SUPERVISED_TASKS,
    ProblemCandidate,
    ProblemCandidateSet,
    ProblemDiscoveryProposal,
)
from ads.discovery.support import compute_support, support_digest
from ads.intake.profiler import datacard_digest
from ads.llm.client import LARGE

SYSTEM_PROMPT = """\
You are a senior data scientist scoping ML projects on enterprise data.

You are given the profile of one analytical base table (ABT) and a list of \
candidate target columns with MEASURED statistics. Propose the ML problems this \
data could support.

Rules:
- Propose between 2 and 4 candidates, ranked best first.
- `target_column` must be an exact column name from the ABT profile.
- Match `task_type` to the target's measured shape: a column with exactly 2 \
distinct values is binary_classification, 3 or more categories is \
multiclass_classification, a continuous numeric column is regression. Use \
anomaly_detection only when no suitable label exists, and then leave \
target_column null.
- Choose `primary_metric` appropriately. For imbalanced binary targets prefer \
average_precision or roc_auc over accuracy.
- `evidence_columns` must list real ABT columns that make the framing plausible.
- `business_rationale` states who would use the model and for what decision. \
Be concrete and avoid generic phrasing.
- Write every human-facing field twice in the same response: canonical English
  in `title` and `business_rationale`, and a faithful Turkish rendering in
  `title_tr` and `business_rationale_tr`. Do not run a separate translation pass.
- Do NOT assess feasibility, sample sizes or class balance. That is measured \
separately. Propose the framing; the system will check whether the data \
supports it.
- If the user has stated an intended project, rank that framing first, even if \
you suspect the data is weak for it.

Do not invent columns. Use exact names.\
"""


def build_context(abt_card: DataCard, *, user_intent: str | None = None) -> AgentContext:
    """Project the ABT profile into the agent's context."""
    sections = {
        "Analytical base table": datacard_digest(abt_card),
        "Candidate targets": support_digest(abt_card),
        "Task": ("Propose the ML problems this table could support, ranked best first."),
    }
    if user_intent:
        sections["Stated user intent"] = (
            f"The user asked for: {user_intent}\n"
            "Rank this framing first if it is expressible against these columns."
        )

    return AgentContext(
        sections=sections,
        facts={
            "known_columns": abt_card.column_names,
            "target_candidates": [c.name for c in abt_card.candidate_targets()],
            "distinct_by_column": {c.name: c.n_unique for c in abt_card.columns},
            "semantic_by_column": {c.name: c.semantic_type.value for c in abt_card.columns},
        },
    )


def validate_targets_exist(
    proposal: ProblemDiscoveryProposal, context: AgentContext
) -> list[ValidationFailure]:
    known: list[str] = list(context.facts.get("known_columns", []))
    failures: list[ValidationFailure] = []

    for i, candidate in enumerate(proposal.candidates):
        if candidate.target_column and candidate.target_column not in known:
            failures.append(
                ValidationFailure(
                    layer="semantic",
                    code="unknown_target",
                    detail=f"Target {candidate.target_column!r} is not a column of the ABT.",
                    field_path=f"candidates[{i}].target_column",
                    repair_suggestion=suggest_name(candidate.target_column, known),
                )
            )
        for column in candidate.evidence_columns:
            if column not in known:
                failures.append(
                    ValidationFailure(
                        layer="semantic",
                        code="unknown_column",
                        detail=f"Evidence column {column!r} is not a column of the ABT.",
                        field_path=f"candidates[{i}].evidence_columns",
                        repair_suggestion=suggest_name(column, known),
                    )
                )
    return failures


def validate_task_matches_target_shape(
    proposal: ProblemDiscoveryProposal, context: AgentContext
) -> list[ValidationFailure]:
    """Reject framings the target's measured cardinality contradicts.

    Catches the common local-model error of labelling a 6-category column
    ``binary_classification`` — cheap to check, and expensive to discover at the
    training stage.
    """
    distinct: dict[str, int] = context.facts.get("distinct_by_column", {})
    semantic: dict[str, str] = context.facts.get("semantic_by_column", {})
    failures: list[ValidationFailure] = []

    for i, candidate in enumerate(proposal.candidates):
        target = candidate.target_column
        if not target or target not in distinct:
            continue
        n_unique = distinct[target]
        kind = semantic.get(target, "unknown")
        path = f"candidates[{i}].task_type"

        from ads.contracts.problem import TaskType

        if candidate.task_type is TaskType.BINARY_CLASSIFICATION and n_unique != 2:
            failures.append(
                ValidationFailure(
                    layer="semantic",
                    code="task_target_mismatch",
                    detail=(
                        f"{target!r} has {n_unique} distinct values, so it cannot be "
                        "binary_classification."
                    ),
                    field_path=path,
                )
            )
        elif candidate.task_type is TaskType.MULTICLASS_CLASSIFICATION and n_unique < 3:
            failures.append(
                ValidationFailure(
                    layer="semantic",
                    code="task_target_mismatch",
                    detail=f"{target!r} has only {n_unique} distinct values; not multiclass.",
                    field_path=path,
                )
            )
        elif candidate.task_type is TaskType.REGRESSION and not kind.startswith("numeric"):
            failures.append(
                ValidationFailure(
                    layer="semantic",
                    code="task_target_mismatch",
                    detail=f"{target!r} is {kind}, which cannot be a regression target.",
                    field_path=path,
                )
            )
    return failures


def validate_candidates_are_distinct(
    proposal: ProblemDiscoveryProposal, context: AgentContext
) -> list[ValidationFailure]:
    """A repeated (target, task) pair is padding, not a second option."""
    failures: list[ValidationFailure] = []

    framings = [(c.target_column, c.task_type.value) for c in proposal.candidates]
    if len(set(framings)) != len(framings):
        failures.append(
            ValidationFailure(
                layer="consistency",
                code="duplicate_framing",
                detail=(
                    "Two candidates share the same target and task type. Each candidate "
                    "must be a genuinely different framing."
                ),
                field_path="candidates",
            )
        )
    return failures


def validate_metric_matches_task(
    proposal: ProblemDiscoveryProposal, context: AgentContext
) -> list[ValidationFailure]:
    """Belt-and-braces over the contract validator, with an actionable message."""
    failures: list[ValidationFailure] = []
    for i, candidate in enumerate(proposal.candidates):
        allowed = METRICS_BY_TASK[candidate.task_type]
        if candidate.primary_metric not in allowed:
            failures.append(
                ValidationFailure(
                    layer="semantic",
                    code="invalid_metric",
                    detail=(
                        f"{candidate.primary_metric.value!r} is not valid for "
                        f"{candidate.task_type.value!r}."
                    ),
                    field_path=f"candidates[{i}].primary_metric",
                    repair_suggestion=sorted(m.value for m in allowed)[0],
                )
            )
    return failures


def build_spec() -> AgentSpec[ProblemDiscoveryProposal]:
    return AgentSpec(
        id="problem_discovery",
        system_prompt=SYSTEM_PROMPT,
        output_contract=ProblemDiscoveryProposal,
        profile=LARGE,
        validators=(
            validate_targets_exist,
            validate_task_matches_target_shape,
            validate_candidates_are_distinct,
            validate_metric_matches_task,
        ),
        max_attempts=3,
        rubric="problem_discovery.v1",
        # #166: the problem_investigator scout runs first on this same context and
        # admits its tool evidence into it (problem_investigator.investigate_
        # problem_context -> context.admit_tool_result). Its declared tools are a
        # superset of these, so whenever the scout measured `correlation` the
        # panel here rejected the shared context with "received evidence from
        # undeclared tools: ['correlation']" and the pipeline stopped dead at
        # problem_discovery -- exactly the "stops after data understanding, never
        # reaches ML data prep" defect. The panel legitimately consumes what its
        # own stage's scout gathered, so it must declare the scout's tools too.
        allowed_tools=frozenset(
            {"cardinality", "column_profile", "correlation", "null_rate", "value_counts"}
        ),
        max_tool_tier=PermissionTier.READ_DATA,
        column_fields=frozenset({"target_column", "evidence_columns"}),
        # How a candidate is explained to a human. Everything else --
        # target_column, task_type, primary_metric, evidence_columns -- commits
        # the pipeline to something and is compared.
        narration_fields=frozenset(
            {"title", "title_tr", "business_rationale", "business_rationale_tr"}
        ),
    )


def attach_support(
    proposal: ProblemDiscoveryProposal,
    abt_card: DataCard,
    frame: pd.DataFrame,
    *,
    user_intent: str | None = None,
) -> ProblemCandidateSet:
    """Measure each proposed framing and build the reviewable candidate set.

    Viable candidates are ranked ahead of blocked ones while preserving the
    agent's ordering within each group — so the human sees workable options
    first without the blocked ones being hidden.
    """
    measured = [
        ProblemCandidate.from_proposal(
            candidate,
            compute_support(
                abt_card,
                frame,
                target_column=candidate.target_column,
                task_type=candidate.task_type,
            ),
            candidate_id=f"c{i}",
        )
        for i, candidate in enumerate(proposal.candidates, 1)
    ]
    ranked = [c for c in measured if c.support.is_viable] + [
        c for c in measured if not c.support.is_viable
    ]
    return ProblemCandidateSet(candidates=ranked, user_intent=user_intent)


__all__ = [
    "SUPERVISED_TASKS",
    "SYSTEM_PROMPT",
    "attach_support",
    "build_context",
    "build_spec",
    "validate_candidates_are_distinct",
    "validate_metric_matches_task",
    "validate_targets_exist",
    "validate_task_matches_target_shape",
]
