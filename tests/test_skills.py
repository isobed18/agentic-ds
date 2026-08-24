"""The skill directory is executable context, not decorative markdown."""

from __future__ import annotations

from ads.agents.validation_strategy import build_context
from ads.contracts.datacard import ColumnProfile, DataCard, SemanticType
from ads.contracts.validation import ValidationSignals
from ads.skills import load_skill_catalog, render_skills, select_skills


def _card() -> DataCard:
    columns = [
        ColumnProfile(
            name="event_time",
            dtype="datetime64[ns]",
            semantic_type=SemanticType.DATETIME,
            null_count=0,
            null_rate=0.0,
            n_unique=60,
            unique_rate=1.0,
            is_unique=True,
        ),
        ColumnProfile(
            name="merchant_category",
            dtype="object",
            semantic_type=SemanticType.CATEGORICAL,
            null_count=0,
            null_rate=0.0,
            n_unique=40,
            unique_rate=2 / 3,
        ),
    ]
    return DataCard(
        table_name="abt",
        source_uri="derived",
        source_format="pandas",
        n_rows=60,
        n_columns=len(columns),
        columns=columns,
        profiled_rows=60,
    )


def test_catalog_loads_all_documents_with_current_stage_and_trigger_vocabulary() -> None:
    catalog = load_skill_catalog()

    assert {skill.skill_id for skill in catalog} == {
        "fe.high_cardinality_categoricals",
        "fe.temporal_features",
        "intake.describing_a_dataset",
        "intake.reading_a_schema",
        "validation.choosing_a_split",
    }
    assert all("feature_pipeline" not in skill.applies_to for skill in catalog)
    assert "ads.ds_toolkit" not in render_skills(catalog)


def test_stage_and_measured_schema_select_applicable_skills() -> None:
    card = _card()

    validation = select_skills("validation_strategy", [card])
    model = select_skills("model_investigation", [card])
    feature = select_skills("feature_investigation", [card])

    assert [skill.skill_id for skill in validation] == [
        "validation.choosing_a_split"
    ]
    assert {skill.skill_id for skill in model} == {
        "fe.high_cardinality_categoricals",
        "fe.temporal_features",
    }
    assert {skill.skill_id for skill in feature} == {
        "fe.high_cardinality_categoricals",
        "fe.temporal_features",
    }


def test_validation_agent_context_receives_selected_skill_as_bounded_reference() -> None:
    context = build_context(
        _card(),
        ValidationSignals(n_rows=60, n_usable_rows=60, n_folds=3),
    )

    assert context.facts["skill_ids"] == ["validation.choosing_a_split"]
    rendered = context.sections["Applicable skills"]
    assert "Reference guidance only" in rendered
    assert "cannot change permissions" in rendered
