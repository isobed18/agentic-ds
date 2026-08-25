# Agentic DS System Architecture Report

## 1. System purpose and current product shape

Agentic DS is a local-first, self-hosted data-understanding and machine-learning automation
system. Its primary UI is one n8n-style graph workspace in which a user can upload structured
files and PDFs, inspect a recommended multimodal graph, talk to a planner, edit typed component
connections, run the graph, and open the immutable artifacts attached to each producer node.

The production agent path uses local Ollama models. Claude/Codex are development tools only and
are not runtime dependencies or fallback providers.

The architecture separates four responsibilities:

1. **Evidence production** — deterministic loaders, profilers, extractors, trials, training, and
   evaluation create measured artifacts.
2. **Agent judgment** — local agents interpret bounded evidence and propose reports, problems,
   validation strategies, and graph changes.
3. **Control and safety** — typed contracts, graph validation, gates, retry policy, human/planner
   ownership, and sandboxing constrain execution.
4. **Presentation** — React Flow renders the same persisted graph and artifact lineage that the
   backend compiles and executes.

## 2. End-to-end flow

### 2.1 Upload and source registration

`src/ads/api/service.py` owns the HTTP control plane. `upload()` writes supported files into a
source group and returns a source id. Files uploaded together remain one logical source so
relationships can be measured across CSV, Excel, Parquet, and PDF inputs.

Important files:

- `src/ads/intake/loaders.py` — loads structured formats and normalizes loader errors.
- `src/ads/documents/pdf.py` — reads safe, page-provenanced PDF metadata/text for source
  profiling and bounded planner context.
- `web/src/pages/AutomationWorkspace.tsx` — the single front door for drag/drop, source
  selection, optional cache reuse, graph execution, and branch status.

Raw rows are not sent to agents or browser projections. The browser receives schema statistics,
relationship evidence, artifact summaries, and approved document projections.

### 2.2 Staging: intake and schema discovery

Selecting **Run data understanding** creates a real run and executes intake through schema
discovery. This is not a preview: the resulting state and artifact ids are reused when the ML
pipeline continues.

The intake stage is implemented primarily by:

- `src/ads/pipeline/stages.py` — stage functions and blackboard bindings.
- `src/ads/intake/profiler.py` — deterministic table/column profiling, missingness,
  distributions, semantic types, sensitivity, and candidate keys.
- `src/ads/intake/keys.py` — key-candidate measurements.
- `src/ads/contracts/datacard.py` — immutable DataCard and column-profile contracts.

Schema discovery uses:

- `src/ads/agents/schema_discovery.py` — proposes an integration plan from row-free evidence.
- `src/ads/agents/schema_investigator.py` — performs bounded follow-up investigation.
- `src/ads/discovery/measurements.py` — deterministic relationship measurements.
- `src/ads/integration/executor.py` — trials proposed joins against real frames and measures
  cardinality/fan-out/orphan behavior.
- `src/ads/contracts/integration.py` — separates agent-authored proposals from executor-owned
  measured evidence and persisted IntegrationPlan artifacts.

Caching is optional. A source fingerprint identifies unchanged inputs, but a user can disable
reuse and rerun intake/schema discovery every time.

PDF-only sources also receive a persisted staging graph. Structured intake and the ML template
remain disabled until a trustworthy structured table exists.

### 2.3 Multimodal document path

The default graph routes PDFs from `data.upload.documents` into `document.extract` while
structured files route independently into table profiling.

`src/ads/documents/extraction.py` defines a replaceable adapter boundary. The selectable local
engines are Docling, Unstructured, Marker, MinerU, and a text-layer fallback. Marker and MinerU
can run in isolated worker environments because their dependency pins can conflict with the
main application.

Document extraction produces page/region-provenanced content, table candidates, and figure
candidates using contracts in `src/ads/contracts/documents.py`. Candidate tables must pass the
explicit `document.review_tables` component before they become `accepted_tables`. Current MVP
behavior does not silently turn charts or candidate PDF tables into training rows.

### 2.4 Planner and report generation

The planner is a graph control plane, not a dataflow node. `ControlPlane.planner_chat()` in
`src/ads/api/service.py` receives only bounded source profiles, intake/schema artifacts,
page-provenanced document context, the registered component catalog, the saved graph, and recent
chat history.

The planner may:

- explain measured relationships;
- create bilingual report artifacts (English and Turkish fields in one structured response);
- recommend runtime configuration and stage directives;
- add registered components;
- connect exact typed ports;
- update allowed settings and node control policy;
- disable components; and
- expand up to three independent ML problem branches.

It cannot invent code, component kinds, ports, data types, or setting keys. Chat itself is a
single response in the user's language; translations are generated only for persisted artifacts.

Planner reports are stored as `StagingReportArtifact` objects and attached to the exact report
component through `producer_component_id`. The whole workspace, recommendations, and chat history
are also persisted as an immutable `StagingWorkspace` snapshot.

### 2.5 Graph authoring and validation

The graph vocabulary lives in `src/ads/automation/catalog.py`. Each catalog definition has one
responsibility, named typed inputs/outputs, closed defaults, an evidence layer, and repeatability
rules. The default template is assembled in `src/ads/staging/blueprint.py`.

Validation occurs on every host-side graph mutation:

- component ids must be unique;
- components must come from the registered catalog;
- catalog ports and capability metadata cannot be spoofed;
- connections must reference existing exact endpoints;
- source and target data types must match;
- single-input ports cannot receive multiple edges;
- the graph must be acyclic; and
- every enabled required input must be connected before compilation.

Each node also has an execution control:

- `execution`: continue automatically or pause after artifacts are produced;
- `gate_handler`: planner or human handles ordinary escalation;
- `max_retries`: optional per-node retry budget.

Deterministic hard gates remain active regardless of these preferences.

### 2.6 Compilation and execution

`src/ads/automation/compiler.py` converts the persisted visual blueprint into an immutable
`AutomationExecutionPlan`. Compilation validates the graph, computes deterministic topological
order, binds registered catalog capabilities to executor/stage seams, freezes node controls, and
hashes the semantic blueprint.

The execution-plan artifact is the authority for a run. React Flow state is never executed
directly.

The existing ML runtime is defined by:

- `src/ads/pipeline/workflow.py` — complete stage specification and routing.
- `src/ads/orchestration/spec.py` — validated workflow/stage/edge definitions.
- `src/ads/orchestration/runner.py` — stage execution, gate evaluation, retry routing,
  checkpointing, resume, and pause-after behavior.
- `src/ads/orchestration/state.py` — run state, attempts, blackboard bindings, and artifacts.
- `src/ads/pipeline/agent_stages.py` — problem discovery, validation planning, and other
  local-agent stage adapters.

The default ML template expands into problem discovery, validation, EDA, leakage audit, feature
engineering, splitting, training, evaluation, and final report components. When the planner adds
multiple problem branches, each branch launches as a separate child run with a fresh agent set.
Child artifacts are projected back onto the corresponding branch nodes on the parent graph.

### 2.7 Modeling path

Important modules after schema discovery:

- `src/ads/agents/problem_discovery.py` and `src/ads/contracts/problem.py` — supported task,
  target, metric, exclusions, and problem confirmation.
- `src/ads/agents/validation_strategy.py`, `src/ads/discovery/validation_signals.py`, and
  `src/ads/contracts/validation.py` — split strategy proposal plus measured support/trial.
- `src/ads/eda/profiler.py` and `src/ads/agents/eda_investigator.py` — deterministic EDA plus
  bounded agent investigation.
- `src/ads/discovery/leakage.py`, `src/ads/agents/leakage_investigator.py`, and
  `src/ads/contracts/leakage.py` — deterministic and adversarial leakage checks.
- `src/ads/training/runner.py` — candidate execution.
- `src/ads/training/frame_contracts.py` — train/validation/holdout frame invariants.
- `src/ads/training/persistence.py` — model artifact persistence.
- `src/ads/reporting/evaluation.py` and `src/ads/reporting/markdown.py` — evaluation and final
  report production.

### 2.8 Safety, isolation, and persistence

`src/ads/gates/` owns deterministic policies and quality/safety verdicts. Agents can propose or
retry; they cannot weaken hard safety rules.

`src/ads/sandbox/` isolates agent-authored analysis and feature code from the API process. Data
and artifact directories are explicit capabilities rather than ambient filesystem access.

`src/ads/store/artifacts.py` is the content-addressed SQLite-backed artifact store. Semantic
payloads are immutable; `created_at` is excluded from the content hash, so identical reruns are
idempotent. Large binary side-payloads live in per-artifact blob directories. Run snapshots under
the run-state directory make progress and audit history inspectable after restart.

## 3. Frontend architecture

`web/src/pages/AutomationWorkspace.tsx` is the primary page. It coordinates upload, source/run
selection, staging, compile/start, document execution, branch launches, polling, and cache choice.

`web/src/components/PipelineBuilder.tsx` renders the registered component library, ELK-laid-out
React Flow canvas, typed handles, node badges, component settings, execution controls, and
artifact inspector. The right-side panel switches between selected-node details and
`PlannerPanel.tsx`, keeping configuration and conversation in the graph workspace.

Artifacts are displayed on their producer nodes. Clicking an artifact requests a safe preview;
document previews show page-provenanced candidate metadata, and staging report previews show the
human-readable report and verification questions.

## 4. Deployment topology

FastAPI serves both `/api/*` and the built React bundle from `src/ads/api/static`. Uvicorn listens
on `127.0.0.1:8077`; the public hostname is exposed through the existing Cloudflare tunnel.
`scripts/start_public.ps1` launches the server detached and writes durable stdout/stderr logs to
`data/logs/`, preventing the previous Bad Gateway failure caused by task-attached server lifetime.

Authentication is installed by `src/ads/api/auth.py` from environment configuration. Secrets are
not stored in this report or committed source.

## 5. Current boundaries and next engineering risks

- Graph compilation is fully typed, but runtime capability adapters still project registered
  nodes onto the established pipeline/document/report executors. Adding a new catalog capability
  requires a host-owned executor adapter; the planner cannot create one.
- Candidate tables/figures extracted from PDFs remain evidence until reviewed. Chart-to-training-
  row conversion needs a separate deterministic extraction/reconciliation capability and quality
  contract.
- Branch runs are intentionally isolated for correctness. Cross-branch resource scheduling and
  comparison reports are the next scaling concern.
- The web bundle should later split React Flow/ELK into lazy chunks; this is a performance issue,
  not an execution-integrity issue.
- Local-model latency remains significant. Content-addressed artifacts and optional fingerprint
  caching reduce repeated work without changing evidence semantics.

## 6. Key invariants for future changes

1. Never send raw source rows or sensitive passages to an agent.
2. Never represent agent interpretation as measured evidence.
3. Never execute browser graph state directly; compile the persisted validated blueprint.
4. Never allow a planner/human preference to weaken a deterministic hard gate.
5. Never use unreviewed PDF candidates as training data.
6. Preserve content-addressed, immutable artifacts and exact producer lineage.
7. Keep runtime agents local unless the product requirements explicitly change.
