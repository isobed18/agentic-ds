# Implementation Report II — Control Plane, Hardening, and the Assurance Track

**Project:** Agentic Data Science / ML Pipeline — local, self-hosted
**Period:** 2026-08-12 → 2026-08-14
**Built by:** Claude (Claude Code) and Codex (codex CLI, `gpt-5.6-sol`, high reasoning), two
sessions collaborating in one repository
**Companion documents:** [implementation-report.md](implementation-report.md) (phase I),
[architecture-report.md](architecture-report.md) (design),
[assurance-benchmark-report.md](assurance-benchmark-report.md) (the differentiation track)

---

## 1. Executive summary

Phase I delivered a working pipeline. This phase did three things: replaced the control
plane with a real application, hardened the agent layer against defects that measurement
exposed, and opened a track to answer whether this product has a defensible reason to exist.

| | |
|---|---|
| Python source | **18,360 lines** across 21 packages |
| Python tests | **9,685 lines** — **557 passed, 2 skipped** |
| Frontend source | **3,048 lines** TypeScript / TSX |
| Benchmark source | **1,688 lines** (new, see companion report) |
| Lint | `ruff` clean across `src`, `tests`, `benchmarks` |
| Commits | 67 total, **36 this phase** |
| LLM dependency in tests | **none** |

**Fifteen issues were found: twelve defects fixed, three outstanding.** Most of the fixed
defects were in work produced during this phase, which is the honest way to read that number —
building a visual layer over a measurement layer, and a benchmark over both, is what exposed
them. The three outstanding items are recorded in §6.

The most important single result is negative: **a change I reported as shipped had not
shipped.** Panel agreement was tightened to unanimity in the dataclass default while the
loaded YAML policy kept the old value, so the behaviour never changed. Codex found it. It is
covered in §5.1 because the mechanism that let it happen is more instructive than the fix.

---

## 2. The control plane

### 2.1 What was replaced

The previous UI was `src/ads/api/dashboard.html`: a single hand-written 106KB file with
inline styles and imperative DOM updates. It had no component boundaries, no collapsible
regions, and did not fill the viewport. It was never React, despite being described as such.

It is replaced by a React 19 + Vite 7 + TypeScript + Tailwind 3 application under `web/`,
built into `src/ads/api/static/` and served by FastAPI. No CDN: the deployment target is
air-gappable, so every asset is bundled.

The SPA fallback route is registered **after** every API route. Registered before them — the
first thing I tried — it swallowed the entire API, because `/{full_path:path}` matches
everything. The Prefect experiment under `alternatives/` broke for the same reason and now
strips routes by name rather than by path.

### 2.2 Layout

Follows `design_assets/design_prompt.txt`: pipeline rail across the top, stage workspace in
the centre, planner panel on the right, and a functional left sidebar. Sidebar and planner
both collapse; the sidebar's state persists to `localStorage`. Branching is preserved
visually — nodes declaring `branch_of` stack into one column rather than flattening into a
line.

### 2.3 The visual layer

Stage output is no longer a vertical stack of collapsed headings. Each stage that measures
something renders a **horizontal strip of analysis thumbnails**, each carrying its own chart,
severity chip and one-line finding, expanding on click into the full chart, key insights and
the numbers behind it. Panels are ordered most-severe-first, because the strip is read left
to right and the finding most likely to invalidate the model should not be the one you scroll
to.

Coverage: EDA (target distribution, missingness, correlation heatmap, outliers, feature
relationships, class balance), training (candidate comparison, cross-validation stability,
lift over baseline), evaluation, leakage audit, validation strategy (split protection, split
setup), and intake (one panel per source table).

Two design decisions in `src/ads/api/panels.py` are load-bearing:

**Severity is assigned server-side.** Whether 11.8% missingness is "review" or "warning" is a
judgment about data, and it belongs on the side that measured it. A frontend computing its own
severity would eventually disagree with the gate, and a UI that stays calm about something the
pipeline stopped for is worse than either being slightly miscalibrated.

**Charts carry aggregates, never rows.** Every chart is built from counts, quantiles,
correlations and bin tallies. There is deliberately no scatter plot of observations anywhere:
a scatter is a picture of individual records, so the relationship panel ranks measured
association strength instead. This is the same two-plane boundary the agents sit behind — a
browser is no more entitled to raw records than a model is. A test asserts the emitted chart
shapes rather than grepping for a magic string.

Six chart shapes are drawn as inline SVG in `web/src/components/Charts.tsx`. No chart library:
a CDN import renders nothing on an air-gapped host, and bundling a full library costs hundreds
of kilobytes to draw six shapes.

### 2.4 Schema legibility

`design_prompt.txt` requires that schema discovery not be legible only to the agent. The
integration plan now emits a graph — base entity, joined tables, measured overlap on each
edge — rendered as a relationship map.

Two properties worth noting. A **derived table inherits the measurement taken on the table it
was rolled up from**; without that, the most common join in this pipeline (aggregate a
one-to-many table, then join the aggregate) would always display as unmeasured, which reads
as a warning about a join that is fine. And an **unmeasured join reports as `unmeasured`, not
`low`** — colouring an unknown red asserts a finding nobody made.

### 2.5 Needs attention

A stage's concerns were scattered across three places: warnings an artifact recorded about
itself, the severity assigned to each analysis panel, and the rules a gate escalation
triggered. Answering "is anything wrong here" meant opening all three. They are now collected
once at the top of the stage, blocking issues first. Nothing is recomputed — severity was
assigned when the measurement was taken.

Deduplicated in the process: an artifact's warning strings and its panel severities are two
views of the same measurements, so on EDA the same missingness finding arrived twice. Seven
items became five distinct concerns.

### 2.6 Launching a run

The "Run pipeline" button posted an empty body and received a 400. It had never worked.

It is replaced by a launch dialog that reads every choice from `/api/run-options` and the
selected source's profile — task types, metrics, split strategies, tables, candidate keys and
target columns are the backend's own closed vocabularies rather than a list retyped in the
frontend that could drift from what the API accepts. That dialog is also what finally makes
`agent_panel_size` reachable; sampling a stage several times is what produces the agreement
signal the gate consumes, and there was previously no way to ask for it.

---

## 3. Hardening: the deterministic layer

Two gaps were found by trying to draw a chart, which is a useful property of building a
visual layer over a measurement layer.

**Numeric distributions carried quantiles and no bins.** `min/p25/p50/p75/max` are identical
for a bimodal and a uniform column, and the difference changes what someone should do about
it. `TargetDistribution.histogram` now records binned counts. Binning returns nothing rather
than a degenerate chart for a constant column: a single bar spanning zero width is not a
distribution, and drawing one would imply a shape that was never measured.

**Outlier summaries carried fences without the quartiles they came from.** A fence says where
outliers begin and nothing about where the mass sits — precisely the distinction that decides
whether to clip, transform, or leave a column alone. `OutlierSummary` now carries p25/p50/p75.

Both changes are additive with defaults. Artifacts are immutable, so runs recorded before them
still render: a target distribution without bins falls back to a box built from its quantiles,
and an outlier panel without quartiles falls back to a bar of outlier shares. Nothing-to-see
is not nothing-measured.

**The split protection model is now visible.** Strategies are not ranked — each prevents a
different leak, and `grouped` is not a safer `temporal`. That partial order was enforced
deterministically but invisible: a reader saw the word "temporal" and had no way to know it
does nothing about entity repetition. The validation stage now shows, per protection, whether
the data requires it and whether the chosen strategy provides it, with one sentence on what
goes wrong without it. A required protection the strategy does not provide is an issue,
because scores from that split would be optimistic in a way no later stage can detect.

The counter-tests are the point: `grouped` against a multi-period span must report temporal
ordering as required-but-absent, and `random` must provide nothing. A panel that only ever
said yes would be worse than no panel.

---

## 4. Hardening: the agent layer

### 4.1 The tool boundary

Codex built `src/ads/tools/` — a closed registry with permission tiers
(`READ_META < READ_DATA < EXECUTE < WRITE_ARTIFACT < MUTATE_SOURCE`, the last denied to every
agent unconditionally), a broker checking both the agent allowlist and maximum tier before
dispatch, and deterministic DS tools routed through it.

Reviewing it surfaced a gap between what `execute_python` guaranteed and what it said.
Suppressing `DataFrameOutput` removes the implicit repr of a frame; it does not remove
`print(df.head().to_string())`. `/data` is mounted read-only into the sandbox container, so
code running there can put real rows on stdout, and stdout was returned in full with no cap.

Not exploitable: every planner agent is capped at `READ_DATA` and the tool is `EXECUTE`, so
the broker denies it. The risk was the docstring being read as a guarantee the next time
someone widened an allowlist. Three changes rather than a wording fix: stdout is capped at
4000 characters with an explicit `stdout_truncated` flag (a silently shortened result reads as
complete output, which is the worse failure), the docstring now names the property that
actually holds the separation, and a test fails if any planner agent is granted `EXECUTE`.
**That test was verified by deliberately raising the tier and confirming it goes red.**

### 4.2 Panel agreement

`run_agent_panel` fingerprinted the whole validated contract. These contracts carry free
prose — `business_rationale` allows 800 characters, every join and aggregation step carries
its own `rationale` — so three samples picking the same target, task and metric but wording
the explanation differently fingerprinted as three distinct answers.

Measured before changing anything, with a scripted LLM whose members differed only in prose:
**agreement 0.333, below the 0.60 threshold, escalate with reason `candidate_disagreement`** —
whose message says "genuine ambiguity rather than a clear answer". There was no ambiguity.
`panel_size > 1` was therefore actively harmful to turn on: it guaranteed a human stop on every
agent stage and blamed the model for it.

Agents now declare `narration_fields`, stripped at any depth before fingerprinting. The first
attempt declared *decision* fields instead and broke on a real collision: `aggregations` is
both the list of steps and the mapping inside each step, so keeping that subtree whole
preserved the prose inside it. Naming prose is also the safer direction — a missed prose field
costs a human an unnecessary look, while a missed decision field would hide the disagreement
the signal exists to surface. The default declares nothing and compares everything.

Counter-tests included, because a fix that only makes agreement go up is worse than the bug: a
differing join direction, base grain or target still drops agreement.

### 4.3 Unanimity

At `panel_size` 3 the reachable agreement values are 1/3, 2/3 and 1. A 0.60 threshold
therefore meant "escalate only when all three differ" — a clean 2-1 split on the target column
passed silently, on the stage whose entire purpose is to surface an ambiguous decision. The
same 0.60 at `panel_size` 2 meant "escalate unless unanimous". One number, two policies.

The threshold is now unanimity. A panel is consequently *more* interrupting than a single
agent, which is what asking for one should mean; `panel_size` 1 remains the default.

Falling short of unanimity has two distinct causes, so `panel_valid_members` travels with the
signal and the gate message distinguishes them. Calling an unstable generation "ambiguity"
would send a person hunting for a disagreement nobody expressed.

### 4.4 Abandoned runs

Runs execute in worker threads owned by the API process. A snapshot left in `queued` or
`running` with no in-memory runtime belongs to a process that is gone, and nothing will
advance it. It read as active indefinitely, with three compounding effects: the run list
showed permanent activity, a polling client refreshed forever, and `delete_run` refused it as
"active" — so the stale run could not be cleared through the product at all.

Such runs now report `interrupted` and are deletable. `awaiting_human` is deliberately
excluded and has its own test: it is durable by design and is what human-resume depends on,
so this must never be narrowed to "not terminal".

---

## 5. The defect ledger

Twelve fixed defects, ordered by what they teach rather than by severity. Three further
findings that were not fixed appear in §6.

### 5.1 The one that matters most: a fix that never shipped

I tightened panel agreement to unanimity by editing the dataclass default in
`src/ads/gates/policy.py`. `GatePolicy.load()` reads `src/ads/gates/default_policy.yaml`,
which still said `0.60`. The loaded behaviour never changed. I reported it to the user as
shipped.

Codex found it. Verified both directions afterwards:

```
2 of 3 split, threshold 0.60 (as I shipped it) -> auto_proceed (no_rule_triggered)
2 of 3 split, threshold 1.00 (after the fix)   -> escalate    (candidate_disagreement)
```

Three things worth extracting:

1. **A threshold defined in two places with nothing asserting they agree is a defect waiting
   to happen.** A test now asserts the loaded value.
2. **I verified the wrong thing.** Checking the rule through `evaluate_gate` with a bare
   `AutonomyProfile(name="full_auto")` showed it firing at no split at all, which looked like
   the whole mechanism being inert. It is not — `candidate_disagreement` is an opt-in tier-3
   signal and `full_auto` deliberately opts out. I was one step from reporting a false alarm
   about another session's area.
3. **Process:** Codex's fix was swept into one of my commits by `git add -A` while its edit sat
   uncommitted, so its work briefly carried my authorship. Corrected, and staging practice
   changed.

### 5.2 Client-side shapes invented rather than read

Three defects with one cause — I wrote TypeScript interfaces describing what I assumed the API
returned instead of reading it.

| Defect | Symptom |
|---|---|
| `StageStatus` union used `completed`/`retrying`; backend emits `succeeded`/`retry` | A finished run displayed **0/11 stages complete** |
| Node status polled from `/progress`, which returns the raw event log and no nodes | The pipeline rail never advanced |
| `StageDetail` described cards/sections the backend does not produce | Every stage read "No output recorded" |

The status vocabulary now lives in one module (`web/src/lib/status.ts`) so the rail, the tone
map and the progress bar cannot drift apart again.

### 5.3 Controls that could never have worked

- **Delete run** posted no body; the endpoint requires the run id back as confirmation and
  answers 409 without it. It now asks in-row before destroying artifacts.
- **Run pipeline** posted `{}` and received a 400 (§2.6).
- **`instructions`** means free text on `POST /api/runs` and a list of correction lines on
  `/answer`. A list reached `.strip()` and raised `AttributeError`, which is not in the caught
  tuple, so a malformed body returned a 500 stack trace instead of a 400.

### 5.4 React state defects

- **A polling loop.** The interval effect depended on the `nodes` array that `setNodes`
  replaces every tick, so each poll rescheduled itself immediately.
- **A stale closure.** Launching a run set the new id and immediately called `refresh()`, a
  `useCallback` closed over the *previous* `runId` — so a brand-new run rendered the last run's
  eleven completed stages under its own header. Caught by watching a real launch in the
  browser, not by a test.

---

## 6. Outstanding

Three findings that were not fixed, with the reasoning.

### 6.1 The splitting stage records no artifact

**The splitting stage records no artifact.** Every other stage produces at least one; this one
produces none. Cosmetically it is a blank pane, but the real issue is that the resolved fold
boundaries are never stored — the validation *strategy* is recorded (`temporal`, 5 folds,
cutoff `2021-01-01`) but not what the split resolved to.

That is a reproducibility hole. A holdout score is only meaningful with respect to a specific
split, and if the split is never recorded, a rerun producing a different partition cannot be
distinguished from one that reproduced it.

It sits in Codex's ownership area and was flagged rather than fixed. Both sessions agreed the
artifact should record per-fold digests, realised boundaries, counts, and the ordering rule and
seed — **not row membership**, because row identifiers are row data and the artifact store is
served to the browser.

### 6.2 `src/ads/skills/` is not a mechanism

Three markdown files, zero loaders; the only reference anywhere is a docstring in
`validation_signals.py`. The "procedural knowledge library" described in the architecture
report is documentation of what a human already implemented, not something the system consults.
Raised by Codex in audit, confirmed independently by grep. Either load it and make it a
mechanism, or stop describing it as one.

### 6.3 `require_tool_evidence` is weaker than it was presented

It establishes that a named tool ran, not that the tool's output entails the agent's
conclusion. Closing that gap is part of the evidence-ledger design in the companion report and
was deliberately not patched in isolation.

---

## 7. Collaboration mechanics

Two agent sessions worked this repository simultaneously. The working agreement is
a written working agreement; the channel was an append-only dialogue file with git
as transport and tagged entries — FINDING, QUESTION, PUSHBACK, PROPOSAL, DECISION, HANDOFF,
BLOCKED.

Ownership: Claude holds `web/**` and `src/ads/api/**`; Codex holds `agents/`, `discovery/`,
`eda/`, `intake/`, `integration/`, `orchestration/`, `gates/`; `contracts/` is shared and
announce-first.

**One environment constraint shaped the whole phase.** Codex's sandbox cannot spawn processes
on this host (`CreateProcessWithLogonW failed: 2`), so it can run neither Python nor git. Every
command it needed was executed by the Claude session and the real output pasted back, and every
commit was made on its behalf with attribution.

This halves its throughput. It also, unexpectedly, improved quality: a second party executing
and independently verifying caught defects the author could not have caught, including both
benchmark leaks in the companion report. Codex was consistently scrupulous about never claiming
a verification it could not perform, which is what made the arrangement workable.

---

## 8. Verification standard

This project's phase I ledger recorded that tests written alongside code caught **zero of
eighteen** real defects, and that three separate fixes recreated the defect they closed. Both
sessions are held to a stricter bar as a result:

- Measure before claiming.
- When adding a test for a defect, break the fix deliberately and confirm the test goes red.
  Done for the `EXECUTE`-tier test (§4.1).
- Prefer counter-tests: if a change makes a signal go up, add the case where it must still go
  down. Done for panel agreement (§4.2) and split protection (§3).
- Never key a control decision off an incidental property of the data when control flow already
  knows the answer — the mechanism behind all three recreated defects.

---

## 9. Recommended next steps

1. **Record the split artifact** (§6). It is the only known reproducibility hole.
2. **Resolve the benchmark comparator decision** — see the companion report; it is a budget
   question, not a technical one.
3. **Predeclare falsification thresholds** in the benchmark spec before any baseline exists,
   so they cannot be tuned to results.
4. **Consider making the dataclass defaults the single source for gate thresholds**, with YAML
   as override-only (§5.1).
5. **Restore or retire `src/ads/skills/`** — either load it and make it a mechanism, or stop
   describing it as one.
