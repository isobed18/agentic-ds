# Agentic Data Science / ML Pipeline

Local, self-hosted agentic pipeline that takes messy multi-table enterprise data to a
baseline ML project. No cloud LLM APIs, no cloud services.

See [docs/architecture-report.md](docs/architecture-report.md) for the full architecture
and technology evaluation.

## Status

The full 11-stage pipeline runs end-to-end on a local 27B model: raw multi-table files
through to a trained, evaluated model and a standalone reproduction script, pausing for a
human wherever a decision cannot be made safely without one.

| Component | State |
|---|---|
| Typed contracts (`ads.contracts`) — 17 artifact types | done |
| Content-addressed artifact store (`ads.store`) | done |
| Intake loaders — CSV/Excel/Parquet (`ads.intake`) | done |
| DataCard profiler + PII redaction + text shape | done |
| Key & relationship detection | done |
| Local LLM client, constrained decoding (`ads.llm`) | done |
| Agent base: 4-layer validation, auto-repair, corrective retry | done |
| SchemaDiscoveryAgent · ProblemDiscoveryAgent · ValidationStrategyAgent | done |
| Integration executor — DuckDB, grain-verified (`ads.integration`) | done |
| Gate Evaluator + policy engine (`ads.gates`) — 15 rules, 4 tiers | done |
| Leakage audit (`ads.discovery`) — 7 independent families | done |
| Validation signals + containment matrix | done |
| Splitting, training, evaluation, reporting | done |
| Tool registry + permission broker (`ads.tools`) | done |
| Isolated Docker sandbox (`ads.sandbox`) | done |
| Comprehension layer — cited interpretations (`ads.agents.interpretation`) | in progress |
| Assurance benchmark (`benchmarks/assurance_v0`) — 20 paired cases | corpus built, baseline pending |

**Measured:** 588 tests pass with no GPU, model or network. A completed run emits a
standalone `train.py` that reproduces the recorded holdout metric exactly in a fresh
interpreter (`25898.5376005` against a recorded `-25898.5376004928`).

Model routing is configured in `ads/llm/client.py`: `LARGE` for planning and code
generation, `SMALL` for classification and extraction. Point `LARGE` at a 70B-class
model for production; nothing above that line changes.

## Setup

```bash
python -m pip install uv
```

```bash
python -m uv venv .venv
```

```bash
python -m uv pip install --python .venv/Scripts/python.exe -e ".[dev]"
```

## Try it

Generate the synthetic messy dataset (physicians / ledger / transactions):

```bash
.venv/Scripts/python.exe scripts/make_sample_data.py
```

Run the deterministic intake spine and print what an agent would see:

```bash
.venv/Scripts/python.exe scripts/smoke_intake.py
```

Run the full pipeline, T0 → ranked ML problem candidates (needs Ollama serving):

```bash
.venv/Scripts/python.exe scripts/run_pipeline.py --intent "predict physician income"
```

Measure first-pass contract validity for one agent:

```bash
.venv/Scripts/python.exe scripts/run_schema_discovery.py --runs 3
```

Run the tests (no GPU, model or network required):

```bash
.venv/Scripts/python.exe -m pytest
```

## Design rules this code enforces

These are the invariants worth preserving as the system grows. Each is covered by tests.

1. **Agents see DataCards, never raw rows.** A 20k-row table becomes ~600 tokens of
   schema and statistics. Solves token cost, arithmetic accuracy and PII exposure at once.
2. **PII never enters LLM context.** Columns classified as sensitive contribute no sample
   values and no top-values, regardless of caller settings.
3. **Artifacts are immutable and content-addressed.** Identical output yields an identical
   id, which makes stage re-execution idempotent and fork/time-travel cheap.
4. **Deterministic work stays deterministic.** Parsing, profiling, key detection and
   leakage auditing contain no LLM calls — a model there would only add errors.
5. **Messiness is surfaced, not repaired silently.** Every inferred header row, dropped
   column and renamed duplicate is recorded as a `LoadIssue` for the human gate.
6. **Contracts reject unknown fields.** An LLM inventing a key fails validation instead of
   having it silently carried downstream.
7. **Agents author judgment, never measurements.** The agent emits an
   `IntegrationPlanProposal`; the system attaches measured `evidence` to build the
   `IntegrationPlan`. Asking a model to re-emit numbers it was given wastes tokens and
   invites corruption.
8. **The deterministic layer has veto power.** `validate_joins_supported_by_evidence`
   rejects any join not backed by a measurement, so the LLM cannot assert a relationship
   into existence.
9. **Auto-repair is explicit and narrow.** Only fields declared in `AgentSpec.column_fields`
   are fuzzy-matched, and only above a 0.85 similarity threshold — rewriting a prose field
   or a genuinely different name would be worse than failing.
10. **The LLM never decides when to ask the human.** `ads.gates.evaluate_gate` is a pure
    function of measured signals, stage risk class, retry history and the autonomy profile.
    It is deterministic, names every rule that fired, and the model never sees it.
11. **User autonomy is the weakest input, not the strongest.** Rule tiers run
    hard → risk → signal → profile. `full_auto` still stops for leakage, PII egress and
    source-mutating tools; a preference cannot switch off safety on medical or financial data.
12. **A procedural stop never masks a substantive one.** All rules are evaluated; precedence
    picks the verdict, but the headline reason prefers a real finding ("model below baseline")
    over a policy one ("this stage is high risk").

## Layout

```
src/ads/
  contracts/     Typed inter-stage contracts — the architecture's backbone
  store/         Content-addressed immutable artifact store
  intake/        Loaders, profiler, key/relationship detection
  discovery/     Leakage families, problem support, validation signals, measurements
  integration/   DuckDB plan executor with grain verification
  eda/           Deterministic distribution, correlation, outlier, shape descriptors
  splitting/     Split strategies and their execution
  training/      Candidate search over sklearn, model persistence
  reporting/     Final report assembly
  gates/         Rule tiers, policy engine, decision matrix
  orchestration/ Workflow spec, runner, run state, critic
  pipeline/      Stage definitions and the agent-backed workflow
  agents/        Agent base + specialized agents + interpretation
  tools/         Tool registry, permission broker, deterministic DS tools
  sandbox/       Isolated Docker Jupyter kernel
  llm/           Local model client + grammar-constrained structured output
  api/           FastAPI control plane and measurement-to-panel presentation
web/             React control-plane frontend (built into src/ads/api/static)
benchmarks/      Assurance corpus: paired safe/mutated cases with hidden oracles
scripts/         CLI entry points
tests/           pytest suite
docs/            Architecture and implementation reports
```
