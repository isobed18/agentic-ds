"""The external feature-engineering client, driven without a live service.

Every test here injects a transport. The suite must pass with no sidecar
running, and it must pass identically on a machine where one happens to be.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import numpy as np
import pandas as pd
import pytest

from ads.integrations.rlfe import (
    APPLICABLE,
    NOT_APPLICABLE,
    UNAVAILABLE,
    RlfeClient,
    apply_generated_features,
    build_enhanced_frame,
    resolve_base_url,
)


def _response(status: int, body: Any) -> httpx.Response:
    return httpx.Response(
        status,
        content=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
        request=httpx.Request("POST", "http://testserver/"),
    )


ANALYSIS_OK = {
    "dataset_id": "a" * 32,
    "filename": "dataset.csv",
    "applicability": {"applicable": True, "status": "SUPPORTED", "reasons": []},
    "leakage_review": {"required": False, "detected": []},
}

RESULT_OK = {
    "result": {
        "target_selected": "churned",
        "primary_metric": "f1",
        "primary_direction": "maximize",
        "baseline": {"score": 0.76},
        "optimized": {"score": 0.82},
        "score_improvement": 0.06,
        "selected_features": ["income", "debt"],
        "removed_features": ["noise"],
        "generated_features": [
            {
                "name": "divide__income__debt",
                "operation": "divide",
                "inputs": ["income", "debt"],
                "expression": "income / debt",
                "transformation_depth": 1,
            }
        ],
        "search": {"termination_reason": "budget_reached"},
    }
}


class _Recorder:
    """A scripted transport that remembers what it was asked."""

    def __init__(self, routes: dict[tuple[str, str], Any]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def __call__(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        path = url.split("8000", 1)[-1] if "8000" in url else url
        for (route_method, fragment), response in self.routes.items():
            if method == route_method and fragment in url:
                self.calls.append((method, path, kwargs))
                if isinstance(response, Exception):
                    raise response
                return response
        raise AssertionError(f"unscripted request: {method} {url}")

    def call_for(self, fragment: str) -> dict[str, Any]:
        for _, path, kwargs in self.calls:
            if fragment in path:
                return kwargs
        raise AssertionError(f"no call matching {fragment!r}; saw {[c[1] for c in self.calls]}")


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {"income": [10.0, 20.0, 30.0], "debt": [2.0, 4.0, 5.0], "churned": [0, 1, 0]}
    )


def _client(routes: dict[tuple[str, str], Any]) -> tuple[RlfeClient, _Recorder]:
    recorder = _Recorder(routes)
    client = RlfeClient(
        base_url="http://127.0.0.1:8000",
        transport=recorder,
        poll_interval_seconds=0.0,
        sleep=lambda _: None,
    )
    return client, recorder


def test_applicable_run_returns_the_recipe() -> None:
    client, _ = _client(
        {
            ("POST", "/analyze"): _response(200, ANALYSIS_OK),
            ("POST", "/optimize"): _response(202, {"job_id": "j1", "status": "queued"}),
            ("GET", "/jobs/"): _response(200, {"status": "completed"}),
            ("GET", "/results/"): _response(200, RESULT_OK),
        }
    )
    outcome = client.enhance(_frame(), target_column="churned")

    assert outcome.status == APPLICABLE
    assert outcome.selected_features == ("income", "debt")
    assert outcome.removed_features == ("noise",)
    assert outcome.score_improvement == pytest.approx(0.06)
    assert outcome.primary_metric == "f1"
    assert outcome.generated_features[0]["name"] == "divide__income__debt"
    assert outcome.sent_row_count == 3


def test_quick_is_always_requested() -> None:
    """A full search is minutes to tens of minutes; this runs inside a stage."""
    client, recorder = _client(
        {
            ("POST", "/analyze"): _response(200, ANALYSIS_OK),
            ("POST", "/optimize"): _response(202, {"job_id": "j1", "status": "queued"}),
            ("GET", "/jobs/"): _response(200, {"status": "completed"}),
            ("GET", "/results/"): _response(200, RESULT_OK),
        }
    )
    client.enhance(_frame(), target_column="churned")

    assert recorder.call_for("/optimize")["json"]["quick"] is True


def test_detected_leakage_is_confirmed_before_optimizing() -> None:
    """The 422 trap: optimization refuses to start until detections are confirmed.

    Delete the `leakage_review` branch in `_enhance` and the service rejects the
    optimize call for any dataset with a target companion -- which is a large
    share of real ones -- and every such run silently loses its enhanced model.
    """
    analysis = {
        **ANALYSIS_OK,
        "leakage_review": {
            "required": True,
            "detected": [{"column": "churn_date", "reason": "target companion"}],
        },
    }
    client, recorder = _client(
        {
            ("POST", "/analyze"): _response(200, analysis),
            ("POST", "/optimize"): _response(202, {"job_id": "j1", "status": "queued"}),
            ("GET", "/jobs/"): _response(200, {"status": "completed"}),
            ("GET", "/results/"): _response(200, RESULT_OK),
        }
    )
    outcome = client.enhance(_frame(), target_column="churned")

    review = recorder.call_for("/optimize")["json"]["leakage_review"]
    assert review == {"confirmed": True, "include_detected_columns": []}
    assert outcome.status == APPLICABLE


def test_no_leakage_review_is_sent_when_none_was_detected() -> None:
    """`extra="forbid"` is not the issue; sending a confirmation nobody asked for is."""
    client, recorder = _client(
        {
            ("POST", "/analyze"): _response(200, ANALYSIS_OK),
            ("POST", "/optimize"): _response(202, {"job_id": "j1", "status": "queued"}),
            ("GET", "/jobs/"): _response(200, {"status": "completed"}),
            ("GET", "/results/"): _response(200, RESULT_OK),
        }
    )
    client.enhance(_frame(), target_column="churned")

    assert "leakage_review" not in recorder.call_for("/optimize")["json"]


def test_unapplicable_analysis_carries_its_reason_codes() -> None:
    analysis = {
        **ANALYSIS_OK,
        "applicability": {
            "applicable": False,
            "status": "UNSUPPORTED",
            "reasons": ["INSUFFICIENT_ROWS", "TARGET_CONSTANT"],
        },
    }
    client, recorder = _client({("POST", "/analyze"): _response(200, analysis)})
    outcome = client.enhance(_frame(), target_column="churned")

    assert outcome.status == NOT_APPLICABLE
    assert outcome.reasons == ("INSUFFICIENT_ROWS", "TARGET_CONSTANT")
    # Nothing was optimized, so nothing was queued on the service.
    assert all("/optimize" not in path for _, path, _ in recorder.calls)


def test_optimize_422_is_not_applicable_with_reasons() -> None:
    body = {
        "detail": {
            "dataset_id": "a" * 32,
            "analysis": {
                "applicability": {
                    "applicable": False,
                    "status": "UNSUPPORTED",
                    "reasons": ["NO_ENHANCEABLE_TARGET"],
                }
            },
        }
    }
    client, _ = _client(
        {
            ("POST", "/analyze"): _response(200, ANALYSIS_OK),
            ("POST", "/optimize"): _response(422, body),
        }
    )
    outcome = client.enhance(_frame(), target_column="churned")

    assert outcome.status == NOT_APPLICABLE
    assert outcome.reasons == ("NO_ENHANCEABLE_TARGET",)


def test_connection_error_is_unavailable_not_an_exception() -> None:
    client, _ = _client({("POST", "/analyze"): httpx.ConnectError("refused")})
    outcome = client.enhance(_frame(), target_column="churned")

    assert outcome.status == UNAVAILABLE
    assert "ConnectError" in (outcome.detail or "")


def test_failed_job_is_unavailable() -> None:
    client, _ = _client(
        {
            ("POST", "/analyze"): _response(200, ANALYSIS_OK),
            ("POST", "/optimize"): _response(202, {"job_id": "j1", "status": "queued"}),
            ("GET", "/jobs/"): _response(200, {"status": "failed", "error": "boom"}),
        }
    )
    outcome = client.enhance(_frame(), target_column="churned")

    assert outcome.status == UNAVAILABLE
    assert "failed" in (outcome.detail or "")


def test_poll_budget_gives_up_and_cancels_the_job() -> None:
    """A job we stop waiting for must be cancelled, or the sidecar keeps burning CPU."""
    clock = iter([0.0, 0.0, 1000.0, 1000.0, 1000.0])
    recorder = _Recorder(
        {
            ("POST", "/analyze"): _response(200, ANALYSIS_OK),
            ("POST", "/optimize"): _response(202, {"job_id": "j1", "status": "queued"}),
            ("GET", "/jobs/"): _response(200, {"status": "running"}),
            ("DELETE", "/jobs/"): _response(202, {"status": "cancelling"}),
        }
    )
    client = RlfeClient(
        base_url="http://127.0.0.1:8000",
        transport=recorder,
        poll_budget_seconds=600.0,
        poll_interval_seconds=0.0,
        sleep=lambda _: None,
        monotonic=lambda: next(clock),
    )
    outcome = client.enhance(_frame(), target_column="churned")

    assert outcome.status == UNAVAILABLE
    assert "did not finish" in (outcome.detail or "")
    assert any(method == "DELETE" for method, _, _ in recorder.calls)


def test_base_url_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADS_RLFE_API_URL", "http://example.invalid:9000/")
    assert resolve_base_url() == "http://example.invalid:9000"
    assert resolve_base_url("http://explicit:1/") == "http://explicit:1"


# ------------------------------------------------------------ feature replay


def test_replay_matches_the_service_domain_masks() -> None:
    """log of a non-positive value, and division by ~zero, are missing, not errors."""
    frame = pd.DataFrame({"a": [1.0, 0.0, -4.0, 16.0], "b": [2.0, 0.0, 4.0, 0.0]})
    recipe = [
        {"name": "log__a", "operation": "log", "inputs": ["a"]},
        {"name": "sqrt__a", "operation": "sqrt", "inputs": ["a"]},
        {"name": "divide__a__b", "operation": "divide", "inputs": ["a", "b"]},
    ]
    result, skipped = apply_generated_features(frame, recipe)

    assert skipped == ()
    # log(1)=0 is kept; log(0) and log(-4) are out of domain and imputed.
    assert result["log__a"].iloc[0] == pytest.approx(0.0)
    assert np.isfinite(result["log__a"]).all()
    # sqrt(-4) is out of domain; sqrt(16)=4 survives.
    assert result["sqrt__a"].iloc[3] == pytest.approx(4.0)
    # b=0 twice, so those divisions are masked then imputed from the rest.
    assert result["divide__a__b"].iloc[0] == pytest.approx(0.5)
    assert np.isfinite(result["divide__a__b"]).all()


def test_imputation_uses_only_the_rows_it_is_given() -> None:
    """The holdout must not decide a value the model trains on.

    The service imputes from every row it holds. Doing the same here would let a
    holdout row set the median that fills a training row -- a quiet leak that no
    other check in the pipeline would catch. Drop `impute_from` and this test
    reports the whole-frame median instead.
    """
    # Row 2 divides by zero and is masked. The two candidate medians are chosen
    # to differ: training rows alone give 2.0, the whole frame gives 51.5.
    frame = pd.DataFrame(
        {"a": [1.0, 3.0, 5.0, 100.0, 100.0], "b": [1.0, 1.0, 0.0, 1.0, 1.0]}
    )
    recipe = [{"name": "divide__a__b", "operation": "divide", "inputs": ["a", "b"]}]
    train_only = frame.index[[0, 1, 2]]

    result, _ = apply_generated_features(frame, recipe, impute_from=train_only)

    assert result["divide__a__b"].iloc[2] == pytest.approx(2.0)

    # The same recipe with no split imputes from every row, which is what the
    # service does and what this must not do.
    whole, _ = apply_generated_features(frame, recipe)
    assert whole["divide__a__b"].iloc[2] == pytest.approx(51.5)


def test_unknown_operations_are_skipped_not_raised() -> None:
    frame = pd.DataFrame({"a": [1.0, 2.0]})
    recipe = [
        {"name": "cube__a", "operation": "cube", "inputs": ["a"]},
        {"name": "missing__z", "operation": "log", "inputs": ["z"]},
        {"name": "square__a", "operation": "square", "inputs": ["a"]},
    ]
    result, skipped = apply_generated_features(frame, recipe)

    assert set(skipped) == {"cube__a", "missing__z"}
    assert "square__a" in result.columns


def test_enhanced_frame_keeps_row_order_and_reduces_columns() -> None:
    """The run's SplitManifest addresses rows by position, so order is load-bearing."""
    frame = pd.DataFrame(
        {
            "income": [10.0, 20.0, 30.0],
            "debt": [2.0, 4.0, 5.0],
            "noise": [1, 2, 3],
            "churned": [0, 1, 0],
        }
    )
    enhanced, skipped = build_enhanced_frame(
        frame,
        target_column="churned",
        selected_features=["income", "debt"],
        generated_features=[
            {"name": "divide__income__debt", "operation": "divide", "inputs": ["income", "debt"]}
        ],
    )

    assert skipped == ()
    assert list(enhanced.columns) == ["income", "debt", "churned", "divide__income__debt"]
    assert enhanced.index.equals(frame.index)
    assert enhanced["income"].tolist() == [10.0, 20.0, 30.0]
    # The source frame is untouched.
    assert "divide__income__debt" not in frame.columns


def test_generated_feature_survives_its_source_being_dropped() -> None:
    """Transformations are applied before columns are reduced, as the service does."""
    frame = pd.DataFrame({"a": [1.0, 2.0], "b": [4.0, 8.0], "y": [0, 1]})
    enhanced, skipped = build_enhanced_frame(
        frame,
        target_column="y",
        selected_features=["a"],
        generated_features=[
            {"name": "divide__b__a", "operation": "divide", "inputs": ["b", "a"]}
        ],
    )

    assert skipped == ()
    assert "b" not in enhanced.columns
    assert enhanced["divide__b__a"].tolist() == [4.0, 4.0]
