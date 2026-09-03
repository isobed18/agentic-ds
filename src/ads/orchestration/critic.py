"""The Orchestrator's critique: judge a stage's output against a rubric it owns.

Closes a real gap. Until now `StageResult.critique` was supplied by the stage
itself — the stage graded its own homework, and a stage that failed to notice a
problem also failed to report it. Report §7 puts the rubric with the
Orchestrator precisely so the judge is not the judged.

The division inside the critique is the same one the whole architecture uses:

* **Whatever can be checked deterministically, is** — and it runs *first*. A
  criterion with a `check` function never reaches a model.
* The LLM evaluates only the residual judgment criteria, and it receives the
  deterministic results as input, so it is reasoning about a stage whose facts
  are already established rather than re-deriving them.

The output is a :class:`CritiqueResult`: findings and unmet criteria, no verdict
and no confidence field. The gate decides what the findings mean.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from ads.contracts.base import Artifact
from ads.contracts.gates import CritiqueResult, Finding, Severity
from ads.llm.client import LARGE, ModelProfile, StructuredLLM


@dataclass
class CritiqueContext:
    """What a criterion may look at when judging a stage."""

    stage_id: str
    artifacts: Sequence[Artifact]
    facts: dict[str, Any] = field(default_factory=dict)
    """Measurements the stage produced, for criteria that need numbers."""

    def artifact_of[A: Artifact](self, model: type[A]) -> A | None:
        for artifact in self.artifacts:
            if isinstance(artifact, model):
                return artifact
        return None


#: A deterministic check. Returns True (met), False (unmet), or None (not
#: applicable to this run — neither met nor a failure).
Check = Callable[[CritiqueContext], bool | None]


@dataclass(frozen=True)
class Criterion:
    """One rubric item.

    ``check`` present means deterministic; absent means it needs judgment. The
    split is declared per criterion rather than per rubric because most real
    rubrics are mixed — "every feature is covered" is countable, "the outlier
    treatment is appropriate" is not.
    """

    id: str
    description: str
    check: Check | None = None
    severity: Severity = Severity.WARN
    #: What to say when the check does not hold.
    #:
    #: #427: `description` states the condition that *should* hold, and a
    #: failure used to be rendered as `"Mechanical check failed: " +
    #: description` -- so the panel printed the success sentence with "failed:"
    #: glued to the front and both bullets read as assertions that everything
    #: was fine. Every criterion states its own failure; a criterion that
    #: somehow has none reports its id rather than the inverted sentence.
    failure: str | None = None
    #: Measured detail for a failure, read from the same context the check saw.
    #:
    #: The reason a framing is not viable is already computed and already
    #: specific; without this it stays in the artifact while the panel shows a
    #: tautology (#427).
    evidence: Callable[[CritiqueContext], list[str]] | None = None

    @property
    def is_deterministic(self) -> bool:
        return self.check is not None


@dataclass(frozen=True)
class Rubric:
    """The Orchestrator's standard for one stage.

    Versioned because a critique is only meaningful against a stated standard;
    ``CritiqueResult.rubric_version`` records which one judged a given attempt.
    """

    stage_id: str
    version: str
    criteria: tuple[Criterion, ...] = ()

    def deterministic(self) -> tuple[Criterion, ...]:
        return tuple(c for c in self.criteria if c.is_deterministic)

    def judgment(self) -> tuple[Criterion, ...]:
        return tuple(c for c in self.criteria if not c.is_deterministic)


class JudgmentAssessment(BaseModel):
    """What the LLM critic returns. Evidence only — deliberately no verdict."""

    model_config = {"extra": "forbid"}

    unmet_criteria: list[str] = Field(
        default_factory=list,
        description="Criterion ids that are NOT satisfied. Use the exact ids given.",
    )
    findings: list[JudgmentFinding] = Field(default_factory=list)


class JudgmentFinding(BaseModel):
    model_config = {"extra": "forbid"}

    criterion_id: str
    evidence: str = Field(max_length=400, description="Cite the artifact under review.")
    suggested_fix: str | None = Field(default=None, max_length=200)


JudgmentAssessment.model_rebuild()


CRITIC_SYSTEM_PROMPT = """\
You are reviewing one stage of a data science pipeline against a fixed checklist.

You will be given: the stage's output, the checklist items you must judge, and the \
results of checks that were already computed mechanically. Trust those computed \
results completely — do not recompute or dispute them.

Report ONLY the checklist items that are not satisfied, using their exact ids. For \
each, cite specific evidence from the artifact. If everything is satisfied, return \
empty lists.

Do not invent checklist items. Do not comment on items outside the list. Do not \
judge whether a human should be consulted — that is decided elsewhere and is not \
your concern.\
"""


def critique_stage(
    rubric: Rubric,
    context: CritiqueContext,
    *,
    llm: StructuredLLM | None = None,
    profile: ModelProfile = LARGE,
    artifact_digest: str | None = None,
) -> CritiqueResult:
    """Judge a stage's output. Deterministic checks first, model only for the rest.

    ``llm=None`` runs the deterministic half alone, which is the right default
    for stages whose rubric is fully mechanical — and keeps the critique
    available in tests and air-gapped runs with no model at hand.
    """
    unmet: list[str] = []
    findings: list[Finding] = []
    computed: list[str] = []

    for criterion in rubric.deterministic():
        outcome = criterion.check(context)  # type: ignore[misc]
        if outcome is None:
            continue  # not applicable to this run
        computed.append(f"{criterion.id}: {'met' if outcome else 'NOT met'}")
        if not outcome:
            unmet.append(criterion.id)
            findings.append(
                Finding(
                    check_id=criterion.id,
                    severity=criterion.severity,
                    # #427: never `"failed: " + <the thing that should be
                    # true>`. The criterion's own failure wording, or its id --
                    # a bare code is a worse read than a sentence but an
                    # honest one, where the inverted sentence was neither.
                    evidence=criterion.failure or f"{criterion.id}: this check did not hold.",
                    suggested_fix=None,
                    measurements=(
                        criterion.evidence(context)[:20] if criterion.evidence else []
                    ),
                )
            )

    judgment = rubric.judgment()
    if llm is not None and judgment:
        findings.extend(
            _assess_judgment(rubric, judgment, computed, llm, profile, artifact_digest, unmet)
        )

    return CritiqueResult(
        stage_id=rubric.stage_id,
        rubric_version=f"{rubric.stage_id}.{rubric.version}",
        findings=findings,
        unmet_criteria=sorted(set(unmet)),
    )


def _assess_judgment(
    rubric: Rubric,
    judgment: Sequence[Criterion],
    computed: Sequence[str],
    llm: StructuredLLM,
    profile: ModelProfile,
    artifact_digest: str | None,
    unmet: list[str],
) -> list[Finding]:
    """Ask the model about the residual items only."""
    checklist = "\n".join(f"- {c.id}: {c.description}" for c in judgment)
    already = "\n".join(f"- {line}" for line in computed) or "- (none)"
    prompt = (
        f"## Stage\n{rubric.stage_id}\n\n"
        f"## Stage output\n{artifact_digest or '(no digest supplied)'}\n\n"
        f"## Already computed mechanically — treat as established fact\n{already}\n\n"
        f"## Checklist items you must judge\n{checklist}\n"
    )

    response = llm.generate_structured(
        system=CRITIC_SYSTEM_PROMPT,
        prompt=prompt,
        json_schema=JudgmentAssessment.model_json_schema(),
        profile=profile,
    )
    if response.parsed is None:
        # A critic that cannot be parsed must not silently approve the stage.
        return [
            Finding(
                check_id="critique.unavailable",
                severity=Severity.WARN,
                evidence=(
                    "The judgment critique could not be parsed "
                    f"({response.parse_error}); only mechanical checks were applied."
                ),
                suggested_fix=None,
            )
        ]

    try:
        assessment = JudgmentAssessment.model_validate(response.parsed)
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        return [
            Finding(
                check_id="critique.invalid",
                severity=Severity.WARN,
                evidence=f"Critique failed validation: {exc}",
                suggested_fix=None,
            )
        ]

    valid_ids = {c.id for c in judgment}
    findings: list[Finding] = []
    by_id = {c.id: c for c in judgment}

    for criterion_id in assessment.unmet_criteria:
        # Ignore ids outside the rubric: a model inventing a criterion must not
        # be able to fail a stage against a standard nobody declared.
        if criterion_id in valid_ids:
            unmet.append(criterion_id)

    for item in assessment.findings:
        if item.criterion_id not in valid_ids:
            continue
        findings.append(
            Finding(
                check_id=item.criterion_id,
                severity=by_id[item.criterion_id].severity,
                evidence=item.evidence,
                suggested_fix=item.suggested_fix,
            )
        )
    return findings


@dataclass
class RubricRegistry:
    """Rubrics by stage id. Absent means the stage is not critiqued."""

    rubrics: dict[str, Rubric] = field(default_factory=dict)

    def register(self, rubric: Rubric) -> None:
        if rubric.stage_id in self.rubrics:
            raise ValueError(f"rubric for stage {rubric.stage_id!r} is already registered")
        self.rubrics[rubric.stage_id] = rubric

    def get(self, stage_id: str) -> Rubric | None:
        return self.rubrics.get(stage_id)


__all__ = [
    "CRITIC_SYSTEM_PROMPT",
    "Check",
    "CritiqueContext",
    "Criterion",
    "JudgmentAssessment",
    "Rubric",
    "RubricRegistry",
    "critique_stage",
]
