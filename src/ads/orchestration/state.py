"""Run state: what a stage reads, and what the run accumulated.

Three levels, kept separate on purpose (report §9.2):

* **Workflow state** — graph position and attempt counts. Owned by the runtime.
* **Domain state** — the run, its stages, its gate decisions. Queryable by us.
* **Payload state** — artifacts, in the content-addressed store.

Keeping domain state in our own structures rather than inside a kernel's opaque
checkpoint blob is what makes the kernel replaceable. It is also what lets the
critique loop ask "what did the previous attempt fail on?" without replaying a
transcript.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ads.contracts.base import Artifact, ArtifactType
from ads.contracts.gates import AutonomyProfile, CritiqueResult, GateDecision, GateVerdict
from ads.store import ArtifactRef, ArtifactStore


@dataclass
class StageAttempt:
    """One execution of one stage, kept whole for the audit trail."""

    stage_id: str
    attempt: int
    started_at: datetime
    ended_at: datetime | None = None
    artifact_ids: list[str] = field(default_factory=list)
    input_bindings: dict[str, str] = field(default_factory=dict)
    """Artifact type -> exact artifact id this attempt read.

    Resolved once when the attempt opens and immutable thereafter. Without it a
    stage reads whatever is newest *at the moment it looks*, so a retry is not a
    corrected replay of the rejected attempt — it is a different experiment run
    against different inputs, carrying a correction derived from inputs it no
    longer sees.

    Timestamps cannot substitute for this. Content-addressed writes preserve the
    original ``created_at``, so a stage that reverts to an earlier semantic
    version does not become "newest" again (Codex ANSWER 5).
    """
    critique: CritiqueResult | None = None
    decision: GateDecision | None = None
    error: str | None = None

    @property
    def unmet_criteria(self) -> frozenset[str]:
        if self.critique is None:
            return frozenset()
        return frozenset(self.critique.unmet_criteria)

    @property
    def succeeded(self) -> bool:
        return self.error is None


@dataclass
class RunState:
    """Everything a stage may read, and the record of what has happened.

    A stage receives this and returns artifacts. It never reaches into the
    runtime, which is what keeps stages testable in isolation and portable
    across kernels.
    """

    run_id: str
    store: ArtifactStore
    profile: AutonomyProfile
    user_intent: str | None = None
    attempts: list[StageAttempt] = field(default_factory=list)
    active_attempt: StageAttempt | None = None
    """The attempt currently executing. Set by the runner, read by `latest()`."""
    blackboard: dict[str, Any] = field(default_factory=dict)
    """Small cross-stage facts that are decisions rather than artifacts.

    Deliberately not a conversation log. Agents communicate through typed
    artifacts; this holds things like a human's answer to a gate question.
    """

    # ------------------------------------------------------------- artifacts

    def put(self, artifact: Artifact, *, stage_id: str, name: str | None = None) -> ArtifactRef:
        return self.store.put(artifact, run_id=self.run_id, stage_exec_id=stage_id, name=name)

    def latest[A: Artifact](
        self, artifact_type: ArtifactType, model: type[A], *, name: str | None = None
    ) -> A | None:
        """The artifact this attempt is bound to, or the newest if unbound.

        While a stage is executing, a declared input resolves through the
        attempt's immutable binding rather than by recency. That is what makes a
        retry a *replay with a correction* instead of a new experiment: the
        correction was derived from specific inputs, so the retry must see those
        inputs.

        Undeclared reads still fall back to newest-wins. Declare an artifact type
        in the stage's ``consumes`` to get binding — an undeclared dependency is
        invisible to the spec's validation too, so it is worth declaring anyway.
        """
        bound = self._bound_id(artifact_type)
        if bound is not None:
            return self.store.load(bound, model)
        ref = self.store.latest(self.run_id, artifact_type, name=name)
        return self.store.load(ref.artifact_id, model) if ref else None

    def _bound_id(self, artifact_type: ArtifactType) -> str | None:
        if self.active_attempt is None:
            return None
        return self.active_attempt.input_bindings.get(artifact_type.value)

    def resolve_bindings(
        self, consumes: Sequence[ArtifactType], *, inherit: Mapping[str, str] | None = None
    ) -> dict[str, str]:
        """Pin declared inputs to exact artifact ids for one attempt.

        ``inherit`` carries a self-retry: the rejected attempt's exact inputs are
        reused so the only thing that changes between attempts is the correction.
        A deliberate retreat that re-runs an upstream stage resolves afresh, and
        that lineage change is then explicit in the attempt log rather than an
        accident of timing.
        """
        if inherit is not None:
            return dict(inherit)
        bindings: dict[str, str] = {}
        for artifact_type in consumes:
            ref = self.store.latest(self.run_id, artifact_type)
            if ref is not None:
                bindings[artifact_type.value] = ref.artifact_id
        return bindings

    def require[A: Artifact](
        self, artifact_type: ArtifactType, model: type[A], *, name: str | None = None
    ) -> A:
        artifact = self.latest(artifact_type, model, name=name)
        if artifact is None:
            raise MissingArtifactError(
                f"run {self.run_id!r} has no {artifact_type.value!r} artifact; "
                "an upstream stage did not produce it"
            )
        return artifact

    def all_of[A: Artifact](self, artifact_type: ArtifactType, model: type[A]) -> list[A]:
        return self.store.load_all(self.run_id, artifact_type, model)

    # ----------------------------------------------------------- attempt log

    def attempts_for(self, stage_id: str) -> list[StageAttempt]:
        return [a for a in self.attempts if a.stage_id == stage_id]

    def attempt_count(self, stage_id: str) -> int:
        return len(self.attempts_for(stage_id))

    def prior_unmet(self, stage_id: str) -> tuple[frozenset[str], ...]:
        """Unmet criteria per previous attempt, for stuck-loop detection."""
        return tuple(a.unmet_criteria for a in self.attempts_for(stage_id))

    def last_decision(self, stage_id: str) -> GateDecision | None:
        prior = self.attempts_for(stage_id)
        return prior[-1].decision if prior else None

    def escalations(self) -> list[GateDecision]:
        return [
            a.decision
            for a in self.attempts
            if a.decision is not None and a.decision.verdict is GateVerdict.ESCALATE
        ]

    def begin_attempt(self, stage_id: str) -> StageAttempt:
        attempt = StageAttempt(
            stage_id=stage_id,
            attempt=self.attempt_count(stage_id) + 1,
            started_at=datetime.now(UTC),
        )
        self.attempts.append(attempt)
        return attempt

    def __iter__(self) -> Iterator[StageAttempt]:
        return iter(self.attempts)


class MissingArtifactError(RuntimeError):
    """A stage's declared input was never produced."""


__all__ = ["MissingArtifactError", "RunState", "StageAttempt"]
