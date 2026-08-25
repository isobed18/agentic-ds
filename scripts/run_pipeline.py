"""End-to-end run: raw files -> ranked ML problem candidates.

Covers stages 0-2 of the MVP pipeline:

    0. Intake & profiling          (deterministic)
    1. Schema discovery            (agent, validated against measurements)
    2. Integration                 (deterministic, grain-verified)
    3. Problem discovery           (agent proposes, Python measures support)

Everything an agent asserts is checked against something Python measured. The
output is the candidate set a human would see at the stage-2 gate.

Usage:
    python scripts/run_pipeline.py --intent "predict physician income"
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from ads.agents import problem_discovery as pd_agent
from ads.agents import run_agent
from ads.agents import schema_discovery as sd_agent
from ads.agents import validation_strategy as vs_agent
from ads.contracts import IntegrationPlan, TaskType, ValidationStrategy
from ads.contracts.gates import BUILTIN_PROFILES, QualitySignals
from ads.discovery import (
    audit_leakage,
    compute_support,
    detect_validation_signals,
    leakage_digest,
    recommend_strategy,
)
from ads.gates import GatePolicy, StageHistory, evaluate_gate
from ads.intake import (
    LoadedTable,
    detect_relationships,
    load_directory,
    profile_table,
    profile_tables,
)
from ads.integration import execute_plan
from ads.llm.client import LARGE, ModelProfile, OllamaClient
from ads.store import ArtifactStore


def _banner(text: str) -> None:
    print("\n" + "=" * 78)
    print(text)
    print("=" * 78)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/sample"))
    parser.add_argument("--artifacts", type=Path, default=Path("data/artifacts"))
    parser.add_argument("--model", default=LARGE.name)
    parser.add_argument("--intent", default=None, help="Optional stated user goal.")
    parser.add_argument(
        "--profile",
        default="checkpointed",
        choices=sorted(BUILTIN_PROFILES),
        help="Autonomy profile governing when the run stops for a human.",
    )
    parser.add_argument("--run-id", default=f"run_{int(time.time())}")
    args = parser.parse_args()

    client = OllamaClient()
    if not client.is_available():
        raise SystemExit("Ollama is not responding at http://localhost:11434")

    profile = ModelProfile(name=args.model)
    store = ArtifactStore(args.artifacts)
    run_id = args.run_id

    # ---------------------------------------------------------------- stage 0
    _banner("STAGE 0  Intake & profiling  (deterministic)")
    tables = load_directory(args.data)
    cards = profile_tables(tables)
    frames = {t.name: t.frame for t in tables}
    for card in cards:
        store.put(card, run_id=run_id, stage_exec_id="s0", name=card.table_name)
        flagged = sum(1 for i in card.issues if i.severity in ("warn", "error"))
        print(f"  {card.table_name:34s} {card.n_rows:>7,} x {card.n_columns:<3} issues={flagged}")

    relationships = detect_relationships(cards, frames)
    print(f"  measured {len(relationships)} candidate relationship(s)")

    # ---------------------------------------------------------------- stage 1
    _banner("STAGE 1  Schema discovery  (agent)")
    spec = sd_agent.build_spec()
    spec = type(spec)(
        id=spec.id,
        system_prompt=spec.system_prompt,
        output_contract=spec.output_contract,
        profile=profile,
        validators=spec.validators,
        max_attempts=spec.max_attempts,
        rubric=spec.rubric,
        column_fields=spec.column_fields,
    )
    context = sd_agent.build_context_with_evidence(cards, relationships)
    result = run_agent(spec, context, client)
    print(
        f"  attempts={result.n_attempts} first_pass={result.first_pass_valid} "
        f"latency={result.total_latency_s:.1f}s"
    )
    for attempt in result.attempts:
        for failure in attempt.failures:
            print(f"    [{attempt.attempt}] {failure.code}: {failure.detail[:110]}")
    proposal = result.require()

    plan = IntegrationPlan.from_proposal(proposal, evidence=relationships)
    store.put(plan, run_id=run_id, stage_exec_id="s1", name="integration_plan")
    print(
        f"  base={plan.base_table} grain={plan.base_grain} "
        f"joins={len(plan.joins)} aggregations={len(plan.aggregations)}"
    )
    for warning in plan.warnings:
        print(f"  warning: {warning[:150]}")

    # ---------------------------------------------------------------- stage 2
    _banner("STAGE 2  Integration  (deterministic, grain-verified)")
    integration = execute_plan(proposal, frames)
    abt = integration.frame
    print(
        f"  ABT: {integration.result_rows:,} rows x {len(abt.columns)} cols "
        f"(base was {integration.base_rows:,})"
    )
    print(f"  grain preserved: {integration.grain_preserved}")
    for warning in integration.warnings:
        print(f"  warning: {warning}")

    abt_card = profile_table(
        LoadedTable(name="abt", frame=abt, source_uri="derived", source_format="duckdb")
    )
    store.put(abt_card, run_id=run_id, stage_exec_id="s2", name="abt")

    # ---------------------------------------------------------------- stage 3
    _banner("STAGE 3  Problem discovery  (agent proposes, Python measures)")
    spec = pd_agent.build_spec()
    spec = type(spec)(
        id=spec.id,
        system_prompt=spec.system_prompt,
        output_contract=spec.output_contract,
        profile=profile,
        validators=spec.validators,
        max_attempts=spec.max_attempts,
        rubric=spec.rubric,
        column_fields=spec.column_fields,
    )
    context = pd_agent.build_context(abt_card, user_intent=args.intent)
    result = run_agent(spec, context, client)
    print(
        f"  attempts={result.n_attempts} first_pass={result.first_pass_valid} "
        f"latency={result.total_latency_s:.1f}s"
    )
    for attempt in result.attempts:
        for failure in attempt.failures:
            print(f"    [{attempt.attempt}] {failure.code}: {failure.detail[:110]}")

    candidate_set = pd_agent.attach_support(
        result.require(), abt_card, abt, user_intent=args.intent
    )
    store.put(candidate_set, run_id=run_id, stage_exec_id="s3", name="problem_candidates")

    # ------------------------------------------------------------------ gate
    _banner("GATE EVALUATION  (deterministic, no LLM)")
    policy = GatePolicy.load()
    autonomy = BUILTIN_PROFILES[args.profile]

    # Signals are measured, never self-reported. `n_viable` drives statistical
    # support; validation_failures come from the agent's own retry record.
    best = candidate_set.viable()[0] if candidate_set.viable() else None
    signals = QualitySignals(
        n_rows=best.support.n_rows if best else 0,
        minority_class_count=best.support.minority_class_count if best else None,
        rows_per_feature=best.support.rows_per_feature if best else None,
        validation_failures=sum(len(a.failures) for a in result.attempts),
        self_consistency_agreement=1.0 if result.first_pass_valid else None,
    )

    decision = evaluate_gate(
        stage=policy.stage("problem_discovery"),
        signals=signals,
        profile=autonomy,
        policy=policy,
        history=StageHistory(attempts=result.n_attempts),
        artifact_ids=[
            ref.artifact_id for ref in store.list(run_id) if ref.name == "problem_candidates"
        ],
        context_summary=(
            f"{len(candidate_set.viable())} of {len(candidate_set.candidates)} proposed "
            f"problems are statistically supported."
        ),
    )
    store.put(decision, run_id=run_id, stage_exec_id="s3", name="gate_problem_discovery")

    print(f"  profile        : {autonomy.name}")
    print(f"  verdict        : {decision.verdict.value.upper()}")
    print(f"  reason_code    : {decision.reason_code}")
    print(f"  triggered_rules: {decision.triggered_rules or '(none)'}")

    _banner("CANDIDATES FOR REVIEW")
    for i, candidate in enumerate(candidate_set.candidates, 1):
        mark = "VIABLE  " if candidate.support.is_viable else "BLOCKED "
        print(f"\n[{i}] {mark} {candidate.title}")
        print(
            f"      task={candidate.task_type.value}  target={candidate.target_column}  "
            f"metric={candidate.primary_metric.value}"
        )
        print(f"      why: {candidate.business_rationale[:150]}")
        support = candidate.support
        facts = [f"rows={support.n_rows:,}", f"features={support.n_usable_features}"]
        if support.minority_class_count is not None:
            facts.append(
                f"minority={support.minority_class_count:,} ({support.minority_class_rate:.2%})"
            )
        print(f"      measured: {'  '.join(facts)}")
        for reason in support.blocking_reasons:
            print(f"      BLOCKED: {reason[:160]}")
        for warning in support.warnings:
            print(f"      warn: {warning[:160]}")

    # --------------------------------------------------------------- stage 4
    # In a real run the human picks here. Autonomous mode takes the top viable
    # candidate so the rest of the pipeline can be exercised end to end; the
    # gate decision above records that a human was owed a say.
    chosen = candidate_set.viable()[0] if candidate_set.viable() else None
    if chosen is None:
        _banner("STOPPED  no statistically supported problem to pursue")
        return

    _banner(f"STAGE 4  Validation strategy  (assuming candidate: {chosen.title})")
    signals_v = detect_validation_signals(
        abt_card, abt, target_column=chosen.target_column, task_type=chosen.task_type
    )
    recommended = recommend_strategy(signals_v)
    print(f"  deterministic minimum: {recommended.value}")
    for entity in signals_v.repeated_entity_keys:
        print(
            f"  repeated entity      : {entity.column} "
            f"({entity.n_entities:,} entities, {entity.rows_per_entity:.1f} rows each)"
        )
    for span in signals_v.temporal_spans:
        print(
            f"  temporal span        : {span.column} "
            f"{span.min_date[:10]}..{span.max_date[:10]} ({span.span_days:,}d)"
        )

    spec = vs_agent.build_spec()
    spec = type(spec)(
        id=spec.id,
        system_prompt=spec.system_prompt,
        output_contract=spec.output_contract,
        profile=profile,
        validators=spec.validators,
        max_attempts=spec.max_attempts,
        rubric=spec.rubric,
        column_fields=spec.column_fields,
    )
    result = run_agent(spec, vs_agent.build_context(abt_card, signals_v), client)
    print(
        f"  attempts={result.n_attempts} first_pass={result.first_pass_valid} "
        f"latency={result.total_latency_s:.1f}s"
    )
    for attempt in result.attempts:
        for failure in attempt.failures:
            print(f"    [{attempt.attempt}] {failure.code}: {failure.detail[:110]}")

    strategy = ValidationStrategy.from_proposal(result.require(), signals_v)
    store.put(strategy, run_id=run_id, stage_exec_id="s4", name="validation_strategy")
    print(
        f"  chosen               : {strategy.strategy.value} "
        f"group={strategy.group_column} time={strategy.time_column} "
        f"cutoff={strategy.holdout_cutoff}"
    )
    print(f"  why                  : {strategy.rationale[:150]}")

    # --------------------------------------------------------------- stage 7
    _banner("STAGE 7  Leakage audit  (deterministic, blocking)")
    report = audit_leakage(
        abt_card,
        abt,
        target_column=chosen.target_column,
        task_type=chosen.task_type,
        validation_strategy=strategy,
        integration_plan=proposal,
    )
    store.put(report, run_id=run_id, stage_exec_id="s7", name="leakage_report")
    print(leakage_digest(report))

    leak_decision = evaluate_gate(
        stage=policy.stage("leakage_audit"),
        signals=report.to_quality_signals(),
        profile=autonomy,
        policy=policy,
        history=StageHistory(attempts=1),
        context_summary=f"{len(report.blocking_findings)} blocking leakage finding(s).",
    )
    store.put(leak_decision, run_id=run_id, stage_exec_id="s7", name="gate_leakage")
    print(f"\n  verdict        : {leak_decision.verdict.value.upper()}")
    print(f"  reason_code    : {leak_decision.reason_code}")
    for instruction in leak_decision.correction_instructions[:6]:
        print(f"    -> {instruction}")

    if decision.human_prompt is not None:
        prompt = decision.human_prompt
        _banner("RUN PAUSED  awaiting human input")
        print(f"  {prompt.question}\n")
        print("  " + prompt.context_summary.replace("\n", "\n  "))
        print("\n  Options:")
        for option in prompt.options:
            mark = " (recommended)" if option.recommended else ""
            print(f"    [{option.option_id}]{mark} {option.label}")
            print(f"        {option.consequence}")
            if option.downstream_effect:
                print(f"        effect: {option.downstream_effect}")
        print(f"\n  free text allowed: {prompt.allows_free_text}")
        print(f"  on timeout       : {prompt.timeout_behavior} (never auto-decides)")

    _banner("SUMMARY")
    print(f"  run_id      : {run_id}")
    print(f"  artifacts   : {len(store.list(run_id))} stored in {args.artifacts}")
    print(f"  viable      : {len(candidate_set.viable())}/{len(candidate_set.candidates)}")

    # The deterministic layer holds regardless of what the agent proposed: check
    # a known-weak framing directly, so its rejection is visible even if the
    # agent never suggested it.
    if "flagged" in abt.columns:
        flagged = compute_support(
            abt_card,
            abt,
            target_column="flagged",
            task_type=TaskType.BINARY_CLASSIFICATION,
        )
        if not flagged.is_viable:
            print(
                f"  note        : 'flagged' checked independently -> "
                f"{flagged.blocking_reasons[0][:100]}"
            )


if __name__ == "__main__":
    main()
