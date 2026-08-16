"""Run SchemaDiscoveryAgent against a local model and measure contract validity.

This is the Phase 1 spike from the architecture report, folded into normal work:
the number that matters is **first-pass validity** — how often the model produces
a schema-valid, semantically-consistent IntegrationPlan with no repair and no
retry. If that number is high on a 27B dev model, the typed-contract
architecture is safe on the larger production model.

Usage:
    python scripts/run_schema_discovery.py --runs 3
"""

from __future__ import annotations

import argparse
import statistics
from pathlib import Path

from ads.agents.base import run_agent
from ads.agents.schema_discovery import build_context_with_evidence, build_spec
from ads.intake import detect_relationships, load_directory, profile_tables
from ads.llm.client import LARGE, ModelProfile, OllamaClient


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/sample"))
    parser.add_argument("--model", default=LARGE.name)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--temperature", type=float, default=0.0)
    args = parser.parse_args()

    client = OllamaClient()
    if not client.is_available():
        raise SystemExit("Ollama is not responding at http://localhost:11434")

    tables = load_directory(args.data)
    cards = profile_tables(tables)
    frames = {t.name: t.frame for t in tables}
    relationships = detect_relationships(cards, frames)
    context = build_context_with_evidence(cards, relationships)

    spec = build_spec()
    profile = ModelProfile(name=args.model, temperature=args.temperature)
    spec = type(spec)(
        id=spec.id,
        system_prompt=spec.system_prompt,
        output_contract=spec.output_contract,
        profile=profile,
        validators=spec.validators,
        max_attempts=spec.max_attempts,
        rubric=spec.rubric,
    )

    print(f"model={args.model} temperature={args.temperature} runs={args.runs}")
    print(f"context={context.estimated_tokens():,} est. tokens, "
          f"{len(cards)} tables, {len(relationships)} measured relationships\n")

    first_pass = 0
    succeeded = 0
    latencies: list[float] = []
    plans = []

    for run_no in range(1, args.runs + 1):
        result = run_agent(spec, context, client)
        latencies.append(result.total_latency_s)
        succeeded += int(result.succeeded)
        first_pass += int(result.first_pass_valid)

        status = "OK " if result.succeeded else "FAIL"
        print(f"[run {run_no}] {status} attempts={result.n_attempts} "
              f"first_pass={result.first_pass_valid} "
              f"latency={result.total_latency_s:.1f}s")

        for attempt in result.attempts:
            for repair in attempt.repairs:
                print(f"    repaired: {repair}")
            for failure in attempt.failures:
                print(f"    [{attempt.attempt}] {failure.code}: {failure.detail[:130]}")

        if result.output is not None:
            plans.append(result.output)
            plan = result.output
            print(f"    base={plan.base_table} grain={plan.base_grain} "
                  f"joins={len(plan.joins)} aggs={len(plan.aggregations)}")

    print("\n" + "=" * 70)
    print(f"first-pass validity : {first_pass}/{args.runs}")
    print(f"eventual success    : {succeeded}/{args.runs}")
    print(f"median latency      : {statistics.median(latencies):.1f}s")

    if plans:
        print("\nBEST PLAN")
        print("=" * 70)
        plan = plans[0]
        print(f"base_table : {plan.base_table}")
        print(f"base_grain : {plan.base_grain}")
        print(f"grain      : {plan.grain_description}")
        for agg in plan.aggregations:
            print(f"aggregate  : {agg.source_table} GROUP BY {agg.group_by}")
            for out_col, expr in agg.aggregations.items():
                print(f"             {out_col} = {expr}")
            print(f"             why: {agg.rationale[:110]}")
        for join in plan.joins:
            print(f"join       : {join.left_table}.{join.left_columns} "
                  f"{join.how.upper()} {join.right_table}.{join.right_columns}")
            print(f"             why: {join.rationale[:110]}")
        for warning in plan.warnings:
            print(f"warning    : {warning}")


if __name__ == "__main__":
    main()
