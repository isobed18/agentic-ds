"""Orchestration tests: spec validation, the critique loop, and gate integration.

No LLM, no model, no graph library. Stages here are trivial callables, because
what is under test is the *loop* — that a RETRY re-runs with a correction, that
an ESCALATE stops resumably, and that a spec with a structural error is rejected
before anything runs rather than three stages into a four-hour job.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ads.contracts.base import ArtifactType
from ads.contracts.gates import (
    BUILTIN_PROFILES,
    CritiqueResult,
    Finding,
    GateVerdict,
    QualitySignals,
    RiskClass,
    Severity,
)
from ads.contracts.validation import SplitStrategy, ValidationStrategy
from ads.gates import GatePolicy, StageSpec
from ads.orchestration import (
    ComponentRegistry,
    Edge,
    EdgeCondition,
    RunState,
    RunStatus,
    StageDefinition,
    StageResult,
    WorkflowSpec,
    linear_spec,
    run_workflow,
)
from ads.store import ArtifactStore

CHECKPOINTED = BUILTIN_PROFILES["checkpointed"]
FULL_AUTO = BUILTIN_PROFILES["full_auto"]


def _stage_def(stage_id: str, **kwargs) -> StageDefinition:
    return StageDefinition(id=stage_id, component=f"{stage_id}_component", **kwargs)


@pytest.fixture
def state(tmp_path: Path) -> RunState:
    return RunState(
        run_id="run1",
        store=ArtifactStore(tmp_path / "artifacts"),
        profile=FULL_AUTO,
    )


def _artifact(rationale: str = "x") -> ValidationStrategy:
    return ValidationStrategy(strategy=SplitStrategy.RANDOM, rationale=rationale)


class TestSpecValidation:
    """A malformed spec must fail at construction, not mid-run."""

    def test_duplicate_stage_ids_rejected(self) -> None:
        with pytest.raises(ValueError, match="duplicate stage ids"):
            WorkflowSpec(
                name="w", version="1",
                stages=(_stage_def("a"), _stage_def("a")),
                edges=(), entry="a",
            )

    def test_unknown_entry_rejected(self) -> None:
        with pytest.raises(ValueError, match="entry stage"):
            WorkflowSpec(
                name="w", version="1", stages=(_stage_def("a"),), edges=(), entry="ghost"
            )

    def test_edge_to_unknown_stage_rejected(self) -> None:
        with pytest.raises(ValueError, match="edge to unknown stage"):
            WorkflowSpec(
                name="w", version="1",
                stages=(_stage_def("a"),),
                edges=(Edge("a", "ghost"),),
                entry="a",
            )

    def test_unreachable_stage_rejected(self) -> None:
        with pytest.raises(ValueError, match="unreachable"):
            WorkflowSpec(
                name="w", version="1",
                stages=(_stage_def("a"), _stage_def("orphan")),
                edges=(), entry="a",
            )

    def test_proceed_self_edge_rejected(self) -> None:
        """A stage proceeding to itself would loop forever; retry is different."""
        with pytest.raises(ValueError, match="proceeds to itself"):
            Edge("a", "a", EdgeCondition.ON_PROCEED)

    def test_retry_self_edge_allowed(self) -> None:
        assert Edge("a", "a", EdgeCondition.ON_RETRY).source == "a"

    def test_consuming_an_unproduced_artifact_is_rejected(self) -> None:
        """Catches a stage inserted before its dependency."""
        with pytest.raises(ValueError, match="which no earlier stage produces"):
            linear_spec(
                "w", "1",
                [
                    _stage_def("first", consumes=(ArtifactType.EDA_REPORT,)),
                    _stage_def("second", produces=(ArtifactType.EDA_REPORT,)),
                ],
            )

    def test_valid_dependency_order_accepted(self) -> None:
        spec = linear_spec(
            "w", "1",
            [
                _stage_def("first", produces=(ArtifactType.EDA_REPORT,)),
                _stage_def("second", consumes=(ArtifactType.EDA_REPORT,)),
            ],
        )
        assert spec.stage_ids() == ("first", "second")


class TestSerialisation:
    """The spec is the visual editor's data model, so it must round-trip."""

    def test_round_trip(self) -> None:
        spec = linear_spec(
            "baseline", "2",
            [
                _stage_def("intake", produces=(ArtifactType.DATA_CARD,)),
                _stage_def("eda", consumes=(ArtifactType.DATA_CARD,)),
            ],
            retry_stages=["eda"],
        )
        restored = WorkflowSpec.from_dict(spec.to_dict())
        assert restored.to_dict() == spec.to_dict()
        assert restored.next_stage("eda", EdgeCondition.ON_RETRY) == "eda"


class TestRegistry:
    def test_unregistered_component_fails_before_running(self, state: RunState) -> None:
        spec = linear_spec("w", "1", [_stage_def("a")])
        with pytest.raises(KeyError, match="unregistered components"):
            run_workflow(spec, ComponentRegistry(), state)

    def test_duplicate_registration_rejected(self) -> None:
        registry = ComponentRegistry()
        registry.register("x", lambda s, c: StageResult())
        with pytest.raises(ValueError, match="already registered"):
            registry.register("x", lambda s, c: StageResult())


class TestHappyPath:
    def test_linear_run_completes(self, state: RunState) -> None:
        calls: list[str] = []

        def make(stage_id: str):
            def stage(s: RunState, correction=None) -> StageResult:
                calls.append(stage_id)
                return StageResult(artifacts=[_artifact(stage_id)])
            return stage

        spec = linear_spec("w", "1", [_stage_def("a"), _stage_def("b"), _stage_def("c")])
        registry = ComponentRegistry()
        for sid in ("a", "b", "c"):
            registry.register(f"{sid}_component", make(sid))

        outcome = run_workflow(spec, registry, state)
        assert outcome.completed
        assert calls == ["a", "b", "c"]
        assert len(state.attempts) == 3

    def test_artifacts_are_persisted_per_stage(self, state: RunState) -> None:
        spec = linear_spec("w", "1", [_stage_def("a")])
        registry = ComponentRegistry()
        registry.register(
            "a_component", lambda s, c: StageResult(artifacts=[_artifact("only")])
        )
        run_workflow(spec, registry, state)
        assert len(state.store.list("run1", artifact_type=ArtifactType.VALIDATION_STRATEGY)) == 1

    def test_gate_decisions_are_persisted(self, state: RunState) -> None:
        spec = linear_spec("w", "1", [_stage_def("a")])
        registry = ComponentRegistry()
        registry.register("a_component", lambda s, c: StageResult())
        run_workflow(spec, registry, state)
        assert state.store.list("run1", artifact_type=ArtifactType.GATE_DECISION)


class TestCritiqueLoop:
    """The loop the whole architecture is built around."""

    @staticmethod
    def _failing_then_passing(fail_times: int):
        """A stage that reports unmet criteria until it has been corrected."""
        seen: list[list[str] | None] = []

        def stage(s: RunState, correction=None) -> StageResult:
            seen.append(correction)
            unmet = ["eda.all_features_covered"] if len(seen) <= fail_times else []
            return StageResult(
                artifacts=[_artifact(f"attempt{len(seen)}")],
                critique=CritiqueResult(
                    stage_id="eda", rubric_version="eda.v1", unmet_criteria=unmet
                ),
            )

        return stage, seen

    def _spec(self) -> WorkflowSpec:
        return linear_spec(
            "w", "1", [_stage_def("eda"), _stage_def("done")], retry_stages=["eda"]
        )

    def _policy(self, max_attempts: int = 3) -> GatePolicy:
        return GatePolicy(
            stages={
                "eda": StageSpec(
                    id="eda",
                    risk_class=RiskClass.LOW,
                    max_attempts=max_attempts,
                    mandatory_criteria=frozenset({"eda.all_features_covered"}),
                )
            }
        )

    def test_retry_reruns_the_same_stage(self, state: RunState) -> None:
        stage, seen = self._failing_then_passing(fail_times=1)
        registry = ComponentRegistry()
        registry.register("eda_component", stage)
        registry.register("done_component", lambda s, c: StageResult())

        outcome = run_workflow(self._spec(), registry, state, policy=self._policy())
        assert outcome.completed
        assert len(seen) == 2, "stage should run twice: fail, then corrected"
        assert state.attempt_count("eda") == 2

    def test_retry_passes_a_targeted_correction(self, state: RunState) -> None:
        """The re-invoked stage gets instructions, not a transcript."""
        stage, seen = self._failing_then_passing(fail_times=1)
        registry = ComponentRegistry()
        registry.register("eda_component", stage)
        registry.register("done_component", lambda s, c: StageResult())

        run_workflow(self._spec(), registry, state, policy=self._policy())
        assert seen[0] is None
        assert seen[1] is not None
        assert any("eda.all_features_covered" in i for i in seen[1])

    def test_exhausted_retries_escalate(self, state: RunState) -> None:
        stage, seen = self._failing_then_passing(fail_times=99)
        registry = ComponentRegistry()
        registry.register("eda_component", stage)
        registry.register("done_component", lambda s, c: StageResult())

        outcome = run_workflow(self._spec(), registry, state, policy=self._policy(2))
        assert outcome.status == RunStatus.AWAITING_HUMAN
        assert outcome.pending_question is not None

    def test_repeated_identical_failure_escalates_early(self, state: RunState) -> None:
        """Same failure twice means more attempts will not help."""
        stage, seen = self._failing_then_passing(fail_times=99)
        registry = ComponentRegistry()
        registry.register("eda_component", stage)
        registry.register("done_component", lambda s, c: StageResult())

        outcome = run_workflow(self._spec(), registry, state, policy=self._policy(10))
        assert outcome.status == RunStatus.AWAITING_HUMAN
        assert len(seen) < 10, "must give up before burning the whole budget"

    def test_retry_without_a_retry_edge_is_a_spec_error(self, state: RunState) -> None:
        spec = linear_spec("w", "1", [_stage_def("eda"), _stage_def("done")])
        stage, _ = self._failing_then_passing(fail_times=99)
        registry = ComponentRegistry()
        registry.register("eda_component", stage)
        registry.register("done_component", lambda s, c: StageResult())

        outcome = run_workflow(spec, registry, state, policy=self._policy())
        assert outcome.status == RunStatus.FAILED
        assert "no ON_RETRY edge" in outcome.error


class TestEscalation:
    def test_critical_stage_stops_the_run(self, tmp_path: Path) -> None:
        state = RunState(
            run_id="run1",
            store=ArtifactStore(tmp_path / "a"),
            profile=CHECKPOINTED,
        )
        spec = linear_spec("w", "1", [_stage_def("problem_discovery"), _stage_def("next")])
        registry = ComponentRegistry()
        ran: list[str] = []
        registry.register(
            "problem_discovery_component",
            lambda s, c: (ran.append("pd"), StageResult())[1],
        )
        registry.register("next_component", lambda s, c: (ran.append("next"), StageResult())[1])

        outcome = run_workflow(spec, registry, state, policy=GatePolicy.load())
        assert outcome.status == RunStatus.AWAITING_HUMAN
        assert ran == ["pd"], "downstream stages must not run past an escalation"

    def test_escalation_is_resumable(self, tmp_path: Path) -> None:
        """Artifacts survive the stop, which is what makes resume possible."""
        state = RunState(
            run_id="run1", store=ArtifactStore(tmp_path / "a"), profile=CHECKPOINTED
        )
        spec = linear_spec("w", "1", [_stage_def("problem_discovery"), _stage_def("next")])
        registry = ComponentRegistry()
        registry.register(
            "problem_discovery_component",
            lambda s, c: StageResult(artifacts=[_artifact("kept")]),
        )
        registry.register("next_component", lambda s, c: StageResult())

        run_workflow(spec, registry, state, policy=GatePolicy.load())
        assert state.store.list("run1", artifact_type=ArtifactType.VALIDATION_STRATEGY)

    def test_hard_rule_stops_even_in_full_auto(self, state: RunState) -> None:
        """The gate's authority survives being driven by the orchestrator."""
        spec = linear_spec("w", "1", [_stage_def("a"), _stage_def("b")])
        registry = ComponentRegistry()
        registry.register(
            "a_component",
            lambda s, c: StageResult(signals=QualitySignals(pii_columns_in_context=2)),
        )
        registry.register("b_component", lambda s, c: StageResult())

        outcome = run_workflow(spec, registry, state)
        assert outcome.status == RunStatus.AWAITING_HUMAN
        assert outcome.decisions[-1].reason_code == "pii_egress_requested"


class TestFailureHandling:
    def test_stage_exception_fails_the_run_with_context(self, state: RunState) -> None:
        spec = linear_spec("w", "1", [_stage_def("a")])
        registry = ComponentRegistry()

        def boom(s: RunState, correction=None) -> StageResult:
            raise ValueError("column not found")

        registry.register("a_component", boom)
        outcome = run_workflow(spec, registry, state)
        assert outcome.status == RunStatus.FAILED
        assert "column not found" in outcome.error
        assert state.attempts[-1].error is not None

    def test_forward_cycle_is_rejected_at_construction(self) -> None:
        """Codex FINDING 14: a forward cycle disabled dependency validation.

        Falling back to declaration order let a spec whose consumer runs before
        its producer pass validation and fail at runtime with a missing artifact.
        Retreats belong on ON_RETRY, which is excluded from the sort.
        """
        with pytest.raises(ValueError, match="cycle among non-retry edges"):
            WorkflowSpec(
                name="w", version="1",
                stages=(_stage_def("a"), _stage_def("b")),
                edges=(Edge("a", "b"), Edge("b", "a")),
                entry="a",
            )

    def test_declaration_order_cannot_smuggle_a_missing_input(self) -> None:
        """The exact spec Codex used: declared out of execution order."""
        with pytest.raises(ValueError, match="cycle among non-retry edges"):
            WorkflowSpec(
                name="w", version="1",
                stages=(
                    _stage_def("seed"),
                    _stage_def("evaluation", produces=(ArtifactType.EDA_REPORT,)),
                    _stage_def("feature", consumes=(ArtifactType.EDA_REPORT,)),
                ),
                edges=(
                    Edge("seed", "feature"),
                    Edge("feature", "evaluation"),
                    Edge("evaluation", "feature"),
                ),
                entry="seed",
            )

    def test_retry_cycle_is_bounded_at_runtime(self, state: RunState) -> None:
        """Retry edges bypass the sort, so the step backstop still earns its keep."""
        spec = WorkflowSpec(
            name="w", version="1",
            stages=(_stage_def("a"), _stage_def("b")),
            edges=(
                Edge("a", "b", EdgeCondition.ON_PROCEED),
                Edge("b", "a", EdgeCondition.ON_RETRY),
            ),
            entry="a",
        )
        registry = ComponentRegistry()
        registry.register("a_component", lambda s, c: StageResult())
        # The failure must differ each time, or `repeated_identical_failure`
        # escalates first — which is the gate working correctly and means this
        # backstop is genuinely hard to reach.
        counter = {"n": 0}

        def flaky(s: RunState, correction=None) -> StageResult:
            counter["n"] += 1
            criterion = f"b.needs_work_{counter['n'] % 3}"
            return StageResult(
                critique=CritiqueResult(
                    stage_id="b", rubric_version="v1", unmet_criteria=[criterion]
                )
            )

        registry.register("b_component", flaky)
        policy = GatePolicy(
            stages={
                "b": StageSpec(
                    id="b",
                    max_attempts=999,
                    mandatory_criteria=frozenset(
                        {"b.needs_work_0", "b.needs_work_1", "b.needs_work_2"}
                    ),
                )
            }
        )
        outcome = run_workflow(spec, registry, state, policy=policy, max_total_steps=8)
        assert outcome.status == RunStatus.FAILED
        assert "cycling" in outcome.error


class TestEvents:
    def test_events_are_emitted_for_observability(self, state: RunState) -> None:
        events: list[tuple[str, dict]] = []
        spec = linear_spec("w", "1", [_stage_def("a")])
        registry = ComponentRegistry()
        registry.register("a_component", lambda s, c: StageResult())

        run_workflow(spec, registry, state, on_event=lambda e, p: events.append((e, p)))
        names = [e for e, _ in events]
        assert "stage_started" in names
        assert "gate_decided" in names


class TestRunStateProjection:
    def test_latest_returns_the_corrected_artifact(self, state: RunState) -> None:
        """A retried stage's downstream must see the correction, not the reject."""
        from ads.contracts.validation import ValidationStrategy as VS

        state.put(_artifact("first"), stage_id="a")
        state.put(_artifact("second"), stage_id="a")
        latest = state.latest(ArtifactType.VALIDATION_STRATEGY, VS)
        assert latest is not None
        assert latest.rationale == "second"

    def test_require_names_the_missing_input(self, state: RunState) -> None:
        from ads.contracts.validation import ValidationStrategy as VS

        with pytest.raises(Exception, match="no 'validation_strategy' artifact"):
            state.require(ArtifactType.VALIDATION_STRATEGY, VS)

    def test_critique_errors_drive_a_retry(self, state: RunState) -> None:
        seen: list[list[str] | None] = []

        def stage(s: RunState, correction=None) -> StageResult:
            seen.append(correction)
            findings = (
                [
                    Finding(
                        check_id="eda.missing_plot",
                        severity=Severity.ERROR,
                        evidence="no target plot",
                        suggested_fix="Add the target distribution plot.",
                    )
                ]
                if len(seen) == 1
                else []
            )
            return StageResult(
                critique=CritiqueResult(
                    stage_id="eda", rubric_version="eda.v1", findings=findings
                )
            )

        spec = linear_spec("w", "1", [_stage_def("eda")], retry_stages=["eda"])
        registry = ComponentRegistry()
        registry.register("eda_component", stage)

        outcome = run_workflow(
            spec, registry, state,
            policy=GatePolicy(stages={"eda": StageSpec(id="eda", max_attempts=3)}),
        )
        assert outcome.completed
        assert len(seen) == 2
        assert seen[1] == ["Add the target distribution plot."]


class TestVerdictRouting:
    @pytest.mark.parametrize(
        ("verdict", "expected_status"),
        [
            (GateVerdict.AUTO_PROCEED, RunStatus.COMPLETED),
            (GateVerdict.ESCALATE, RunStatus.AWAITING_HUMAN),
        ],
    )
    def test_verdict_determines_run_status(
        self, state: RunState, verdict: GateVerdict, expected_status: str
    ) -> None:
        signals = (
            QualitySignals(pii_columns_in_context=1)
            if verdict is GateVerdict.ESCALATE
            else QualitySignals()
        )
        spec = linear_spec("w", "1", [_stage_def("a")])
        registry = ComponentRegistry()
        registry.register("a_component", lambda s, c: StageResult(signals=signals))
        assert run_workflow(spec, registry, state).status == expected_status


class TestPerAttemptInputBinding:
    """Codex ANSWER 5: latest-wins made a retry a new experiment, not a replay.

    `_correction_for` promises the retry is the rejected attempt plus a
    correction. Without pinned inputs a stage read whatever was newest when it
    looked, so a correction derived from V1 was applied to a stage now reading V2.
    """

    def test_retry_reads_the_same_inputs_as_the_rejected_attempt(
        self, state: RunState
    ) -> None:
        seen_rationales: list[str | None] = []

        def upstream(s: RunState, correction=None) -> StageResult:
            return StageResult(artifacts=[_artifact("V1")])

        def downstream(s: RunState, correction=None) -> StageResult:
            from ads.contracts.validation import ValidationStrategy as VS

            bound = s.latest(ArtifactType.VALIDATION_STRATEGY, VS)
            seen_rationales.append(bound.rationale if bound else None)
            # Write a newer artifact of the same type between attempts, which
            # under latest-wins would hijack the retry's view of its input.
            s.put(_artifact("V2"), stage_id="interloper")
            unmet = ["d.needs_work"] if len(seen_rationales) == 1 else []
            return StageResult(
                critique=CritiqueResult(
                    stage_id="down", rubric_version="v1", unmet_criteria=unmet
                )
            )

        spec = linear_spec(
            "w", "1",
            [
                _stage_def("up", produces=(ArtifactType.VALIDATION_STRATEGY,)),
                _stage_def("down", consumes=(ArtifactType.VALIDATION_STRATEGY,)),
            ],
            retry_stages=["down"],
        )
        registry = ComponentRegistry()
        registry.register("up_component", upstream)
        registry.register("down_component", downstream)

        outcome = run_workflow(
            spec, registry, state,
            policy=GatePolicy(
                stages={
                    "down": StageSpec(
                        id="down",
                        max_attempts=3,
                        mandatory_criteria=frozenset({"d.needs_work"}),
                    )
                }
            ),
        )
        assert outcome.completed
        assert seen_rationales == ["V1", "V1"], (
            f"retry must replay against its original input, saw {seen_rationales}"
        )

    def test_bindings_are_recorded_on_the_attempt(self, state: RunState) -> None:
        """Lineage must be inspectable, not inferred from timestamps."""
        spec = linear_spec(
            "w", "1",
            [
                _stage_def("up", produces=(ArtifactType.VALIDATION_STRATEGY,)),
                _stage_def("down", consumes=(ArtifactType.VALIDATION_STRATEGY,)),
            ],
        )
        registry = ComponentRegistry()
        registry.register("up_component", lambda s, c: StageResult(artifacts=[_artifact("V1")]))
        registry.register("down_component", lambda s, c: StageResult())

        run_workflow(spec, registry, state)
        down = state.attempts_for("down")[0]
        assert ArtifactType.VALIDATION_STRATEGY.value in down.input_bindings

    def test_undeclared_reads_still_fall_back_to_newest(self, state: RunState) -> None:
        """Binding covers declared inputs only; the fallback must stay usable."""
        from ads.contracts.validation import ValidationStrategy as VS

        state.put(_artifact("first"), stage_id="x")
        state.put(_artifact("second"), stage_id="x")
        assert state.latest(ArtifactType.VALIDATION_STRATEGY, VS).rationale == "second"
