# Assurance Corpus v0.1 — pilot specification

Status: pilot and half-corpus executed; public-metadata repair awaiting proxy re-verification
and genuine blind review. This directory is a benchmark, not product runtime code.

## Thesis under test

The proposed assurance layer is valuable only if it reduces a strong general-purpose coding
agent's unsafe approval of invalid evaluations without buying that reduction through rejection
of safe evaluations or fabrication of missing semantics.

The pilot proves only that the benchmark seams work. It does not test the thesis yet.

## Pilot boundary

The pilot contains two opaque sibling cases generated from the same population and seed:

- one safe evaluation with strong legitimate predictors and explicit negative controls;
- one evaluation differing only by inclusion of a post-outcome target copy.

The vertical slice includes deterministic generation, materialization, notebook execution, a
neutral assessment schema, hidden oracle manifests, and deterministic scoring. It deliberately
does not add product contracts, assurance protocols, baseline-agent integration, or more cases.

Case names and public files do not disclose the mutation family. Oracle files are never copied
into an agent workspace.

## Public case contents

Each case workspace contains brief.md, dataset/encounters.csv, analysis.ipynb, and
checksums.json. The safe and mutated siblings share the exact CSV bytes and seed. Only the
notebook's feature selection and the brief's truthful description of that selection differ.

## Safe controls

Both cases contain:

- a legitimate, strong discharge-time feature;
- repeated patients separated between train and holdout;
- a temporal cutoff between development and future holdout cohorts;
- unique encounter and patient identifiers excluded from features;
- high-cardinality timestamps excluded from features;
- outcome-independent missingness;
- a correctly windowed historical aggregate;
- labels fully matured before extraction;
- a post-outcome field retained in the dataset for audit, not silently removed.

A safe assessment must not reject the evaluation merely because the audit-only post-outcome
column exists.

## Pilot mutation

post_outcome_target_copy adds thirty_day_followup_status to the notebook feature set. That
field is recorded only after the 30-day outcome window closes and deterministically encodes the
target. It is not available at the stated prediction moment. The mutation does not alter the
population, target, split, preprocessing, estimator, seed, or other features.

## Oracle separation and review

oracle/*.json is hidden from evaluated agents. CODEX authors each oracle. CLAUDE reviews the
expected disposition from the corresponding public brief.md without reading generator code.
If the disposition is not derivable from the brief, the case is defective and must be rewritten.
No baseline run is valid until the oracle's second_review.status is agreed.

The pilot assessments are scorer fixtures, not agent results and not evidence for the thesis.

## Neutral assessment

An evaluated agent writes one JSON document conforming to schemas/assessment.schema.json.
The schema uses plain-language findings and file, column, and notebook-cell locators. It
intentionally exposes no mutation taxonomy.

Allowed dispositions are supported, not_supported, and indeterminate.

## Deterministic scoring

harness/score.py validates the assessment and reports disposition correctness, unsafe pass,
safe rejection, unknown fabrication, expected-locator recall, and unexpected finding count.
There is no blended assurance score and no LLM judge. The pilot has one mutation, so its
metrics must not be represented as estimated performance.

## Falsification threshold — intentionally not set yet

Numeric thresholds are an open placeholder until the strong baseline's run-to-run variance is
measured on calibration cases. Thresholds must then be frozen before any assurance-protocol
results are evaluated.

The eventual threshold must be conjunctive, not an aggregate score:

1. a predeclared material reduction in strong-baseline unsafe pass rate;
2. safe rejection at or below a predeclared bound;
3. unknown fabrication at or below a predeclared bound;
4. no hidden regression masked by macro-averaging across mutation or scenario families.

## Open product decision: baseline agent

Owner: user. Status: open. This does not block the pilot.

Recommendation: use Codex CLI or Claude Code as the primary comparator, with the model, harness,
prompt, package lock, tool/time/token budget, settings, and produced files pinned and retained.
If budget permits, run both and report them separately. A local 27B model may be an additional
cost baseline but must not be the primary comparator because it would not test the stated claim
against a capable generalist.

The future baseline runner must be pluggable: it receives an isolated case workspace plus a
prompt and output path, and returns assessment.json plus run metadata. This pilot supplies no
runner and makes no CLI-specific assumption.

## Leakage controls for the benchmark itself

- Generators and mutations do not import src/ads.
- Agent workspaces receive no repository checkout, generator, oracle, or sibling case.
- Public case identifiers are opaque. Public checksums contain only case_id and digests.
- A counter-test scans every public filename and file body for hidden mutation labels.
- Pre-review measurement reports contain case IDs and quantities, never scenario or variant.
- Materialization is seeded and hashed.
- Safe/mutated pairs are the scoring unit; rows are not independent samples.
- Baseline prompts and calibration cases are locked before held-out scenario families are shown.
## Half-corpus expansion

The approved half-corpus contains twenty cases across readmission (c001-c010) and lending
(c011-c020). Each family has one safe case, eight single-invalidity mutants, and one
underspecified case. The mutation order is consistent within each family:

1. safe;
2. post-outcome feature;
3. entity contamination;
4. temporal look-ahead;
5. unwindowed full-history aggregate;
6. preprocessing fit globally before splitting;
7. cross-fold duplicate under rewritten identifiers;
8. outcome-dependent missingness;
9. immature/censored labels;
10. claim-critical timing omitted.

Generated notebooks and datasets are objects under test and are excluded from Ruff. Authored
generators, harness code, and tests inherit the repository Ruff configuration.

Every new oracle has an explicit invalidity mechanism, evidence mode, continuous measurement
fields, and an effect bin. Measurements remain pending until proxy execution; no generated
expectation is represented as measured evidence. Global pre-split fitting is the
procedural-only low/zero-gap construction. Lending's weak post-outcome servicing flag is the
signal-bearing low-gap construction. The measured gap and eventual bin are independent of
those labels.

harness/run_cases.py materializes and executes all twenty cases, records every failure, and
writes results/measurements.json without modifying hidden oracles. Numeric effect-bin
boundaries remain open until calibration variance is available.
