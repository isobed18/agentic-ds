"""Orchestrator critique and human-resume tests.

Two things the pipeline claimed but did not have:

* the Orchestrator judging a stage against a rubric **it** owns, rather than the
  stage grading its own homework
* a way to act on a human's answer once a gate escalated

Both run without a model. The critic's mechanical half is deliberately usable
with ``llm=None`` so air-gapped runs and tests still get it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_agents import FakeLLM

from ads.contracts.base import ArtifactType
from ads.contracts.gates import BUILTIN_PROFILES, CritiqueResult, QualitySignals
from ads.contracts.problem import (
    Metric,
    ProblemCandidate,
    ProblemCandidateSet,
    ProblemSupport,
    TaskType,
)
from ads.contracts.validation import SplitStrategy, ValidationStrategy
from ads.gates import GatePolicy, StageSpec
from ads.orchestration import (
    ComponentRegistry,
    Criterion,
    CritiqueContext,
    Rubric,
    RubricRegistry,
    RunState,
    RunStatus,
    StageDefinition,
    StageResult,
    critique_stage,
    linear_spec,
    resume_workflow,
    run_workflow,
)
from ads.store import ArtifactStore

CHECKPOINTED = BUILTIN_PROFILES["checkpointed"]
FULL_AUTO = BUILTIN_PROFILES["full_auto"]


def _stage_def(stage_id: str, **kwargs) -> StageDefinition:
    return StageDefinition(id=stage_id, component=f"{stage_id}_component", **kwargs)


def _artifact(rationale: str = "x") -> ValidationStrategy:
    return ValidationStrategy(strategy=SplitStrategy.RANDOM, rationale=rationale)


def _rubric() -> Rubric:
    """One mechanical criterion and one that needs judgment — the realistic mix."""
    return Rubric(
        stage_id="eda",
        version="v1",
        criteria=(
            Criterion(
                id="eda.produced_something",
                description="The stage produced at least one artifact.",
                check=lambda ctx: bool(ctx.artifacts),
            ),
            Criterion(
                id="eda.outliers_discussed",
                description="Outlier treatment is addressed.",
            ),
        ),
    )


@pytest.fixture
def state(tmp_path: Path) -> RunState:
    return RunState(run_id="run1", store=ArtifactStore(tmp_path / "artifacts"), profile=FULL_AUTO)


class TestDeterministicHalf:
    def test_mechanical_criteria_need_no_model(self) -> None:
        result = critique_stage(_rubric(), CritiqueContext(stage_id="eda", artifacts=[]))
        assert "eda.produced_something" in result.unmet_criteria
        assert result.rubric_version == "eda.v1"

    def test_passing_mechanical_check_is_silent(self) -> None:
        result = critique_stage(_rubric(), CritiqueContext(stage_id="eda", artifacts=[_artifact()]))
        assert result.unmet_criteria == []

    def test_judgment_items_are_skipped_without_a_model(self) -> None:
        """Air-gapped and test runs still get the mechanical half."""
        result = critique_stage(
            _rubric(), CritiqueContext(stage_id="eda", artifacts=[_artifact()]), llm=None
        )
        assert "eda.outliers_discussed" not in result.unmet_criteria

    def test_critique_carries_no_confidence_field(self) -> None:
        assert "confidence" not in CritiqueResult.model_fields


class TestHowAFailedCheckReads:
    """#427: a failure said the opposite of what happened.

    `Criterion.description` states the condition that *should* hold, and the
    critic rendered a failure as `"Mechanical check failed: " + description` --
    so the panel printed the success sentence with "failed:" glued to the
    front. On the reported run both bullets read as assertions that everything
    was fine: "Mechanical check failed: At least one proposed problem has
    measured viable support."
    """

    def test_the_finding_states_the_failure_not_the_condition(self) -> None:
        rubric = Rubric(
            stage_id="eda",
            version="v1",
            criteria=(
                Criterion(
                    id="eda.produced_something",
                    description="The stage produced at least one artifact.",
                    failure="The stage produced no artifacts at all.",
                    check=lambda ctx: bool(ctx.artifacts),
                ),
            ),
        )

        result = critique_stage(rubric, CritiqueContext(stage_id="eda", artifacts=[]))

        finding = next(f for f in result.findings if f.check_id == "eda.produced_something")
        assert finding.evidence == "The stage produced no artifacts at all."
        assert "Mechanical check failed" not in finding.evidence
        assert "produced at least one artifact" not in finding.evidence

    def test_a_criterion_with_no_failure_wording_reports_its_id(self) -> None:
        """A bare code is a worse read than a sentence, but an honest one --
        where the inverted sentence was neither. `build_pipeline_rubrics` gives
        every criterion wording, so this is the safety net, not the path."""
        result = critique_stage(_rubric(), CritiqueContext(stage_id="eda", artifacts=[]))

        finding = next(f for f in result.findings if f.check_id == "eda.produced_something")
        assert finding.evidence.startswith("eda.produced_something:")
        assert "Mechanical check failed" not in finding.evidence

    def test_measured_evidence_reaches_the_finding(self) -> None:
        """The reason is measured; without this it stays in the artifact while
        the panel shows a tautology."""
        rubric = Rubric(
            stage_id="eda",
            version="v1",
            criteria=(
                Criterion(
                    id="eda.produced_something",
                    description="The stage produced at least one artifact.",
                    failure="The stage produced no artifacts at all.",
                    check=lambda ctx: bool(ctx.artifacts),
                    evidence=lambda ctx: ["the sandbox exited before writing anything"],
                ),
            ),
        )

        result = critique_stage(rubric, CritiqueContext(stage_id="eda", artifacts=[]))

        finding = next(f for f in result.findings if f.check_id == "eda.produced_something")
        assert finding.measurements == ["the sandbox exited before writing anything"]

    def test_a_met_check_computes_no_evidence(self) -> None:
        """Nothing to explain, and the evidence function may be expensive."""
        calls: list[int] = []
        rubric = Rubric(
            stage_id="eda",
            version="v1",
            criteria=(
                Criterion(
                    id="eda.produced_something",
                    description="The stage produced at least one artifact.",
                    failure="The stage produced no artifacts at all.",
                    check=lambda ctx: bool(ctx.artifacts),
                    evidence=lambda ctx: calls.append(1) or ["never"],
                ),
            ),
        )

        result = critique_stage(rubric, CritiqueContext(stage_id="eda", artifacts=[_artifact()]))

        assert result.unmet_criteria == []
        assert calls == []

    def test_every_pipeline_criterion_states_its_own_failure(self) -> None:
        """The regression was one criterion short of a rendering bug; the fix
        is only complete if none of them can fall back to the id."""
        from ads.pipeline.rubrics import build_pipeline_rubrics

        registry = build_pipeline_rubrics()
        wordless = [
            criterion.id
            for rubric in registry.rubrics.values()
            for criterion in rubric.deterministic()
            if not criterion.failure
        ]

        assert wordless == []


class TestTheProblemDiscoveryRubricExplainsItself:
    """#427 part 2: the reason is measured, and then was discarded.

    `compute_support` exists to turn "this framing is not viable" into facts
    and writes them to `ProblemSupport.blocking_reasons` on every candidate.
    Nothing surfaced them, so the panel showed a tautology while the actual
    explanation sat in the artifact.
    """

    @staticmethod
    def _candidates() -> ProblemCandidateSet:
        def blocked(candidate_id: str, target: str, task: TaskType, reason: str):
            return ProblemCandidate(
                candidate_id=candidate_id,
                title=f"Predict {target}",
                task_type=task,
                target_column=target,
                business_rationale="because",
                primary_metric=Metric.RMSE if task is TaskType.REGRESSION else Metric.ROC_AUC,
                support=ProblemSupport(
                    n_rows=100_000,
                    target_null_rate=0.0,
                    n_usable_features=12,
                    rows_per_feature=8333.0,
                    blocking_reasons=[reason],
                ),
            )

        return ProblemCandidateSet(
            candidates=[
                blocked(
                    "c1",
                    "claim_description",
                    TaskType.MULTICLASS_CLASSIFICATION,
                    "too_many_classes: 'claim_description' has 97219 levels (limit 50)",
                ),
                blocked(
                    "c2",
                    "date_time_of_accident",
                    TaskType.REGRESSION,
                    "task_target_mismatch: regression needs a numeric target",
                ),
            ]
        )

    def _finding(self, candidates: ProblemCandidateSet | None):
        from ads.pipeline.rubrics import build_pipeline_rubrics

        rubric = build_pipeline_rubrics().get("problem_discovery")
        assert rubric is not None
        result = critique_stage(
            rubric,
            CritiqueContext(
                stage_id="problem_discovery",
                artifacts=[candidates] if candidates else [],
                facts={"signals": QualitySignals(validation_failures=0)},
            ),
        )
        return next(
            f for f in result.findings if f.check_id == "problem.at_least_one_viable_candidate"
        )

    def test_each_rejected_framing_and_its_measured_reason_are_reported(self) -> None:
        finding = self._finding(self._candidates())

        assert finding.evidence == "None of the proposed ML problems is viable against this data."
        assert len(finding.measurements) == 2
        assert any("97219 levels" in line for line in finding.measurements)
        assert any("needs a numeric target" in line for line in finding.measurements)
        # The framing, not just the reason: two candidates can be blocked for
        # the same reason on different columns.
        assert any("claim_description" in line for line in finding.measurements)
        assert any("date_time_of_accident" in line for line in finding.measurements)

    def test_no_candidates_at_all_says_so_rather_than_going_quiet(self) -> None:
        assert self._finding(None).measurements == ["No problem candidates were produced at all."]

    def test_a_failed_contract_check_names_the_validators_that_fired(self) -> None:
        """`_valid_contract` is a count, so a reader was told a contract check
        failed and never which validator fired or on what column -- which is
        why the reported run could not be diagnosed from the product."""
        from ads.pipeline.rubrics import build_pipeline_rubrics

        rubric = build_pipeline_rubrics().get("problem_discovery")
        assert rubric is not None
        result = critique_stage(
            rubric,
            CritiqueContext(
                stage_id="problem_discovery",
                artifacts=[self._candidates()],
                facts={
                    "signals": QualitySignals(
                        validation_failures=1,
                        validation_failure_details=[
                            "semantic/unknown_target: Target 'UltimateIncurredClaimCost' is not "
                            "a column of the ABT."
                        ],
                    )
                },
            ),
        )

        finding = next(f for f in result.findings if f.check_id == "problem.targets_exist")
        assert "unknown_target" in finding.measurements[0]
        assert "UltimateIncurredClaimCost" in finding.measurements[0]

    def test_a_count_with_no_detail_still_says_how_many(self) -> None:
        # Older records, and stages that report the count without the list.
        from ads.pipeline.rubrics import build_pipeline_rubrics

        rubric = build_pipeline_rubrics().get("problem_discovery")
        assert rubric is not None
        result = critique_stage(
            rubric,
            CritiqueContext(
                stage_id="problem_discovery",
                artifacts=[self._candidates()],
                facts={"signals": QualitySignals(validation_failures=3)},
            ),
        )

        finding = next(f for f in result.findings if f.check_id == "problem.targets_exist")
        assert "3 deterministic validator failure(s)" in finding.measurements[0]


class TestJudgmentHalf:
    def test_model_is_asked_only_about_residual_items(self) -> None:
        llm = FakeLLM([{"unmet_criteria": ["eda.outliers_discussed"], "findings": []}])
        result = critique_stage(
            _rubric(),
            CritiqueContext(stage_id="eda", artifacts=[_artifact()]),
            llm=llm,
            artifact_digest="EDA report: 12 columns profiled.",
        )
        assert result.unmet_criteria == ["eda.outliers_discussed"]
        checklist = llm.calls[0]["prompt"].split("must judge")[-1]
        assert "eda.outliers_discussed" in checklist
        assert "eda.produced_something" not in checklist

    def test_computed_results_are_handed_over_as_established_fact(self) -> None:
        llm = FakeLLM([{"unmet_criteria": [], "findings": []}])
        critique_stage(_rubric(), CritiqueContext(stage_id="eda", artifacts=[_artifact()]), llm=llm)
        assert "eda.produced_something: met" in llm.calls[0]["prompt"]

    def test_invented_criteria_are_ignored(self) -> None:
        """A model must not fail a stage against a standard nobody declared."""
        llm = FakeLLM([{"unmet_criteria": ["eda.made_up_rule"], "findings": []}])
        result = critique_stage(
            _rubric(), CritiqueContext(stage_id="eda", artifacts=[_artifact()]), llm=llm
        )
        assert result.unmet_criteria == []

    def test_unparseable_critique_does_not_silently_approve(self) -> None:
        result = critique_stage(
            _rubric(),
            CritiqueContext(stage_id="eda", artifacts=[_artifact()]),
            llm=FakeLLM(["not json at all"]),
        )
        assert any(f.check_id == "critique.unavailable" for f in result.findings)

    def test_findings_cite_the_criterion(self) -> None:
        llm = FakeLLM(
            [
                {
                    "unmet_criteria": ["eda.outliers_discussed"],
                    "findings": [
                        {
                            "criterion_id": "eda.outliers_discussed",
                            "evidence": "No outlier section in the report.",
                            "suggested_fix": "Add an outlier summary.",
                        }
                    ],
                }
            ]
        )
        result = critique_stage(
            _rubric(), CritiqueContext(stage_id="eda", artifacts=[_artifact()]), llm=llm
        )
        finding = next(f for f in result.findings if f.check_id == "eda.outliers_discussed")
        assert finding.suggested_fix == "Add an outlier summary."


class TestRunnerUsesTheRubric:
    def test_orchestrator_rubric_overrides_stage_self_report(self, state: RunState) -> None:
        """A stage claiming success must not be able to approve itself."""
        rubrics = RubricRegistry()
        rubrics.register(
            Rubric(
                stage_id="eda",
                version="v1",
                criteria=(
                    Criterion(
                        id="eda.produced_something",
                        description="Produced an artifact.",
                        check=lambda ctx: bool(ctx.artifacts),
                    ),
                ),
            )
        )
        spec = linear_spec("w", "1", [_stage_def("eda")], retry_stages=["eda"])
        registry = ComponentRegistry()
        registry.register(
            "eda_component",
            lambda s, c: StageResult(
                critique=CritiqueResult(stage_id="eda", rubric_version="self", unmet_criteria=[])
            ),
        )
        outcome = run_workflow(
            spec,
            registry,
            state,
            rubrics=rubrics,
            policy=GatePolicy(
                stages={
                    "eda": StageSpec(
                        id="eda",
                        max_attempts=2,
                        mandatory_criteria=frozenset({"eda.produced_something"}),
                    )
                }
            ),
        )
        assert outcome.status == RunStatus.AWAITING_HUMAN
        assert state.attempts[0].critique.rubric_version == "eda.v1"

    def test_stage_self_critique_is_kept_when_no_rubric_exists(self, state: RunState) -> None:
        spec = linear_spec("w", "1", [_stage_def("a")])
        registry = ComponentRegistry()
        registry.register(
            "a_component",
            lambda s, c: StageResult(critique=CritiqueResult(stage_id="a", rubric_version="self")),
        )
        run_workflow(spec, registry, state, rubrics=RubricRegistry())
        assert state.attempts[0].critique.rubric_version == "self"

    def test_duplicate_rubric_registration_rejected(self) -> None:
        registry = RubricRegistry()
        registry.register(_rubric())
        with pytest.raises(ValueError, match="already registered"):
            registry.register(_rubric())


class TestResumeAfterHuman:
    """An escalation is only useful if the answer can be acted on."""

    @staticmethod
    def _spec():
        return linear_spec(
            "w",
            "1",
            [_stage_def("problem_discovery"), _stage_def("after")],
            retry_stages=["problem_discovery"],
        )

    @staticmethod
    def _registry(ran: list[str]) -> ComponentRegistry:
        registry = ComponentRegistry()
        registry.register(
            "problem_discovery_component",
            lambda s, c: (ran.append(f"pd:{c}"), StageResult())[1],
        )
        registry.register("after_component", lambda s, c: (ran.append("after"), StageResult())[1])
        return registry

    def _stopped(self, tmp_path: Path):
        state = RunState(run_id="run1", store=ArtifactStore(tmp_path / "a"), profile=CHECKPOINTED)
        ran: list[str] = []
        spec, registry = self._spec(), self._registry(ran)
        outcome = run_workflow(spec, registry, state, policy=GatePolicy.load())
        assert outcome.status == RunStatus.AWAITING_HUMAN
        return spec, registry, state, ran

    def test_approve_continues_past_the_gate(self, tmp_path: Path) -> None:
        spec, registry, state, ran = self._stopped(tmp_path)
        outcome = resume_workflow(
            spec, registry, state, decision="approve", policy=GatePolicy.load()
        )
        assert outcome.completed
        assert "after" in ran

    def test_retry_reruns_with_the_human_instruction(self, tmp_path: Path) -> None:
        spec, registry, state, ran = self._stopped(tmp_path)
        resume_workflow(
            spec,
            registry,
            state,
            decision="retry",
            instructions=["use the fraud target instead"],
            policy=GatePolicy.load(),
        )
        assert any("use the fraud target instead" in entry for entry in ran)

    def test_human_instruction_is_consumed_once(self, tmp_path: Path) -> None:
        """A person's correction applies to the next attempt, not to all of them."""
        spec, registry, state, _ = self._stopped(tmp_path)
        resume_workflow(
            spec,
            registry,
            state,
            decision="retry",
            instructions=["do it differently"],
            policy=GatePolicy.load(),
        )
        assert "human_correction::problem_discovery" not in state.blackboard

    def test_abort_stops_the_run(self, tmp_path: Path) -> None:
        spec, registry, state, _ = self._stopped(tmp_path)
        assert resume_workflow(spec, registry, state, decision="abort").status == RunStatus.ABORTED

    def test_decision_is_recorded_for_the_report(self, tmp_path: Path) -> None:
        """The report must distinguish human approval from autonomous progress."""
        spec, registry, state, _ = self._stopped(tmp_path)
        resume_workflow(spec, registry, state, decision="approve", policy=GatePolicy.load())
        recorded = state.blackboard["human_decisions"]
        assert recorded[0]["stage_id"] == "problem_discovery"
        assert recorded[0]["decision"] == "approve"

    def test_artifacts_survive_the_stop(self, tmp_path: Path) -> None:
        """Resume works because nothing was discarded, not because it replays."""
        spec, registry, state, _ = self._stopped(tmp_path)
        before = len(state.attempts)
        resume_workflow(spec, registry, state, decision="approve", policy=GatePolicy.load())
        assert len(state.attempts) > before

    def test_partial_output_cannot_be_approved_when_next_stage_input_is_missing(
        self, tmp_path: Path
    ) -> None:
        """An audit artifact is not a substitute for the contract the graph requires.

        A failed agent stage can still emit its audit record. The old prompt saw
        one artifact and offered Approve even when the next stage's declared
        input (the integration plan in #263) was absent, so accepting the option
        could only end in MissingArtifactError.
        """
        spec = linear_spec(
            "partial-output",
            "1",
            [
                _stage_def(
                    "schema_discovery",
                    produces=(ArtifactType.INTEGRATION_PLAN,),
                ),
                _stage_def(
                    "integration",
                    consumes=(ArtifactType.INTEGRATION_PLAN,),
                ),
            ],
            retry_stages=["schema_discovery"],
        )
        registry = ComponentRegistry()
        registry.register(
            "schema_discovery_component",
            lambda state, correction: StageResult(
                artifacts=[_artifact("audit-only partial output")],
            ),
        )
        registry.register("integration_component", lambda state, correction: StageResult())
        state = RunState(
            run_id="run-partial",
            store=ArtifactStore(tmp_path / "partial"),
            profile=FULL_AUTO,
        )

        outcome = run_workflow(spec, registry, state, policy=GatePolicy.load())

        assert outcome.status == RunStatus.AWAITING_HUMAN
        prompt = outcome.pending_question.human_prompt
        assert prompt is not None
        assert "approve" not in {option.option_id for option in prompt.options}
        assert prompt.question_kind == "missing_output"
        assert prompt.missing_artifact_types == ["integration_plan"]

    def test_resuming_a_run_that_is_not_waiting_is_an_error(self, state: RunState) -> None:
        spec = linear_spec("w", "1", [_stage_def("a")])
        registry = ComponentRegistry()
        registry.register("a_component", lambda s, c: StageResult())
        run_workflow(spec, registry, state)
        with pytest.raises(RuntimeError, match="not waiting on a human decision"):
            resume_workflow(spec, registry, state, decision="approve")

    def test_unknown_decision_rejected(self, tmp_path: Path) -> None:
        spec, registry, state, _ = self._stopped(tmp_path)
        with pytest.raises(ValueError, match="unknown decision"):
            resume_workflow(spec, registry, state, decision="maybe")


class TestSelfRetryIdentity:
    """Codex FINDING 17: correction text is not retry identity.

    Inheritance of pinned inputs was triggered by `correction is not None`. A
    genuine RETRY carrying no instruction text mapped to None, so the runner
    rebound to newer inputs — the different-experiment defect through a narrower
    door. Identity now comes from the graph and the verdict.
    """

    @staticmethod
    def _spec():
        return linear_spec(
            "w",
            "1",
            [
                _stage_def("up", produces=(ArtifactType.VALIDATION_STRATEGY,)),
                _stage_def("down", consumes=(ArtifactType.VALIDATION_STRATEGY,)),
            ],
            retry_stages=["down"],
        )

    def test_retry_without_instructions_still_inherits_inputs(self, state: RunState) -> None:
        seen: list[str | None] = []

        def downstream(s: RunState, correction=None) -> StageResult:
            bound = s.latest(ArtifactType.VALIDATION_STRATEGY, ValidationStrategy)
            seen.append(bound.rationale if bound else None)
            s.put(_artifact("V2"), stage_id="interloper")
            # A signal-driven retry: the rule that fires carries no instructions.
            signals = (
                QualitySignals(max_target_correlation=0.99) if len(seen) == 1 else QualitySignals()
            )
            return StageResult(signals=signals)

        registry = ComponentRegistry()
        registry.register("up_component", lambda s, c: StageResult(artifacts=[_artifact("V1")]))
        registry.register("down_component", downstream)

        run_workflow(
            self._spec(),
            registry,
            state,
            policy=GatePolicy(stages={"down": StageSpec(id="down", max_attempts=3)}),
        )
        assert seen == ["V1", "V1"], (
            f"a retry with no instruction text must still replay its inputs, saw {seen}"
        )

    def test_correction_may_be_empty_on_a_real_retry(self, state: RunState) -> None:
        """Pins the precondition: the leakage rule retries with instructions,
        but a rule need not supply any, and GateDecision permits an empty list."""
        corrections: list[list[str] | None] = []

        def downstream(s: RunState, correction=None) -> StageResult:
            corrections.append(correction)
            signals = (
                QualitySignals(max_target_correlation=0.99)
                if len(corrections) == 1
                else QualitySignals()
            )
            return StageResult(signals=signals)

        registry = ComponentRegistry()
        registry.register("up_component", lambda s, c: StageResult(artifacts=[_artifact("V1")]))
        registry.register("down_component", downstream)
        run_workflow(
            self._spec(),
            registry,
            state,
            policy=GatePolicy(stages={"down": StageSpec(id="down", max_attempts=3)}),
        )
        assert len(corrections) == 2

    def test_retreat_to_a_different_stage_resolves_afresh(self, state: RunState) -> None:
        """A retreat is a deliberate re-derivation and should see current inputs."""
        from ads.orchestration import Edge, EdgeCondition, WorkflowSpec

        spec = WorkflowSpec(
            name="w",
            version="1",
            stages=(
                _stage_def("up", produces=(ArtifactType.VALIDATION_STRATEGY,)),
                _stage_def("down", consumes=(ArtifactType.VALIDATION_STRATEGY,)),
            ),
            edges=(
                Edge("up", "down", EdgeCondition.ON_PROCEED),
                Edge("down", "up", EdgeCondition.ON_RETRY),
            ),
            entry="up",
        )
        versions = iter(["V1", "V2", "V3", "V4"])
        seen: list[str | None] = []

        def upstream(s: RunState, correction=None) -> StageResult:
            return StageResult(artifacts=[_artifact(next(versions))])

        def downstream(s: RunState, correction=None) -> StageResult:
            bound = s.latest(ArtifactType.VALIDATION_STRATEGY, ValidationStrategy)
            seen.append(bound.rationale if bound else None)
            signals = (
                QualitySignals(max_target_correlation=0.99) if len(seen) == 1 else QualitySignals()
            )
            return StageResult(signals=signals)

        registry = ComponentRegistry()
        registry.register("up_component", upstream)
        registry.register("down_component", downstream)
        run_workflow(
            spec,
            registry,
            state,
            policy=GatePolicy(stages={"down": StageSpec(id="down", max_attempts=3)}),
        )
        assert seen == ["V1", "V2"], f"retreat must re-derive, saw {seen}"

    def test_human_retry_inherits_inputs(self, tmp_path: Path) -> None:
        """A person reviewed the rejected attempt and its inputs; keep them."""
        run_state = RunState(
            run_id="run1", store=ArtifactStore(tmp_path / "a"), profile=CHECKPOINTED
        )
        seen: list[str | None] = []

        def stage(s: RunState, correction=None) -> StageResult:
            bound = s.latest(ArtifactType.VALIDATION_STRATEGY, ValidationStrategy)
            seen.append(bound.rationale if bound else None)
            s.put(_artifact("V2"), stage_id="interloper")
            return StageResult()

        spec = linear_spec(
            "w",
            "1",
            [
                _stage_def("seed", produces=(ArtifactType.VALIDATION_STRATEGY,)),
                _stage_def("problem_discovery", consumes=(ArtifactType.VALIDATION_STRATEGY,)),
            ],
            retry_stages=["problem_discovery"],
        )
        registry = ComponentRegistry()
        registry.register("seed_component", lambda s, c: StageResult(artifacts=[_artifact("V1")]))
        registry.register("problem_discovery_component", stage)

        run_workflow(spec, registry, run_state, policy=GatePolicy.load())
        resume_workflow(
            spec,
            registry,
            run_state,
            decision="retry",
            instructions=["try the other target"],
            policy=GatePolicy.load(),
        )
        assert seen == ["V1", "V1"], f"human retry must replay its inputs, saw {seen}"
