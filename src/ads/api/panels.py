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
quantiles, correlations and bin tallies that the EDA profiler already computed.
This is the same two-plane boundary the agents live behind — a browser is no
more entitled to raw records than a model is. It is also why there is no
scatter plot of observations anywhere in this file: a scatter is a picture of
individual rows, so the relationship panel ranks measured association strength
instead.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

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


def _target_panel(target: dict[str, Any]) -> dict[str, Any]:
    column = target.get("target_column") or "target"
    null_rate = target.get("null_rate") or 0.0
    values = target.get("values") or []
    histogram = target.get("histogram") or []
    numeric = target.get("numeric") or {}

    severity = WARNING if null_rate >= HIGH_MISSING_RATE else REVIEW
    insights = []
    if null_rate:
        insights.append(
            f"{_pct(null_rate)} of {column} is missing "
            f"({target.get('total_count', 0) - target.get('non_null_count', 0):,} rows), "
            "so those rows cannot be trained on or scored against."
        )

    if values:
        total = sum(item["count"] for item in values) or 1
        top = values[0]
        insights.append(
            f"The most frequent class is {top['value']!r} at {_pct(top['count'] / total)}."
        )
        return _panel(
            "target_distribution",
            "Target distribution",
            {
                "kind": "bar",
                "x_label": column,
                "y_label": "Count",
                "series": [
                    {"label": item["value"], "value": item["count"]} for item in values[:24]
                ],
            },
            severity=severity,
            caption=f"{_pct(top['count'] / total)} {top['value']}",
            description=f"How the target {column!r} is distributed across observed rows.",
            insights=insights,
            table={
                "columns": ["Class", "Count", "Share"],
                "rows": [
                    [item["value"], f"{item['count']:,}", _pct(item["count"] / total)]
                    for item in values[:24]
                ]
                + [["Total", f"{total:,}", "100.0%"]],
            },
        )

    spread = numeric.get("std")
    mean = numeric.get("mean")
    if spread and mean:
        insights.append(
            f"Values range {_fmt(numeric.get('min'))} to {_fmt(numeric.get('max'))} "
            f"with a standard deviation of {_fmt(spread)}."
        )
    # Artifacts are immutable, so runs recorded before binning was added carry
    # quantiles and nothing else. A box drawn from min/p25/p50/p75/max shows
    # real measured spread instead of an empty frame apologising for itself.
    chart = (
        {"kind": "histogram", "x_label": column, "y_label": "Count", "bins": histogram}
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
        "Target distribution",
        chart,
        severity=severity,
        caption=f"median {_fmt(numeric.get('p50'))}",
        description=f"How the numeric target {column!r} is distributed across observed rows.",
        insights=insights,
        table={
            "columns": ["Statistic", "Value"],
            "rows": [
                ["Rows", f"{target.get('total_count', 0):,}"],
                ["Observed", f"{target.get('non_null_count', 0):,}"],
                ["Missing", _pct(null_rate)],
                ["Minimum", _fmt(numeric.get("min"))],
                ["25th percentile", _fmt(numeric.get("p25"))],
                ["Median", _fmt(numeric.get("p50"))],
                ["75th percentile", _fmt(numeric.get("p75"))],
                ["Maximum", _fmt(numeric.get("max"))],
                ["Mean", _fmt(mean)],
                ["Std deviation", _fmt(spread)],
            ],
        },
    )


def _missingness_panel(missingness: list[dict[str, Any]]) -> dict[str, Any]:
    high = [m for m in missingness if (m.get("null_rate") or 0.0) >= HIGH_MISSING_RATE]
    present = [m for m in missingness if (m.get("null_rate") or 0.0) > 0]
    severity = WARNING if high else (REVIEW if present else OK)
    insights = []
    if high:
        insights.append(
            f"{len(high)} column(s) are at or above {_pct(HIGH_MISSING_RATE)} missing: "
            + ", ".join(f"{m['column']} ({_pct(m['null_rate'])})" for m in high[:6])
            + "."
        )
        insights.append(
            "Decide per column whether to impute, drop the column, or drop the rows — "
            "each choice changes what the model can be used for."
        )
    elif present:
        insights.append("Missingness is present but every column is below the 10% threshold.")
    else:
        insights.append("No missing values were measured in any column.")

    return _panel(
        "missing_values",
        "Missing values",
        {
            "kind": "hbar",
            "x_label": "Missing share",
            "unit": "percent",
            "series": [
                {"label": m["column"], "value": m.get("null_rate") or 0.0}
                for m in missingness[:20]
            ],
        },
        severity=severity,
        caption=(
            f"{len(high)} column(s) over {_pct(HIGH_MISSING_RATE)}"
            if high
            else ("all columns under 10%" if present else "no missing values")
        ),
        description="Share of rows with no value, per column.",
        insights=insights,
        table={
            "columns": ["Column", "Missing rows", "Missing share"],
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
            f"{len(strong)} feature pair(s) are correlated at or above "
            f"{STRONG_CORRELATION:.2f}. Treat these as leakage candidates until the "
            "audit confirms otherwise, and expect unstable coefficients if both are kept."
        )
    else:
        insights.append("No feature pair reaches the 0.90 correlation threshold.")

    return _panel(
        "correlation_heatmap",
        "Correlation heatmap",
        {
            "kind": "heatmap",
            "columns": columns[:24],
            "values": [row[:24] for row in values[:24]],
            "min": -1.0,
            "max": 1.0,
        },
        severity=WARNING if strong else INFO,
        caption=(
            f"{len(strong)} strong pair(s)" if strong else "no strong correlation"
        ),
        description="Pearson correlation between every pair of numeric columns.",
        insights=insights,
        table={
            "columns": ["Column A", "Column B", "Correlation"],
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
        insights.append(
            f"{len(flagged)} column(s) have more than {_pct(HIGH_OUTLIER_RATE)} of values "
            "outside the 1.5×IQR fences: "
            + ", ".join(f"{o['column']} ({_pct(o['outlier_rate'])})" for o in flagged[:6])
            + "."
        )
        insights.append(
            "Far values are not automatically errors. Confirm whether they are real "
            "before clipping — removing genuine extremes biases the model toward the middle."
        )
    elif any_outliers:
        insights.append("Some values sit outside the fences, but no column exceeds 5%.")
    else:
        insights.append("No values fall outside the 1.5×IQR fences.")

    boxes = [o for o in outliers[:12] if o.get("p25") is not None]
    # Runs recorded before quartiles were carried on this artifact can still
    # show which columns are affected and by how much, which is the finding.
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
            "x_label": "Outlier share",
            "unit": "percent",
            "series": [
                {"label": o["column"], "value": o.get("outlier_rate") or 0.0}
                for o in outliers[:20]
            ],
        }
    )
    return _panel(
        "outliers",
        "Outliers",
        chart,
        severity=severity,
        caption=(
            f"{len(flagged)} column(s) over {_pct(HIGH_OUTLIER_RATE)}"
            if flagged
            else f"{len(any_outliers)} column(s) with outliers"
        ),
        description="Quartiles and 1.5×IQR fences per numeric column, with the share beyond them.",
        insights=insights,
        table={
            "columns": ["Column", "Lower fence", "Median", "Upper fence", "Outliers", "Share"],
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


def _relationship_panel(
    relationships: list[dict[str, Any]], target: str | None
) -> dict[str, Any]:
    strong = [
        r
        for r in relationships
        if abs(r.get("pearson_correlation") or 0.0) >= STRONG_CORRELATION
    ]
    insights = [
        "Ranked by absolute Pearson correlation with the target. Adjusted mutual "
        "information is shown alongside because it also catches non-linear "
        "association that correlation misses entirely.",
    ]
    if strong:
        insights.insert(
            0,
            f"{len(strong)} feature(s) correlate with {target or 'the target'} at or above "
            f"{STRONG_CORRELATION:.2f}. A feature that predicts the target almost perfectly "
            "is usually leakage rather than a finding.",
        )

    return _panel(
        "feature_relationships",
        "Feature relationships",
        {
            "kind": "hbar",
            "x_label": f"|correlation| with {target or 'target'}",
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
            f"{len(strong)} near-perfect predictor(s)"
            if strong
            else f"{len(relationships)} feature(s) measured"
        ),
        description=(
            "Measured association between each feature and the target. These are "
            "aggregate statistics — no individual rows are plotted."
        ),
        insights=insights,
        table={
            "columns": ["Feature", "Pearson", "Adjusted MI"],
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
        f"The majority class {balance.get('majority_class')!r} outnumbers "
        f"{balance.get('minority_class')!r} by {ratio:.1f}:1.",
    ]
    if ratio >= IMBALANCE_RATIO:
        insights.append(
            "At this ratio accuracy is misleading — a model predicting the majority "
            "class every time would score "
            f"{_pct(balance.get('majority_rate'))}. Judge it on the minority class."
        )

    classes = balance.get("classes", [])
    return _panel(
        "class_balance",
        "Class balance",
        {
            "kind": "donut",
            "series": [
                {"label": c["value"], "value": c["count"]} for c in classes[:12]
            ],
        },
        severity=severity,
        caption=f"{ratio:.1f}:1 majority to minority",
        description="Share of each target class among observed rows.",
        insights=insights,
        table={
            "columns": ["Class", "Count", "Share"],
            "rows": [[c["value"], f"{c['count']:,}", _pct(c["rate"])] for c in classes[:12]],
        },
    )


# ------------------------------------------------------------------- intake


def source_panels(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One panel per profiled source table, comparable side by side.

    Intake emits a separate DataCard artifact per table, so each rendered as its
    own isolated block and there was no way to see four tables next to each
    other. The question a person actually has at this stage — which of these is
    the messy one — needs them in the same strip.

    Built from the stage's artifacts rather than from a single payload, which is
    why this takes a list where the other builders take one.
    """
    panels: list[dict[str, Any]] = []
    for card in cards:
        payload = card.get("payload") or {}
        name = payload.get("table_name") or card.get("name") or "table"
        columns = payload.get("columns", [])
        # The field is `candidate_primary_keys`; reading `candidate_keys` made
        # every source card report "0 candidate key(s) measured" on data whose
        # keys had in fact been found.
        keys = payload.get("candidate_primary_keys", [])

        # Intake records informational notes alongside real problems --
        # "renamed 'Physician ID' to physician_id", "delimiter detected". Counting
        # every entry made a tidy table with five successful renames read as five
        # issues, and two of the four sample tables were shown as blocking when
        # nothing was wrong with either. Only warn and above is a finding.
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
        severity = (
            ISSUE
            if issues
            else WARNING
            if worst >= HIGH_MISSING_RATE or sensitive
            else INFO
        )

        # What one row of this table is, in the measured key's own words. Every
        # source card used to carry the identical sentence "Schema, size and
        # quality statistics for this source table", which describes the
        # artifact rather than the data and is the same for every table in every
        # dataset. The grain is the first thing a person needs and it was
        # already measured.
        grain = (
            f"one row per {' + '.join(keys[0])}"
            if keys and keys[0]
            else "no column or combination was measured unique, so one row's identity is unclear"
        )
        kinds = Counter(str(column.get("semantic_type", "unknown")) for column in columns)
        composition = ", ".join(
            f"{count} {kind.replace('_', ' ')}" for kind, count in kinds.most_common()
        )
        description = (
            f"{payload.get('n_rows', 0):,} rows, {grain}. Columns: {composition}."
        )

        insights = [
            f"Measured grain: {grain}."
            + (
                f" {len(keys)} candidate key(s) in total: "
                + "; ".join(" + ".join(k) for k in keys[:4])
                + "."
                if len(keys) > 1
                else ""
            )
        ]
        if sensitive:
            insights.append(
                f"{len(sensitive)} column(s) classified as sensitive: "
                + ", ".join(c.get("name", "?") for c in sensitive[:6])
                + ". Values from these are never shown or sent to a model."
            )
        if issues:
            insights.append(
                f"{len(issues)} quality issue(s) recorded during load: "
                + "; ".join(str(n.get("detail", n.get("code", "?")))[:120] for n in issues[:3])
                + "."
            )
        if notes:
            insights.append(
                f"{len(notes)} informational note(s) from loading — renames, inferred "
                "headers, detected delimiters. Recorded for provenance, not problems."
            )
        if missing:
            insights.append(
                f"Highest missingness is {missing[0]['label']} at {_pct(worst)}."
            )
        else:
            insights.append("No missing values in any column.")

        panels.append(
            _panel(
                f"source_{name}",
                name,
                # A clean table has no missingness to plot, but "nothing to see"
                # is not the same as "nothing measured". The column-type mix is
                # always available and is what tells a reader at a glance
                # whether a table is mostly identifiers or mostly measurements.
                {
                    "kind": "hbar",
                    "x_label": "Missing share",
                    "unit": "percent",
                    "series": missing[:14],
                }
                if missing
                else {
                    "kind": "donut",
                    "series": [
                        {"label": semantic, "value": count}
                        for semantic, count in sorted(
                            Counter(
                                str(column.get("semantic_type", "unknown"))
                                for column in columns
                            ).items(),
                            key=lambda item: -item[1],
                        )
                    ],
                }
                if columns
                else {"kind": "empty"},
                severity=severity,
                caption=(
                    f"{payload.get('n_rows', 0):,} rows · {len(columns)} cols"
                    + (f" · {len(issues)} issue(s)" if issues else "")
                ),
                description=description,
                insights=insights,
                table={
                    "columns": ["Column", "Type", "Sensitivity", "Missing", "Distinct"],
                    "rows": [
                        [
                            column.get("name"),
                            column.get("semantic_type", "—"),
                            column.get("sensitivity", "—"),
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

_PROTECTION_LABELS = {
    "entity_isolation": "Entity isolation",
    "temporal_ordering": "Temporal ordering",
    "class_balance": "Class balance",
}

_PROTECTION_MEANING = {
    "entity_isolation": (
        "The same entity cannot appear in both training and evaluation. Without it a "
        "model can memorise an individual and be scored on that same individual."
    ),
    "temporal_ordering": (
        "Training data never comes from later than evaluation data. Without it the "
        "model has seen the future and the score is unachievable in production."
    ),
    "class_balance": (
        "Each fold keeps the observed class proportions, so per-fold scores are "
        "comparable to each other."
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
    for key, label in _PROTECTION_LABELS.items():
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
        insights.append(
            f"{strategy} does not provide "
            + ", ".join(r["label"].lower() for r in gaps)
            + ", which the measured data requires. Scores from this split would be "
            "optimistic in a way no later stage can detect."
        )
    else:
        insights.append(
            f"{strategy} provides every protection the measured data requires."
        )
    for key in sorted(required):
        insights.append(f"{_PROTECTION_LABELS[key]}: {_PROTECTION_MEANING[key]}")
    if not required:
        insights.append(
            "No repeated entities and no multi-period time span were measured, so no "
            "structural protection is strictly required."
        )

    spans = signals.get("temporal_spans") or []
    return [
        _panel(
            "split_protection",
            "Split protection",
            {
                "kind": "hbar",
                "x_label": "Protection provided",
                "unit": "ratio",
                "series": [{"label": r["label"], "value": r["value"]} for r in rows],
            },
            severity=ISSUE if gaps else INFO,
            caption=(
                f"{len(gaps)} required protection(s) missing"
                if gaps
                else f"{strategy} covers what the data requires"
            ),
            description=(
                "Strategies are not ranked. Each prevents a different leak, so the "
                "question is whether the chosen one covers what the data measured."
            ),
            insights=insights,
            table={
                "columns": ["Protection", "Required by data", "Provided by strategy"],
                "rows": [
                    [r["label"], "yes" if r["need"] else "no", "yes" if r["have"] else "no"]
                    for r in rows
                ],
            },
        ),
        _panel(
            "split_setup",
            "Split setup",
            {
                "kind": "bar",
                "y_label": "Rows",
                "series": [
                    {
                        "label": "Training",
                        "value": round(
                            (signals.get("n_usable_rows") or 0)
                            * (1 - (payload.get("test_size") or 0.2))
                        ),
                    },
                    {
                        "label": "Holdout",
                        "value": round(
                            (signals.get("n_usable_rows") or 0)
                            * (payload.get("test_size") or 0.2)
                        ),
                    },
                ],
            },
            severity=(
                WARNING
                if (signals.get("n_usable_rows") or 0) < (signals.get("n_rows") or 0)
                else INFO
            ),
            caption=f"{payload.get('n_folds')} folds, {_pct(payload.get('test_size'))} holdout",
            description="How the rows are divided, and how many survive the target filter.",
            insights=[
                f"{signals.get('n_usable_rows', 0):,} of {signals.get('n_rows', 0):,} rows "
                "are usable; the rest have no target value and cannot be trained on or "
                "scored against."
            ]
            + (
                [
                    f"Time column {spans[0]['column']} spans "
                    f"{spans[0]['min_date'][:10]} to {spans[0]['max_date'][:10]} "
                    f"({spans[0]['span_days']:,} days)."
                ]
                if spans
                else []
            ),
            table={
                "columns": ["Setting", "Value"],
                "rows": [
                    ["Strategy", str(strategy)],
                    ["Folds", str(payload.get("n_folds"))],
                    ["Holdout fraction", _pct(payload.get("test_size"))],
                    ["Time column", str(payload.get("time_column") or "—")],
                    ["Group column", str(payload.get("group_column") or "—")],
                    ["Holdout cutoff", str(payload.get("holdout_cutoff") or "—")],
                    ["Usable rows", f"{signals.get('n_usable_rows', 0):,}"],
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
        key=lambda r: (r["value"] if r["value"] is not None else 0),
        reverse=not lower_is_better,
    )
    winner_row = next((r for r in rows if r["selected"]), rows[0])
    return _panel(
        "candidate_comparison",
        "Candidate comparison",
        {
            "kind": "hbar",
            "x_label": f"Holdout {metric}",
            "unit": "ratio",
            "series": [{"label": r["label"], "value": r["value"] or 0.0} for r in ordered],
        },
        severity=INFO,
        caption=f"{winner_row['label']} selected",
        description=(
            f"Every candidate's holdout {metric}. "
            f"{'Lower is better' if lower_is_better else 'Higher is better'}."
        ),
        insights=[
            f"{winner_row['label']} was selected on holdout {metric} "
            f"{_fmt(winner_row['value'])}.",
            "Holdout is scored once, after selection, so it is not the number the "
            "candidates were chosen by — the cross-validation mean is.",
        ],
        table={
            "columns": ["Model", f"CV mean ({metric})", "CV spread", f"Holdout ({metric})", "Role"],
            "rows": [
                [
                    r["label"],
                    _fmt(r["cv_mean"]),
                    _fmt(r["cv_std"]),
                    _fmt(r["value"]),
                    "selected" if r["selected"] else ("baseline" if r["baseline"] else "candidate"),
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
        "Cross-validation stability",
        {
            "kind": "bar",
            "x_label": "Fold",
            "y_label": metric,
            "series": [
                {"label": f"Fold {i + 1}", "value": score} for i, score in enumerate(folds)
            ],
        },
        severity=severity,
        caption=f"spread {_pct(cov)} of mean",
        description=(
            f"{winner.get('display_name')} scored on each validation fold. Consistency "
            "across folds is what makes a single holdout number believable."
        ),
        insights=[
            f"Fold scores range {_fmt(min(folds))} to {_fmt(max(folds))} around a mean of "
            f"{_fmt(mean)}.",
            (
                "That spread is over a quarter of the mean, so the reported score is "
                "unstable and the difference between candidates may be noise."
                if severity == WARNING
                else "The spread is small relative to the mean, so the score is stable "
                "across resampling."
            ),
        ],
        table={
            "columns": ["Fold", metric],
            "rows": [[f"Fold {i + 1}", _fmt(s)] for i, s in enumerate(folds)]
            + [["Mean", _fmt(mean)], ["Std deviation", _fmt(spread)]],
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
        "Lift over baseline",
        {
            "kind": "bar",
            "y_label": metric,
            "series": [
                {"label": baseline.get("display_name", "Baseline"), "value": b_score or 0.0},
                {"label": winner.get("display_name", "Selected"), "value": w_score or 0.0},
            ],
        },
        severity=INFO if beat else ISSUE,
        caption=("beats baseline" if beat else "does not beat baseline"),
        description=(
            "The selected model against a naive baseline on the same holdout. A model "
            "that cannot beat the baseline has learned nothing worth deploying."
        ),
        insights=[
            f"Selected {_fmt(w_score)} versus baseline {_fmt(b_score)} on holdout {metric}."
            if beat
            else f"The selected model scored {_fmt(w_score)} against a baseline of "
            f"{_fmt(b_score)}. It has not demonstrated value over guessing.",
        ],
        table={
            "columns": ["Model", f"CV mean ({metric})", f"Holdout ({metric})"],
            "rows": [
                [baseline.get("display_name"), _fmt(b.get("cv_mean")), _fmt(b_score)],
                [winner.get("display_name"), _fmt(w.get("cv_mean")), _fmt(w_score)],
            ],
        },
    )


# -------------------------------------------------------------- evaluation


def evaluation_panels(payload: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = payload.get("holdout_metrics", [])
    if not metrics:
        return []
    alerts = payload.get("alerts", [])
    unresolved = [a for a in alerts if not a.get("resolved")]
    primary_name = payload.get("primary_metric")
    primary_score = next(
        (m.get("score") for m in metrics if m.get("metric") == primary_name), None
    )
    return [
        _panel(
            "holdout_metrics",
            "Holdout performance",
            {
                "kind": "hbar",
                "x_label": "Score",
                "unit": "ratio",
                "series": [
                    {"label": m.get("metric", "?"), "value": m.get("score") or 0.0}
                    for m in metrics
                ],
            },
            severity=WARNING if unresolved else INFO,
            caption=f"{len(metrics)} metric(s) measured",
            description=(
                "Every metric measured on the held-out split, scored once after the "
                "model was selected."
            ),
            insights=[
                f"Primary metric {primary_name} scored {_fmt(primary_score)}.",
                f"Baseline improvement was {_fmt(payload.get('baseline_delta'))}.",
            ]
            + (
                [f"{len(unresolved)} alert(s) remain unresolved on this run."]
                if unresolved
                else []
            ),
            table={
                "columns": ["Metric", "Score"],
                "rows": [[m.get("metric"), _fmt(m.get("score"))] for m in metrics],
            },
        )
    ]


# ----------------------------------------------------------- leakage audit


#: Leakage families, in the language a person needs rather than the code name.
_LEAKAGE_KINDS = {
    "target_correlation": "Correlates with the target almost perfectly",
    "perfect_separator": "Separates the target classes perfectly",
    "unwindowed_aggregate": "Aggregates data from the holdout period",
    "missingness_separator": "Its missingness alone predicts the target",
}


def leakage_panels(payload: dict[str, Any]) -> list[dict[str, Any]]:
    findings = payload.get("findings", [])
    checked = payload.get("n_features_checked", 0)
    blocking = [f for f in findings if f.get("blocking")]
    if not checked:
        return []

    severity = ISSUE if blocking else (WARNING if findings else OK)
    insights = []
    if blocking:
        insights.append(
            f"{len(blocking)} feature(s) block training. Each would make holdout "
            "results look better than anything achievable in production."
        )
        for finding in blocking[:4]:
            insights.append(
                f"{finding.get('column')}: "
                f"{_LEAKAGE_KINDS.get(finding.get('kind'), finding.get('kind'))}."
            )
    elif findings:
        insights.append(f"{len(findings)} feature(s) were flagged but none block training.")
    else:
        insights.append(
            f"All {checked} feature(s) passed every leakage family checked: "
            + ", ".join(_LEAKAGE_KINDS.values()).lower()
            + "."
        )

    return [
        _panel(
            "leakage_findings",
            "Leakage audit",
            {
                "kind": "hbar",
                "x_label": "Leakage score",
                "unit": "ratio",
                "series": [
                    {"label": f.get("column", "?"), "value": f.get("score") or 0.0}
                    for f in sorted(
                        findings, key=lambda f: f.get("score") or 0.0, reverse=True
                    )[:20]
                ],
            }
            if findings
            else {"kind": "empty"},
            severity=severity,
            caption=(
                f"{len(blocking)} blocking of {checked} checked"
                if blocking
                else f"{checked} feature(s) clean"
            ),
            description=(
                "Four independent leakage families are measured against every feature. "
                "A high score is not proof of leakage, but it is a reason not to trust "
                "the result until someone confirms the feature is available at "
                "prediction time."
            ),
            insights=insights,
            table={
                "columns": ["Feature", "Family", "Score", "Threshold", "Blocking"],
                "rows": [
                    [
                        f.get("column"),
                        _LEAKAGE_KINDS.get(f.get("kind"), f.get("kind")),
                        _fmt(f.get("score"), 3),
                        _fmt(f.get("threshold"), 3),
                        "yes" if f.get("blocking") else "no",
                    ]
                    for f in findings[:20]
                ],
            }
            if findings
            else None,
        )
    ]


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
    evidence = {
        (e.get("from_table"), e.get("to_table")): e for e in payload.get("evidence", [])
    }

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
    "evaluation_panels",
    "leakage_panels",
    "schema_graph",
    "source_panels",
    "training_panels",
    "validation_panels",
]
