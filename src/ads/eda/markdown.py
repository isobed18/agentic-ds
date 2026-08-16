"""Render deterministic EDA measurements as human-facing Markdown."""

from __future__ import annotations

from ads.contracts.eda import DistributionKind, EDAReport


def _number(value: float | None) -> str:
    return "—" if value is None else f"{value:,.6g}"


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _target_section(report: EDAReport) -> list[str]:
    distribution = report.target_distribution
    if distribution is None:
        return ["## Target distribution", "", "No target is defined for this task.", ""]

    lines = [
        "## Target distribution",
        "",
        f"Target: `{distribution.target_column}`; observed "
        f"{distribution.non_null_count:,} of {distribution.total_count:,} rows "
        f"({distribution.null_rate:.2%} missing).",
        "",
    ]
    if distribution.kind is DistributionKind.CLASS_COUNTS:
        lines.extend(
            [
                "| Class | Count | Rate |",
                "|---|---:|---:|",
                *(
                    f"| {_cell(item.value)} | {item.count:,} | {item.rate:.2%} |"
                    for item in distribution.values
                ),
                "",
            ]
        )
        if report.class_balance is not None:
            balance = report.class_balance
            lines.extend(
                [
                    f"Minority class `{_cell(balance.minority_class)}` has "
                    f"{balance.minority_count:,} rows ({balance.minority_rate:.2%}); "
                    f"the majority-to-minority ratio is "
                    f"{balance.majority_to_minority_ratio:,.3g}:1.",
                    "",
                ]
            )
        return lines

    numeric = distribution.numeric
    if numeric is None:  # guarded by the contract
        return lines
    lines.extend(
        [
            "| Min | P25 | Median | Mean | P75 | Max | Std |",
            "|---:|---:|---:|---:|---:|---:|---:|",
            f"| {_number(numeric.min)} | {_number(numeric.p25)} | "
            f"{_number(numeric.p50)} | {_number(numeric.mean)} | "
            f"{_number(numeric.p75)} | {_number(numeric.max)} | "
            f"{_number(numeric.std)} |",
            "",
        ]
    )
    return lines


def _correlation_section(report: EDAReport) -> list[str]:
    matrix = report.correlation_matrix
    lines = ["## Pearson correlation matrix", ""]
    if not matrix.columns:
        return [*lines, "No numeric columns were available for correlation.", ""]
    lines.extend(
        [
            "| Column | " + " | ".join(f"`{name}`" for name in matrix.columns) + " |",
            "|---|" + "---:|" * len(matrix.columns),
        ]
    )
    for name, row in zip(matrix.columns, matrix.values, strict=True):
        lines.append(
            f"| `{name}` | " + " | ".join(_number(value) for value in row) + " |"
        )
    lines.append("")
    return lines


def render_markdown(report: EDAReport) -> str:
    """Render the EDA artifact without inferring facts from prose."""
    lines = [
        f"# EDA report: {report.problem_title}",
        "",
        f"Analyzed {report.row_count:,} rows and covered "
        f"{len(report.covered_columns):,} of {len(report.feature_columns):,} ABT features.",
        "",
        "## Rubric coverage",
        "",
    ]
    for criterion, passed in report.criterion_results().items():
        lines.append(f"- `{criterion}`: {'pass' if passed else 'fail'}")
    lines.append("")
    lines.extend(_target_section(report))

    lines.extend(
        [
            "## Missingness",
            "",
            "| Column | Missing rows | Missing rate |",
            "|---|---:|---:|",
        ]
    )
    lines.extend(
        f"| `{item.column}` | {item.null_count:,} | {item.null_rate:.2%} |"
        for item in report.missingness
    )
    lines.append("")
    lines.extend(_correlation_section(report))

    lines.extend(
        [
            "## Feature relationships with target",
            "",
        ]
    )
    if report.target_column is None:
        lines.extend(["No target is defined, so target relationships are not applicable.", ""])
    else:
        lines.extend(
            [
                "| Feature | Pearson correlation | Adjusted mutual information |",
                "|---|---:|---:|",
            ]
        )
        lines.extend(
            f"| `{item.column}` | {_number(item.pearson_correlation)} | "
            f"{_number(item.adjusted_mutual_information)} |"
            for item in report.target_relationships
        )
        lines.append("")

    lines.extend(["## IQR outlier counts", ""])
    if not report.outliers:
        lines.extend(["No numeric feature columns were available for outlier counting.", ""])
    else:
        lines.extend(
            [
                "| Feature | Lower fence | Upper fence | Evaluated | Outliers | Rate |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        lines.extend(
            f"| `{item.column}` | {_number(item.lower_fence)} | "
            f"{_number(item.upper_fence)} | {item.evaluated_count:,} | "
            f"{item.outlier_count:,} | {item.outlier_rate:.2%} |"
            for item in report.outliers
        )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


__all__ = ["render_markdown"]
