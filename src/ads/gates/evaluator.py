"""The Gate Evaluator — deterministic, auditable, unbypassable.

This is the answer to the report's central question: *how should the Orchestrator
decide when human input is required, without relying only on a system prompt?*

:func:`evaluate_gate` is a pure function. Given the same stage spec, critique,
signals, profile and history it always returns the same verdict, and it names the
exact rules that fired. The LLM is never asked whether to consult the human, and
never sees the rules.
"""

from __future__ import annotations

from dataclasses import replace

from ads.contracts.gates import (
    AutonomyProfile,
    CritiqueResult,
    DecisionOption,
    GateDecision,
    GateVerdict,
    HumanPrompt,
    QualitySignals,
)
from ads.gates.policy import GatePolicy, StageHistory, StageSpec
from ads.gates.rules import (
    RuleContext,
    RuleOutcome,
    Tier,
    applicable_rules,
    detect_failure,
)

_VERDICT_RANK = {
    GateVerdict.AUTO_PROCEED: 0,
    GateVerdict.RETRY: 1,
    GateVerdict.ESCALATE: 2,
    GateVerdict.ABORT: 3,
}


def evaluate_gate(
    *,
    stage: StageSpec,
    signals: QualitySignals,
    profile: AutonomyProfile,
    history: StageHistory | None = None,
    critique: CritiqueResult | None = None,
    policy: GatePolicy | None = None,
    artifact_ids: list[str] | None = None,
    missing_required_artifacts: list[str] | None = None,
    context_summary: str = "",
) -> GateDecision:
    """Decide what happens after a stage completes.

    Rules are evaluated in tier order and the **first tier that produces any
    outcome wins**. Within a tier, the most severe verdict is taken and every
    rule that fired is recorded, so the audit trail shows the full picture rather
    than only the winning rule.
    """
    policy = policy or GatePolicy()
    history = history or StageHistory()
    context = RuleContext(
        stage=stage,
        critique=critique,
        signals=signals,
        profile=profile,
        history=history,
        policy=policy,
    )

    # Two passes. The first asks "did anything actually go wrong?" across the
    # *full* rule set, so the hard retry-budget rule cannot be silently disabled
    # by a profile opting out of the signal that failed.
    context = replace(context, failure_detected=detect_failure(context))

    # Evaluate every applicable rule, not just up to the first tier that fires.
    # Precedence decides the *verdict*; the audit trail keeps everything, so a
    # procedural stop can never hide a substantive finding underneath it.
    fired: list[tuple[Tier, str, RuleOutcome]] = []
    for rule in applicable_rules(profile):
        outcome = rule(context)
        if outcome is not None:
            fired.append((rule.tier, rule.id, outcome))

    if not fired:
        return GateDecision(
            stage_id=stage.id,
            attempt=history.attempts,
            verdict=GateVerdict.AUTO_PROCEED,
            reason_code="no_rule_triggered",
            triggered_rules=[],
        )

    fired.sort(key=lambda item: (item[0], -_VERDICT_RANK[item[2].verdict]))

    # The verdict comes from the highest tier that fired, most severe within it,
    # so hard constraints retain authority over the action taken.
    top_tier = fired[0][0]
    in_top_tier = [item for item in fired if item[0] is top_tier]
    _, _, winner = max(in_top_tier, key=lambda item: _VERDICT_RANK[item[2].verdict])

    # The headline reason prefers a substantive finding over a procedural one:
    # "your model is below baseline" is more useful than "this stage is high risk".
    substantive = [
        outcome for _, _, outcome in fired if outcome.reason_code not in POLICY_ONLY_REASONS
    ]
    headline = substantive[0] if substantive else winner

    prompt = None
    if winner.verdict in (GateVerdict.ESCALATE, GateVerdict.ABORT):
        prompt = build_human_prompt(
            stage=stage,
            outcomes=[outcome for _, _, outcome in fired],
            artifact_ids=artifact_ids or [],
            missing_required_artifacts=missing_required_artifacts or [],
            context_summary=context_summary or headline.message,
        )

    return GateDecision(
        stage_id=stage.id,
        attempt=history.attempts,
        verdict=winner.verdict,
        reason_code=headline.reason_code,
        triggered_rules=[rule_id for _, rule_id, _ in fired],
        human_prompt=prompt,
        correction_instructions=[
            instruction for _, _, outcome in fired for instruction in outcome.instructions
        ],
    )


#: Reasons that mean "policy says ask", not "something is wrong". When a stop is
#: purely one of these, the system must not steer the answer.
POLICY_ONLY_REASONS = frozenset(
    {"risk_class_gate", "profile_checkpoint", "profile_requires_confirmation"}
)


def build_human_prompt(
    *,
    stage: StageSpec,
    outcomes: list[RuleOutcome],
    artifact_ids: list[str],
    missing_required_artifacts: list[str] | None = None,
    context_summary: str,
) -> HumanPrompt:
    """Turn an escalation into a typed question with pre-computed options.

    Pre-computing options is what lets a domain expert participate: they can
    choose between annotated consequences, but cannot usefully answer an
    open-ended "what should I do?".

    Recommendations are withheld when the stop is purely procedural. Suggesting
    "send back for rework" on a clean result is misleading, and nudging either
    way on a genuinely open decision defeats the point of asking.
    """
    reasons = "\n".join(f"- {outcome.message}" for outcome in outcomes)
    # Ordered, de-duplicated: multiple tiers can fire the same rule family (a
    # retry-budget rule and a stuck-loop rule can both name the same criteria),
    # and the card renders one line per code via the client's own reasonLabel
    # translation rather than this English `reasons` prose.
    reason_codes = list(dict.fromkeys(outcome.reason_code for outcome in outcomes))
    policy_only = {o.reason_code for o in outcomes} <= POLICY_ONLY_REASONS
    # #262: surface the leakage rule's own suspect-column data so the card can
    # offer checkboxes instead of asking for hand-typed `drop_feature:` syntax.
    leakage_outcome = next((o for o in outcomes if o.correction_columns), None)

    # Uretilmemis bir ciktiyi onaylamak MUMKUN degil: sonraki asama onu zorunlu
    # girdi olarak istiyor ve kosum `MissingArtifactError` ile patliyor. Olculen
    # vaka: problem_discovery `problem_definition` uretemedi, "Approve and
    # continue" yine de sunuldu, secilince kosum "run '...' has no
    # 'problem_definition' artifact" diye durdu (#198).
    #
    # Kural #191 ile ayni: kosumun kabul etmeyecegi bir secenek ekranda
    # durmamali. Burada bilinen sey artifact_ids'in BOS olmasi -- asama hicbir
    # sey uretmemis demek; kismi cikti bu kontrolden gecer, cunku hangi
    # artifactin zorunlu oldugunu bilen yer burasi degil.
    produced_nothing = not artifact_ids
    missing_required_artifacts = list(missing_required_artifacts or [])
    options = []
    if not produced_nothing and not missing_required_artifacts:
        options.append(
            DecisionOption(
                option_id="approve",
                label="Approve and continue",
                consequence="Accept this stage's output as-is and proceed to the next stage.",
                recommended=False,
            )
        )
    options += [
        DecisionOption(
            option_id="retry",
            label="Send back for rework",
            consequence="Re-run this stage with your instructions attached.",
            downstream_effect=f"Re-runs {stage.id} and everything after it.",
            # Only recommended when something actually went wrong.
            recommended=not policy_only,
        ),
        DecisionOption(
            option_id="abort",
            label="Stop the run",
            consequence="Halt here. Completed artifacts are kept and the run can be resumed.",
        ),
    ]
    if stage.irreversible:
        options.insert(
            0,
            DecisionOption(
                option_id="review_first",
                label="Inspect artifacts before deciding",
                consequence="Pause without committing. This stage discards work that "
                "cannot be rebuilt automatically.",
                recommended=True,
            ),
        )

    if missing_required_artifacts:
        question = (
            f"Stage {stage.id!r} did not produce output required by the next stage "
            f"({', '.join(missing_required_artifacts)}). Send it back for rework, or stop "
            "the run."
        )
    elif produced_nothing:
        question = (
            f"Stage {stage.id!r} produced nothing, so there is no output to approve. "
            "Send it back for rework, or stop the run."
        )
    elif policy_only:
        question = (
            f"Stage {stage.id!r} is a required checkpoint. "
            "Review the output and choose how to proceed."
        )
    else:
        question = (
            f"Stage {stage.id!r} stopped because a problem was detected. Your decision is needed."
        )

    return HumanPrompt(
        stage_id=stage.id,
        question=question,
        question_kind=(
            "missing_output"
            if missing_required_artifacts
            else "no_output"
            if produced_nothing
            else "checkpoint"
            if policy_only
            else "problem"
        ),
        missing_artifact_types=missing_required_artifacts,
        context_summary=f"{context_summary}\n\nWhy this stopped:\n{reasons}"[:1500],
        context_note=context_summary[:500],
        reason_codes=reason_codes,
        leakage_suspect_columns=(
            list(leakage_outcome.correction_columns) if leakage_outcome else []
        ),
        leakage_target_columns=(
            sorted(leakage_outcome.target_leakage_columns) if leakage_outcome else []
        ),
        options=options,
        allows_free_text=True,
        artifacts_to_review=artifact_ids,
        default_option=None,  # never auto-decide an escalation
        timeout_behavior="wait",
    )


__all__ = ["build_human_prompt", "evaluate_gate"]
