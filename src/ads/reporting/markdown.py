"""Render EvaluationReport artifacts as conservative, human-facing Markdown."""

from __future__ import annotations

from ads.contracts.reporting import DecisionAuthority, EvaluationReport


def _number(value: float) -> str:
    return f"{value:,.6g}"


def _baseline_statement(report: EvaluationReport) -> str:
    winner = _number(report.winner_holdout_score)
    baseline = _number(report.baseline_holdout_score)
    delta = _number(abs(report.baseline_delta))
    if report.baseline_delta > 0:
        direction = "lower" if report.lower_is_better else "higher"
        return (
            f"On the untouched holdout, the winner scored **{winner}** versus "
            f"**{baseline}** for the naive baseline: an improvement of **{delta}** "
            f"({direction} is better for {report.primary_metric.value})."
        )
    if report.baseline_delta == 0:
        return (
            f"On the untouched holdout, the winner and naive baseline both scored "
            f"**{winner}**. The model showed **no improvement over the baseline**."
        )
    return (
        f"On the untouched holdout, the selected model scored **{winner}** versus "
        f"**{baseline}** for the naive baseline. It was **worse than the baseline by "
        f"{delta}** on the oriented {report.primary_metric.value} scale."
    )


def _alert_block(report: EvaluationReport) -> list[str]:
    lines: list[str] = []
    prominent = report.prominent_alerts
    unresolved = report.unresolved_blocking_leakage
    unconfirmed = report.unresolved_separator_confirmation
    if prominent or unresolved or unconfirmed or report.baseline_delta <= 0:
        lines.extend(["> [!WARNING]", "> **This evaluation is not clear to ship.**"])
        if report.baseline_delta <= 0:
            lines.append("> The selected model did not demonstrate lift over the baseline.")
        for alert in prominent:
            lines.append(f"> **{alert.reason_code}**: {alert.detail}")
        if unresolved:
            columns = ", ".join(f"`{item.column}`" for item in unresolved)
            lines.append(f"> Blocking leakage remains unresolved for {columns}.")
        if unconfirmed:
            columns = ", ".join(f"`{item.column}`" for item in unconfirmed)
            lines.append(
                "> Separator provenance remains unconfirmed for "
                f"{columns}; the model is not leakage-clean."
            )
        lines.append("")
    return lines


def _headline_metric_caveat(report: EvaluationReport) -> str:
    caveats = {
        alert.reason_code: alert.detail
        for alert in report.prominent_alerts
        if alert.reason_code in {"lift_within_noise", "degenerate_split"}
    }
    if not caveats:
        return ""
    details = " ".join(
        f"**{reason_code}**: {detail}" for reason_code, detail in caveats.items()
    )
    return f" **Headline metric caveat:** {details}"


def _gate_history_section(report: EvaluationReport) -> list[str]:
    lines = ["## Decisions and escalations", ""]
    if not report.gate_history:
        lines.extend(
            [
                "No gate decision history was attached to this evaluation artifact.",
                "",
            ]
        )
        return lines

    authority_labels = {
        DecisionAuthority.HUMAN: "human-approved",
        DecisionAuthority.AUTONOMOUS: "autonomous",
        DecisionAuthority.UNRECORDED: "human approval not recorded",
    }
    lines.extend(
        [
            "| Stage | Attempt | Verdict | Reason code | Rules fired | Authority |",
            "|---|---:|---|---|---|---|",
        ]
    )
    for decision in report.gate_history:
        rules = ", ".join(f"`{rule}`" for rule in decision.triggered_rules) or "none"
        lines.append(
            f"| `{decision.stage_id}` | {decision.attempt} | "
            f"**{decision.verdict.value}** | `{decision.reason_code}` | {rules} | "
            f"{authority_labels[decision.authority]} |"
        )
    lines.append("")
    return lines


def render_markdown(report: EvaluationReport) -> str:
    """Render a complete report while keeping gate escalations above the results."""
    lines = [f"# Evaluation report: {report.problem_title}", ""]
    lines.extend(_alert_block(report))
    lines.extend(_gate_history_section(report))

    target = f"`{report.target_column}`" if report.target_column else "the defined outcome"
    if report.persisted_model_matches_measured_model is True:
        persistence_statement = (
            "The persisted model is the same fitted pipeline that produced the holdout "
            "measurement. It was not silently refit on the holdout rows."
        )
    elif report.persisted_model_matches_measured_model is False:
        persistence_statement = (
            "The persisted model was refit after holdout evaluation. The holdout metric "
            "does not directly measure that refitted artifact."
        )
    else:
        persistence_statement = "No persisted model blob is referenced by this evaluation artifact."
    lines.extend(
        [
            "## Problem and model",
            "",
            report.problem_description,
            "",
            f"The model predicts {target} for a {report.task_type.value.replace('_', ' ')} "
            f"problem. **{report.winner_display_name}** was selected from "
            f"{len(report.candidate_comparisons)} candidates using "
            f"{report.primary_metric.value}.",
            "",
            persistence_statement,
            "",
            "## Performance against the baseline",
            "",
            _baseline_statement(report) + _headline_metric_caveat(report),
            "",
            f"Training received {report.input_row_count:,} rows, dropped "
            f"{report.target_null_rows_dropped:,} rows with no target, and evaluated "
            f"{report.training_row_count:,} labeled rows.",
            "",
            "### Candidate comparison",
            "",
            "| Candidate | Baseline | Selected | CV mean | CV std | Holdout |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for candidate in report.candidate_comparisons:
        lines.append(
            f"| {candidate.display_name} | {'yes' if candidate.is_baseline else 'no'} | "
            f"{'yes' if candidate.selected else 'no'} | {_number(candidate.cv_mean)} | "
            f"{_number(candidate.cv_std)} | {_number(candidate.holdout_score)} |"
        )

    lines.extend(["", "### Winner holdout metrics", ""])
    lines.extend(
        f"- {metric.metric.value}: **{_number(metric.score)}**"
        for metric in report.holdout_metrics
    )

    lines.extend(
        [
            "",
            "## Validation strategy",
            "",
            f"The run used **{report.validation_strategy.value}** validation with "
            f"{report.validation_n_folds} inner folds and a "
            f"{report.validation_test_size:.0%} outer holdout.",
            "",
            report.validation_rationale,
        ]
    )
    validation_details = [
        ("Group column", report.validation_group_column),
        ("Time column", report.validation_time_column),
        ("Holdout cutoff", report.validation_holdout_cutoff),
    ]
    for label, value in validation_details:
        if value:
            lines.append(f"- {label}: `{value}`")

    lines.extend(["", "## Leakage controls", ""])
    if report.cleared_leakage:
        lines.append("The following audited leakage findings were cleared by exclusion:")
        lines.append("")
        for finding in report.cleared_leakage:
            lines.append(
                f"- `{finding.column}` — {finding.kind.value} "
                f"(score {_number(finding.score)}). {finding.action}"
            )
    else:
        lines.append("No audited leakage finding was recorded as cleared by exclusion.")
    uncleared_warnings = [
        item for item in report.leakage_dispositions if not item.blocking and not item.cleared
    ]
    if uncleared_warnings:
        lines.extend(["", "Non-blocking leakage-audit warnings remain:", ""])
        lines.extend(
            f"- `{item.column}` — {item.kind.value} (score {_number(item.score)})."
            for item in uncleared_warnings
        )

    lines.extend(["", "## Decision provenance", ""])
    for authority in DecisionAuthority:
        matching = [item for item in report.decisions if item.authority is authority]
        if not matching:
            continue
        label = {
            DecisionAuthority.HUMAN: "Human-approved",
            DecisionAuthority.AUTONOMOUS: "Autonomous",
            DecisionAuthority.UNRECORDED: "Approval not recorded",
        }[authority]
        lines.extend([f"### {label}", ""])
        for decision in matching:
            rationale = f" {decision.rationale}" if decision.rationale else ""
            lines.append(f"- **{decision.stage}:** {decision.decision}{rationale}")
        lines.append("")

    other_alerts = [item for item in report.alerts if item not in report.prominent_alerts]
    if other_alerts:
        lines.extend(["## Other gate outcomes", ""])
        lines.extend(
            f"- **{item.reason_code}** ({item.verdict.value}, `{item.stage_id}`): {item.detail}"
            for item in other_alerts
        )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


__all__ = ["render_markdown"]
