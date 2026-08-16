"""Fixed, deterministic scikit-learn candidate menus."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from sklearn.dummy import DummyClassifier, DummyRegressor
from sklearn.ensemble import (
    HistGradientBoostingClassifier,
    HistGradientBoostingRegressor,
    RandomForestClassifier,
    RandomForestRegressor,
)
from sklearn.linear_model import LogisticRegression, Ridge

from ads.contracts.problem import TaskType

RANDOM_SEED = 20260812


@dataclass(frozen=True, slots=True)
class CandidateSpec:
    """An immutable recipe for constructing one fresh candidate estimator."""

    id: str
    display_name: str
    estimator_factory: Callable[[], Any]
    hyperparameters: Mapping[str, bool | int | float | str | None]

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Candidate id must not be empty.")
        if not self.display_name.strip():
            raise ValueError("Candidate display_name must not be empty.")
        if not callable(self.estimator_factory):
            raise TypeError("estimator_factory must be a zero-argument callable.")
        object.__setattr__(self, "hyperparameters", MappingProxyType(dict(self.hyperparameters)))


def _candidate(
    candidate_id: str,
    display_name: str,
    estimator_type: type,
    **hyperparameters: bool | int | float | str | None,
) -> CandidateSpec:
    frozen_parameters = MappingProxyType(dict(hyperparameters))

    def factory() -> Any:
        return estimator_type(**frozen_parameters)

    return CandidateSpec(
        id=candidate_id,
        display_name=display_name,
        estimator_factory=factory,
        hyperparameters=frozen_parameters,
    )


def default_candidates(task_type: TaskType) -> list[CandidateSpec]:
    """Return the fixed sklearn-only menu for a supported supervised task."""
    task_type = TaskType(task_type)
    if task_type is TaskType.REGRESSION:
        return [
            _candidate("dummy_regressor", "Dummy Regressor", DummyRegressor, strategy="mean"),
            _candidate("ridge", "Ridge", Ridge, alpha=1.0),
            _candidate(
                "random_forest_regressor",
                "Random Forest Regressor",
                RandomForestRegressor,
                n_estimators=100,
                random_state=RANDOM_SEED,
                n_jobs=1,
            ),
            _candidate(
                "hist_gradient_boosting_regressor",
                "Histogram Gradient Boosting Regressor",
                HistGradientBoostingRegressor,
                random_state=RANDOM_SEED,
            ),
        ]
    if task_type in {
        TaskType.BINARY_CLASSIFICATION,
        TaskType.MULTICLASS_CLASSIFICATION,
    }:
        return [
            _candidate(
                "dummy_classifier",
                "Dummy Classifier",
                DummyClassifier,
                strategy="prior",
                random_state=RANDOM_SEED,
            ),
            _candidate(
                "logistic_regression",
                "Logistic Regression",
                LogisticRegression,
                max_iter=1000,
                random_state=RANDOM_SEED,
            ),
            _candidate(
                "random_forest_classifier",
                "Random Forest Classifier",
                RandomForestClassifier,
                n_estimators=100,
                random_state=RANDOM_SEED,
                n_jobs=1,
            ),
            _candidate(
                "hist_gradient_boosting_classifier",
                "Histogram Gradient Boosting Classifier",
                HistGradientBoostingClassifier,
                random_state=RANDOM_SEED,
            ),
        ]
    raise ValueError(f"No supervised candidate menu exists for task_type={task_type.value!r}.")


__all__ = ["CandidateSpec", "RANDOM_SEED", "default_candidates"]
