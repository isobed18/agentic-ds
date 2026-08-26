# AI_HANDOFF

You are picking up a repository nobody is going to explain to you in person.
This file is a map, not a task list: where things live, which rules are load
bearing, and where the current work stopped. Read it, then read the code —
this file tells you which code to read first.

---

## What this is

A guided mixed-source data-understanding and machine-learning system. A person
can give it unfamiliar PDFs, CSV/TSV/TXT files, workbooks, or Parquet files. The
default product routes every source visibly, profiles structured data, extracts
documents, synthesizes what the sources contain, decides with the person what
may enter ML, proposes a plan, and then runs the established gated ML pipeline.
The free-form graph editor is retained as **Advanced / Experimental** rather
than presented as the primary journey.

The default model transport is local Ollama on `http://localhost:11434`. A
private test deployment may explicitly opt into the authenticated Claude CLI
subscription backend. That path uses no Anthropic API key, disables tools and
repository settings, and removes provider credentials from the child process;
it is nevertheless remote inference and must never be described as local.
Dependencies must be open source; copyleft is out.

Python 3.12, FastAPI, pandas/polars/duckdb, scikit-learn, pydantic. Frontend is
React 19 + Vite + TypeScript + Tailwind. Tests are pytest, lint is ruff.

---

## The three ideas everything else follows from

**1. Three classes of evidence, and only one of them clears a gate.**
Registered deterministic tools produce gate-eligible evidence. Code an agent
writes is exploratory and can never clear a gate no matter how good it looks. A
falsifiable test an agent *proposes* and the host *executes* counts, because
the host ran it. If you blur this distinction you have deleted the product's
reason to exist.

**2. Two planes.** Agents see DataCards — schema, statistics, measured
descriptors. Agents never see source rows, and contracts carry no measurement
fields. A card is a few hundred tokens; the row data stays on the host side.
When you add a field to a contract, ask which plane it belongs to.

**3. Artifacts are content-addressed and immutable.** An artifact's id is a
SHA-256 of its semantic payload. Two identical findings have one id; changing a
payload produces a different artifact rather than mutating one. Anything you
add to a canonical payload changes every id that contains it — that is why, for
example, the Turkish half of a bilingual explanation is deliberately outside
the payload.

---

## Where things live

Read in roughly this order when orienting.

| Concern | Directory |
|---|---|
| Typed contracts — every artifact shape, the gate's vocabulary | `src/ads/contracts/` |
| Gate rules and the evaluator that applies them | `src/ads/gates/` |
| Workflow spec, runner, run state, critic | `src/ads/orchestration/` |
| Stage implementations and how they are wired into a spec | `src/ads/pipeline/` |
| Agents — one file per agent, plus the shared spec/runtime | `src/ads/agents/` |
| Loading, routing, and profiling source files | `src/ads/intake/`, `src/ads/api/service.py` |
| Document extraction, review, and promotion | `src/ads/documents/` |
| Staging blueprint and durable pre-ML workspace | `src/ads/staging/`, `src/ads/contracts/staging.py` |
| Automation catalog, compiler, runner, persistence | `src/ads/automation/` |
| Leakage, measurements, validation signals | `src/ads/discovery/` |
| Joining tables into an analytical base table | `src/ads/integration/` |
| Splitting, feature work, training, export | `src/ads/splitting/`, `src/ads/training/` |
| Report and analytics rendering | `src/ads/reporting/`, `src/ads/eda/` |
| Sandboxed execution of agent-authored code | `src/ads/sandbox/` |
| Registered tools agents may call | `src/ads/tools/`, `src/ads/ds_toolkit/` |
| Procedural guidance loaded into agent prompts | `src/ads/skills/` |
| Content-addressed artifact store | `src/ads/store/` |
| HTTP API, auth gate, i18n, panel rendering | `src/ads/api/` |
| Guided and advanced frontend | `web/src/` |
| Launchers and one-off probes | `scripts/` |
| Design notes, research, backlog | `docs/` |

The API surface is one large module, `src/ads/api/service.py`: the control
plane and every route. Start there when you want to know what the frontend can
actually do. `web/src/lib/api.ts` is the other half of that contract.

Tests under `tests/` and colocated frontend tests are named after the behavior
they cover. They are the best executable documentation in the repository —
when you want to know what a component is supposed to do, read its test before
its implementation.

---

## Invariants you must not quietly break

- **Agents never see rows.** Not in prompts, not in error messages, not in
  panels.
- **The gate is deterministic and cannot be talked out of a verdict.** An agent
  may challenge a decision through a registered mechanism; it may not overrule
  one. Autonomy settings are the *weakest* input — they can never weaken a hard
  rule such as leakage or PII egress.
- **Personal columns stay out of model features.** The sensitivity
  classification at intake decides this, and a person can correct it before
  anything downstream is built on it.
- **Artifact ids are derived, never chosen.** Build artifacts through their own
  constructors.
- **Language is chosen at the edge, not baked in.** English source strings are
  the catalogue keys on both sides (`src/ads/api/i18n.py`,
  `web/src/lib/i18n.ts`). Never write Turkish literals into code, and never
  evaluate a catalogue lookup at import time — a test guards that.
- **Codes are not words.** Stage ids, reason codes and check ids are
  identifiers used for lookups; translate them for display only.
- **Bilingual persisted artifacts are produced in one pass.** User-facing
  artifact contracts carry canonical English and Turkish companions from the
  same configured model response. Planner chat simply answers in the language
  of the conversation; do not add a chat-translation agent. Never translate
  table and column identifiers, metrics, configuration keys, or code.

---

## How a run works

The primary UI is a guided Data Project:

```text
Upload → Intake/routing → Structured and document understanding → Synthesis
       → Decide ML inputs → Propose/accept plan → Guided base pipeline → Results
```

`ControlPlane.source_profile()` performs the first deterministic routing and
keeps an exact record of every source file. Choosing **Run Intake** starts a
real, durable run. Structured sources execute intake and schema discovery;
documents execute through the selected normalized adapter. The branches may
progress independently, then converge into one bounded staging synthesis.
PDF-only sources still execute document extraction and synthesis; they do not
pretend a tabular ML problem exists.

The outputs, Planner reports/chat, selected graph, proposal, errors, and
component artifacts are persisted in immutable `StagingWorkspace` snapshots.
The run keeps its id; accepting a justified plan continues it rather than
starting a second run against the same sources. Reusing a matching staging
result is optional and off by default.

Staging carries a persisted `PipelineBlueprint`. Every component has typed
input/output ports; persisted edits are validated and use the prior artifact id
as an optimistic-concurrency token. `src/ads/staging/blueprint.py` defines the
opinionated multimodal graph. The guided UI hides authoring complexity;
`web/src/components/PipelineBuilder.tsx` exposes React Flow/ELK editing only
under **Advanced / Experimental**.

Docling, Unstructured, Marker, MinerU, and the built-in text-layer reader now
have normalized extraction adapters in `src/ads/documents/extraction.py`.
Candidate tables and figures remain evidence candidates. Only a human-reviewed
table may be promoted by `src/ads/documents/promotion.py` into a provenance-bound
`TableAsset`; the guided review/promotion interaction is not complete yet.

The default ML continuation still delegates to the established gated runner.
`src/ads/automation/compiler.py` and `src/ads/automation/runner.py` provide a
tested graph-native baseline, but many catalog capabilities still lack
production executor adapters. Do not claim arbitrary n8n-level execution.

Three supervision modes: `auto`, which retains the problem-discovery human
checkpoint; `manual`, where every stage is declared a checkpoint; and
`fully_auto`, where a human first accepts the saved planner plan and routine
human decisions are replaced by its pre-run configuration. All modes use the
same deterministic gate. Planner/autonomy preferences cannot weaken a hard
rule such as leakage, privacy, or failed exact-trial binding.

Runs persist as snapshots so they survive a restart. A run parked waiting for a
human is durable by design and is never downgraded.

---

## Working agreement

- **Write the test first, and prove it means something.** Delete the fix, watch
  the test go red, put the fix back. A test that passes with the implementation
  removed is not a test. Several tests in this repository carry a comment
  explaining the exact defect they were written against; match that standard.
- **Run things before claiming they work.** "The suite is green" is not
  verification of a change — say which test was red before it.
- **Comments explain why, not what.** The existing code documents the decision
  and, where a bug motivated it, the measurement that exposed it. Keep that.
- **Commit by pathspec, often.** Two agents have shared this working tree; a
  blanket `git add -A` has swept away another agent's staged work before.
- **Keep ruff clean and the web build passing.** The committed bundle under
  `src/ads/api/static/` must match the frontend sources — CI fails if it does
  not, and the deployment has silently served a stale UI because of this.

---

## State as of this handover

Implementation baseline before this report consolidation: `8862fa5` on branch
`codex/graph-automation` in the private `agentic-ds-dev` remote. The production
`agentic-ds` remote was deliberately not changed by this delivery sequence.

**Implemented and tested.** The default product is a guided, durable
mixed-source data-understanding flow. File routing, structured measurements,
document extraction, synthesis reports, Planner chat, plan acceptance, the
guided base-pipeline projection, artifacts, failure banners, retry, continuation,
and cooperative pause-after-current-stage are persisted or derived from the
real run. PDF-only staging executes the selected document engine. Document
candidate ids are sanitized independently from unsafe source filenames.

The full Python suite collected 818 tests: 813 passed and 5 skipped. Ruff
passed over `src` and `tests`; 17 frontend tests and the production build passed.
The loopback deployment health check returned 200 and an unauthenticated API
request returned 401.

**Still incomplete.** Narrative TXT files are currently treated as delimited
tables. The guided PDF-table review/promotion UI and automatic ML-input refresh
are incomplete. Chart values are not trusted training data. Immediate hard
cancel is absent. Many advanced graph components still lack graph-native
executors.

**Model path.** Ollama remains the default self-hosted transport. The current
private deployment is explicitly configured for `claude_cli`, model `haiku`,
low effort, and a 90-second timeout. It uses the logged-in Claude Code
subscription and no Anthropic API key, but it is remote inference.

**Deployment.** The current working checkout is served behind Cloudflare Tunnel
at `api.altspacelabs.com`, password-gated and bound to `127.0.0.1:8077`.
`deploy/README.md` documents startup and the limits of this protection.

Start future architecture work at `docs/REPORT_INDEX.md` and update
`docs/SYSTEM_ARCHITECTURE_REPORT.md` instead of creating another dated report.
