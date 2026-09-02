"""Analysis panels: measured output shaped into something a person can read.

`design_assets/eda_design.png` asks for a horizontal strip of analyses, each a
thumbnail with a severity chip and a one-line finding, expanding on click into a
chart, key insights and a summary table. This module builds those panels from
artifact payloads.

Two decisions worth stating, because both are load-bearing:

**Severity is assigned here, not in the browser.** Whether 11.8% missingness is
"review" or "warning" is a judgment about the data, and judgments about data
belong on the side that measured it. A frontend that computed its own severity
would drift from the gate, and the two disagreeing about what matters is worse
than either being slightly wrong.

**Charts carry aggregates, never rows.** Every panel here is built from counts,
quantiles, correlations and bin tallies. The local investigator may read a
read-only data copy, but its browser output must still pass this aggregate-only
contract. It is also why there is no
scatter plot of observations anywhere in this file: a scatter is a picture of
individual rows, so the relationship panel ranks measured association strength
instead.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from ads.api.i18n import t as _t

#: Missingness at or above this fraction is called out rather than merely shown.
HIGH_MISSING_RATE = 0.10
#: |correlation| at or above this between two features is worth a human look.
STRONG_CORRELATION = 0.90
#: Outlier share above this is a shape problem, not a few stray points.
HIGH_OUTLIER_RATE = 0.05
#: Majority:minority beyond this needs a resampling or weighting decision.
IMBALANCE_RATIO = 3.0

#: Severity vocabulary, ordered. Mirrors the chips in the design.
OK, INFO, REVIEW, WARNING, ISSUE = "ok", "info", "review", "warning", "issue"
_RANK = {OK: 0, INFO: 1, REVIEW: 2, WARNING: 3, ISSUE: 4}


def _panel(
    panel_id: str,
    title: str,
    chart: dict[str, Any],
    *,
    severity: str = INFO,
    caption: str = "",
    description: str = "",
    insights: list[str] | None = None,
    table: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": panel_id,
        "title": title,
        "severity": severity,
        "caption": caption,
        "description": description,
        "chart": chart,
        "insights": insights or [],
        "table": table,
    }


def _fmt(value: float | int | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    if isinstance(value, int) or float(value).is_integer():
        return f"{int(value):,}"
    return f"{float(value):,.{digits}f}"


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


# --------------------------------------------------------------------- EDA


def eda_panels(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Build the EDA analysis strip from a measured EDAReport payload."""
    panels: list[dict[str, Any]] = []
    target = payload.get("target_distribution") or {}
    missingness = sorted(
        payload.get("missingness", []),
        key=lambda item: item.get("null_rate") or 0.0,
        reverse=True,
    )
    relationships = sorted(
        payload.get("target_relationships", []),
        key=lambda item: abs(item.get("pearson_correlation") or 0.0),
        reverse=True,
    )
    outliers = sorted(
        payload.get("outliers", []),
        key=lambda item: item.get("outlier_rate") or 0.0,
        reverse=True,
    )

    if target:
        panels.append(_target_panel(target))
    if missingness:
        panels.append(_missingness_panel(missingness))
    correlation = payload.get("correlation_matrix") or {}
    if correlation.get("columns"):
        panels.append(_correlation_panel(correlation))
    if outliers:
        panels.append(_outlier_panel(outliers))
    if relationships:
        panels.append(_relationship_panel(relationships, payload.get("target_column")))
    balance = payload.get("class_balance")
    if balance:
        panels.append(_balance_panel(balance))

    # Most severe first: the strip is read left to right, and the thing most
    # likely to invalidate the model should not be the one you have to scroll to.
    return sorted(panels, key=lambda item: -_RANK[item["severity"]])


def exploratory_panel(
    payload: dict[str, Any],
    interpretations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Render one validated agent-authored analysis through the existing card shape."""
    manifest = payload.get("manifest") or {}
    measured = manifest.get("chart") or {}
    kind = measured.get("kind")
    chart: dict[str, Any] = {
        "kind": kind,
        "x_label": measured.get("x_label"),
        "y_label": measured.get("y_label"),
    }
    if kind == "histogram":
        chart["bins"] = measured.get("bins", [])
    else:
        chart["series"] = measured.get("series", [])
        chart["signed"] = bool(measured.get("signed"))
        if measured.get("unit") in {"percent", "ratio"}:
            chart["unit"] = measured["unit"]
    table = manifest.get("table")
    return {
        "id": "exploratory_" + str(payload.get("code_hash", ""))[:16],
        "title": manifest.get("title") or _t("Exploratory analysis"),
        "severity": REVIEW,
        "caption": _t("Agent-authored · exploratory evidence"),
        "description": (
            _t(
                "Additional analysis written by the local agent and executed against a read-only "
                "data copy. It cannot affect gates."
            )
        ),
        "chart": chart,
        "insights": [
            _t(
                "This result is exploratory. Review its proposed interpretation and verification "
                "question before relying on it."
            )
        ],
        "table": table,
        "origin": "agent_authored",
        "proposed_interpretations": interpretations,
    }


def _target_panel(target: dict[str, Any]) -> dict[str, Any]:
    column = target.get("target_column") or "target"
    null_rate = target.get("null_rate") or 0.0
    values = target.get("values") or []
    histogram = target.get("histogram") or []
    numeric = target.get("numeric") or {}

    severity = WARNING if null_rate >= HIGH_MISSING_RATE else REVIEW
    insights = []
    if null_rate:
        missing_rows = target.get("total_count", 0) - target.get("non_null_count", 0)
        insights.append(
            _t(
                "{pct} of {column} is missing ({missing_rows:,} rows), so those rows cannot "
                "be trained on or scored against.",
                pct=_pct(null_rate),
                column=column,
                missing_rows=missing_rows,
            )
        )

    if values:
        total = sum(item["count"] for item in values) or 1
        top = values[0]
        insights.append(
            _t(
                "The most frequent class is {value} at {pct}.",
                value=repr(top["value"]),
                pct=_pct(top["count"] / total),
            )
        )
        return _panel(
            "target_distribution",
            _t("Target distribution"),
            {
                "kind": "bar",
                "x_label": column,
                "y_label": _t("Count"),
                "series": [
                    {"label": item["value"], "value": item["count"]} for item in values[:24]
                ],
            },
            severity=severity,
            caption=f"{_pct(top['count'] / total)} {top['value']}",
            description=_t(
                "How the target {column} is distributed across observed rows.",
                column=repr(column),
            ),
            insights=insights,
            table={
                "columns": [_t("Class"), _t("Count"), _t("Share")],
                "rows": [
                    [item["value"], f"{item['count']:,}", _pct(item["count"] / total)]
                    for item in values[:24]
                ]
                + [[_t("Total"), f"{total:,}", "100.0%"]],
            },
        )

    spread = numeric.get("std")
    mean = numeric.get("mean")
    if spread and mean:
        insights.append(
            _t(
                "Values range {min_val} to {max_val} with a standard deviation of {spread}.",
                min_val=_fmt(numeric.get("min")),
                max_val=_fmt(numeric.get("max")),
                spread=_fmt(spread),
            )
        )
    # Artifacts are immutable, so runs recorded before binning was added carry
    # quantiles and nothing else. A box drawn from min/p25/p50/p75/max shows
    # real measured spread instead of an empty frame apologising for itself.
    chart = (
        {"kind": "histogram", "x_label": column, "y_label": _t("Count"), "bins": histogram}
        if histogram
        else {
            "kind": "box",
            "series": [
                {
                    "label": column,
                    "p25": numeric.get("p25"),
                    "p50": numeric.get("p50"),
                    "p75": numeric.get("p75"),
                    "lower": numeric.get("min"),
                    "upper": numeric.get("max"),
                }
            ],
        }
        if numeric
        else {"kind": "empty"}
    )
    return _panel(
        "target_distribution",
        _t("Target distribution"),
        chart,
        severity=severity,
        caption=_t("median {val}", val=_fmt(numeric.get("p50"))),
        description=_t(
            "How the numeric target {column} is distributed across observed rows.",
            column=repr(column),
        ),
        insights=insights,
        table={
            "columns": [_t("Statistic"), _t("Value")],
            "rows": [
                [_t("Rows"), f"{target.get('total_count', 0):,}"],
                [_t("Observed"), f"{target.get('non_null_count', 0):,}"],
                [_t("Missing"), _pct(null_rate)],
                [_t("Minimum"), _fmt(numeric.get("min"))],
                [_t("25th percentile"), _fmt(numeric.get("p25"))],
                [_t("Median"), _fmt(numeric.get("p50"))],
                [_t("75th percentile"), _fmt(numeric.get("p75"))],
                [_t("Maximum"), _fmt(numeric.get("max"))],
                [_t("Mean"), _fmt(mean)],
                [_t("Std deviation"), _fmt(spread)],
            ],
        },
    )


def _missingness_panel(missingness: list[dict[str, Any]]) -> dict[str, Any]:
    high = [m for m in missingness if (m.get("null_rate") or 0.0) >= HIGH_MISSING_RATE]
    present = [m for m in missingness if (m.get("null_rate") or 0.0) > 0]
    severity = WARNING if high else (REVIEW if present else OK)
    insights = []
    if high:
        cols_str = ", ".join(f"{m['column']} ({_pct(m['null_rate'])})" for m in high[:6])
        insights.append(
            _t(
                "{count} column(s) are at or above {threshold} missing: {columns}.",
                count=len(high),
                threshold=_pct(HIGH_MISSING_RATE),
                columns=cols_str,
            )
        )
        insights.append(
            _t(
                "Decide per column whether to impute, drop the column, or drop the rows — "
                "each choice changes what the model can be used for."
            )
        )
    elif present:
        insights.append(_t("Missingness is present but every column is below the 10% threshold."))
    else:
        insights.append(_t("No missing values were measured in any column."))

    caption = (
        _t(
            "{count} column(s) over {threshold}",
            count=len(high),
            threshold=_pct(HIGH_MISSING_RATE),
        )
        if high
        else (_t("all columns under 10%") if present else _t("no missing values"))
    )

    return _panel(
        "missing_values",
        _t("Missing values"),
        {
            "kind": "hbar",
            "x_label": _t("Missing share"),
            "unit": "percent",
            "series": [
                {"label": m["column"], "value": m.get("null_rate") or 0.0} for m in missingness[:20]
            ],
        },
        severity=severity,
        caption=caption,
        description=_t("Share of rows with no value, per column."),
        insights=insights,
        table={
            "columns": [_t("Column"), _t("Missing rows"), _t("Missing share")],
            "rows": [
                [m["column"], f"{m.get('null_count', 0):,}", _pct(m.get("null_rate"))]
                for m in missingness[:24]
            ],
        },
    )


def _correlation_panel(matrix: dict[str, Any]) -> dict[str, Any]:
    columns: list[str] = matrix.get("columns", [])
    values: list[list[float | None]] = matrix.get("values", [])

    strong: list[tuple[str, str, float]] = []
    for i, row_name in enumerate(columns):
        for j in range(i + 1, len(columns)):
            value = values[i][j] if i < len(values) and j < len(values[i]) else None
            if value is not None and abs(value) >= STRONG_CORRELATION:
                strong.append((row_name, columns[j], value))
    strong.sort(key=lambda item: -abs(item[2]))

    insights = []
    if strong:
        insights.append(
            _t(
                "{n} feature pair(s) are correlated at or above {threshold}. Treat "
                "these as leakage candidates until the audit confirms otherwise, and "
                "expect unstable coefficients if both are kept."
            ).format(n=len(strong), threshold=f"{STRONG_CORRELATION:.2f}")
        )
    else:
        insights.append(_t("No feature pair reaches the 0.90 correlation threshold."))

    return _panel(
        "correlation_heatmap",
        _t("Correlation heatmap"),
        {
            "kind": "heatmap",
            "columns": columns[:24],
            "values": [row[:24] for row in values[:24]],
            "min": -1.0,
            "max": 1.0,
        },
        severity=WARNING if strong else INFO,
        caption=(
            _t("{n} strong pair(s)").format(n=len(strong))
            if strong
            else _t("no strong correlation")
        ),
        description=_t("Pearson correlation between every pair of numeric columns."),
        insights=insights,
        table={
            "columns": [_t("Column A"), _t("Column B"), _t("Correlation")],
            "rows": [[a, b, f"{v:+.3f}"] for a, b, v in strong[:20]],
        }
        if strong
        else None,
    )


def _outlier_panel(outliers: list[dict[str, Any]]) -> dict[str, Any]:
    flagged = [o for o in outliers if (o.get("outlier_rate") or 0.0) >= HIGH_OUTLIER_RATE]
    any_outliers = [o for o in outliers if (o.get("outlier_count") or 0) > 0]
    severity = ISSUE if flagged else (REVIEW if any_outliers else OK)

    insights = []
    if flagged:
        cols_str = ", ".join(f"{o['column']} ({_pct(o['outlier_rate'])})" for o in flagged[:6])
        insights.append(
            _t(
                "{count} column(s) have more than {threshold} of values outside "
                "the 1.5×IQR fences: {columns}.",
                count=len(flagged),
                threshold=_pct(HIGH_OUTLIER_RATE),
                columns=cols_str,
            )
        )
        insights.append(
            _t(
                "Far values are not automatically errors. Confirm whether they are real "
                "before clipping — removing genuine extremes biases the model toward the middle."
            )
        )
    elif any_outliers:
        insights.append(_t("Some values sit outside the fences, but no column exceeds 5%."))
    else:
        insights.append(_t("No values fall outside the 1.5×IQR fences."))

    boxes = [o for o in outliers[:12] if o.get("p25") is not None]
    chart = (
        {
            "kind": "box",
            "series": [
                {
                    "label": o["column"],
                    "p25": o.get("p25"),
                    "p50": o.get("p50"),
                    "p75": o.get("p75"),
                    "lower": o.get("lower_fence"),
                    "upper": o.get("upper_fence"),
                    "outlier_rate": o.get("outlier_rate") or 0.0,
                }
                for o in boxes
            ],
        }
        if boxes
        else {
            "kind": "hbar",
            "x_label": _t("Outlier share"),
            "unit": "percent",
            "series": [
                {"label": o["column"], "value": o.get("outlier_rate") or 0.0} for o in outliers[:20]
            ],
        }
    )
    caption = (
        _t(
            "{count} column(s) over {threshold}",
            count=len(flagged),
            threshold=_pct(HIGH_OUTLIER_RATE),
        )
        if flagged
        else _t("{n} column(s) with outliers", n=len(any_outliers))
    )
    return _panel(
        "outliers",
        _t("Outliers"),
        chart,
        severity=severity,
        caption=caption,
        description=_t(
            "Quartiles and 1.5×IQR fences per numeric column, with the share beyond them."
        ),
        insights=insights,
        table={
            "columns": [
                _t("Column"),
                _t("Lower fence"),
                _t("Median"),
                _t("Upper fence"),
                _t("Outliers"),
                _t("Share"),
            ],
            "rows": [
                [
                    o["column"],
                    _fmt(o.get("lower_fence")),
                    _fmt(o.get("p50")),
                    _fmt(o.get("upper_fence")),
                    f"{o.get('outlier_count', 0):,}",
                    _pct(o.get("outlier_rate")),
                ]
                for o in outliers[:20]
            ],
        },
    )


def _relationship_panel(relationships: list[dict[str, Any]], target: str | None) -> dict[str, Any]:
    strong = [
        r for r in relationships if abs(r.get("pearson_correlation") or 0.0) >= STRONG_CORRELATION
    ]
    insights = [
        _t(
            "Ranked by absolute Pearson correlation with the target. Adjusted mutual "
            "information is shown alongside because it also catches non-linear "
            "association that correlation misses entirely."
        ),
    ]
    if strong:
        target_name = target or _t("the target")
        insights.insert(
            0,
            _t(
                "{count} feature(s) correlate with {target} at or above {threshold:.2f}. "
                "A feature that predicts the target almost perfectly is usually leakage "
                "rather than a finding.",
                count=len(strong),
                target=target_name,
                threshold=STRONG_CORRELATION,
            ),
        )

    return _panel(
        "feature_relationships",
        _t("Feature relationships"),
        {
            "kind": "hbar",
            "x_label": _t("|correlation| with {target}", target=target or "target"),
            "unit": "ratio",
            "signed": True,
            "series": [
                {
                    "label": r["column"],
                    "value": r.get("pearson_correlation") or 0.0,
                    "secondary": r.get("adjusted_mutual_information"),
                }
                for r in relationships[:20]
            ],
        },
        severity=ISSUE if strong else REVIEW,
        caption=(
            _t("{n} near-perfect predictor(s)", n=len(strong))
            if strong
            else _t("{n} feature(s) measured", n=len(relationships))
        ),
        description=(
            _t(
                "Measured association between each feature and the target. These are aggregate "
                "statistics — no individual rows are plotted."
            )
        ),
        insights=insights,
        table={
            "columns": [_t("Feature"), "Pearson", _t("Adjusted MI")],
            "rows": [
                [
                    r["column"],
                    f"{r['pearson_correlation']:+.3f}"
                    if r.get("pearson_correlation") is not None
                    else "—",
                    f"{r.get('adjusted_mutual_information', 0.0):.3f}",
                ]
                for r in relationships[:24]
            ],
        },
    )


def _balance_panel(balance: dict[str, Any]) -> dict[str, Any]:
    ratio = balance.get("majority_to_minority_ratio") or 1.0
    severity = WARNING if ratio >= IMBALANCE_RATIO else INFO
    insights = [
        _t(
            "The majority class {majority} outnumbers {minority} by {ratio:.1f}:1.",
            majority=repr(balance.get("majority_class")),
            minority=repr(balance.get("minority_class")),
            ratio=ratio,
        ),
    ]
    if ratio >= IMBALANCE_RATIO:
        insights.append(
            _t(
                "At this ratio accuracy is misleading — a model predicting the majority "
                "class every time would score {rate}. Judge it on the minority class.",
                rate=_pct(balance.get("majority_rate")),
            )
        )

    classes = balance.get("classes", [])
    return _panel(
        "class_balance",
        _t("Class balance"),
        {
            "kind": "donut",
            "series": [{"label": c["value"], "value": c["count"]} for c in classes[:12]],
        },
        severity=severity,
        caption=_t("{r}:1 majority to minority", r=f"{ratio:.1f}"),
        description=_t("Share of each target class among observed rows."),
        insights=insights,
        table={
            "columns": [_t("Class"), _t("Count"), _t("Share")],
            "rows": [[c["value"], f"{c['count']:,}", _pct(c["rate"])] for c in classes[:12]],
        },
    )


# ------------------------------------------------------------------- intake


def source_panels(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One panel per profiled source table, comparable side by side."""
    panels: list[dict[str, Any]] = []
    for card in cards:
        payload = card.get("payload") or {}
        name = payload.get("table_name") or card.get("name") or "table"
        columns = payload.get("columns", [])
        keys = payload.get("candidate_primary_keys", [])

        all_notes = payload.get("quality_issues", []) or payload.get("issues", [])
        issues = [n for n in all_notes if n.get("severity") not in {"info", None}]
        notes = [n for n in all_notes if n.get("severity") in {"info", None}]
        sensitive = [
            column for column in columns if column.get("sensitivity") in {"pii", "sensitive"}
        ]
        missing = sorted(
            (
                {"label": column.get("name"), "value": column.get("null_rate") or 0.0}
                for column in columns
                if (column.get("null_rate") or 0.0) > 0
            ),
            key=lambda item: -item["value"],
        )

        worst = missing[0]["value"] if missing else 0.0
        severity = ISSUE if issues else WARNING if worst >= HIGH_MISSING_RATE or sensitive else INFO

        grain = (
            _t("one row per {k}", k=" + ".join(keys[0]))
            if keys and keys[0]
            else _t(
                "no column or combination was measured unique, so one row's identity is unclear"
            )
        )
        kinds = Counter(str(column.get("semantic_type", "unknown")) for column in columns)
        composition = ", ".join(
            f"{count} {_t(kind.replace('_', ' '))}" for kind, count in kinds.most_common()
        )
        description = _t(
            "{n_rows:,} rows, {grain}. Columns: {composition}.",
            n_rows=payload.get("n_rows", 0),
            grain=grain,
            composition=composition,
        )

        keys_str = "; ".join(" + ".join(k) for k in keys[:4])
        measured_grain_insight = (
            _t(
                "Measured grain: {grain}. {count} candidate key(s) in total: {keys}.",
                grain=grain,
                count=len(keys),
                keys=keys_str,
            )
            if len(keys) > 1
            else _t("Measured grain: {grain}.", grain=grain)
        )
        insights = [measured_grain_insight]
        if sensitive:
            sens_cols = ", ".join(c.get("name", "?") for c in sensitive[:6])
            insights.append(
                _t(
                    "{count} column(s) classified as sensitive: {columns}. "
                    "Values from these are never shown or sent to a model.",
                    count=len(sensitive),
                    columns=sens_cols,
                )
            )
        if issues:
            issues_details = "; ".join(
                str(n.get("detail") or _t(str(n.get("code", "?")).replace("_", " ")))[:120]
                for n in issues[:3]
            )
            insights.append(
                _t(
                    "{count} quality issue(s) recorded during load: {details}.",
                    count=len(issues),
                    details=issues_details,
                )
            )
        if notes:
            insights.append(
                _t(
                    "{count} informational note(s) from loading — renames, inferred "
                    "headers, detected delimiters. Recorded for provenance, not problems.",
                    count=len(notes),
                )
            )
        if missing:
            insights.append(
                _t(
                    "Highest missingness is {column} at {rate}.",
                    column=missing[0]["label"],
                    rate=_pct(worst),
                )
            )
        else:
            insights.append(_t("No missing values in any column."))

        caption = (
            _t(
                "{n_rows:,} rows · {n_cols} cols · {n_issues} issue(s)",
                n_rows=payload.get("n_rows", 0),
                n_cols=len(columns),
                n_issues=len(issues),
            )
            if issues
            else _t(
                "{n_rows:,} rows · {n_cols} cols",
                n_rows=payload.get("n_rows", 0),
                n_cols=len(columns),
            )
        )

        panels.append(
            _panel(
                f"source_{name}",
                name,
                {
                    "kind": "hbar",
                    "x_label": _t("Missing share"),
                    "unit": "percent",
                    "series": missing[:14],
                }
                if missing
                else {
                    "kind": "donut",
                    "series": [
                        {"label": _t(semantic.replace("_", " ")), "value": count}
                        for semantic, count in sorted(
                            Counter(
                                str(column.get("semantic_type", "unknown")) for column in columns
                            ).items(),
                            key=lambda item: -item[1],
                        )
                    ],
                }
                if columns
                else {"kind": "empty"},
                severity=severity,
                caption=caption,
                description=description,
                insights=insights,
                table={
                    "columns": [
                        _t("Column"),
                        _t("Type"),
                        _t("Sensitivity"),
                        _t("Missing"),
                        _t("Distinct"),
                    ],
                    "rows": [
                        [
                            column.get("name"),
                            _t(str(column.get("semantic_type", "—")).replace("_", " ")),
                            _t(str(column.get("sensitivity", "—"))),
                            _pct(column.get("null_rate")),
                            f"{column.get('n_unique', 0):,}",
                        ]
                        for column in columns[:40]
                    ],
                },
            )
        )
    return sorted(panels, key=lambda item: -_RANK[item["severity"]])


# ------------------------------------------------------- validation strategy


#: What each split strategy actually defends against. This is a partial order,
#: not a ranking: `grouped` and `temporal` are incomparable because they prevent
#: different leaks, and treating one as a stronger version of the other is the
#: mistake this table exists to make impossible to make silently.
_PROTECTIONS: dict[str, set[str]] = {
    "random": set(),
    "stratified": {"class_balance"},
    "temporal": {"temporal_ordering"},
    "grouped": {"entity_isolation"},
    "grouped_temporal": {"entity_isolation", "temporal_ordering"},
}


# A function, not a dict literal: a module-level `_t()` is evaluated once at
# import and would freeze every reader into whichever language happened to be
# active then. Labels are looked up per request.
def _protection_labels() -> dict[str, str]:
    return {
        "entity_isolation": _t("Entity isolation"),
        "temporal_ordering": _t("Temporal ordering"),
        "class_balance": _t("Class balance"),
    }


_PROTECTION_MEANING = {
    "entity_isolation": (
        _t(
            "The same entity cannot appear in both training and evaluation. Without it a model "
            "can memorise an individual and be scored on that same individual."
        )
    ),
    "temporal_ordering": (
        _t(
            "Training data never comes from later than evaluation data. Without it the model has "
            "seen the future and the score is unachievable in production."
        )
    ),
    "class_balance": (
        _t(
            "Each fold keeps the observed class proportions, so per-fold scores are comparable to "
            "each other."
        )
    ),
}


def validation_panels(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Show which leaks the chosen split prevents, and which the data demands.

    This is the one place the protection model is visible to a person. The agent
    reasons over it and a deterministic validator vetoes anything weaker than the
    data requires, but until now the reasoning was invisible: a reader saw the
    word "temporal" and had no way to know it provides no entity protection at
    all.
    """
    strategy = payload.get("strategy")
    if not strategy:
        return []
    signals = payload.get("detected_signals") or {}
    provided = _PROTECTIONS.get(strategy, set())

    # What the measurements say is actually needed.
    required: set[str] = set()
    if signals.get("repeated_entity_keys"):
        required.add("entity_isolation")
    if signals.get("temporal_spans"):
        required.add("temporal_ordering")
    if signals.get("minority_class_rate") is not None:
        required.add("class_balance")

    rows = []
    for key, label in _protection_labels().items():
        need, have = key in required, key in provided
        rows.append(
            {
                "label": label,
                # A protection that is needed and present is the only full bar;
                # present-but-unneeded is shown as partial so it does not read as
                # an achievement, and needed-but-absent as empty.
                "value": 1.0 if (need and have) else (0.5 if have else 0.0),
                "need": need,
                "have": have,
            }
        )

    gaps = [r for r in rows if r["need"] and not r["have"]]
    insights = []
    if gaps:
        gaps_str = ", ".join(_t(r["label"]).lower() for r in gaps)
        insights.append(
            _t(
                "{strategy} does not provide {gaps}, which the measured data requires. "
                "Scores from this split would be optimistic in a way no later stage can detect.",
                strategy=strategy,
                gaps=gaps_str,
            )
        )
    else:
        insights.append(
            _t(
                "{strategy} provides every protection the measured data requires.",
                strategy=strategy,
            )
        )
    for key in sorted(required):
        insights.append(f"{_protection_labels()[key]}: {_PROTECTION_MEANING[key]}")
    if not required:
        insights.append(
            _t(
                "No repeated entities and no multi-period time span were measured, so no "
                "structural protection is strictly required."
            )
        )

    spans = signals.get("temporal_spans") or []
    caption_protection = (
        _t("{count} required protection(s) missing", count=len(gaps))
        if gaps
        else _t("{strategy} covers what the data requires", strategy=strategy)
    )
    caption_setup = _t(
        "{folds} folds, {holdout} holdout",
        folds=payload.get("n_folds"),
        holdout=_pct(payload.get("test_size")),
    )
    insights_setup = [
        _t(
            "{usable:,} of {total:,} rows are usable; the rest have no target value "
            "and cannot be trained on or scored against.",
            usable=signals.get("n_usable_rows", 0),
            total=signals.get("n_rows", 0),
        )
    ]
    if spans:
        insights_setup.append(
            _t(
                "Time column {column} spans {start} to {end} ({days:,} days).",
                column=spans[0]["column"],
                start=spans[0]["min_date"][:10],
                end=spans[0]["max_date"][:10],
                days=spans[0]["span_days"],
            )
        )

    return [
        _panel(
            "split_protection",
            _t("Split protection"),
            {
                "kind": "hbar",
                "x_label": _t("Protection provided"),
                "unit": "ratio",
                "series": [{"label": r["label"], "value": r["value"]} for r in rows],
            },
            severity=ISSUE if gaps else INFO,
            caption=caption_protection,
            description=_t(
                "Strategies are not ranked. Each prevents a different leak, so the "
                "question is whether the chosen one covers what the data measured."
            ),
            insights=insights,
            table={
                "columns": [_t("Protection"), _t("Required by data"), _t("Provided by strategy")],
                "rows": [
                    [
                        r["label"],
                        _t("yes") if r["need"] else _t("no"),
                        _t("yes") if r["have"] else _t("no"),
                    ]
                    for r in rows
                ],
            },
        ),
        _panel(
            "split_setup",
            _t("Split setup"),
            {
                "kind": "bar",
                "y_label": _t("Rows"),
                "series": [
                    {
                        "label": _t("Training"),
                        "value": round(
                            (signals.get("n_usable_rows") or 0)
                            * (1 - (payload.get("test_size") or 0.2))
                        ),
                    },
                    {
                        "label": _t("Holdout"),
                        "value": round(
                            (signals.get("n_usable_rows") or 0) * (payload.get("test_size") or 0.2)
                        ),
                    },
                ],
            },
            severity=(
                WARNING
                if (signals.get("n_usable_rows") or 0) < (signals.get("n_rows") or 0)
                else INFO
            ),
            caption=caption_setup,
            description=_t("How the rows are divided, and how many survive the target filter."),
            insights=insights_setup,
            table={
                "columns": [_t("Setting"), _t("Value")],
                "rows": [
                    [_t("Strategy"), str(strategy)],
                    [_t("Folds"), str(payload.get("n_folds"))],
                    [_t("Holdout fraction"), _pct(payload.get("test_size"))],
                    [_t("Time column"), str(payload.get("time_column") or "—")],
                    [_t("Group column"), str(payload.get("group_column") or "—")],
                    [_t("Holdout cutoff"), str(payload.get("holdout_cutoff") or "—")],
                    [_t("Usable rows"), f"{signals.get('n_usable_rows', 0):,}"],
                ],
            },
        ),
    ]


# ---------------------------------------------------------------- training


def training_panels(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Candidate comparison and the diagnostics that say whether to trust it."""
    results = payload.get("results", [])
    if not results:
        return []
    metric = payload.get("primary_metric", "score")
    winner_id = payload.get("winner_id")
    lower_is_better = metric in {"rmse", "mae", "mape"}

    def primary(candidate: dict[str, Any]) -> dict[str, Any]:
        for item in candidate.get("metrics", []):
            if item.get("metric") == metric:
                return item
        return candidate.get("metrics", [{}])[0]

    winner = next((r for r in results if r.get("candidate_id") == winner_id), results[0])
    baseline = next((r for r in results if r.get("is_baseline")), None)
    panels = [_candidate_panel(results, primary, metric, winner_id, lower_is_better)]

    folds = primary(winner).get("fold_scores") or []
    if len(folds) >= 2:
        panels.append(_fold_panel(winner, primary(winner), folds, metric))
    if baseline is not None and baseline is not winner:
        panels.append(_baseline_panel(winner, baseline, primary, metric, lower_is_better))
    return sorted(panels, key=lambda item: -_RANK[item["severity"]])


def model_experiment_panel(
    payload: dict[str, Any],
    interpretations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Render a host-scored authored experiment inside the training stage."""
    metric = str(payload.get("metric") or "score")
    score = payload.get("score")
    baseline = payload.get("baseline_score")
    manifest = payload.get("manifest") or {}
    return _panel(
        "model_experiment",
        str(manifest.get("title") or _t("Agent-authored model experiment")),
        {
            "kind": "hbar",
            "x_label": _t("Development {metric}", metric=metric),
            "series": [
                {"label": _t("Authored experiment"), "value": score or 0.0},
                {"label": _t("Naive baseline"), "value": baseline or 0.0},
            ],
        },
        severity=REVIEW,
        caption=_t("Agent-authored · host-scored · exploratory"),
        description=(
            _t(
                "Code ran against copied development data. The host scored its predictions "
                "against labels withheld from the execution environment."
            )
        ),
        insights=[
            _t(
                "Measured {metric} on {count} inner-development rows; "
                "the final holdout was not used.",
                metric=metric,
                count=_fmt(payload.get("evaluation_row_count")),
            ),
            _t("This experiment cannot replace the deterministic winner or affect a gate."),
        ],
        table={
            "columns": [_t("Result"), metric, _t("Evidence")],
            "rows": [
                [_t("Authored experiment"), _fmt(score), _t("Host measured")],
                [_t("Naive baseline"), _fmt(baseline), _t("Host measured")],
                [
                    _t("Model family"),
                    str(manifest.get("model_family") or "—"),
                    _t("Agent declared"),
                ],
            ],
        },
    ) | {
        "origin": "agent_authored",
        "evidence_class": "exploratory",
        "proposed_interpretations": interpretations,
    }


def _candidate_panel(results, primary, metric, winner_id, lower_is_better):
    rows = []
    for candidate in results:
        stats = primary(candidate)
        rows.append(
            {
                "label": candidate.get("display_name", candidate.get("candidate_id", "?")),
                "value": stats.get("holdout_score"),
                "cv_mean": stats.get("cv_mean"),
                "cv_std": stats.get("cv_std"),
                "selected": candidate.get("candidate_id") == winner_id,
                "baseline": bool(candidate.get("is_baseline")),
            }
        )
    ordered = sorted(
        rows,
        key=lambda r: r["value"] if r["value"] is not None else 0,
        reverse=not lower_is_better,
    )
    winner_row = next((r for r in rows if r["selected"]), rows[0])
    direction = _t("Lower is better") if lower_is_better else _t("Higher is better")
    return _panel(
        "candidate_comparison",
        _t("Candidate comparison"),
        {
            "kind": "hbar",
            "x_label": _t("Holdout {metric}", metric=metric),
            "unit": "ratio",
            "series": [{"label": r["label"], "value": r["value"] or 0.0} for r in ordered],
        },
        severity=INFO,
        caption=_t("{label} selected", label=winner_row["label"]),
        description=_t(
            "Every candidate's holdout {metric}. {direction}.",
            metric=metric,
            direction=direction,
        ),
        insights=[
            _t(
                "{label} was selected on holdout {metric} {score}.",
                label=winner_row["label"],
                metric=metric,
                score=_fmt(winner_row["value"]),
            ),
            _t(
                "Holdout is scored once, after selection, so it is not the number the "
                "candidates were chosen by — the cross-validation mean is."
            ),
        ],
        table={
            "columns": [
                _t("Model"),
                _t("CV mean ({metric})", metric=metric),
                _t("CV spread"),
                _t("Holdout ({metric})", metric=metric),
                _t("Role"),
            ],
            "rows": [
                [
                    r["label"],
                    _fmt(r["cv_mean"]),
                    _fmt(r["cv_std"]),
                    _fmt(r["value"]),
                    _t("selected")
                    if r["selected"]
                    else (_t("baseline") if r["baseline"] else _t("candidate")),
                ]
                for r in ordered
            ],
        },
    )


def _fold_panel(winner, stats, folds, metric):
    spread = stats.get("cv_std") or 0.0
    mean = stats.get("cv_mean") or 0.0
    cov = abs(spread / mean) if mean else 0.0
    severity = WARNING if cov >= 0.25 else INFO
    return _panel(
        "cv_stability",
        _t("Cross-validation stability"),
        {
            "kind": "bar",
            "x_label": _t("Fold"),
            "y_label": metric,
            "series": [
                {"label": _t("Fold {n}", n=i + 1), "value": score} for i, score in enumerate(folds)
            ],
        },
        severity=severity,
        caption=_t("spread {cov} of mean", cov=_pct(cov)),
        description=_t(
            "{name} scored on each validation fold. Consistency across folds is what makes "
            "a single holdout number believable.",
            name=winner.get("display_name"),
        ),
        insights=[
            _t(
                "Fold scores range {min_val} to {max_val} around a mean of {mean_val}.",
                min_val=_fmt(min(folds)),
                max_val=_fmt(max(folds)),
                mean_val=_fmt(mean),
            ),
            (
                _t(
                    "That spread is over a quarter of the mean, so the reported score is "
                    "unstable and the difference between candidates may be noise."
                )
                if severity == WARNING
                else _t(
                    "The spread is small relative to the mean, so the score is stable "
                    "across resampling."
                )
            ),
        ],
        table={
            "columns": [_t("Fold"), metric],
            "rows": [[_t("Fold {n}", n=i + 1), _fmt(s)] for i, s in enumerate(folds)]
            + [[_t("Mean"), _fmt(mean)], [_t("Std deviation"), _fmt(spread)]],
        },
    )


def _baseline_panel(winner, baseline, primary, metric, lower_is_better):
    w, b = primary(winner), primary(baseline)
    w_score, b_score = w.get("holdout_score"), b.get("holdout_score")
    beat = (
        w_score is not None
        and b_score is not None
        and (w_score < b_score if lower_is_better else w_score > b_score)
    )
    return _panel(
        "baseline_comparison",
        _t("Lift over baseline"),
        {
            "kind": "bar",
            "y_label": metric,
            "series": [
                {"label": baseline.get("display_name", _t("Baseline")), "value": b_score or 0.0},
                {"label": winner.get("display_name", _t("Selected")), "value": w_score or 0.0},
            ],
        },
        severity=INFO if beat else ISSUE,
        caption=(_t("beats baseline") if beat else _t("does not beat baseline")),
        description=_t(
            "The selected model against a naive baseline on the same holdout. A model "
            "that cannot beat the baseline has learned nothing worth deploying."
        ),
        insights=[
            _t(
                "Selected {winner_score} versus baseline {baseline_score} on holdout {metric}.",
                winner_score=_fmt(w_score),
                baseline_score=_fmt(b_score),
                metric=metric,
            )
            if beat
            else _t(
                "The selected model scored {winner_score} against a baseline of "
                "{baseline_score}. It has not demonstrated value over guessing.",
                winner_score=_fmt(w_score),
                baseline_score=_fmt(b_score),
            )
        ],
        table={
            "columns": [
                _t("Model"),
                _t("CV mean ({metric})", metric=metric),
                _t("Holdout ({metric})", metric=metric),
            ],
            "rows": [
                [baseline.get("display_name"), _fmt(b.get("cv_mean")), _fmt(b_score)],
                [winner.get("display_name"), _fmt(w.get("cv_mean")), _fmt(w_score)],
            ],
        },
    )


# ------------------------------------------------- rl feature engineering


def rl_feature_panels(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The external feature search: what it changed, and by how much.

    The scores here are the *service's* own cross-validated numbers, measured on
    the training rows we sent it. They are not the holdout numbers the run
    reports for its models, and the copy says so -- presenting the two as
    interchangeable would overstate what the search demonstrated.
    """
    status = str(payload.get("status") or "")
    if status == "unavailable":
        return [
            _panel(
                "rl_feature_engineering",
                _t("Feature engineering skipped"),
                {"kind": "empty"},
                severity=WARNING,
                caption=_t("service unreachable"),
                description=_t(
                    "The feature engineering service could not be reached, so no enhanced "
                    "model was produced. The rest of the run is unaffected."
                ),
                insights=[_t("No data left this machine.")],
            )
        ]
    if status != "applicable":
        reasons = [str(code) for code in payload.get("reasons") or []]
        return [
            _panel(
                "rl_feature_engineering",
                _t("Feature engineering not applicable"),
                {"kind": "empty"},
                severity=REVIEW,
                caption=_t("no enhanced model"),
                description=_t(
                    "This dataset cannot support the external feature search, so only the "
                    "ordinary model was trained."
                ),
                insights=[_rl_reason_text(code) for code in reasons] or [_t("No reason given.")],
            )
        ]

    metric = str(payload.get("primary_metric") or "")
    baseline = payload.get("api_baseline_score")
    optimized = payload.get("api_optimized_score")
    improvement = payload.get("api_score_improvement")
    generated = payload.get("generated_features") or []
    removed = [str(name) for name in payload.get("removed_features") or []]
    improved = isinstance(improvement, int | float) and improvement > 0

    panels = [
        _panel(
            "rl_feature_engineering",
            _t("Feature search result"),
            {
                "kind": "bar",
                "y_label": metric,
                "series": [
                    {"label": _t("Original features"), "value": baseline or 0.0},
                    {"label": _t("Engineered features"), "value": optimized or 0.0},
                ],
            },
            severity=INFO if improved else REVIEW,
            caption=(_t("improved") if improved else _t("no improvement")),
            description=_t(
                "Measured by the feature engineering service on the training rows only, so "
                "the holdout could not influence which features it chose. Compare models on "
                "the holdout score, not on these numbers."
            ),
            insights=[
                _t(
                    "{metric} moved from {baseline} to {optimized}, a change of {delta}.",
                    metric=metric,
                    baseline=_fmt(baseline),
                    optimized=_fmt(optimized),
                    delta=_fmt(improvement),
                ),
                _t(
                    "{added} feature(s) added, {removed} removed.",
                    added=len(generated),
                    removed=len(removed),
                ),
            ],
        )
    ]
    if generated or removed:
        rows = [
            [_t("Added"), str(item.get("name") or ""), str(item.get("expression") or "")]
            for item in generated
        ]
        rows += [[_t("Removed"), name, "—"] for name in removed]
        panels.append(
            _panel(
                "rl_feature_changes",
                _t("Features added and removed"),
                {"kind": "empty"},
                severity=INFO,
                caption=_t("{count} change(s)", count=len(rows)),
                description=_t(
                    "Every engineered feature is a formula over existing columns, so it can "
                    "be rebuilt on new data."
                ),
                table={
                    "columns": [_t("Change"), _t("Feature"), _t("Formula")],
                    "rows": rows,
                },
            )
        )
    return panels


def _rl_reason_text(code: str) -> str:
    """Turn one service reason code into a sentence. Codes are not words."""
    known = {
        "NO_TARGET": _t("No target column was chosen, so there is nothing to optimise for."),
        "NO_ENHANCEABLE_TARGET": _t("No column in this data works as a prediction target."),
        "TARGET_NOT_FOUND": _t("The chosen target column is not present in the data."),
        "TARGET_CONSTANT": _t("The target never changes, so nothing can be learned from it."),
        "TARGET_TOO_SPARSE": _t("Too many rows are missing the target."),
        "TARGET_LIKELY_IDENTIFIER": _t(
            "The target looks like an identifier rather than an outcome."
        ),
        "INSUFFICIENT_ROWS": _t("There are too few rows for a reliable feature search."),
        "INSUFFICIENT_CLASS_SUPPORT": _t("At least one target class has too few examples."),
        "NO_USABLE_FEATURES": _t("No column is usable as a model feature."),
        "CV_NOT_FEASIBLE": _t("There are too few rows to cross-validate."),
        "BASELINE_TRAINING_FAILED": _t("A baseline model could not be trained on this data."),
        "TOO_HIGH_DIMENSIONAL_FOR_SEARCH": _t("There are too many features to search over."),
    }
    return known.get(code, code)


# -------------------------------------------------------------- evaluation


def evaluation_panels(payload: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = payload.get("holdout_metrics", [])
    if not metrics:
        return []
    alerts = payload.get("alerts", [])
    unresolved = [a for a in alerts if not a.get("resolved")]
    primary_name = payload.get("primary_metric")
    primary_score = next((m.get("score") for m in metrics if m.get("metric") == primary_name), None)
    insights = [
        _t("Primary metric {name} scored {score}.", name=primary_name, score=_fmt(primary_score)),
        _t("Baseline improvement was {delta}.", delta=_fmt(payload.get("baseline_delta"))),
    ]
    if unresolved:
        insights.append(
            _t("{count} alert(s) remain unresolved on this run.", count=len(unresolved))
        )
    return [
        _panel(
            "holdout_metrics",
            _t("Holdout performance"),
            {
                "kind": "hbar",
                "x_label": _t("Score"),
                "unit": "ratio",
                "series": [
                    {"label": m.get("metric", "?"), "value": m.get("score") or 0.0} for m in metrics
                ],
            },
            severity=WARNING if unresolved else INFO,
            caption=_t("{count} metric(s) measured", count=len(metrics)),
            description=(
                _t(
                    "Every metric measured on the held-out split, scored once after the model was "
                    "selected."
                )
            ),
            insights=insights,
            table={
                "columns": [_t("Metric"), _t("Score")],
                "rows": [[m.get("metric"), _fmt(m.get("score"))] for m in metrics],
            },
        )
    ]


# ----------------------------------------------------------- leakage audit


#: Leakage families, in the language a person needs rather than the code name.
_LEAKAGE_KINDS = {
    "target_correlation": _t("Correlates with the target almost perfectly"),
    "perfect_separator": _t("Separates the target classes perfectly"),
    "unwindowed_aggregate": _t("Aggregates data from the holdout period"),
    "missingness_separator": _t("Its missingness alone predicts the target"),
}


def leakage_panels(payload: dict[str, Any]) -> list[dict[str, Any]]:
    findings = payload.get("findings", [])
    challenges = payload.get("challenges", [])
    checked = payload.get("n_features_checked", 0)
    blocking = [f for f in findings if f.get("blocking")]
    if not checked:
        return []

    severity = ISSUE if blocking else (WARNING if findings else OK)
    insights = []
    if blocking:
        insights.append(
            _t(
                "{count} feature(s) block training. Each would make holdout "
                "results look better than anything achievable in production.",
                count=len(blocking),
            )
        )
        for finding in blocking[:4]:
            insights.append(
                f"{finding.get('column')}: "
                f"{_LEAKAGE_KINDS.get(finding.get('kind'), finding.get('kind'))}."
            )
    elif findings:
        insights.append(
            _t("{count} feature(s) were flagged but none block training.", count=len(findings))
        )
    else:
        insights.append(
            _t(
                "All {checked} feature(s) passed every leakage family checked: {families}.",
                checked=checked,
                families=", ".join(_LEAKAGE_KINDS.values()).lower(),
            )
        )

    for challenge in challenges:
        insights.append(str(challenge.get("result_summary") or ""))

    panel = _panel(
        "leakage_findings",
        _t("Leakage audit"),
        {
            "kind": "hbar",
            "x_label": _t("Leakage score"),
            "unit": "ratio",
            "series": [
                {"label": f.get("column", "?"), "value": f.get("score") or 0.0}
                for f in sorted(findings, key=lambda f: f.get("score") or 0.0, reverse=True)[:20]
            ],
        }
        if findings
        else {"kind": "empty"},
        severity=severity,
        caption=(
            _t("{count} blocking of {checked} checked", count=len(blocking), checked=checked)
            if blocking
            else _t("{checked} feature(s) clean", checked=checked)
        ),
        description=_t(
            "Four independent leakage families are measured against every feature. "
            "A high score is not proof of leakage, but it is a reason not to trust "
            "the result until someone confirms the feature is available at "
            "prediction time."
        ),
        insights=insights,
        table={
            "columns": [_t("Feature"), _t("Family"), _t("Score"), _t("Threshold"), _t("Blocking")],
            "rows": [
                [
                    f.get("column"),
                    _LEAKAGE_KINDS.get(f.get("kind"), f.get("kind")),
                    _fmt(f.get("score"), 3),
                    _fmt(f.get("threshold"), 3),
                    _t("yes") if f.get("blocking") else _t("no"),
                ]
                for f in findings[:20]
            ],
        }
        if findings
        else None,
    )
    panel["proposed_interpretations"] = [
        {
            "measurement_id": challenge.get("finding_fingerprint"),
            "epistemic_state": "proposed",
            "interpretation": challenge.get("interpretation"),
            "why_it_matters": challenge.get("why_it_matters"),
            "verification_question": challenge.get("verification_question"),
        }
        for challenge in challenges
    ]
    return [panel]


# ------------------------------------------------------------ schema graph


def schema_graph(payload: dict[str, Any]) -> dict[str, Any]:
    """Nodes and edges for the relationship map in the schema-discovery design.

    The brief is explicit that the schema should not be something only the agent
    understands. The same measured joins the agent reasons over are emitted here
    as a graph a person can look at, with the measured confidence on each edge
    rather than a claim about it.
    """
    base = payload.get("base_table")
    joins = payload.get("joins", [])
    aggregations = payload.get("aggregations", [])
    evidence = {(e.get("from_table"), e.get("to_table")): e for e in payload.get("evidence", [])}

    derived = {a.get("output_name"): a.get("source_table") for a in aggregations}
    nodes: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(name: str | None, role: str) -> None:
        if not name or name in seen:
            return
        seen.add(name)
        nodes.append(
            {
                "id": name,
                "role": role,
                "source_table": derived.get(name),
            }
        )

    add(base, "base")
    for join in joins:
        add(join.get("left_table"), "joined")
        right = join.get("right_table")
        add(right, "aggregate" if right in derived else "joined")

    edges = []
    for join in joins:
        left, right = join.get("left_table"), join.get("right_table")
        # A derived table inherits the measurement taken on the table it was
        # rolled up from; the aggregate itself was never measured for overlap.
        measured = evidence.get((right, left)) or evidence.get((left, right))
        if measured is None and right in derived:
            source = derived[right]
            measured = evidence.get((source, left)) or evidence.get((left, source))
        overlap = (measured or {}).get("overlap_rate")
        edges.append(
            {
                "source": left,
                "target": right,
                "left_columns": join.get("left_columns", []),
                "right_columns": join.get("right_columns", []),
                "how": join.get("how", "left"),
                "overlap_rate": overlap,
                "kind": "measured" if measured is not None else "suggested",
                "rationale": join.get("rationale"),
                "confidence": _confidence(overlap),
                "via": ", ".join(join.get("right_columns", []) or []),
            }
        )

    return {"base_table": base, "nodes": nodes, "edges": edges}


def _confidence(overlap: float | None) -> str:
    """Bucket a measured overlap into the legend used by the design.

    Unmeasured is not low. A join the deterministic layer never scored is
    unknown, and colouring it red would assert a finding nobody made.
    """
    if overlap is None:
        return "unmeasured"
    if overlap >= 0.95:
        return "high"
    if overlap >= 0.80:
        return "medium"
    return "low"


__all__ = [
    "eda_panels",
    "exploratory_panel",
    "evaluation_panels",
    "leakage_panels",
    "model_experiment_panel",
    "schema_graph",
    "source_panels",
    "training_panels",
    "validation_panels",
]
