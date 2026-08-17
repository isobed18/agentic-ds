# Implementation Report

**Project:** Agentic Data Science / ML Pipeline — local, self-hosted
**Period:** 2026-08-11 → 2026-08-12
**Built by:** Claude (Claude Code) and Codex (codex CLI, `gpt-5.6-sol`, high reasoning), collaborating in one repository
**Companion document:** [architecture-report.md](architecture-report.md) — the design this implements

---

## 1. Executive summary

The MVP's deterministic spine, agent layer, governance layer and orchestration layer are
built and tested. A run can go from raw messy files to a trained, evaluated, persisted
model with a reproducible standalone `train.py`, pausing for human decisions according to a
configurable autonomy policy.

| | |
|---|---|
| Source | **11,176 lines** across 17 packages |
| Tests | **6,222 lines**, **508 tests**, all passing |
| Lint | `ruff` clean |
| Commits | 30 |
| LLM dependency in tests | **none** — no test requires Ollama, a GPU or a network |

**Measured on a local 27B model (`qwen3.6:27b`, RTX 3090):** SchemaDiscoveryAgent reached
**5/5 first-pass contract validity** at temperature 0.3 with identical structure every run
(self-consistency 1.0), at ~112s median latency. That retires the largest architectural
risk in the design report — typed contracts are viable on a local model at the dev-hardware
floor, and production (8×H100) is strictly upside.

**The acceptance criterion is met.** A completed run emits a standalone `train.py` that
executes in a fresh interpreter with no LLM and no agent import, reproducing the recorded
holdout metric exactly (`25898.5376005` against a recorded `-25898.5376004928`).

The most valuable output of this period is not the code. It is **18 defects found by
adversarial review between the two agents**, 17 of them real, documented with reproductions
in [AGENT_DIALOGUE.md](AGENT_DIALOGUE.md). Section 8 analyses them, because their
distribution says something about how this codebase should be maintained.

---

## 2. What was built

### 2.1 Module inventory

| Package | Lines | Purpose |
|---|---|---|
| `contracts` | 1,585 | Typed, immutable, versioned inter-stage contracts |
| `discovery` | 1,144 | Deterministic feasibility, validation signals, leakage audit |
| `agents` | 1,074 | Agent framework + 3 specialized agents |
| `orchestration` | 1,062 | WorkflowSpec, critique loop, run state, resume |
| `pipeline` | 971 | Stage adapters wiring everything into the orchestrator |
| `training` | 947 | Candidate training, persistence, script export |
| `intake` | 935 | Loaders, DataCard profiler, key/relationship detection |
| `gates` | 829 | Gate Evaluator, 18 rules in 4 precedence tiers, policy |
| `splitting` | 535 | Fold-safe split execution and diagnostics |
| `reporting` | 471 | Evaluation report, markdown rendering |
| `eda` | 431 | Deterministic EDA profiling |
| `ds_toolkit` | 342 | Fold-safe sklearn preprocessing primitives |
| `store` | 287 | Content-addressed artifact store |
| `integration` | 215 | DuckDB plan executor with grain verification |
| `llm` | 197 | Ollama client, grammar-constrained structured output |
| `testing` | 149 | Deterministic synthetic fixtures |

### 2.2 Test distribution

508 tests. The distribution is deliberate — the layers that decide things carry the most.

| Module | Tests | |
|---|---|---|
| `test_leakage.py` | 87 | Four independent detection families |
| `test_gates.py` | 79 | The governance layer; the most safety-critical |
| `test_intake.py` | 41 | Messy-file handling on real fixtures |
| `test_orchestration.py` | 35 | Spec validation and the critique loop |
| `test_discovery.py` | 30 | Support measurement thresholds |
| `test_agents.py` | 28 | Validation, auto-repair, corrective retry |
| `test_orchestration_critic.py` | 24 | Orchestrator critique, resume, retry identity |
| `test_contracts.py` | 20 | Contract invariants |
| `test_integration.py` | 17 | SQL safety and grain preservation |
| `test_validation_strategy.py` | 17 | Split-strategy protection dominance |
| `test_store.py` | 16 | Content addressing, idempotency |
| `test_training.py` / `test_split_diagnostics.py` | 12 / 12 | |
| Remaining | 41 | ds_toolkit, eda, reporting, splitting, persistence, pipeline, acceptance, export |

---

## 3. The pipeline, end to end

### 3.1 Stages implemented

| # | Stage | Actor | Risk | Status |
|---|---|---|---|---|
| 0 | Intake & profiling | deterministic | low | done |
| 1 | Schema & relationship discovery | agent + measurement | medium | done |
| 2 | Integration (ABT build) | deterministic | medium | done |
| 3 | Problem discovery | agent + measured support | **critical** | done |
| 4 | Validation strategy | agent + measured signals | **critical** | done |
| 5 | EDA | deterministic | low | done |
| 6 | Feature preprocessing | `ds_toolkit` builders | medium | done |
| 7 | Leakage audit | deterministic, blocking | high | done |
| 8 | Splitting | deterministic, verified | — | done |
| 9 | Training + candidate selection | deterministic | low | done |
| 10 | Evaluation & reporting | deterministic + narration | high | done |
| 11 | Persistence & script export | deterministic | — | done |

Roughly **60% of the pipeline contains no LLM decision authority**, against a design target
of 40%. Every stage that could be made deterministic was.

### 3.2 Verified run output

From `scripts/run_pipeline.py` on the synthetic physician/ledger/transaction dataset:

```
STAGE 0  4 tables loaded, 5 relationships measured
STAGE 1  attempts=1 first_pass=True latency=124.2s
         base=physicians__physician_master grain=['physician_id'] joins=3 aggs=2
         warning: provider_ref has a 5.8% orphan rate (rows_matched=94.2%)
STAGE 2  ABT: 800 rows x 18 cols (base was 800), grain preserved: True
STAGE 3  attempts=1 first_pass=True latency=107.9s → 3 viable candidates
STAGE 4  deterministic minimum: temporal; agent chose temporal, first_pass=True
STAGE 7  15 findings → after fixes, total_comp_ytd (0.9948) + 6 unwindowed aggregates
GATE     ESCALATE  reason_code=leakage_unresolved
```

The DataCard projection that agents actually see is **~594 tokens for 4 tables** including
all measured relationships — the two-plane separation working as designed.

---

## 4. Governance: the Gate Evaluator

The design report's central question was *how the Orchestrator decides when human input is
required, without relying only on a system prompt.* The answer implemented is a pure
function over measured signals.

### 4.1 Rules

18 rules in 4 precedence tiers, evaluated highest-first:

**Tier 1 — hard constraints, never overridable**
`pii_egress_requested` · `destructive_operation` · `leakage_detected` ·
`repeated_identical_failure` · `retry_budget_exhausted`

**Tier 2 — declared stage risk**
`risk_class_gate`

**Tier 3 — deterministic quality signals**
`unmet_mandatory_criteria` · `critique_errors` · `model_below_baseline` ·
`lift_within_noise` · `high_cv_variance` · `candidate_disagreement` ·
`statistical_support_low` · `degenerate_split` · `separator_needs_confirmation` ·
`repeated_validation_failures`

**Tier 4 — user autonomy preference, the weakest input**
`profile_checkpoint` · `profile_requires_confirmation`

### 4.2 The decision matrix

```
scenario                            supervised   checkpointed  autonomous   full_auto
leakage: total_comp_ytd @ 0.98      RETRY        RETRY         RETRY        RETRY
PII would enter context             ASK HUMAN    ASK HUMAN     ASK HUMAN    ASK HUMAN
source-mutating tool requested      ASK HUMAN    ASK HUMAN     ASK HUMAN    ASK HUMAN
model below baseline                ASK HUMAN    ASK HUMAN     ASK HUMAN    proceed
correct split, 5-row folds          ASK HUMAN    ASK HUMAN     ASK HUMAN    proceed
fraud label: 39 positives           ASK HUMAN    ASK HUMAN     ASK HUMAN    proceed
healthy model                       ASK HUMAN    ASK HUMAN     proceed      proceed
```

The top three rows are the design working: **`full_auto` still stops.** A parametrised test
asserts this holds for every profile that exists, so adding a profile cannot quietly create
an exemption.

### 4.3 Properties

- **Deterministic.** Same inputs, same verdict. Testable in CI with no model.
- **Auditable.** `triggered_rules` names every rule that fired, and a procedural stop never
  masks a substantive one — the headline reason prefers "model below baseline" over "this
  stage is high risk", while the audit keeps both.
- **Unbypassable.** The LLM never sees the rules and is never asked whether to consult a
  human.
- **No self-reported confidence.** `CritiqueResult` has no confidence field. Uncertainty
  comes from resampling, CV variance, baseline delta and validation counts.

---

## 5. Leakage detection

Four independent families, because each hides from the others' test. This is documented in
the module docstring specifically so a future editor does not delete one as redundant.

| Family | Method | What only it catches |
|---|---|---|
| Target-derived | Pearson/Spearman + adjusted MI | `total_comp_ytd` at **0.9948** correlation |
| Perfect separator | directional single-feature ROC AUC | a continuous separator at 1% prevalence: **AMI 0.02, AUC 1.00** |
| Unwindowed aggregate | provenance from the IntegrationPlan | full-history aggregates under a temporal split — invisible to every statistical test |
| Missingness separator | AMI on `feature.isna()` | when *absence* is the signal and every other scorer drops those rows first |

The evidence that they are genuinely independent: `total_comp_ytd` scores AMI **0.64** but
correlation **0.9948**; the continuous separator scores AMI **0.02** but AUC **1.00**.
Collapsing them into one score would lose both.

### 5.1 Calibration

Thresholds are calibrated against explicit controls, with the tables kept in the constants'
docstrings:

```
LEAK   corrupted copy 100%  agreement : 1.00
LEAK   corrupted copy 95%   agreement : 0.86 - 0.93
LEAK   corrupted copy 90%   agreement : 0.76 - 0.86
OK     years_experience -> annual_comp: 0.32     <- strongest legitimate
OK     specialty / city / hire_date   : < 0.02
```

`LEAKAGE_MUTUAL_INFORMATION = 0.70`, giving 2.2× margin above the strongest legitimate
relationship measured.

### 5.2 Suspects versus verdicts

A perfect separator is **not** treated as proof. `years_experience >= 20` defining
`senior_physician` scores AUC 1.0 and is the correct explanatory feature. Statistical
strength identifies a suspect; only a human knows whether a column is recorded before the
outcome. So separators escalate for confirmation rather than being dropped, and
`confirmed_pre_outcome` / `confirmed_missingness_pre_outcome` record the answer so re-runs
do not re-ask. Those are deliberately **two** flags: "this value is pre-outcome" does not
establish "whether this value exists is pre-outcome".

---

## 6. Orchestration

### 6.1 The seam

`WorkflowSpec` is a declarative, serialisable graph. **Nothing in `ads.orchestration`
imports a graph library.** Stages are plain callables `(RunState, correction) ->
StageResult` and the loop is an explicit state machine.

This is the architecture report's build-vs-buy conclusion made concrete: own the domain
layer, keep the kernel swappable. The spec round-trips to a dict, which is also the future
visual editor's data model — a canvas becomes a view over a document that already exists
rather than a retrofit.

### 6.2 The loop

```
run stage → critique (rubric: mechanical first, LLM for the rest) → gate (deterministic)
  → AUTO_PROCEED : follow the proceed edge
  → RETRY        : re-run with a targeted correction, inputs pinned
  → ESCALATE     : stop resumably with a typed question
  → ABORT        : stop
```

**The Orchestrator owns the rubric, not the stage.** Until this landed, `StageResult.critique`
came from the stage itself — a stage that failed to notice a problem also failed to report
it. Deterministic criteria run first and never reach a model; the LLM judges only residual
items and receives the computed results as established fact. Invented criterion ids are
dropped, and an unparseable critique produces an explicit finding rather than silently
approving.

### 6.3 Per-attempt input binding

Each attempt pins its declared inputs to exact artifact ids when it opens. Without this a
retry was not a corrected replay — it was a different experiment carrying a correction
derived from inputs it no longer saw.

The subtle part, found by Codex: **content-addressed writes preserve the original
`created_at`**, so a stage reverting to an earlier semantic version never becomes "newest"
again and `latest()` keeps returning the superseded one. The idempotency property built to
make retries safe is exactly what broke lineage. Timestamps and logical names both fail
here; only explicit binding works.

### 6.4 Spec validation

Rejected at construction, not mid-run: duplicate ids, unknown entry, dangling edges,
unreachable stages, proceed-self-edges, stages consuming an artifact no earlier stage
produces, and **cycles among non-retry edges**. That last one previously fell back to
declaration order, which let a spec whose consumer runs before its producer pass validation
and fail at runtime.

---

## 7. Reproducibility

The MVP's acceptance criterion, verified end to end by
`scripts/probe_export_reproducibility.py`:

```
recorded winner   : ridge
recorded holdout  : -25898.5376004928
generated train.py (8,052 chars)
contains no agent or LLM import  OK
--- executing in a fresh interpreter ---
exit code: 0
winner=ridge
rmse: cv_mean=25258.6767843 cv_std=604.158584824 holdout=25898.5376005
```

The generated script rebuilds the ABT from source via the recorded `IntegrationPlan`,
re-applies the same split, refits from recorded recipes, and prints matching metrics.

Model persistence records sklearn version, Python version, a SHA-256 of the training frame,
the full `ValidationStrategy` and a blob checksum. Loading verifies the checksum before
deserializing.

**Joint decision (recorded in the dialogue):** the persisted model stays identical to the
model whose holdout metric was measured. A refit on train+holdout is offered as a
separately-labelled artifact with its own provenance, because after refitting consumes the
holdout, the reported metric no longer describes the shipped binary.

---

## 8. The findings ledger

Two agents reviewed each other's work adversarially. 18 findings; 17 were real defects.

| # | Found by | Defect | Severity |
|---|---|---|---|
| — | Claude | FK columns misclassified as measurements; both real foreign keys invisible | high |
| — | Claude | Spurious relationships at 100% (small ranges inside large ones) | high |
| — | Claude | Overlap measured over distinct values, not rows (42.3% vs 94.2%) | high |
| — | Claude | `AggregationStep` had no `output_name`; contract could not express the right answer | high |
| 1–2 | Claude | Total-order split ranking; skill file cited a nonexistent function | medium |
| 3 | Codex | "Unfitted objects" contract *enables* fold safety but does not enforce it | high |
| 4 | Claude | Correct grouped-temporal split retains 20.6% of rows, 5-row folds | high |
| 5 | Codex | Loss-metric sign convention at the gate seam (confirmed correct) | — |
| 6–8 | Codex | Separator false positive; parser desync; missingness false negative | blocking |
| 9 | Claude | `max_attempts: 1` stages escalated unconditionally | high |
| 10 | Codex | Failure predicate was a parallel list, already incomplete | blocking |
| 11 | Codex | One confirmation cleared two different provenance claims | blocking |
| 12 | Codex | 1% null floor excluded the project's own 39-positive fraud case | blocking |
| 13 | Codex | Provenance question counted as retry failure | high |
| 14 | Codex | Forward cycle disabled dependency validation | blocking |
| 17 | Codex | Self-retry identity inferred from correction text | blocking |

### 8.1 The pattern worth acting on

**Three of the last five defects were in code written to fix a previous finding**, and all
three had the same shape: a control decision keyed off an *incidental property of the data
flowing through it* rather than off the control flow itself.

- `has_retryable_failure` stood in for "did any rule fire"
- `correction is not None` stood in for "is this a self-retry"
- `result.critique` stood in for "what did the Orchestrator conclude"

Each was locally reasonable and each silently defeated the mechanism it sat inside. None
failed loudly; every other test kept passing.

### 8.2 How defects were found

| Method | Count |
|---|---|
| Running the system and reading real output | 8 |
| Adversarial review by the other agent | 7 |
| A test written specifically to falsify a claim | 3 |
| Ordinary test failure | 0 |

**Zero defects were caught by tests written alongside the code they test.** Tests written
from the same assumptions as the implementation cannot catch assumption errors. This is the
single most important operational finding of the period and it is recorded as a standing
instruction in `AI_HANDOFF.md`.

---

## 9. Deviations from the architecture report

| Report said | Built | Why |
|---|---|---|
| Problem Discovery before Integration | Integration first | `ProblemSupport` is *exact* on a materialized ABT and only estimated from DataCards, and it gates a CRITICAL decision |
| LangGraph as execution kernel | Own explicit state machine | The loop is ~200 lines; adopting a kernel now would add dependency weight before its checkpointing is needed. The `WorkflowSpec` seam keeps the swap open — the two-week-replaceability test still holds |
| PydanticAI for the agent layer | Thin `StructuredLLM` protocol | Needed an honest first-pass-validity measurement without a framework's retries in the way. PydanticAI implements the same protocol when its MCP client is wanted |
| `min_split_retained_rate = 0.50` | Disabled by default | Coverage and precision are different problems; 49% of ten million rows is not noisy |

None of these change the architecture's conclusions. The kernel deviation is the one to
revisit deliberately: durable checkpointing and fork/time-travel are still the expensive
primitives, and the moment a run needs to survive a process restart, LangGraph earns its
place.

---

## 10. Not built

| Component | Status |
|---|---|
| Sandboxed code execution (Docker + Jupyter kernel) | not started — Docker was unavailable during this period |
| MCP servers for enterprise connectors | not started |
| Control plane (FastAPI) and web UI | not started |
| MLflow experiment tracking | not started — persistence and provenance are in place, tracking is not |
| Visual workflow editor | deliberately deferred; its data model exists |
| Multiclass separation for non-ordered features | designed and calibrated by Codex, recorded in the backlog, not implemented |
| Paired per-fold lift | tracked; `lift_within_noise` is documented as a heuristic, not a statistical test |
| Empty-artifact gate signal | `n_rows=0` still means nothing to the gate |

---

## 11. Risk assessment update

| Risk | Original | Now | Basis |
|---|---|---|---|
| R1 Local LLM cannot produce typed contracts | Med/High | **Low** | 5/5 first-pass validity at temp 0.3 on a 27B model |
| R2 Agent quality on messy data | High/High | **Med/High** | Deterministic layer holds veto; three agents first-pass valid on real data |
| R3 Undetected leakage | Med/**Critical** | **Low/Critical** | Four independent families, calibrated, blocking |
| R4 Kernel lock-in | Med/Med | **Low** | No graph library imported anywhere |
| R5 Sandbox escape | Low/High | **unchanged** | Not built |
| R7 Runs too slow | Med/Med | **Med** | ~112s per agent call on the 3090; three agent stages ≈ 6 min |
| R10 Dependency licence drift | Med/Med | **unchanged** | Automated CI licence scan still not implemented |

---

## 12. Recommended next steps

1. **Sandbox** — the largest unbuilt component, and the one gating any agent that writes
   code rather than filling a contract.
2. **Automated licence check in CI** — R10 is unmitigated, and PyCaret's MIT→FSL move
   during the evaluation is exactly the drift it guards against.
3. **Empty-artifact criterion per stage** — `n_rows=0` currently means nothing to the gate.
4. **Paired per-fold lift** — replace the documented heuristic with a real measurement;
   the per-fold scores already exist for every candidate.
5. **Control plane** — the run lifecycle, approval inbox and artifact viewer are what turn
   this from a library into a product.

Sequenced this way because 1 and 2 are risk reduction, 3 and 4 close known gaps, and 5 is
the first thing that is purely additive.
