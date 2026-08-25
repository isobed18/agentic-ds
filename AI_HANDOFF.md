# AI_HANDOFF

You are picking up a repository nobody is going to explain to you in person.
This file is a map, not a task list: where things live, which rules are load
bearing, and where the current work stopped. Read it, then read the code —
this file tells you which code to read first.

---

## What this is

A fully local agentic data science pipeline. A person points it at a folder of
tabular files; it profiles them, works out how the tables join, decides what
problem is worth predicting, builds features, trains models, and writes a
report — with local LLM agents making the judgement calls and a deterministic
gate deciding whether each stage is allowed to proceed.

Nothing leaves the machine. No cloud LLM API. Models run through Ollama on
`http://localhost:11434`; development uses a 27B model on a single RTX 3090,
production is 8×H100. Dependencies must be open source; copyleft is out.

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
| Loading and profiling source files | `src/ads/intake/` |
| Leakage, measurements, validation signals | `src/ads/discovery/` |
| Joining tables into an analytical base table | `src/ads/integration/` |
| Splitting, feature work, training, export | `src/ads/splitting/`, `src/ads/training/` |
| Report and analytics rendering | `src/ads/reporting/`, `src/ads/eda/` |
| Sandboxed execution of agent-authored code | `src/ads/sandbox/` |
| Registered tools agents may call | `src/ads/tools/`, `src/ads/ds_toolkit/` |
| Procedural guidance loaded into agent prompts | `src/ads/skills/` |
| Content-addressed artifact store | `src/ads/store/` |
| HTTP API, auth gate, i18n, panel rendering | `src/ads/api/` |
| Frontend | `web/src/` |
| Launchers and one-off probes | `scripts/` |
| Design notes, research, backlog | `docs/` |

The API surface is one large module, `src/ads/api/service.py`: the control
plane and every route. Start there when you want to know what the frontend can
actually do. `web/src/lib/api.ts` is the other half of that contract.

56 test files under `tests/`, named after the thing they cover. They are the
best documentation in the repository — when you want to know what a component
is supposed to do, read its test before its implementation.

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
- **Bilingual agent prose is produced in one pass.** User-facing agent
  contracts carry canonical English and Turkish companions from the same local
  Ollama response. Do not introduce a translation agent or translate table and
  column identifiers, metrics, configuration keys, or code.

---

## How a run works

Choosing a dataset on the Staging screen starts a real run and stops it after
two stages — intake and schema discovery — so a person sees measured data, a
drawn schema, local-planner reports, relationship explanations, and a proposed
runtime plan before configuring anything. These outputs and the planner chat
are persisted in immutable `StagingWorkspace` snapshots. That stopped run keeps
its id; accepting the plan continues the same run rather than starting a second
one against the same files. Reusing a matching first-two-stage result is
optional and off by default.

Staging now also carries a persisted `PipelineBlueprint`. The browser can show
and edit this graph before the run exists, then sends it with the staging
request. Every component has typed input/output ports; connections are accepted
only when the port data types match. Once a staging artifact exists, graph
edits use its artifact ID as an optimistic concurrency token and create a new
immutable snapshot. `src/ads/staging/blueprint.py` defines the default graph and
the closed component-setting vocabulary; `web/src/components/PipelineBuilder.tsx`
renders it with React Flow and ELK.

The blueprint is not a second workflow engine. The **Default ML pipeline**
component still delegates to the existing 12-stage runner. Document engines
(Docling, Unstructured, Marker, MinerU, and the built-in text reader) are a
truthful selectable catalog and typed I/O boundary, but Docling/OCR/table
execution adapters are not implemented yet. An enabled document component with
no ready outputs blocks `fully_auto`; disable it to run only the structured
tables. Do not mark a configured engine as executed merely because it appears
in the graph.

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

Last committed MVP baseline: `c0600c8` on the private `agentic-ds-dev` remote.
The production `agentic-ds` remote was deliberately not changed.

**Implemented and tested.** Staging is a durable bilingual data-understanding
workspace with optional fingerprint cache reuse, planner reports/chat, accepted
`fully_auto` plans, measured-schema React Flow/ELK views, and a persisted
multimodal pipeline blueprint. The builder supports expandable nodes, typed
ports, matching-port connections, document-engine preferences, immutable
saves, stale-write rejection, and explicit output readiness/artifact IDs.

**Still a baseline, not n8n parity.** Components cannot yet be added from an
arbitrary registry, the graph is not compiled into a dynamic workflow spec,
and only the Default ML component executes. Document understanding still uses
the existing bounded `pypdf` text path for planner chat; Docling and the other
engine choices do not yet emit document/table/figure artifacts. Implement the
document adapter and graph compiler before claiming arbitrary pipelines run.

**Model path.** Product agents still use the configured local Ollama transport.
Claude Haiku was used once through the authenticated CLI as a cheap, read-only
development architecture reviewer; it is not imported by, configured in, or
available to the Agentic DS runtime.

**Deployment.** Served from a separate checkout behind a Cloudflare tunnel at
`api.altspacelabs.com`, password-gated, bound to loopback only. `deploy/README.md`
has the order of operations and an honest list of what that protection does not
cover. It runs from its own checkout, so edits in the working tree do not affect
the live site until it is updated deliberately.
