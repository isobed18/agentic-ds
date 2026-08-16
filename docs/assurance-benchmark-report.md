# Assurance Benchmark Report — Does This Product Have a Reason to Exist?

**Project:** Agentic Data Science / ML Pipeline — local, self-hosted
**Period:** 2026-08-13 → 2026-08-14
**Built by:** Codex (codex CLI, `gpt-5.6-sol`, high reasoning) with Claude (Claude Code) as
reviewer, execution proxy and second oracle reviewer
**Companion documents:** [implementation-report-2.md](implementation-report-2.md),
[architecture-report.md](architecture-report.md)

---

## 1. The question

A strong general-purpose coding agent, handed a dataset and a notebook, can already do a large
fraction of an ML project. That is the baseline this product must beat, and it is a high one.

So the differentiation cannot be multiple agents, per-stage system prompts, tool-calling,
orchestration, retries, or an n8n-style workflow UI. Every one of those is infrastructure that
a capable generalist with a good prompt largely renders unnecessary. If that is all this is, it
is a worse notebook.

> **What mechanism, behind the agents, makes them genuinely reliable data scientists rather
> than generic coding agents assigned to pipeline stages?**

This track exists to answer that with evidence rather than assertion. It is deliberately not a
report-writing exercise: the deliverable is a measuring instrument and, if the thesis survives,
a system built against it.

---

## 2. Audit: what is intelligence here, and what is plumbing

Codex audited the repository before proposing anything, and was asked to be blunt.

**Genuine domain content**, concentrated in a few modules:

- `intake/keys.py` — measures candidate keys and relationship overlap instead of trusting names.
- `discovery/support.py` — checks whether a proposed target and task type are supportable.
- `discovery/validation_signals.py` and the split executor — encode partial-order constraints:
  validation topology precedes fitting, learned transforms do not see validation data.
- `discovery/leakage.py` — the strongest concentration. Association, ordered separation,
  missingness separation, identifier proxies and aggregation provenance, and it correctly
  admits in the implementation that statistical strength cannot establish temporal provenance.
- Baseline comparison, untouched holdout evaluation, persisted reproduction inputs.

**Valuable plumbing, not intelligence:** Pydantic contracts, content addressing, orchestration,
retries, permission brokering, tool registration, UI panels, the gate evaluator. Calling these
domain intelligence is a category error.

**Overstated claims**, both independently confirmed:

- `src/ads/skills/` contains three markdown files and no loader. The "procedural knowledge
  library" is documentation of what a human implemented, not a mechanism the system consults.
- `require_tool_evidence` establishes that a named tool ran, not that its output entails the
  agent's conclusion.
- Panel unanimity measures response *stability*, not correctness. Three agents can share one
  bad assumption.
- Problem discovery can auto-confirm the first viable candidate. Viability is not stakeholder
  value, correct outcome construction, or availability at prediction time.
- The evidence base is one synthetic dataset plus adversarial variants. No multi-domain error
  rate, no false-positive rate, no head-to-head generalist baseline.

---

## 3. What the outside landscape already solves

Researched against primary sources, with licences verified against actual repository LICENSE
files rather than product copy.

**Agentic ML systems are strong once the objective is supplied.** MLE-bench starts from 75
Kaggle competitions with defined objectives. AIDE searches code trees against a user metric;
RD-Agent iterates research and development. These begin *after* the question this product has
to answer: whether the target, admissible features, split and metric represent the intended
future decision at all.

**AutoML already handles model and hyperparameter selection.** AutoGluon, auto-sklearn, and the
AMLB benchmark across 104 tasks — which explicitly finds that rankings vary by task, framework
failures matter, and strict reproduction is difficult. Building another model search layer is
neither novel nor the highest-risk part of an applied project.

**Data-quality systems execute declared expectations.** Great Expectations, Pandera, Deequ,
TensorFlow Data Validation, Evidently, NannyML. They validate a declared expectation; they do
not supply missing operational semantics. None can decide whether "discharge disposition"
existed when a readmission prediction was supposed to be made.

**Leakage research says the same more sharply.** Kaufman et al. distinguish the model's learning
data from information legitimately available to the deployed prediction; later systematic
reviews found leakage widespread enough to undermine reproducibility. Notebook static analysis
catches common procedural leakage, but medical label-leakage work shows "no time machine" is
necessary and still insufficient — validity depends on the prediction scenario.

**Causal and statistical systems point at the right abstraction.** DoWhy requires a causal
graph and does not infer one from a CSV. Tea compiles declared study design and assumptions
into valid test selection. Tisane represents conceptual relationships and asks users to
disambiguate. The pattern is not "add causal inference" — it is: make semantic assumptions
explicit, compile them into valid method choices, retain the evidence and the unresolved
assumptions.

**The direct evidence against trusting a generalist's analysis is BLADE**, whose expert-annotated
open-ended scientific tasks require domain knowledge, data semantics and statistical judgment.
Agents with data access improve but remain limited and tend toward basic analyses. DABench and
ScienceAgentBench agree that execution access does not make end-to-end scientific reasoning
reliable.

### Licence findings

| Project | Licence | Usable here |
|---|---|---|
| AIDE, RD-Agent, Pandera, DoWhy | MIT | yes |
| Great Expectations, Deequ, Evidently, TFDV, NannyML | Apache-2.0 | yes |
| auto-sklearn | BSD-3-Clause | yes |
| **Deepchecks** | AGPL-3.0 + commercial | **no** |
| **PyCaret** | FSL-1.1-MIT (root LICENSE) | **no** |
| **n8n** | Sustainable Use License | **no** |

No new dependency has been proposed or added.

---

## 4. The proposal: an executable scientific assurance layer

Codex's position, after the audit and research:

> The mechanism should be a versioned library of data-science protocols that compiles an
> explicit prediction or study contract into mandatory measurements, negative controls,
> falsification tests and human questions, and records the resulting evidence against every
> material claim.
>
> The agent proposes semantics and explains evidence. It must not be where scientific validity
> lives. The gate routes on evidence; it is not itself the intelligence. The intelligence is
> the protocol library plus the empirical corpus used to calibrate and falsify it.

Six components: prediction/study contract, provenance graph, versioned assurance protocols,
falsification suite, evidence ledger, calibrated case corpus.

### 4.1 The epistemic-state debate

The proposal's central risk, raised in review: **a contract with required fields will be filled
in with plausible guesses.** That is exactly the failure the proposal/artifact split exists to
prevent, reintroduced one level up and called semantics. If `label_availability_delay` is
required and an agent emits `0` because something must go there, a false assurance has been
manufactured and protocol selection built on top of it.

Codex accepted the objection and went further — `unknown` alone is insufficient, because an
agent can put a plausible value in a field and call it known. The agreed model:

| State | Meaning | Who can produce it |
|---|---|---|
| `unknown` | no value, plus why it matters and what could resolve it | agent |
| `proposed` | an agent hypothesis; may select probes, cannot satisfy a prerequisite | agent |
| `supported` | value with evidence lineage — measured by a named method or asserted by a named human/source | **measurement executor, ingestion path, or human confirmation only** |
| `disputed` | incompatible supported values | system |
| `not_applicable` | explicit justified claim, not a synonym for missing | with justification |

The authority boundary is enforced by the runtime, not requested in a prompt. This is the
proposal/artifact split applied to *semantics* rather than to numbers.

A second correction went the other way. The reviewer's phrase "reduced coverage" was rejected by
Codex as a laundering mechanism: five green checks plus one unknown prediction timestamp
becomes "83% covered" while the unknown invalidates the headline claim. Coverage is a matrix of
named properties; a claim-critical unknown yields `unassessed`, which can never be `pass` and
can never be averaged away. The public conclusion reads:

> Split fitting isolation: supported. Entity separation: supported. Prediction-time feature
> availability: **unassessed** — prediction timestamp has no authoritative source. Therefore the
> evaluation is not yet assured for the stated deployment decision.

---

## 5. The instrument: Assurance Corpus v0.1

Rather than build protocols first, both sessions agreed to build the **measuring instrument
first**, with a strong generic-agent baseline. Reasoning: the corpus is the only thing that can
falsify the whole thesis cheaply, it needs no contract agreement to build, and if protocols come
first they will be calibrated against our own intuitions — which is how the existing hand-set
thresholds got there.

A hidden oracle manifest states the true scenario and expected disposition. It is an evaluation
specification, never the runtime product contract, and never shown to the system under test. If
the product contract were written first, its vocabulary and blind spots would define its own exam.

### 5.1 Structure

`benchmarks/assurance_v0/` — 1,688 lines, no new dependencies, imports no product module. That
last constraint matters: generators importing our leakage code would test whether the product
recognises its own vocabulary.

**20 cases across two domains** — 30-day hospital readmission (c001–c010) and 90-day loan default
at application (c011–c020). Each family: one safe control, eight single-fault mutants, one
underspecified control.

**Eight mutation families:** entity contamination, temporal look-ahead, post-outcome feature,
unwindowed history, global pre-split fit, cross-fold duplicate, outcome-dependent missingness,
immature labels.

**Safe cases carry hard negative controls**, not merely the absence of traps: a legitimate strong
pre-prediction feature that must survive, correctly windowed historical aggregates,
outcome-independent missingness, matured labels, identifiers excluded from fitting. These target
the project's real false-positive history — a mutual information of 1.0 once flagged a legitimate
`hire_date`, and the first fix for that created a false negative on an exact target copy.

**Underspecified controls** remove a claim-critical fact from the brief. Their expected
disposition is `indeterminate`, not safe. They measure whether an assessor invents semantics
rather than exposing an unknown.

### 5.2 Measured effect sizes

Every case executes; supplied and oracle metrics are measured by running the notebooks, not
asserted by the author.

```
case  scenario     variant                           supplied   oracle       gap  ovl
c001  readmission  safe                                0.6495   0.6495  +0.00000    0
c002  readmission  post_outcome_feature                1.0000   0.6495  +0.35045    0
c003  readmission  entity_contamination                0.6495   0.6495  +0.00000   12
c004  readmission  temporal_lookahead                  0.6616   0.6495  +0.01205    0
c005  readmission  unwindowed_history                  0.5607   0.6495  -0.08886    0
c006  readmission  global_fit_before_split             0.6493   0.6495  -0.00023    0
c007  readmission  cross_fold_duplicate                0.6608   0.6495  +0.01126    0
c008  readmission  outcome_dependent_missingness       0.6461   0.6495  -0.00341    0
c009  readmission  immature_labels                     0.6495   0.6495  +0.00000    0
c010  readmission  underspecified                      0.6495   0.6495  +0.00000    0
c011  lending      safe                                0.6353   0.6353  +0.00000    0
c012  lending      post_outcome_feature                0.7333   0.6353  +0.09794    0
c013  lending      entity_contamination                0.6353   0.6353  +0.00000   12
c014  lending      temporal_lookahead                  0.5131   0.6353  -0.12225    0
c015  lending      unwindowed_history                  0.6331   0.6353  -0.00218    0
c016  lending      global_fit_before_split             0.6350   0.6353  -0.00031    0
c017  lending      cross_fold_duplicate                0.6416   0.6353  +0.00631    0
c018  lending      outcome_dependent_missingness       0.6331   0.6353  -0.00218    0
c019  lending      immature_labels                     0.6353   0.6353  +0.00000    0
c020  lending      underspecified                      0.6353   0.6353  +0.00000    0
```

Gap range **-0.122 to +0.350**; **8 cases at exactly zero**, **7 negative**.

Three properties matter more than the numbers:

**Negative gaps.** c005 and c014 are invalid methods that make the metric *worse*. These may be
the most valuable cases in the corpus: an assessor looking for a suspiciously good number misses
them entirely, and so does any heuristic of the form "leakage inflates scores". Catching them
requires reasoning about method, which is the entire thesis. They were not requested — Codex
produced them in response to a reviewer note asking for low-gap mutants early rather than late,
so the benchmark would not be calibrated toward the easy end.

**Zero-gap structural faults.** c003/c013 register gap 0.0000 with 12 entities crossing the split
boundary; c009/c019 the same for immature labels. Real, structurally detectable, metric-invisible.

**The same family has opposite signs across domains.** Temporal look-ahead is +0.012 on
readmission and -0.122 on lending. Anyone later tempted to write "look-ahead inflates the metric"
has to meet c014 first.

---

## 6. Two leaks that would have invalidated the benchmark

Both found in review, neither by the author. This is the strongest argument for the two-session
arrangement.

### 6.1 The fault name was printed on the exam paper

Every mutated case shipped this inside the directory the agent under test receives:

```json
{ "case_id": "c003", "seed": 4107, "sha256": {...},
  "variant": "entity_contamination" }
```

The corpus's own spec forbids exactly this — "neither filenames nor public metadata may say
`post_outcome_copy` or `unsafe_random_split`". Reading `checksums.json` when handed a case
directory is the obvious first move. Any baseline run would have measured whether an agent can
read a label and produced a number that *looks* like a result.

Fixed: public checksums now carry only `case_id` and digests; `variant`, `scenario` and `seed`
are removed, and a counter-test walks every public file for every mutation-family name.

**Verified independently** rather than accepted: the fault vocabulary was built from the hidden
oracles themselves — so the scan could not miss a name the reviewer failed to think of — and swept
across all 100 public case files. Zero hits.

### 6.2 The briefs stated the diagnosis

Subtler and worse. The briefs did not name the family, but they described the conclusion:

- c005: "including encounters **after the row's prediction_time**"
- c008: "That masking is applied **after outcomes are known**."
- c009: "Their 30-day outcomes are **not yet mature**"
- c007: "the duplicated model inputs and outcomes **cross the evaluation boundary**"

The reviewer's blind review scored **20 of 20 without opening a single dataset or notebook** —
reported as a failure of the instrument, not a pass. It required reading English, not evaluating
anything.

**Why fatal rather than untidy:** the falsification threshold requires a protocol to reduce the
strong baseline's unsafe-pass rate by a predeclared margin. A capable generalist handed those
briefs sits at ~zero unsafe passes immediately. No headroom — the benchmark could neither
validate the thesis nor falsify it, and would return a baseline so strong that any protocol looks
redundant, on an instrument that never measured anything.

**The fix, which is the elegant part.** Faults in the *data* now get a **byte-identical brief to
their safe control**; faults in the *notebook's method* describe the method neutrally, as c006
already did:

> "The notebook first fits median imputation, standardization, and categorical encoding on all
> rows. It then uses the intended temporal/entity split to fit the classifier and score holdout."

Every word is something a stakeholder could truthfully say; nothing says "this is wrong". These
are not two standards but one standard applied to two locations of fault: a fault in the data
cannot be mentioned at all, because any mention is the answer.

```
c001 c003 c005 c007 c008 c009   brief identical to safe control (readmission)
c011 c013 c015 c017 c018 c019   brief identical to safe control (lending)
c002 c004 c006 c012 c014 c016   brief differs — fault is in the stated method
c010 c020                       underspecified
```

---

## 7. Verification: the corpus is solvable from the data

With briefs byte-identical, brief-only review is impossible by construction. So the reviewer ran
five checks over the readmission family from the data alone — no oracle, no variant labels:

```
case    ent_ovl  dup_x   miss~y  immature agg_future
c001          0      0    +0.06         0          0   <- safe control
c002          0      0    +0.06         0          0
c003         12      0    +0.06         0         12
c004          0      0    +0.06         0          0
c005          0      0    +0.06         0        650
c006          0      0    +0.06         0          0
c007          0     16    +0.06         0          1
c008          0      0    +0.88         0          0
c009          0      0    +0.06        24         15
c010          0      0    +0.06         0          0
```

Checks: patients shared across the split boundary; identical feature/target records on both
sides; difference in missing-rate between outcome classes; holdout rows too recent for a 30-day
outcome to have matured by the extract date; and the 180-day aggregate recomputed from strictly
earlier encounters.

**Every data-located mutant trips exactly one distinctive check; the safe control trips none.**
c001, c002, c004, c006 and c010 are correctly silent, because their faults are in the notebook or
absent. The corpus is solvable from data without the oracle — demonstrated, not asserted.

**One open item.** `agg_future` also fires on c003 and c009, because reassigning `patient_id`
leaves the prior-admits column inconsistent with the id column. Single-fault isolation holds for
*what was changed* but not for *what is observable*. The recommendation was to record the
secondary observable in the oracle rather than engineer it away — a real id-remapping bug leaves
exactly that trace, and an assessor who spots it is doing good work, but strict locator matching
would currently score them as having found the wrong thing.

---

## 8. What gets measured, and the falsification threshold

Primary measures, macro-averaged by scenario and mutation family, with the case *pair* as the
sampling unit:

- **unsafe pass rate** — mutated cases called `supported`; the primary safety loss
- **safe rejection rate** — safe cases called `not_supported`
- **unknown fabrication rate** — underspecified cases resolved without identifying the missing fact
- **paired discrimination** — whether safe/mutated siblings receive different correct dispositions
- **fault-localization recall and precision**, penalising deletion of legitimate strong controls
- **optimism-gap awareness**, reported by delta bins rather than rewarding only dramatic leaks
- **remediation success** — does an executable repair run, remove the fault, preserve topology
- **run-to-run stability** — variance and worst-run unsafe pass rate, not just the mean

A confusion matrix and per-family results; never one blended "assurance score". Bootstrap
intervals resample pairs and scenarios, not rows.

**The falsification threshold, and it is the point of the whole exercise:**

> The assurance thesis survives only if a protocol reduces the strong baseline's unsafe pass rate
> by a material, predeclared margin **without increasing safe rejection or unknown fabrication
> beyond a predeclared bound.**
>
> If it cannot beat the generalist, the proposed differentiator is false and the larger
> architecture should not be built.

The baseline must be deliberately strong, not a straw man: two locked prompts, one asking simply
whether the evaluation supports the decision and one explicitly instructing the same
general-purpose agent to challenge entity/time/duplicate separation, prediction-time
availability, transform fitting and label maturity. **The product must beat the stronger one**,
because "a capable generalist with a good prompt" is the actual comparator. Assessments are
scored deterministically; no LLM judge is used as ground truth.

---

## 9. Status and open decisions

**Built and verified:** 20 cases, 2 domains, 8 mutation families, hidden oracles, deterministic
scorer, 12 counter-tests passing, Ruff clean, imports no product module, product test suite
unaffected throughout (557 passed, 2 skipped).

**Deliberately unbuilt:** the second half of the corpus — two further scenario families — stays
unwritten and unseen while the first protocol is designed, so it can serve as a holdout.

**No protocol implementation has started.** No product contract, artifact meaning or gate rule
has been changed by this track.

### Open decisions, both requiring the project owner

1. **Which agent is the comparator.** A local 27B model would flatter us; Codex or Claude Code via
   CLI is the honest comparator, since the thesis explicitly claims to beat a capable generalist.
   At 40 cases × 2 prompts × 3 repetitions this is 240 runs. The runner is pluggable and
   unimplemented; nothing has been spent.
2. **Whether to fix the Codex sandbox.** It cannot spawn processes on this host, so it runs
   neither Python nor git, and every command and commit is proxied. This halves its throughput —
   though the proxy arrangement is also what caught both leaks in §6.

### Standing recommendation

**Predeclare the falsification thresholds in `SPEC.md` now**, while no results exist and they
therefore cannot be tuned toward what we hope to see. It costs nothing today and it is the
cheapest available defence against fooling ourselves later.
