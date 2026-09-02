"""Client for the external RL feature-engineering service.

The service (`rl_feature_engineering_api`, a separate repository) searches for a
better feature set with a Masked DQN. It is a loopback sidecar: no auth, no
upload-size limit, no server-side timeout. Do not point ``ADS_RLFE_API_URL`` at a
non-local host without putting a limit in front of it.

Two things about this integration are load-bearing.

**It never raises for an expected failure.** The service is an enhancement, not
evidence: a run whose sidecar is down must still produce its ordinary model. Every
outcome -- including "unreachable" -- comes back as an :class:`RlfeOutcome` with a
status the UI renders, so the calling stage has no error path that can fail a run.

**The generated-feature replay here must match the service exactly.** The service
returns a recipe rather than a dataset, and we rebuild the columns ourselves so
they line up with our own row positions. The operation semantics below mirror
`rl_feature_engineering_api/features/operations.py` -- the domain masks, the
float widening, and the infinity-to-missing step are all copied deliberately.
The one place we differ is imputation, and that difference is the point: the
service takes the median over every row it was given, which would let the holdout
influence a training-time value. We impute from the outer-train rows only.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

import httpx
import numpy as np
import pandas as pd

DEFAULT_RLFE_BASE_URL = "http://127.0.0.1:8000"
#: Per-request read timeout. `/analyze` fits a baseline model before answering,
#: so it is not instant; `/optimize` returns 202 immediately.
DEFAULT_RLFE_TIMEOUT = 120.0
#: How long we wait for a job before giving up and reporting it unavailable.
#: `quick=true` caps the search at 25 evaluations, which is interactive, so a job
#: still running after this is not one worth blocking a pipeline on.
DEFAULT_POLL_BUDGET_SECONDS = 600.0
DEFAULT_POLL_INTERVAL_SECONDS = 1.0

#: The search found a usable feature set.
APPLICABLE = "applicable"
#: The data cannot support the search, and the service said so with reason codes.
NOT_APPLICABLE = "not_applicable"
#: The service could not be reached, failed, or outran the poll budget.
UNAVAILABLE = "unavailable"

#: Mirrors `features/operations.py:DIVISION_EPSILON`.
DIVISION_EPSILON = 1e-12

_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_UNARY_OPERATIONS = frozenset({"log", "log1p", "sqrt", "square"})
_BINARY_OPERATIONS = frozenset({"add", "subtract", "multiply", "divide"})
SUPPORTED_OPERATIONS = _UNARY_OPERATIONS | _BINARY_OPERATIONS


@dataclass(frozen=True)
class RlfeOutcome:
    """What the service concluded, in a shape the stage can persist verbatim."""

    status: str
    reasons: tuple[str, ...] = ()
    detail: str | None = None
    selected_features: tuple[str, ...] = ()
    removed_features: tuple[str, ...] = ()
    generated_features: tuple[dict[str, Any], ...] = ()
    baseline_score: float | None = None
    optimized_score: float | None = None
    score_improvement: float | None = None
    primary_metric: str | None = None
    primary_direction: str | None = None
    excluded_detected_columns: tuple[str, ...] = ()
    termination_reason: str | None = None
    sent_row_count: int = 0
    sent_column_count: int = 0


def resolve_base_url(explicit: str | None = None) -> str:
    """The service root, from an explicit value, the environment, or the default."""
    value = explicit or os.environ.get("ADS_RLFE_API_URL") or DEFAULT_RLFE_BASE_URL
    return value.strip().rstrip("/")


# --------------------------------------------------------------- feature replay


def _as_float(column: pd.Series) -> pd.Series:
    return column.astype("float64")


def _finite(result: pd.Series) -> pd.Series:
    """Infinities become missing, which imputation can handle. Estimators reject them."""
    return result.where(np.isfinite(result))


def _apply_operation(
    frame: pd.DataFrame, operation: str, inputs: Sequence[str]
) -> pd.Series | None:
    """One depth-one generated column, or None for an operation we do not know.

    Domain masks are the service's: log needs a positive operand, sqrt a
    non-negative one, and divide a divisor clear of zero. Operands are widened to
    float first so an integer column cannot overflow silently into a wrapped
    negative.
    """
    left = _as_float(frame[inputs[0]])
    if operation == "log":
        return _finite(np.log(left.where(left > 0)))
    if operation == "log1p":
        return _finite(np.log1p(left.where(left >= -1)))
    if operation == "sqrt":
        return _finite(np.sqrt(left.where(left >= 0)))
    if operation == "square":
        return _finite(left**2)
    if len(inputs) < 2:
        return None
    right = _as_float(frame[inputs[1]])
    if operation == "divide":
        return _finite(left / right.where((right.abs() > DIVISION_EPSILON) & np.isfinite(right)))
    if operation == "multiply":
        return _finite(left * right)
    if operation == "add":
        return _finite(left + right)
    if operation == "subtract":
        return _finite(left - right)
    return None


def apply_generated_features(
    frame: pd.DataFrame,
    generated_features: Iterable[dict[str, Any]],
    *,
    impute_from: pd.Index | None = None,
) -> tuple[pd.DataFrame, tuple[str, ...]]:
    """Replay the service's recipe onto ``frame``, returning a new frame.

    ``impute_from`` names the rows whose median fills a missing generated value.
    Pass the outer-train index: the service imputes from every row it holds, and
    doing the same here would let holdout values decide a number the model trains
    on. Leaving it None imputes from the whole frame and is only for callers that
    have no split yet.

    A feature we cannot rebuild -- an unknown operation, a missing input column,
    or one with no finite value to impute from -- is skipped and named in the
    returned tuple rather than raising. The service is allowed to grow operations
    without breaking every run here.
    """
    result = frame.copy()
    skipped: list[str] = []
    for item in generated_features:
        name = str(item.get("name") or "")
        operation = str(item.get("operation") or "")
        inputs = [str(value) for value in item.get("inputs") or ()]
        if not name or not inputs or name in frame.columns:
            skipped.append(name or operation)
            continue
        if any(column not in result.columns for column in inputs):
            skipped.append(name)
            continue
        try:
            values = _apply_operation(result, operation, inputs)
        except (TypeError, ValueError):
            # A categorical operand reaching a numeric operation. The service
            # only generates these over numeric groups, so this means the recipe
            # and our frame disagree about a column -- skip it, do not fail.
            values = None
        if values is None:
            skipped.append(name)
            continue
        reference = values if impute_from is None else values.reindex(impute_from)
        median = reference.median(skipna=True)
        if pd.isna(median):
            skipped.append(name)
            continue
        result[name] = values.fillna(median)
    return result, tuple(skipped)


def build_enhanced_frame(
    frame: pd.DataFrame,
    *,
    target_column: str,
    selected_features: Sequence[str],
    generated_features: Sequence[dict[str, Any]],
    impute_from: pd.Index | None = None,
) -> tuple[pd.DataFrame, tuple[str, ...]]:
    """The frame the enhanced model trains on: kept raw columns plus generated ones.

    Transformations are applied against the whole frame *before* columns are
    reduced, matching the service: a generated feature stays available even when
    one of its source columns was dropped from the final raw selection.

    Row order and index are preserved exactly, so the run's existing
    ``SplitManifest`` positions still address the same rows.
    """
    enhanced, skipped = apply_generated_features(
        frame, generated_features, impute_from=impute_from
    )
    kept = set(selected_features)
    raw_columns = [
        column for column in frame.columns if column == target_column or column in kept
    ]
    generated_names = [
        str(item["name"])
        for item in generated_features
        if str(item.get("name") or "") not in skipped
        and str(item.get("name") or "") in enhanced.columns
    ]
    return enhanced.loc[:, [*raw_columns, *generated_names]], skipped


# --------------------------------------------------------------------- client


class RlfeClient:
    """Walks the service's analyze -> optimize -> poll -> results job flow.

    ``transport`` is the test seam, called as ``transport(method, url, **kwargs)``
    and returning an :class:`httpx.Response`, so the whole flow can be exercised
    without a live sidecar. The suite must pass with no sidecar running.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout: float = DEFAULT_RLFE_TIMEOUT,
        poll_budget_seconds: float = DEFAULT_POLL_BUDGET_SECONDS,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        transport: Callable[..., httpx.Response] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.base_url = resolve_base_url(base_url)
        self.timeout = timeout
        self.poll_budget_seconds = poll_budget_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self._transport = transport
        self._sleep = sleep
        self._monotonic = monotonic

    # ------------------------------------------------------------- transport

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        url = f"{self.base_url}{path}"
        if self._transport is not None:
            return self._transport(method, url, timeout=self.timeout, **kwargs)
        return httpx.request(method, url, timeout=self.timeout, **kwargs)

    def is_available(self) -> bool:
        try:
            return self._request("GET", "/api/v1/health").status_code == 200
        except httpx.HTTPError:
            return False

    # ------------------------------------------------------------------ flow

    def enhance(self, frame: pd.DataFrame, *, target_column: str) -> RlfeOutcome:
        """Run the whole job flow. Returns an outcome; never raises for a service failure."""
        sent = _Sent(rows=len(frame), columns=len(frame.columns))
        try:
            return self._enhance(frame, target_column=target_column, sent=sent)
        except httpx.HTTPError as exc:
            return _unavailable(f"{type(exc).__name__}: {exc}", sent)
        except (ValueError, KeyError, TypeError) as exc:
            # A response we could not read the way the contract describes. That is
            # a version skew with the sidecar, not a statement about the data, so
            # it reads as unavailable rather than not-applicable.
            return _unavailable(f"unreadable response: {type(exc).__name__}: {exc}", sent)

    def _enhance(
        self, frame: pd.DataFrame, *, target_column: str, sent: _Sent
    ) -> RlfeOutcome:
        analysis = self._request(
            "POST",
            "/api/v1/analyze",
            files={"file": ("dataset.csv", _to_csv_bytes(frame), "text/csv")},
            data={"target": target_column},
        )
        if analysis.status_code != 200:
            return _unavailable(_detail_text(analysis), sent)
        body = analysis.json()

        applicability = body.get("applicability") or {}
        if not applicability.get("applicable"):
            return RlfeOutcome(
                status=NOT_APPLICABLE,
                reasons=_codes(applicability.get("reasons")),
                detail=str(applicability.get("status") or ""),
                sent_row_count=sent.rows,
                sent_column_count=sent.columns,
            )

        payload: dict[str, Any] = {
            "dataset_id": body["dataset_id"],
            "target": target_column,
            # Always quick: a full search is minutes to tens of minutes and this
            # runs inside an interactive pipeline stage.
            "quick": True,
        }
        # The 422 trap: when analysis detected target companions, optimization
        # refuses to start until they are confirmed. The default is to exclude
        # them, which is what we want -- our own leakage audit already ran -- so
        # confirm without restoring any.
        if (body.get("leakage_review") or {}).get("required"):
            payload["leakage_review"] = {"confirmed": True, "include_detected_columns": []}

        accepted = self._request("POST", "/api/v1/optimize", json=payload)
        if accepted.status_code == 422:
            return _not_applicable_from_422(accepted, sent)
        if accepted.status_code != 202:
            return _unavailable(_detail_text(accepted), sent)
        job_id = str(accepted.json()["job_id"])

        status = self._poll(job_id)
        if status != "completed":
            if status == "__budget__":
                self._cancel(job_id)
                return _unavailable(
                    f"job {job_id} did not finish within {self.poll_budget_seconds:.0f}s", sent
                )
            return _unavailable(f"job {job_id} reported {status}", sent)

        results = self._request("GET", f"/api/v1/results/{job_id}")
        if results.status_code != 200:
            return _unavailable(_detail_text(results), sent)
        return _outcome_from_result(results.json().get("result") or {}, sent)

    def _poll(self, job_id: str) -> str:
        """Block until the job reaches a terminal status, or `__budget__` on give-up."""
        deadline = self._monotonic() + self.poll_budget_seconds
        while True:
            response = self._request("GET", f"/api/v1/jobs/{job_id}")
            if response.status_code != 200:
                return f"HTTP {response.status_code}"
            status = str((response.json() or {}).get("status") or "")
            if status in _TERMINAL_STATUSES:
                return status
            if self._monotonic() >= deadline:
                return "__budget__"
            self._sleep(self.poll_interval_seconds)

    def _cancel(self, job_id: str) -> None:
        """Ask the sidecar to stop work we are no longer waiting for. Best effort."""
        try:
            self._request("DELETE", f"/api/v1/jobs/{job_id}")
        except httpx.HTTPError:
            pass


@dataclass(frozen=True)
class _Sent:
    rows: int
    columns: int


def _to_csv_bytes(frame: pd.DataFrame) -> bytes:
    # The service reads utf-8 and sniffs the delimiter from the first 8 KiB
    # against ",\t;|", so a comma-separated, quoted export is what it expects.
    return frame.to_csv(index=False).encode("utf-8")


def _codes(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if isinstance(item, str | int | float))


def _detail_text(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}: {response.text.strip()[:500]}"
    detail = body.get("detail") if isinstance(body, dict) else body
    return f"HTTP {response.status_code}: {str(detail)[:500]}"


def _unavailable(detail: str, sent: _Sent) -> RlfeOutcome:
    return RlfeOutcome(
        status=UNAVAILABLE,
        detail=detail,
        sent_row_count=sent.rows,
        sent_column_count=sent.columns,
    )


def _not_applicable_from_422(response: httpx.Response, sent: _Sent) -> RlfeOutcome:
    """Read the structured analysis the service attaches to an unsupported dataset."""
    try:
        detail = (response.json() or {}).get("detail")
    except ValueError:
        detail = None
    if isinstance(detail, dict):
        applicability = ((detail.get("analysis") or {}).get("applicability")) or {}
        reasons = _codes(applicability.get("reasons"))
        if reasons:
            return RlfeOutcome(
                status=NOT_APPLICABLE,
                reasons=reasons,
                detail=str(applicability.get("status") or ""),
                sent_row_count=sent.rows,
                sent_column_count=sent.columns,
            )
    return RlfeOutcome(
        status=NOT_APPLICABLE,
        detail=str(detail)[:500] if detail is not None else "",
        sent_row_count=sent.rows,
        sent_column_count=sent.columns,
    )


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _outcome_from_result(result: dict[str, Any], sent: _Sent) -> RlfeOutcome:
    generated = tuple(
        {
            "name": str(item.get("name") or ""),
            "operation": str(item.get("operation") or ""),
            "inputs": [str(value) for value in item.get("inputs") or ()],
            "expression": str(item.get("expression") or ""),
        }
        for item in result.get("generated_features") or ()
        if isinstance(item, dict)
    )
    baseline = result.get("baseline") or {}
    optimized = result.get("optimized") or {}
    leakage = result.get("leakage_review") or {}
    search = result.get("search") or {}
    return RlfeOutcome(
        status=APPLICABLE,
        selected_features=tuple(str(name) for name in result.get("selected_features") or ()),
        removed_features=tuple(str(name) for name in result.get("removed_features") or ()),
        generated_features=generated,
        baseline_score=_number(baseline.get("score")),
        optimized_score=_number(optimized.get("score")),
        score_improvement=_number(result.get("score_improvement")),
        primary_metric=str(result.get("primary_metric") or "") or None,
        primary_direction=str(result.get("primary_direction") or "") or None,
        excluded_detected_columns=tuple(
            str(name) for name in leakage.get("excluded_detected_columns") or ()
        ),
        termination_reason=str(search.get("termination_reason") or "") or None,
        sent_row_count=sent.rows,
        sent_column_count=sent.columns,
    )


__all__ = [
    "APPLICABLE",
    "DEFAULT_POLL_BUDGET_SECONDS",
    "DEFAULT_RLFE_BASE_URL",
    "DEFAULT_RLFE_TIMEOUT",
    "NOT_APPLICABLE",
    "SUPPORTED_OPERATIONS",
    "UNAVAILABLE",
    "RlfeClient",
    "RlfeOutcome",
    "apply_generated_features",
    "build_enhanced_frame",
    "resolve_base_url",
]
