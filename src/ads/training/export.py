"""Export completed training reports as deterministic, LLM-free Python scripts."""

from __future__ import annotations

import json
from collections.abc import Sequence
from textwrap import dedent

from ads.contracts.datacard import DataCard
from ads.contracts.integration import IntegrationPlan
from ads.contracts.problem import TaskType
from ads.contracts.training import CandidateResult, TrainingReport
from ads.contracts.validation import ValidationStrategy


def _candidate_payload(result: CandidateResult) -> dict[str, object]:
    if result.estimator_recipe is None:
        raise ValueError(
            f"Candidate {result.candidate_id!r} has no estimator recipe and cannot be exported."
        )
    return {
        "candidate_id": result.candidate_id,
        "display_name": result.display_name,
        "hyperparameters": result.hyperparameters,
        "recipe": result.estimator_recipe.model_dump(mode="json"),
    }


def export_training_script(
    report: TrainingReport,
    strategy: ValidationStrategy,
    plan: IntegrationPlan,
    *,
    target_column: str,
    task_type: TaskType,
    source_cards: Sequence[DataCard] = (),
) -> str:
    """Render a standalone ``train.py`` driven only by recorded deterministic artifacts.

    Source paths default to matching DataCard ``source_uri`` values when supplied.
    Repeatable ``--table NAME=PATH`` arguments override those recorded locations, which
    keeps exports portable across machines. Multi-sheet workbooks are supported when
    their normalized table name matches ``NAME``.
    """
    task_type = TaskType(task_type)
    if task_type is not report.task_type:
        raise ValueError(
            f"task_type={task_type.value!r} does not match report task {report.task_type.value!r}."
        )
    if not target_column.strip():
        raise ValueError("target_column must not be empty.")
    if report.preprocessor_recipe is None:
        raise ValueError("TrainingReport has no preprocessor recipe and cannot be exported.")

    selected = [report.baseline]
    if report.winner_id != report.baseline.candidate_id:
        selected.append(report.winner)
    candidate_payload = [_candidate_payload(candidate) for candidate in selected]
    derived_tables = {aggregation.output_name for aggregation in plan.aggregations}
    source_tables = {plan.base_table}
    source_tables.update(aggregation.source_table for aggregation in plan.aggregations)
    for join in plan.joins:
        if join.left_table not in derived_tables:
            source_tables.add(join.left_table)
        if join.right_table not in derived_tables:
            source_tables.add(join.right_table)

    duplicate_card_names = {
        card.table_name
        for card in source_cards
        if sum(other.table_name == card.table_name for other in source_cards) > 1
    }
    if duplicate_card_names:
        raise ValueError(
            f"source_cards contains duplicate table names: {sorted(duplicate_card_names)}."
        )
    default_paths = {
        card.table_name: card.source_uri
        for card in source_cards
        if card.table_name in source_tables
    }

    plan_json = plan.model_dump_json()
    strategy_json = strategy.model_dump_json()
    preprocessor_json = json.dumps(
        report.preprocessor_recipe.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    candidates_json = json.dumps(
        candidate_payload,
        sort_keys=True,
        separators=(",", ":"),
    )
    tables_json = json.dumps(sorted(source_tables), separators=(",", ":"))
    default_paths_json = json.dumps(default_paths, sort_keys=True, separators=(",", ":"))

    return dedent(
        f'''\
        """Reproduce the selected model from deterministic ADS artifacts."""

        from __future__ import annotations

        import argparse
        import json

        from ads.contracts import IntegrationPlan, TaskType, ValidationStrategy
        from ads.contracts.training import SklearnComponentRecipe
        from ads.integration import build_sql, execute_plan
        from ads.intake import load_path
        from ads.training.candidates import CandidateSpec
        from ads.training.recipes import build_component
        from ads.training.runner import train_candidates


        PLAN = IntegrationPlan.model_validate_json({plan_json!r})
        STRATEGY = ValidationStrategy.model_validate_json({strategy_json!r})
        PREPROCESSOR_RECIPE = SklearnComponentRecipe.model_validate(
            json.loads({preprocessor_json!r})
        )
        CANDIDATE_DATA = json.loads({candidates_json!r})
        REQUIRED_TABLES = json.loads({tables_json!r})
        DEFAULT_TABLE_PATHS = json.loads({default_paths_json!r})
        TARGET_COLUMN = {target_column!r}
        TASK_TYPE = TaskType({task_type.value!r})


        def parse_table_paths(values: list[str]) -> dict[str, str]:
            paths: dict[str, str] = dict(DEFAULT_TABLE_PATHS)
            overridden: set[str] = set()
            for value in values:
                if "=" not in value:
                    raise ValueError(f"Expected --table NAME=PATH, got {{value!r}}.")
                name, path = value.split("=", 1)
                if not name or not path:
                    raise ValueError(f"Expected --table NAME=PATH, got {{value!r}}.")
                if name in overridden and paths[name] != path:
                    raise ValueError(f"Table {{name!r}} was mapped to multiple paths.")
                paths[name] = path
                overridden.add(name)
            missing = sorted(set(REQUIRED_TABLES) - paths.keys())
            if missing:
                raise ValueError(f"Missing --table mappings for {{missing}}.")
            return paths


        def load_source_tables(paths: dict[str, str]):
            frames = {{}}
            loaded_by_path = {{}}
            for name in REQUIRED_TABLES:
                if paths[name] not in loaded_by_path:
                    loaded_by_path[paths[name]] = load_path(paths[name])
                tables = loaded_by_path[paths[name]]
                matches = [table for table in tables if table.name == name]
                if len(matches) == 1:
                    frames[name] = matches[0].frame
                elif len(tables) == 1:
                    frames[name] = tables[0].frame
                else:
                    available = [table.name for table in tables]
                    raise ValueError(
                        f"Path {{paths[name]!r}} contains tables {{available}}, not {{name!r}}."
                    )
            return frames


        def make_candidate(data: dict[str, object]) -> CandidateSpec:
            recipe = SklearnComponentRecipe.model_validate(data["recipe"])
            return CandidateSpec(
                id=str(data["candidate_id"]),
                display_name=str(data["display_name"]),
                estimator_factory=lambda recipe=recipe: build_component(recipe),
                hyperparameters=dict(data["hyperparameters"]),
            )


        def main() -> None:
            parser = argparse.ArgumentParser(description=__doc__)
            parser.add_argument(
                "--table",
                action="append",
                default=[],
                metavar="NAME=PATH",
                help="Map a logical IntegrationPlan table to its source file.",
            )
            arguments = parser.parse_args()
            frames = load_source_tables(parse_table_paths(arguments.table))
            sql = build_sql(PLAN, {{name: set(frame.columns) for name, frame in frames.items()}})
            integration = execute_plan(PLAN, frames)
            if integration.sql != sql:
                raise RuntimeError("Integration SQL changed between build and execution.")

            candidates = [make_candidate(data) for data in CANDIDATE_DATA]
            reproduced = train_candidates(
                integration.frame,
                STRATEGY,
                lambda: build_component(PREPROCESSOR_RECIPE),
                candidates,
                target_column=TARGET_COLUMN,
                task_type=TASK_TYPE,
            )
            print("Integration SQL:")
            print(sql)
            print(f"winner={{reproduced.winner_id}}")
            for evaluation in reproduced.winner.metrics:
                print(
                    f"{{evaluation.metric.value}}: cv_mean={{evaluation.cv_mean:.12g}} "
                    f"cv_std={{evaluation.cv_std:.12g}} "
                    f"holdout={{evaluation.holdout_score:.12g}}"
                )


        if __name__ == "__main__":
            main()
        '''
    )


__all__ = ["export_training_script"]
