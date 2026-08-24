"""JSON-safe constructor recipes for reproducible scikit-learn graphs."""

from __future__ import annotations

import importlib
import math
from collections.abc import Mapping
from enum import Enum
from typing import Any

import numpy as np
from sklearn.base import BaseEstimator

from ads.contracts.training import SklearnComponentRecipe

_TAG = "__ads_recipe_type__"


class RecipeError(ValueError):
    """Raised when a component cannot be represented as constructor parameters."""


def _symbol_path(value: Any) -> str:
    module = getattr(value, "__module__", None)
    qualname = getattr(value, "__qualname__", None)
    if not module or not qualname or "<locals>" in qualname:
        raise RecipeError(f"Cannot record non-importable value {value!r} in a model recipe.")
    return f"{module}:{qualname}"


def _resolve_symbol(path: str) -> Any:
    try:
        module_name, qualname = path.split(":", 1)
        value: Any = importlib.import_module(module_name)
        for part in qualname.split("."):
            value = getattr(value, part)
        return value
    except (AttributeError, ImportError, ValueError) as exc:
        raise RecipeError(f"Could not import recipe symbol {path!r}.") from exc


def _encode(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        return {_TAG: "float", "value": str(value)}
    if isinstance(value, np.generic):
        return _encode(value.item())
    if isinstance(value, BaseEstimator):
        return {_TAG: "component", "recipe": component_recipe(value).model_dump(mode="json")}
    if isinstance(value, tuple):
        return {_TAG: "tuple", "items": [_encode(item) for item in value]}
    if isinstance(value, list):
        return [_encode(item) for item in value]
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise RecipeError("Model recipe mappings must have string keys.")
        return {key: _encode(item) for key, item in value.items()}
    if isinstance(value, np.ndarray):
        return {
            _TAG: "ndarray",
            "dtype": str(value.dtype),
            "items": _encode(value.tolist()),
        }
    if isinstance(value, slice):
        return {
            _TAG: "slice",
            "start": _encode(value.start),
            "stop": _encode(value.stop),
            "step": _encode(value.step),
        }
    if isinstance(value, Enum):
        return {_TAG: "enum", "class_path": _symbol_path(type(value)), "value": value.value}
    if isinstance(value, type):
        return {_TAG: "type", "class_path": _symbol_path(value)}
    if callable(value):
        return {_TAG: "callable", "path": _symbol_path(value)}
    raise RecipeError(f"Unsupported constructor value {value!r} of type {type(value).__name__}.")


def _decode(value: Any) -> Any:
    if isinstance(value, list):
        return [_decode(item) for item in value]
    if not isinstance(value, dict):
        return value
    tag = value.get(_TAG)
    if tag is None:
        return {key: _decode(item) for key, item in value.items()}
    if tag == "component":
        return build_component(SklearnComponentRecipe.model_validate(value["recipe"]))
    if tag == "tuple":
        return tuple(_decode(item) for item in value["items"])
    if tag == "ndarray":
        return np.asarray(_decode(value["items"]), dtype=value["dtype"])
    if tag == "slice":
        return slice(_decode(value["start"]), _decode(value["stop"]), _decode(value["step"]))
    if tag == "enum":
        return _resolve_symbol(value["class_path"])(value["value"])
    if tag == "type":
        return _resolve_symbol(value["class_path"])
    if tag == "callable":
        return _resolve_symbol(value["path"])
    if tag == "float":
        return float(value["value"])
    raise RecipeError(f"Unknown model recipe value tag {tag!r}.")


def component_recipe(component: BaseEstimator) -> SklearnComponentRecipe:
    """Capture only the importable class and shallow constructor parameters."""
    if not isinstance(component, BaseEstimator):
        raise TypeError("component must be a scikit-learn BaseEstimator.")
    parameters = {name: _encode(value) for name, value in component.get_params(deep=False).items()}
    return SklearnComponentRecipe(
        class_path=_symbol_path(type(component)),
        parameters=parameters,
    )


def build_component(recipe: SklearnComponentRecipe) -> BaseEstimator:
    """Construct a fresh, unfitted component from a recorded recipe."""
    component_type = _resolve_symbol(recipe.class_path)
    try:
        component = component_type(
            **{name: _decode(value) for name, value in recipe.parameters.items()}
        )
    except TypeError as exc:
        raise RecipeError(f"Could not construct component {recipe.class_path!r}: {exc}") from exc
    if not isinstance(component, BaseEstimator):
        raise RecipeError(f"Recipe {recipe.class_path!r} did not construct a BaseEstimator.")
    return component


__all__ = ["RecipeError", "build_component", "component_recipe"]
