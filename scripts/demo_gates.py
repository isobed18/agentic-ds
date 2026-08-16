"""Show how the Gate Evaluator decides, across scenarios and autonomy profiles.

Runs in under a second: the gate is a pure function, so no model, no data and no
run state are needed. Useful for reviewing policy changes before a real run.

Usage:
    python scripts/demo_gates.py
"""

from __future__ import annotations

from ads.contracts.gates import BUILTIN_PROFILES, GateVerdict, PermissionTier, QualitySignals
from ads.gates import GatePolicy, StageHistory, evaluate_gate

POLICY = GatePolicy.load()

SCENARIOS: list[tuple[str, str, QualitySignals]] = [
    ("clean EDA run", "eda", QualitySignals(self_consistency_agreement=1.0)),
    (
        "leakage: total_comp_ytd @ 0.98",
        "feature_pipeline",
        QualitySignals(max_target_correlation=0.98, leakage_suspect_columns=["total_comp_ytd"]),
    ),
    ("PII would enter context", "eda", QualitySignals(pii_columns_in_context=3)),
    (
        "source-mutating tool requested",
        "feature_pipeline",
        QualitySignals(requested_tool_tier=PermissionTier.MUTATE_SOURCE),
    ),
    (
        "model below baseline",
        "evaluation",
        QualitySignals(best_score=0.61, naive_baseline_score=0.63),
    ),
    ("unstable CV (38% spread)", "evaluation", QualitySignals(cv_mean=0.8, cv_std=0.30)),
    (
        "ambiguous decision (40% agreement)",
        "problem_discovery",
        QualitySignals(self_consistency_agreement=0.4),
    ),
    (
        "fraud label: 39 positives",
        "model_selection",
        QualitySignals(minority_class_count=39, n_rows=15_000),
    ),
    (
        "correct split, 5-row folds",
        "validation_strategy",
        QualitySignals(split_retained_rate=0.206, min_validation_fold_size=5),
    ),
    ("healthy model", "evaluation", QualitySignals(best_score=0.88, naive_baseline_score=0.63)),
]

PROFILES = ["supervised", "checkpointed", "autonomous_with_guardrails", "full_auto"]
SYMBOL = {
    GateVerdict.AUTO_PROCEED: "proceed",
    GateVerdict.RETRY: "RETRY",
    GateVerdict.ESCALATE: "ASK HUMAN",
    GateVerdict.ABORT: "ABORT",
}


def main() -> None:
    width = max(len(name) for name, _, _ in SCENARIOS) + 2
    header = "scenario".ljust(width) + "".join(p[:13].ljust(15) for p in PROFILES)
    print(header)
    print("-" * len(header))

    for name, stage_id, signals in SCENARIOS:
        row = name.ljust(width)
        for profile_name in PROFILES:
            decision = evaluate_gate(
                stage=POLICY.stage(stage_id),
                signals=signals,
                profile=BUILTIN_PROFILES[profile_name],
                policy=POLICY,
                history=StageHistory(attempts=1),
            )
            row += SYMBOL[decision.verdict].ljust(15)
        print(row)

    print(
        "\nNote: the four leftmost-stopping rows are hard constraints. They fire in "
        "every profile,\nincluding full_auto, because a user preference must not be "
        "able to switch off\nleakage or PII protection on medical and financial data."
    )

    print("\n\nWHY EACH DECISION WAS MADE (checkpointed profile)")
    print("-" * 78)
    for name, stage_id, signals in SCENARIOS:
        decision = evaluate_gate(
            stage=POLICY.stage(stage_id),
            signals=signals,
            profile=BUILTIN_PROFILES["checkpointed"],
            policy=POLICY,
            history=StageHistory(attempts=1),
        )
        rules = ", ".join(decision.triggered_rules) or "-"
        print(f"  {name:<38} {decision.reason_code:<28} [{rules}]")


if __name__ == "__main__":
    main()
