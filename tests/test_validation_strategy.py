"""Validation strategy measurement and agent-veto tests.

These tests pin the safety boundary: the model may explain and parameterise a
split, but deterministic ABT measurements decide the minimum safe strategy.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd
from tests.conftest import TRANSACTIONS

from ads.agents.base import run_agent
from ads.agents.validation_strategy import (
    build_context,
    build_spec,
    missing_protections,
    validate_group_column_repeats,
    validate_holdout_cutoff,
    validate_strategy_not_weaker,
    validate_stratification_is_executable,
    validate_temporal_column_spans_periods,
)
from ads.contracts.problem import TaskType
from ads.contracts.validation import (
    IdentifierContainmentSignal,
    RepeatedEntitySignal,
    SplitStrategy,
    ValidationStrategy,
    ValidationStrategyProposal,
)
from ads.discovery.validation_signals import (
    ValidationSignals,
    can_stratify,
    detect_validation_signals,
    full_coverage_group_columns,
    recommend_strategy,
    validation_signals_digest,
)
from ads.intake.loaders import LoadedTable
from ads.intake.profiler import profile_table
from ads.llm.client import LLMResponse, ModelProfile


class FakeLLM:
    """Replay structured responses without Ollama, matching the agent tests."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = responses
        self.calls: list[dict[str, Any]] = []

    def generate_structured(
        self, *, system: str, prompt: str, json_schema: dict, profile: ModelProfile
    ) -> LLMResponse:
        self.calls.append({"system": system, "prompt": prompt, "schema": json_schema})
        item = self._responses[min(len(self.calls) - 1, len(self._responses) - 1)]
        text = item if isinstance(item, str) else json.dumps(item)
        parsed = None
        error = None
        try:
            candidate = json.loads(text)
            if isinstance(candidate, dict):
                parsed = candidate
            else:
                error = "not an object"
        except json.JSONDecodeError as exc:
            error = str(exc)
        return LLMResponse(
            text=text,
            model=profile.name,
            latency_s=0.0,
            parsed=parsed,
            parse_error=error,
        )


def _card(frame: pd.DataFrame):
    return profile_table(
        LoadedTable(name="abt", frame=frame, source_uri="mem", source_format="csv")
    )


def _transaction_proposal(**overrides: Any) -> ValidationStrategyProposal:
    payload = {
        "strategy": "grouped_temporal",
        "n_folds": 5,
        "test_size": 0.2,
        "group_column": "physician_id",
        "time_column": "txn_date",
        "holdout_cutoff": "2024-01-01",
        "rationale": (
            "Keep physicians isolated and simulate deployment by holding out "
            "their most recent transactions."
        ),
        "rationale_tr": (
            "Hekimleri ayrık tut ve en son işlemlerini dışarıda bırakarak "
            "dağıtımı simüle et."
        ),
    }
    payload.update(overrides)
    return ValidationStrategyProposal.model_validate(payload)


def _crossing_identifier_signals() -> ValidationSignals:
    """Two repeating domains for which neither group column contains the other."""
    return ValidationSignals(
        n_rows=10,
        n_usable_rows=10,
        n_folds=5,
        repeated_entity_keys=[
            RepeatedEntitySignal(
                column="high_repeat_id",
                n_entities=2,
                unique_rate=0.2,
                rows_per_entity=5.0,
                repeated_value_count=2,
                repeated_row_count=10,
                repeated_row_rate=1.0,
                containment=[
                    IdentifierContainmentSignal(
                        column="high_repeat_id",
                        repeated_value_count=2,
                        spanning_value_count=0,
                    ),
                    IdentifierContainmentSignal(
                        column="secondary_id",
                        repeated_value_count=3,
                        spanning_value_count=1,
                    ),
                ],
            ),
            RepeatedEntitySignal(
                column="secondary_id",
                n_entities=7,
                unique_rate=0.7,
                rows_per_entity=1.429,
                repeated_value_count=3,
                repeated_row_count=6,
                repeated_row_rate=0.6,
                containment=[
                    IdentifierContainmentSignal(
                        column="high_repeat_id",
                        repeated_value_count=2,
                        spanning_value_count=2,
                    ),
                    IdentifierContainmentSignal(
                        column="secondary_id",
                        repeated_value_count=3,
                        spanning_value_count=0,
                    ),
                ],
            ),
        ],
    )


class TestDeterministicSignals:
    def test_real_transactions_require_grouped_temporal(self, cards_by_name, frames) -> None:
        signals = detect_validation_signals(
            cards_by_name[TRANSACTIONS],
            frames[TRANSACTIONS],
            target_column="flagged",
            task_type=TaskType.BINARY_CLASSIFICATION,
        )

        entity = next(s for s in signals.repeated_entity_keys if s.column == "physician_id")
        assert entity.n_entities == 800
        assert entity.unique_rate == 0.053333
        assert entity.rows_per_entity == 18.75

        temporal = next(s for s in signals.temporal_spans if s.column == "txn_date")
        assert temporal.min_date == "2019-01-01 00:00:00"
        assert temporal.max_date == "2024-12-29 00:00:00"
        assert temporal.span_days == 2189
        assert signals.minority_class_count == 41
        assert signals.minority_class_rate == 0.002733

        # Both constraints coexist at transaction grain: temporal alone would still
        # place the same physician in train and holdout, so grouped_temporal wins.
        assert recommend_strategy(signals) is SplitStrategy.GROUPED_TEMPORAL

    def test_self_containment_is_an_explicit_zero_cell(self) -> None:
        frame = pd.DataFrame(
            {
                "physician_id": ["physician_0", "physician_0"]
                + [f"physician_{index}" for index in range(1, 99)],
                "label": [0, 1] * 50,
            }
        )

        signals = detect_validation_signals(
            _card(frame),
            frame,
            target_column="label",
            task_type=TaskType.BINARY_CLASSIFICATION,
        )

        entity = next(
            item for item in signals.repeated_entity_keys if item.column == "physician_id"
        )
        assert len(entity.containment) == 1
        self_cell = entity.containment[0]
        assert self_cell.column == "physician_id"
        assert self_cell.repeated_value_count == 1
        assert self_cell.spanning_value_count == 0
        assert self_cell.missing_group_value_count == 0
        assert full_coverage_group_columns(signals) == ["physician_id"]

    def test_sparse_long_tail_recurrence_is_not_hidden_by_unique_rate(self) -> None:
        repeated_values = [f"customer_{index}" for index in range(3_000)]
        frame = pd.DataFrame(
            {
                "customer_unique_id": [f"customer_{index}" for index in range(97_000)]
                + repeated_values,
                "order_id": [f"order_{index}" for index in range(100_000)],
                "customer_state": ["SP", "RJ"] * 50_000,
                "label": [0, 1] * 50_000,
            }
        )

        signals = detect_validation_signals(
            _card(frame),
            frame,
            target_column="label",
            task_type=TaskType.BINARY_CLASSIFICATION,
        )

        assert [item.column for item in signals.repeated_entity_keys] == ["customer_unique_id"]
        entity = signals.repeated_entity_keys[0]
        assert entity.unique_rate == 0.97
        assert entity.repeated_value_count == 3_000
        assert entity.repeated_row_count == 6_000
        assert entity.repeated_row_rate == 0.06
        assert full_coverage_group_columns(signals) == ["customer_unique_id"]
        assert "order_id" not in {item.column for item in signals.repeated_entity_keys}
        assert "customer_state" not in {item.column for item in signals.repeated_entity_keys}

    def test_precedence_without_entity_or_time(self) -> None:
        frame = pd.DataFrame(
            {
                "feature": np.arange(1000),
                "label": [1] * 50 + [0] * 950,
            }
        )
        signals = detect_validation_signals(
            _card(frame),
            frame,
            target_column="label",
            task_type=TaskType.BINARY_CLASSIFICATION,
        )
        assert signals.minority_class_count == 50
        assert signals.minority_class_rate == 0.05
        assert recommend_strategy(signals) is SplitStrategy.STRATIFIED

    def test_temporal_precedes_imbalance(self) -> None:
        frame = pd.DataFrame(
            {
                "row_id": np.arange(100),
                "event_date": pd.date_range("2022-01-01", periods=100, freq="D"),
                "label": [1] * 10 + [0] * 90,
            }
        )
        signals = detect_validation_signals(
            _card(frame),
            frame,
            target_column="label",
            task_type=TaskType.BINARY_CLASSIFICATION,
        )
        assert signals.repeated_entity_keys == []
        assert recommend_strategy(signals) is SplitStrategy.TEMPORAL

    def test_small_fold_support_is_warned(self) -> None:
        frame = pd.DataFrame(
            {
                "feature": np.arange(12),
                "label": [1, 1] + [0] * 10,
            }
        )
        signals = detect_validation_signals(
            _card(frame),
            frame,
            target_column="label",
            task_type=TaskType.BINARY_CLASSIFICATION,
            n_folds=5,
        )
        assert any("few_rows_per_fold" in item for item in signals.small_sample_warnings)
        assert any(
            "few_minority_examples_per_fold" in item for item in signals.small_sample_warnings
        )


class TestProposalArtifactBoundary:
    def test_agent_contract_has_only_judgment(self) -> None:
        fields = ValidationStrategyProposal.model_fields
        assert "detected_signals" not in fields
        assert "created_at" not in fields
        assert "detected_signals" in ValidationStrategy.model_fields

    def test_legacy_recurrence_does_not_default_unmeasured_containment_to_safe(
        self,
    ) -> None:
        legacy = RepeatedEntitySignal(
            column="customer_id",
            n_entities=80,
            unique_rate=0.8,
            rows_per_entity=1.25,
        )
        signals = ValidationSignals(
            n_rows=100,
            n_usable_rows=100,
            n_folds=5,
            repeated_entity_keys=[legacy],
        )

        assert legacy.repeated_row_rate is None
        assert legacy.containment == []
        assert full_coverage_group_columns(signals) == []

    def test_from_proposal_attaches_measured_signals(self) -> None:
        signals = ValidationSignals(n_rows=100, n_usable_rows=100, n_folds=5)
        strategy = ValidationStrategy.from_proposal(_transaction_proposal(), signals)
        assert strategy.detected_signals is signals
        assert strategy.strategy is SplitStrategy.GROUPED_TEMPORAL


class TestValidationVetoes:
    def test_unique_row_key_cannot_be_used_for_grouping(self, cards_by_name, frames) -> None:
        signals = detect_validation_signals(
            cards_by_name[TRANSACTIONS],
            frames[TRANSACTIONS],
            target_column="flagged",
            task_type=TaskType.BINARY_CLASSIFICATION,
        )
        context = build_context(cards_by_name[TRANSACTIONS], signals)
        proposal = _transaction_proposal(group_column="txn_id")
        failures = validate_group_column_repeats(proposal, context)
        assert failures[0].code == "group_column_does_not_repeat"
        assert failures[0].repair_suggestion == "physician_id"

    def test_highest_repeat_rate_is_rejected_when_it_does_not_contain_all(self) -> None:
        frame = pd.DataFrame(
            {
                "high_repeat_id": ["a"] * 5 + ["b"] * 5,
                "secondary_id": ["c", "c", "d", "d", "e", "e", "f", "g", "h", "i"],
            }
        )
        signals = _crossing_identifier_signals()
        context = build_context(_card(frame), signals)
        proposal = ValidationStrategyProposal(
            strategy=SplitStrategy.GROUPED,
            group_column="high_repeat_id",
            rationale="Use the identifier with the largest repeated-row share.",
            rationale_tr="En büyük yinelenen satır payına sahip tanımlayıcıyı kullan.",
        )

        assert signals.repeated_entity_keys[0].repeated_row_rate == 1.0
        assert full_coverage_group_columns(signals) == []
        failures = validate_group_column_repeats(proposal, context)

        assert failures[0].code == "no_full_coverage_group_column"
        assert failures[0].repair_suggestion is None
        assert (
            "grouping by high_repeat_id leaves 1 of 3 repeated values "
            "of secondary_id spanning groups"
        ) in failures[0].detail
        assert (
            "grouping by secondary_id leaves 2 of 2 repeated values "
            "of high_repeat_id spanning groups"
        ) in failures[0].detail

    def test_no_full_coverage_candidate_exhausts_agent_instead_of_picking_best(
        self,
    ) -> None:
        frame = pd.DataFrame(
            {
                "high_repeat_id": ["a"] * 5 + ["b"] * 5,
                "secondary_id": ["c", "c", "d", "d", "e", "e", "f", "g", "h", "i"],
            }
        )
        context = build_context(_card(frame), _crossing_identifier_signals())
        proposal = {
            "strategy": "grouped",
            "n_folds": 5,
            "test_size": 0.2,
            "group_column": "high_repeat_id",
            "time_column": None,
            "holdout_cutoff": None,
            "rationale": "Use the most frequently repeating identifier.",
            "rationale_tr": "En sık yinelenen tanımlayıcıyı kullan.",
        }

        result = run_agent(build_spec(), context, FakeLLM([proposal]))

        assert not result.succeeded
        assert result.n_attempts == 3
        assert all(
            any(failure.code == "no_full_coverage_group_column" for failure in attempt.failures)
            for attempt in result.attempts
        )

    def test_mutually_containing_aliases_are_both_safe_candidates(self) -> None:
        containment = [
            IdentifierContainmentSignal(
                column="person_id",
                repeated_value_count=2,
                spanning_value_count=0,
            ),
            IdentifierContainmentSignal(
                column="account_id",
                repeated_value_count=2,
                spanning_value_count=0,
            ),
        ]
        signals = ValidationSignals(
            n_rows=6,
            n_usable_rows=6,
            n_folds=3,
            repeated_entity_keys=[
                RepeatedEntitySignal(
                    column="person_id",
                    n_entities=2,
                    unique_rate=1 / 3,
                    rows_per_entity=3.0,
                    repeated_value_count=2,
                    repeated_row_count=6,
                    repeated_row_rate=1.0,
                    containment=containment,
                ),
                RepeatedEntitySignal(
                    column="account_id",
                    n_entities=2,
                    unique_rate=1 / 3,
                    rows_per_entity=3.0,
                    repeated_value_count=2,
                    repeated_row_count=6,
                    repeated_row_rate=1.0,
                    containment=containment,
                ),
            ],
        )
        frame = pd.DataFrame(
            {
                "person_id": ["p1"] * 3 + ["p2"] * 3,
                "account_id": ["a1"] * 3 + ["a2"] * 3,
            }
        )
        context = build_context(_card(frame), signals)

        assert full_coverage_group_columns(signals) == ["person_id", "account_id"]
        for column in ("person_id", "account_id"):
            proposal = ValidationStrategyProposal(
                strategy=SplitStrategy.GROUPED,
                n_folds=3,
                group_column=column,
                rationale="Either alias produces the same safe entity partition.",
                rationale_tr="Her iki takma ad da aynı güvenli varlık bölümünü üretir.",
            )
            assert validate_group_column_repeats(proposal, context) == []

    def test_non_datetime_cannot_drive_temporal_split(self, cards_by_name, frames) -> None:
        signals = detect_validation_signals(
            cards_by_name[TRANSACTIONS],
            frames[TRANSACTIONS],
            target_column="flagged",
            task_type=TaskType.BINARY_CLASSIFICATION,
        )
        context = build_context(cards_by_name[TRANSACTIONS], signals)
        proposal = _transaction_proposal(time_column="amount")
        failures = validate_temporal_column_spans_periods(proposal, context)
        assert failures[0].code == "time_column_not_multi_period"
        assert failures[0].repair_suggestion == "txn_date"

    def test_random_split_is_vetoed_on_repeated_temporal_data(self, cards_by_name, frames) -> None:
        signals = detect_validation_signals(
            cards_by_name[TRANSACTIONS],
            frames[TRANSACTIONS],
            target_column="flagged",
            task_type=TaskType.BINARY_CLASSIFICATION,
        )
        context = build_context(cards_by_name[TRANSACTIONS], signals)
        proposal = ValidationStrategyProposal(
            strategy=SplitStrategy.RANDOM,
            rationale="Use a conventional random holdout.",
            rationale_tr="Geleneksel rastgele bir test kümesi kullan.",
        )
        failures = validate_strategy_not_weaker(proposal, context)
        assert failures[0].code == "strategy_weaker_than_detected_signals"
        assert failures[0].repair_suggestion == "grouped_temporal"
        assert "physician_id" in failures[0].detail
        assert "txn_date" in failures[0].detail

    def test_cutoff_must_be_inside_observed_range(self, cards_by_name, frames) -> None:
        signals = detect_validation_signals(
            cards_by_name[TRANSACTIONS],
            frames[TRANSACTIONS],
            target_column="flagged",
            task_type=TaskType.BINARY_CLASSIFICATION,
        )
        context = build_context(cards_by_name[TRANSACTIONS], signals)
        proposal = _transaction_proposal(holdout_cutoff="2030-01-01")
        failures = validate_holdout_cutoff(proposal, context)
        assert failures[0].code == "holdout_cutoff_outside_observed_range"
        assert "2019-01-01..2024-12-29" in failures[0].detail


class TestAgentExecution:
    def test_unsafe_first_response_is_retried(self, cards_by_name, frames) -> None:
        signals = detect_validation_signals(
            cards_by_name[TRANSACTIONS],
            frames[TRANSACTIONS],
            target_column="flagged",
            task_type=TaskType.BINARY_CLASSIFICATION,
        )
        context = build_context(cards_by_name[TRANSACTIONS], signals)
        unsafe = {
            "strategy": "random",
            "n_folds": 5,
            "test_size": 0.2,
            "group_column": None,
            "time_column": None,
            "holdout_cutoff": None,
            "rationale": "Use the usual random holdout.",
            "rationale_tr": "Standart rastgele test kümesini kullan.",
        }
        safe = _transaction_proposal().model_dump(mode="json")
        llm = FakeLLM([unsafe, safe])

        result = run_agent(build_spec(), context, llm)

        assert result.succeeded
        assert result.n_attempts == 2
        assert result.require().strategy is SplitStrategy.GROUPED_TEMPORAL
        assert any(
            failure.code == "strategy_weaker_than_detected_signals"
            for failure in result.attempts[0].failures
        )
        assert "grouped_temporal" in llm.calls[1]["prompt"]

    def test_context_contains_profiles_not_raw_rows(self, cards_by_name, frames) -> None:
        signals = detect_validation_signals(
            cards_by_name[TRANSACTIONS],
            frames[TRANSACTIONS],
            target_column="flagged",
            task_type=TaskType.BINARY_CLASSIFICATION,
        )
        context = build_context(cards_by_name[TRANSACTIONS], signals)
        rendered = context.render()
        assert "REPEATING IDENTIFIER CANDIDATES" in rendered
        assert "Deterministic minimum strategy: grouped_temporal" in rendered
        assert context.estimated_tokens() < 3000


class TestProtectionDominance:
    """Safety must be a subset test over protections, not a single ranking.

    Regression guard: `grouped` and `temporal` defend against different leaks and
    are genuinely incomparable. A total order made `grouped` outrank `temporal`,
    which was safe only because `validate_group_column_repeats` happened to
    reject that case first. These tests pin the property directly so it survives
    either validator being edited independently.
    """

    def test_grouped_does_not_satisfy_a_temporal_requirement(self) -> None:
        missing = missing_protections(SplitStrategy.GROUPED, SplitStrategy.TEMPORAL)
        assert missing == frozenset({"temporal_ordering"})

    def test_temporal_does_not_satisfy_a_grouped_requirement(self) -> None:
        missing = missing_protections(SplitStrategy.TEMPORAL, SplitStrategy.GROUPED)
        assert missing == frozenset({"entity_isolation"})

    def test_grouped_temporal_satisfies_everything(self) -> None:
        for recommended in SplitStrategy:
            missing = missing_protections(SplitStrategy.GROUPED_TEMPORAL, recommended)
            assert missing <= frozenset({"class_balance"}), recommended

    def test_identical_strategy_is_always_sufficient(self) -> None:
        for strategy in SplitStrategy:
            assert missing_protections(strategy, strategy) == frozenset()

    def test_random_satisfies_nothing_but_random(self) -> None:
        assert missing_protections(SplitStrategy.RANDOM, SplitStrategy.RANDOM) == frozenset()
        for recommended in (
            SplitStrategy.GROUPED,
            SplitStrategy.TEMPORAL,
            SplitStrategy.GROUPED_TEMPORAL,
            SplitStrategy.STRATIFIED,
        ):
            assert missing_protections(SplitStrategy.RANDOM, recommended)


def _stratified_proposal(**overrides: object) -> ValidationStrategyProposal:
    payload: dict[str, object] = {
        "strategy": "stratified",
        "n_folds": 3,
        "test_size": 0.2,
        "rationale": "Preserve the measured class balance across train and holdout.",
        "rationale_tr": "Ölçülen sınıf dengesini eğitim ve tutulan küme arasında koru.",
    }
    payload.update(overrides)
    return ValidationStrategyProposal.model_validate(payload)


class TestStratificationMustBeExecutable:
    """#450 (split from #440): nothing checked that a strategy can run.

    The proposal was validated for column presence and for not being weaker
    than the measured signals, and never for whether the strategy it names can
    execute against the target it is paired with. `STRATIFIED` is the strategy
    that needs it and the one with no check, so on the MovieLens suite it was
    proposed, trialled, failed, escalated generically, approved by a person
    shown no reason, and crashed five stages later in `splitting_stage`.
    """

    @staticmethod
    def _context(frame: pd.DataFrame, target: str, task: TaskType):
        card = _card(frame)
        signals = detect_validation_signals(
            card, frame, target_column=target, task_type=task, n_folds=3
        )
        return build_context(card, signals), signals

    def test_a_continuous_target_cannot_be_stratified(self) -> None:
        # `ratings.csv target=rating`: a float over 0.5..5.0, which
        # `type_of_target` calls continuous and `StratifiedKFold` refuses --
        # "Supported target types are: ('binary', 'multiclass')".
        frame = pd.DataFrame(
            {
                "user_id": [1, 1, 2, 2, 3, 3, 4, 4],
                "rating": [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.5, 5.0],
            }
        )
        context, _ = self._context(frame, "rating", TaskType.REGRESSION)

        failures = validate_stratification_is_executable(_stratified_proposal(), context)

        assert [failure.code for failure in failures] == ["stratify_non_classification_target"]
        assert "rating" in failures[0].detail

    def test_a_singleton_class_cannot_be_stratified(self) -> None:
        # `movies.csv target=genres` (951 distinct) and `tags.csv target=tag`
        # (1,589): high-cardinality categoricals have singletons by nature, and
        # the outer holdout alone needs two rows of every class.
        frame = pd.DataFrame({"movie_id": range(6), "genres": ["a", "a", "b", "b", "c", "d"]})
        context, _ = self._context(frame, "genres", TaskType.MULTICLASS_CLASSIFICATION)

        failures = validate_stratification_is_executable(_stratified_proposal(), context)

        assert [failure.code for failure in failures] == ["stratify_singleton_class"]
        assert "smallest class has 1 row" in failures[0].detail

    def test_a_null_in_the_target_is_fatal_on_its_own(self) -> None:
        # `links.csv target=tmdb_id` is 0.1% null: "Input y contains NaN".
        frame = pd.DataFrame(
            {"movie_id": range(6), "tmdb_id": ["x", "x", "y", "y", "z", None]}
        )
        context, _ = self._context(frame, "tmdb_id", TaskType.MULTICLASS_CLASSIFICATION)

        failures = validate_stratification_is_executable(_stratified_proposal(), context)

        assert [failure.code for failure in failures] == ["stratify_target_has_nulls"]

    def test_a_stratifiable_target_passes(self) -> None:
        # The point is to reject what cannot run, not to reject stratification.
        frame = pd.DataFrame({"row": range(8), "label": ["a", "a", "a", "a", "b", "b", "b", "b"]})
        context, _ = self._context(frame, "label", TaskType.BINARY_CLASSIFICATION)

        assert validate_stratification_is_executable(_stratified_proposal(), context) == []

    def test_every_other_strategy_is_left_alone(self) -> None:
        # Only stratification reads the target's classes; a grouped or temporal
        # split does not care what shape it is.
        frame = pd.DataFrame(
            {
                "user_id": [1, 1, 2, 2, 3, 3, 4, 4],
                "rating": [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.5, 5.0],
            }
        )
        context, _ = self._context(frame, "rating", TaskType.REGRESSION)

        for strategy in ("random", "grouped"):
            proposal = _stratified_proposal(
                strategy=strategy,
                **({"group_column": "user_id"} if strategy == "grouped" else {}),
            )
            assert validate_stratification_is_executable(proposal, context) == []

    def test_the_rejection_names_a_strategy_that_can_run(self) -> None:
        # A description of the problem sends the agent back to guess. The issue
        # asks for a repair suggestion, and on this data `random` passes.
        frame = pd.DataFrame({"row": range(6), "rating": [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]})
        context, _ = self._context(frame, "rating", TaskType.REGRESSION)

        failures = validate_stratification_is_executable(_stratified_proposal(), context)

        assert failures[0].repair_suggestion == SplitStrategy.RANDOM.value

    def test_the_suggestion_is_never_weaker_than_the_measurements_require(self) -> None:
        # `validate_strategy_not_weaker` has veto power for a reason. Suggesting
        # `random` where entities repeat would swap one rejection for another,
        # and the second one is a leak rather than a crash. The suggestion is
        # the recommendation, and falls back to `random` only when the
        # recommendation is the stratification that cannot run.
        frame = pd.DataFrame({"row": range(6), "genres": ["a", "a", "b", "b", "c", "d"]})
        card = _card(frame)
        measured = detect_validation_signals(
            card,
            frame,
            target_column="genres",
            task_type=TaskType.MULTICLASS_CLASSIFICATION,
            n_folds=3,
        )
        signals = measured.model_copy(
            update={
                "repeated_entity_keys": _crossing_identifier_signals().repeated_entity_keys[:1]
            }
        )
        assert recommend_strategy(signals) is SplitStrategy.GROUPED
        context = build_context(card, signals)

        failures = validate_stratification_is_executable(_stratified_proposal(), context)

        assert failures[0].code == "stratify_singleton_class"
        assert failures[0].repair_suggestion == SplitStrategy.GROUPED.value

    def test_it_is_wired_into_the_agent(self) -> None:
        # A validator nothing runs is a comment.
        assert validate_stratification_is_executable in build_spec().validators


class TestTheRecommendationStaysSatisfiable:
    """#450: a measured imbalance could steer the agent to an impossible split.

    `validate_strategy_not_weaker` rejects any proposal that omits a protection
    the recommendation names, and it says so out loud -- "the measured class
    imbalance requires stratification", with `repair_suggestion="stratified"`.
    On a target with a singleton class that requirement cannot be met by any
    split: every weaker proposal is rejected for omitting class balance, and the
    stratified one cannot execute. The agent had no answer to give, and the run
    reached a generic retry-budget escalation carrying nothing about the target.
    """

    def test_a_singleton_class_is_not_a_reason_to_stratify(self) -> None:
        frame = pd.DataFrame({"row": range(6), "label": ["a", "a", "a", "a", "b", "c"]})
        card = _card(frame)
        signals = detect_validation_signals(
            card, frame, target_column="label", task_type=TaskType.MULTICLASS_CLASSIFICATION,
            n_folds=3,
        )

        # Measurably imbalanced -- and measurably unstratifiable.
        assert signals.minority_class_rate is not None
        assert signals.minority_class_rate < 0.20
        assert can_stratify(signals) is False
        assert recommend_strategy(signals) is SplitStrategy.RANDOM

    def test_the_digest_says_so_where_the_agent_reads_it(self) -> None:
        # A rejection is a last resort; the projection the agent actually reads
        # should have told it already.
        frame = pd.DataFrame({"row": range(6), "label": ["a", "a", "a", "a", "b", "c"]})
        card = _card(frame)
        signals = detect_validation_signals(
            card,
            frame,
            target_column="label",
            task_type=TaskType.MULTICLASS_CLASSIFICATION,
            n_folds=3,
        )

        digest = validation_signals_digest(signals)

        assert "Stratification: NOT AVAILABLE for this target" in digest
        assert "smallest class has 1 row(s)" in digest

    def test_an_imbalance_that_can_be_stratified_still_recommends_it(self) -> None:
        # The rule is unchanged where it was always satisfiable.
        frame = pd.DataFrame({"row": range(12), "label": ["a"] * 10 + ["b", "b"]})
        card = _card(frame)
        signals = detect_validation_signals(
            card, frame, target_column="label", task_type=TaskType.BINARY_CLASSIFICATION,
            n_folds=2,
        )

        assert can_stratify(signals) is True
        assert recommend_strategy(signals) is SplitStrategy.STRATIFIED

    def test_a_regression_target_was_never_a_reason_to_stratify(self) -> None:
        # `minority_class_count` is measured only for a classification task, so
        # a continuous target could not reach the imbalance branch. Pinned
        # because `can_stratify` now depends on that being true.
        frame = pd.DataFrame({"row": range(6), "amount": [1.5, 2.5, 3.5, 4.5, 5.5, 6.5]})
        card = _card(frame)
        signals = detect_validation_signals(
            card, frame, target_column="amount", task_type=TaskType.REGRESSION, n_folds=3
        )

        assert signals.minority_class_count is None
        assert can_stratify(signals) is False
        assert recommend_strategy(signals) is SplitStrategy.RANDOM
