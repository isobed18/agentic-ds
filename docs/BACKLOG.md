# Build Backlog

This file is the interruption-safe source of truth for unfinished work. Update it
when priorities or evidence change; do not rely on conversational memory.

## Interruption point — 2026-08-17

Commit `a90dbf4` completed the shared investigator runtime policy. The tree was
clean. The accepted active-agent slices already committed before the interruption
were:

- isolated agent-authored EDA with a typed manifest (`d69c391`);
- schema/integration plan trials bound to executor-owned evidence (`c557699`);
- leakage challenges that narrow a human question but cannot clear a gate
  (`b6c85c7`);
- host-scored authored model experiments (`4a4002d`);
- a real skills loader and usage audit (`3392997`);
- behavioral proof that exploratory evidence cannot alter gates (`2968af5`);
- a first-class deterministic `FeatureSpec` stage (`70b09eb`);
- host-scored authored feature experiments (`c6f41f0`);
- active problem investigation (`2e7cc93`);
- validation-strategy trials bound to the exact final proposal (`af6a856`);
- configurable per-stage turns, transcript windows, tool-call budgets, and code
  execution without configurable safety ceilings (`a90dbf4`).

The final audit had just concluded that the active-agent roadmap was implemented
as scoped. No code was part-way edited when interrupted. The owner's newer wording
that the “real feature pipeline” is still unfinished therefore needs a capability
gap audit, not an assumption that the two existing feature commits satisfy the
product need.

## Remaining build order

### 1. Finish the real feature pipeline

Start by comparing the intended data-scientist workflow with what commits
`70b09eb` and `c6f41f0` actually provide.

What exists now:

- a deterministic, fold-local preprocessing floor represented by `FeatureSpec`;
- an isolated agent-authored experiment whose predictions are scored by the host;
- an exploratory evidence class that cannot change a gate or silently replace the
  mandatory feature floor.

What is likely still missing and must be resolved explicitly:

- a typed proposal for adopting, rejecting, or revising an engineered feature
  recipe rather than merely scoring a one-off experiment;
- deterministic re-execution of an accepted recipe on copies, per fold, with no
  source mutation;
- lineage from source columns through transformations to model inputs;
- availability/leakage checks for every derived feature;
- comparison against the mandatory floor with uncertainty, not a single score;
- a human decision boundary for promoting an exploratory recipe into the governed
  training path;
- persistence and replay of the exact accepted transformation code or declarative
  recipe;
- UI presentation through existing feature/EDA cards only, with interpretations
  attached to measurements and no free agent prose.

Before implementation, inspect the current contracts, workflow, gates, training
preparation, authored experiment manifest, and panels. Write counter-tests for the
actual promotion/exclusion consequences. Preserve: deterministic floor, host-owned
scores, `MUTATE_SOURCE` denial, copies only, no host-execution fallback, and human
authority wherever an exploratory recipe would affect the governed model.

### 2. Replace or retain commodity agent/execution infrastructure based on research

Use the research note produced in the current research-only run. If an external
executor is recommended, migrate behind the existing execution seam rather than
changing callers or evidence semantics.

The invariant-bearing layer remains ours:

- deterministic versus exploratory evidence classes;
- agent contracts that cannot author measurements;
- gate exclusion of agent-computed numbers;
- typed manifests as the only publish channel;
- fixed permission ceiling with `MUTATE_SOURCE` denied;
- no host fallback and no free UI channel.

Any borrowed layer must run air-gapped, avoid import-time telemetry, have justified
dependency weight, and expose enough lifecycle control for stateful sessions,
timeouts, resource limits, file ingress/egress, and cleanup. Record provenance and
licence for future bookkeeping, but licence is not an adoption gate in this phase.

Implementation status on 2026-08-18:

- Pydantic AI slim now owns turns, tool feedback, output validation, and retries
  for `validation_investigator`; its persisted audit and gate behavior stayed
  stable, including attempted-call budgeting. The other six investigator loops
  are not migrated yet, so commodity-loop consolidation remains open.
- Pandera validates materialized-copy and feature-experiment frame boundaries.
- Cleanlab publishes row-free label-issue summaries from inner-fold OOF
  probabilities; it cannot remove labels or decide a gate.
- skrub dirty-string/datetime transforms are pinned in the sandbox and fitted on
  training only. Relational `AggJoiner` remains deferred until auxiliary-table
  lineage and as-of availability exist. Host-level replay passed; the rebuilt
  Docker image/live test is pending because Docker Desktop failed before build.

### 3. Make non-tabular and deep-learning strategies real

The current control flow still assumes an analytical base table, a target column,
tabular split semantics, scikit-learn estimators, and classification/regression
metrics. Do not add isolated DL conditionals to this graph.

Introduce a swappable problem/execution strategy boundary covering at least:

- dataset modality and sample identity;
- problem/target representation;
- split and contamination semantics;
- feature or representation preparation;
- trainer/evaluator implementation;
- metrics, baselines, persistence, and report projections.

The tabular strategy must remain the first concrete implementation. Add a minimal
second strategy only when it proves the boundary; do not claim DL support from an
unused interface.

### 4. Complete comprehension delivery in existing UI cards

Typed, measurement-bound comprehension artifacts exist, but the full presentation
promise still needs an end-to-end audit. Surface interpretations only inside the
existing card for the cited measurement, visibly mark them proposed, and always
show the verification question. No new free-form agent surface.

Confirm source/schema, EDA, leakage, features, model, and evaluation cards each show
what the user learned, what operation occurred, and what remains a human question.
Evaluation reports must become viewable in the web UI as part of this item.

### 5. Broaden deterministic catalogues, then reconsider curation

Curation was deliberately deferred because choosing from a narrow catalogue adds
little agency and incentivizes building more of the already-strong deterministic
half first. Revisit only after authored investigations naturally expose repeated,
stable analyses worth promoting into registered tools. Then let agents select
additional analyses alongside the mandatory floor; selection alone never changes a
gate.

### 6. Demo-surface product work

These are ordinary product priorities, not production-hardening prerequisites for
the current single-owner demo deployment:

1. improve upload performance with measured profiling of the current path;
2. turn Overview into an attention/status surface showing outstanding warnings,
   human decisions, failures, and changes;
3. make Settings, Account, and Preferences truthful and functional or remove dead
   controls;
4. add typed notifications derived from existing run/gate state;
5. audit account/session behavior and multi-client run-state consistency only when
   the deployment use case expands beyond the present demo.

## Verification deferred during the research-only run

The owner is running the deployed local model and owns the GPU. Until that run is
finished, do not execute live pipelines, end-to-end LLM verification, or any command
that may load a model. Research may inspect source, metadata, lockfiles, repository
documentation, and package manifests. Record any desired executable verification
here for the next build run rather than performing it now.

The dependency research in `docs/AGENT_INFRASTRUCTURE_RESEARCH.md` identified
these executable checks for the next permitted build block:

- resolve `pydantic-ai-slim`, Microsandbox, Pandera, Cleanlab, and skrub in clean
  Python 3.12 environments and record their complete transitive dependency deltas;
- import each candidate, then call the exact proposed APIs, with outbound
  networking denied while detecting attempted DNS/socket/HTTP access;
- migrate one investigator to a Pydantic AI adapter and compare attempted-call
  budgets, permission audits, persisted artifacts, and gate verdicts against the
  current loop, including deliberately failing tool calls;
- exercise a `MicrosandboxBackend` parity spike with a preloaded local OCI image:
  persistent state, no egress, source write denial, artifact writes, timeouts,
  cleanup, resource ceilings, output normalization, and no host fallback;
- counter-test Pandera, Cleanlab, and skrub adapters for fold locality, temporal
  cutoffs, lineage, row-free publication, and exclusion of exploratory evidence
  from gates.

## Claude's session, 2026-08-18

Codex was out of quota from mid-session; everything below was done or found by
Claude working alone. Ownership split suspended for the duration and should
resume when Codex returns.

### Landed

- Run mode (auto / step-by-step), sensitivity override, per-stage directives,
  and the planner route that writes directives on the user's behalf.
- Turkish throughout, with a per-reader language switch. Backend prose is
  translated at composition in `api/panels.py`; everything else already emitted
  codes.
- Agent-assisted PII judgement (`agents/sensitivity_investigator.py`), bounded so
  it can only add and never sees a value.
- Personal columns excluded from the model's features, and `pii_egress_requested`
  given a producer at the point where being non-zero means something is wrong.

### Found while working, worth knowing

- **Personal columns were reaching the model's features.** `discovery.support`
  excluded them from candidate counting only. Fixed, but it means every run
  before this trained on `full_name` and `email_address` where present.
- **`tests/fixtures/pii_sensitivity_cases.json` was not testing the checksums.**
  Two of three national IDs failed the check digit and two of three IBANs failed
  mod-97, so those cases passed on column-name matching. Replaced with computed
  values; with the name list emptied, all four value-shape cases now pass on
  shape alone.
- **The comprehension agent was never shown the citation rules it is judged by.**
  Every attempt was rejected and both briefs came back degraded with zero items,
  so the product's explanation half produced nothing. The rules are in the prompt
  now, the same fix that unblocked tool calls.
- **`comprehension_brief` was never rendered.** Even a successful brief would
  have been invisible.
- **`investigate_sensitivity` claimed to degrade and did not** — `run_agent`
  propagates transport errors, so an unreachable Ollama would have taken intake
  down.

### Still open

- **PII detector.** `docs/PII_DETECTION_RESEARCH.md` recommends Presidio with the
  existing checksums as custom recognizers and Turkish spaCy as the NLP engine.
  Not installed: it is a substantial dependency and the owner was away. The agent
  path landed instead, which needed none.
- **Turkish benchmark.** ai4privacy covers 23 languages and not Turkish. The
  Turkish half cannot be honestly benchmarked without building something, and
  that should be a decision rather than another self-written fixture.
- **Deferred from Codex's framework research**, recorded so they survive:
  Featuretools (Deep Feature Synthesis, once the feature stage matures), FLAML
  (sandbox-only model search), Optuna (deferred on Alembic/SQLAlchemy weight),
  Alibi Detect and NannyML (once a drift stage exists), Microsandbox (microVM
  execution backend parity spike), skrub `AggJoiner` (blocked on auxiliary-table
  lineage and as-of availability), and the six investigator loops still on the
  hand-written turn loop rather than Pydantic AI.
- **Docker/WSL** failed with `0xc00000fd`, so skrub's sandbox image rebuild is
  unverified. Not restarted from a shell, by standing instruction.
