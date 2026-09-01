"""Render EvaluationReport artifacts as conservative, human-facing Markdown."""

from __future__ import annotations

from typing import Any

from ads.contracts.reporting import DecisionAuthority, EvaluationReport


def _t(text: str, /, **params: Any) -> str:
    from ads.api.i18n import t

    return t(text, **params)


def _prose(english: str, turkish: str) -> str:
    """#200: pick the language the report is being rendered in for agent-authored
    prose. The problem is authored bilingually upstream, so a Turkish reader gets
    the Turkish title and description rather than English text interpolated raw
    into an otherwise Turkish document. Falls back to English when no Turkish
    variant was produced, which is better than a blank."""
    from ads.api.i18n import current

    return turkish if current() == "tr" and turkish else english


#: #200: the same verdict/reason labels the UI renders (web/src/lib/status.ts),
#: so a gate reads identically in the report and on screen. Passed through _t,
#: so an English render shows the English label and a Turkish one the Turkish.
_VERDICT_LABELS = {
    "auto_proceed": "Continued",
    "retry": "Sent back",
    "escalate": "Asked you",
    "abort": "Stopped",
}
_REASON_LABELS = {
    "profile_checkpoint": "You asked to review this step",
    "leakage_detected": "A feature may leak the answer",
    "leakage_unresolved": "Leakage still present after rework",
    "leakage_challenge_needs_confirmation": "Agent challenged the leakage finding",
    "missing_required_artifact": "A required stage output is missing",
    "retry_budget_exhausted": "No attempts left",
    "repeated_identical_failure": "The same failure repeated",
    "pii_egress_requested": "Personal data would leave the machine",
    "destructive_operation": "A step would modify the source data",
    "model_below_baseline": "The model did not beat the baseline",
    "lift_within_noise": "The improvement is within noise",
    "high_cv_variance": "Fold-to-fold scores vary widely",
    "candidate_disagreement": "The proposals disagreed",
    "statistical_support_low": "Not enough data to support this",
    "degenerate_split": "The split left a fold unusable",
    "unmet_mandatory_criteria": "A required check did not pass",
    "risk_class_gate": "This step is high risk",
    "clean": "Nothing to flag",
}


def _verdict_label(value: str) -> str:
    return _t(_VERDICT_LABELS.get(value, value.replace("_", " ").capitalize()))


def _reason_label(code: str) -> str:
    return _t(_REASON_LABELS.get(code, code.replace("_", " ").capitalize()))


def _number(value: float) -> str:
    return f"{value:,.6g}"


def _baseline_statement(report: EvaluationReport) -> str:
    winner = _number(report.winner_holdout_score)
    baseline = _number(report.baseline_holdout_score)
    delta = _number(abs(report.baseline_delta))
    if report.baseline_delta > 0:
        direction = _t("lower") if report.lower_is_better else _t("higher")
        return _t(
            "On the untouched holdout, the winner scored **{winner}** versus "
            "**{baseline}** for the naive baseline: an improvement of **{delta}** "
            "({direction} is better for {metric}).",
            winner=winner,
            baseline=baseline,
            delta=delta,
            direction=direction,
            metric=report.primary_metric.value,
        )
    if report.baseline_delta == 0:
        return _t(
            "On the untouched holdout, the winner and naive baseline both scored "
            "**{winner}**. The model showed **no improvement over the baseline**.",
            winner=winner,
        )
    return _t(
        "On the untouched holdout, the selected model scored **{winner}** versus "
        "**{baseline}** for the naive baseline. It was **worse than the baseline by "
        "{delta}** on the oriented {metric} scale.",
        winner=winner,
        baseline=baseline,
        delta=delta,
        metric=report.primary_metric.value,
    )


def _alert_block(report: EvaluationReport) -> list[str]:
    lines: list[str] = []
    prominent = report.prominent_alerts
    unresolved = report.unresolved_blocking_leakage
    unconfirmed = report.unresolved_separator_confirmation
    if prominent or unresolved or unconfirmed or report.baseline_delta <= 0:
        lines.extend(["> [!WARNING]", f"> **{_t('This evaluation is not clear to ship.')}**"])
        if report.baseline_delta <= 0:
            lines.append(
                f"> {_t('The selected model did not demonstrate lift over the baseline.')}"
            )
        for alert in prominent:
            lines.append(f"> **{alert.reason_code}**: {alert.detail}")
        if unresolved:
            columns = ", ".join(f"`{item.column}`" for item in unresolved)
            lines.append(
                f"> {_t('Blocking leakage remains unresolved for {columns}.', columns=columns)}"
            )
        if unconfirmed:
            columns = ", ".join(f"`{item.column}`" for item in unconfirmed)
            msg = (
                "Separator provenance remains unconfirmed for "
                f"{columns}; the model is not leakage-clean."
            )
            lines.append(f"> {_t(msg, columns=columns)}")
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
    details = " ".join(f"**{reason_code}**: {detail}" for reason_code, detail in caveats.items())
    return f" **Headline metric caveat:** {details}"


def _gate_history_section(report: EvaluationReport) -> list[str]:
    lines = [f"## {_t('Decisions and escalations')}", ""]
    if not report.gate_history:
        lines.extend(
            [
                _t("No gate decision history was attached to this evaluation artifact."),
                "",
            ]
        )
        return lines

    authority_labels = {
        DecisionAuthority.HUMAN: _t("human-approved"),
        DecisionAuthority.AUTONOMOUS: _t("autonomous"),
        DecisionAuthority.UNRECORDED: _t("human approval not recorded"),
    }
    header = (
        f"| {_t('Stage')} | {_t('Attempt')} | {_t('Verdict')} | "
        f"{_t('Reason code')} | {_t('Rules fired')} | {_t('Authority')} |"
    )
    lines.extend(
        [
            header,
            "|---|---:|---|---|---|---|",
        ]
    )
    for decision in report.gate_history:
        rules = ", ".join(f"`{rule}`" for rule in decision.triggered_rules) or _t("none")
        # #200: the verdict and reason were emitted as raw enum text
        # (`auto_proceed`, `lift_within_noise`) in an otherwise translated table.
        # Map them through the same labels the UI uses so the report reads in one
        # language; keep the raw code in a trailing code span for auditability.
        lines.append(
            f"| `{decision.stage_id}` | {decision.attempt} | "
            f"**{_verdict_label(decision.verdict.value)}** | "
            f"{_reason_label(decision.reason_code)} `{decision.reason_code}` | {rules} | "
            f"{authority_labels[decision.authority]} |"
        )
    lines.append("")
    return lines


def render_markdown(report: EvaluationReport) -> str:
    """Render a complete report while keeping gate escalations above the results."""
    title = _prose(report.problem_title, report.problem_title_tr)
    lines = [f"# {_t('Evaluation report')}: {title}", ""]
    lines.extend(_alert_block(report))
    lines.extend(_gate_history_section(report))

    target = f"`{report.target_column}`" if report.target_column else _t("the defined outcome")
    if report.persisted_model_matches_measured_model is True:
        persistence_statement = _t(
            "The persisted model is the same fitted pipeline that produced the holdout "
            "measurement. It was not silently refit on the holdout rows."
        )
    elif report.persisted_model_matches_measured_model is False:
        persistence_statement = _t(
            "The persisted model was refit after holdout evaluation. The holdout metric "
            "does not directly measure that refitted artifact."
        )
    else:
        persistence_statement = _t(
            "No persisted model blob is referenced by this evaluation artifact."
        )
    lines.extend(
        [
            f"## {_t('Problem and model')}",
            "",
            _prose(report.problem_description, report.problem_description_tr),
            "",
            _t(
                "The model predicts {target} for a {task} "
                "problem. **{winner}** was selected from "
                "{count} candidates using "
                "{metric}.",
                target=target,
                task=report.task_type.value.replace("_", " "),
                winner=report.winner_display_name,
                count=len(report.candidate_comparisons),
                metric=report.primary_metric.value,
            ),
            "",
            persistence_statement,
            "",
            f"## {_t('Performance against the baseline')}",
            "",
            _baseline_statement(report) + _headline_metric_caveat(report),
            "",
            _t(
                "Training received {input_rows:,} rows, dropped "
                "{dropped_rows:,} rows with no target, and evaluated "
                "{train_rows:,} labeled rows.",
                input_rows=report.input_row_count,
                dropped_rows=report.target_null_rows_dropped,
                train_rows=report.training_row_count,
            ),
            "",
            f"### {_t('Candidate comparison')}",
            "",
            (
                f"| {_t('Candidate')} | {_t('Baseline')} | {_t('Selected')} | "
                f"{_t('CV mean')} | {_t('CV std')} | {_t('Holdout')} |"
            ),
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for candidate in report.candidate_comparisons:
        lines.append(
            f"| {candidate.display_name} | {_t('yes') if candidate.is_baseline else _t('no')} | "
            f"{_t('yes') if candidate.selected else _t('no')} | {_number(candidate.cv_mean)} | "
            f"{_number(candidate.cv_std)} | {_number(candidate.holdout_score)} |"
        )

    lines.extend(["", f"### {_t('Winner holdout metrics')}", ""])
    lines.extend(
        f"- {metric.metric.value}: **{_number(metric.score)}**" for metric in report.holdout_metrics
    )

    lines.extend(
        [
            "",
            f"## {_t('Validation strategy')}",
            "",
            _t(
                "The run used **{strategy}** validation with "
                "{folds} inner folds and a "
                "{holdout:.0%} outer holdout.",
                strategy=report.validation_strategy.value,
                folds=report.validation_n_folds,
                holdout=report.validation_test_size,
            ),
            "",
            report.validation_rationale,
        ]
    )
    validation_details = [
        (_t("Group column"), report.validation_group_column),
        (_t("Time column"), report.validation_time_column),
        (_t("Holdout cutoff"), report.validation_holdout_cutoff),
    ]
    for label, value in validation_details:
        if value:
            lines.append(f"- {label}: `{value}`")

    lines.extend(["", f"## {_t('Leakage controls')}", ""])
    if report.cleared_leakage:
        lines.append(_t("The following audited leakage findings were cleared by exclusion:"))
        lines.append("")
        for finding in report.cleared_leakage:
            lines.append(
                f"- `{finding.column}` — {finding.kind.value} "
                f"({_t('score')} {_number(finding.score)}). {finding.action}"
            )
    else:
        lines.append(_t("No audited leakage finding was recorded as cleared by exclusion."))
    uncleared_warnings = [
        item for item in report.leakage_dispositions if not item.blocking and not item.cleared
    ]
    if uncleared_warnings:
        lines.extend(["", _t("Non-blocking leakage-audit warnings remain:"), ""])
        lines.extend(
            f"- `{item.column}` — {item.kind.value} ({_t('score')} {_number(item.score)})."
            for item in uncleared_warnings
        )

    lines.extend(["", f"## {_t('Decision provenance')}", ""])
    for authority in DecisionAuthority:
        matching = [item for item in report.decisions if item.authority is authority]
        if not matching:
            continue
        label = {
            DecisionAuthority.HUMAN: _t("Human-approved"),
            DecisionAuthority.AUTONOMOUS: _t("Autonomous"),
            DecisionAuthority.UNRECORDED: _t("Approval not recorded"),
        }[authority]
        lines.extend([f"### {label}", ""])
        for decision in matching:
            rationale = f" {decision.rationale}" if decision.rationale else ""
            lines.append(f"- **{decision.stage}:** {decision.decision}{rationale}")
        lines.append("")

    other_alerts = [item for item in report.alerts if item not in report.prominent_alerts]
    if other_alerts:
        lines.extend([f"## {_t('Other gate outcomes')}", ""])
        lines.extend(
            f"- **{item.reason_code}** ({item.verdict.value}, `{item.stage_id}`): {item.detail}"
            for item in other_alerts
        )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


__all__ = ["render_markdown"]
