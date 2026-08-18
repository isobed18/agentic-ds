# Borrowed Infrastructure and Data-Science Engines

**Decision date:** 2026-08-17  
**Scope:** source and documentation research only; no packages were installed or
executed during this block because the deployed local model owned the GPU.

## Decision

Do not replace the current Docker/Jupyter executor now. It is small enough to
audit, already passes live isolation tests, and enforces properties the closest
framework executors do not: no network, a read-only source mount, a separate
writable artifact mount, a read-only root filesystem, a persistent kernel, no
host fallback, and explicit resource limits.

Do stop duplicating the commodity agent loop. Pilot `pydantic-ai-slim` behind an
adapter to the existing local `StructuredLLM`. Let it own model/tool turns,
structured-output decoding, ordinary output retries, and configurable message
history. Keep ADS's `PermissionBroker`, tool implementations, semantic
validators, attempted-call budget, manifests, evidence classes, and gates. The
pilot succeeds only if an existing investigator can be migrated without changing
its persisted artifacts or gate behavior.

For stronger execution isolation, build a parity spike for Microsandbox behind
the existing `ExecutionBackend` protocol after the feature pipeline is complete.
It is the only serious executor candidate found that improves the boundary: a
local microVM with its own kernel rather than another container wrapper. Do not
make it the default until offline image loading, deny-all networking, Windows
support, persistent state, mounts, timeouts, cleanup, and artifact extraction are
measured.

For data science, adopt narrowly rather than importing an entire platform:

- use Pandera for deterministic dataframe pre/postconditions at copy and feature
  recipe boundaries;
- register Cleanlab as a deterministic label/data-issue measurement provider once
  out-of-fold predictions exist;
- make selected skrub transforms available inside the sandbox for dirty strings,
  datetimes, and relational aggregation, subject to fold-local replay and the
  existing leakage checks.

None of these libraries may decide a gate, mutate a source, publish directly to
the UI, or turn an agent-computed value into deterministic evidence.

## The seam is already present

`src/ads/sandbox/backend.py` defines the right boundary:

```text
available()
create_session(run_id)
execute(session, code, timeout) -> ExecutionResult
destroy(session)
```

`data_dir` and `artifacts_dir` are also exposed without mentioning Docker. A
borrowed executor can therefore sit below the evidence-class architecture. The
adapter, not the executor, is responsible for converting raw stdout, figures,
files, and errors into the existing `ExecutionResult`. The manifest validator
continues to be the only publication path. The host still computes gate-eligible
measurements from independently re-executed recipes or returned predictions.

The evidence architecture is not a reason to keep a home-grown executor. It is a
reason to preserve this seam. There is no executor-specific property in
`deterministic` versus `exploratory`, gate exclusion, typed manifests, or the rule
that agent-authored code cannot clear a gate.

There is also less custom executor code than the initial concern implied. ADS has
572 lines under `src/ads/sandbox`; it already delegates the Jupyter wire protocol
to `jupyter_client` inside the image. The larger maintenance burden is the
repeated agent loops and tool plumbing, which is why the first replacement target
is the loop rather than the isolation backend.

## Candidates taken seriously

### Pydantic AI slim: adopt as an agent-loop pilot

**What it replaces.** The repeated turn loop in the investigator agents:
requesting structured actions, dispatching typed function tools, returning tool
results, validating final structured output, and issuing ordinary corrective
retries. It can also replace accidental history handling with an explicit history
processor.

**What remains ours.** `PermissionBroker` stays in front of every registered
tool. ADS contracts remain final outputs. ADS semantic validators still reject
decorative evidence and impossible proposals. The existing `ToolRuntime` still
creates executor-owned measurement records. The orchestration runner and gates
do not move.

**Import-time behavior.** Static inspection of the slim package initializer and
core manifest found local imports and metadata only, with no import-time network
call. OpenTelemetry is an API dependency, but exporting is opt-in through explicit
instrumentation such as Logfire. This is not yet a runtime air-gap proof.

**Dependency weight.** The slim distribution adds `griffelib`,
`pydantic-graph`, `opentelemetry-api`, `typing-inspection`, and `genai-prices`;
ADS already has Pydantic and HTTPX. Do not
install provider, Logfire, web, MCP, or UI extras. The dependency is meaningful
but proportionate to the duplicated loop it replaces. Pin a stable version: the
project is moving quickly and its current documentation spans major-version
changes.

**Migration cost.** Implement one local-model adapter around `StructuredLLM`, one
tool adapter that always calls `PermissionBroker.execute`, and one output adapter
per investigator contract. Migrate a single mature investigator first. Compare
the full persisted artifact and permission audit, not merely its final answer.

**Non-negotiable mismatch.** Pydantic AI's documented `tool_calls_limit` counts
successful invocations. ADS deliberately counts attempted calls, including
failures, because repeated invalid calls still consume the safety and latency
budget. The ADS counter must wrap each tool attempt and remain authoritative.
Pydantic AI request/token limits may supplement it but cannot replace it.

Sources: [agent and structured-output model](https://pydantic.dev/docs/ai/core-concepts/agent/),
[slim dependency manifest](https://github.com/pydantic/pydantic-ai/blob/main/pydantic_ai_slim/pyproject.toml),
[output validators and retries](https://pydantic.dev/docs/ai/core-concepts/output/).
Repository: `pydantic/pydantic-ai`; recorded licence: MIT.

### Microsandbox: prototype as a future execution backend

**What it replaces.** `SandboxManager`'s Docker container lifecycle and the
container security boundary. It does not replace tool permissions, the agent
loop, output normalization, manifests, or host verification.

**Why it is materially different.** Microsandbox embeds a local microVM runtime.
Agent code runs behind a guest-kernel boundary rather than sharing the host kernel
through Docker namespaces. Its current SDK exposes OCI images, execution,
filesystem transfer, mounts, network policy, metrics, logs, and snapshots. The
project explicitly documents local/on-prem air-gapped operation with no telemetry.

**Import-time behavior.** Static inspection found that the Python initializer
loads local native bindings and locates the bundled runtime; it did not contain a
network or telemetry call. The claim still needs a network-denied runtime test.

**Dependency weight.** The Python package declares no Python runtime
dependencies, but this is not “free”: its wheel carries a native runtime and VM
firmware, and the platform must support Linux KVM, Apple virtualization, or the
current Windows hypervisor path. The project is beta and Windows support is
preview. Operational weight and release risk are higher than the Python manifest
suggests.

**Migration cost.** Implement `MicrosandboxBackend(ExecutionBackend)`. Preload or
import the ADS OCI image without a registry call; deny all networking; mount the
copied dataset read-only and the artifact directory read-write; start a persistent
Python kernel or process; normalize outputs into `ExecutionResult`; and prove
destroy-on-timeout and destroy-on-crash. Callers need no rewrite.

**Adoption bar.** Parity tests must cover state across calls, source write denial,
artifact writes, no egress, local-image-only startup, resource ceilings, timeout
cleanup, dataframe suppression, figure extraction, and zero host fallback. Until
then Docker remains the default.

Sources: [Microsandbox architecture and air-gap claims](https://microsandbox.dev/),
[Python package and platform requirements](https://pypi.org/project/microsandbox/).
Repository: `superradcompany/microsandbox`; recorded licence: Apache-2.0 for the
local runtime.

### Pandera: adopt at dataframe boundaries

**What it contributes.** Mature, reusable checks for dataframe schemas and
cross-column invariants. It should validate the input and output of copied-data
operations and accepted feature recipes. It does not replace Pydantic artifact
contracts or the intake profiler.

**Import-time behavior.** Static inspection found local type/backend registration
only and no networking. **Dependency weight:** core adds `packaging`, `typeguard`,
`typing-extensions`, and `typing-inspect`; the pandas extra reuses NumPy/Pandas
already present. **Migration cost:** translate only repeated dataframe assertions,
starting at the feature recipe boundary; do not introduce its optional IO or
distributed stacks.

Source: [Pandera manifest](https://github.com/unionai-oss/pandera/blob/main/pyproject.toml).
Repository: `unionai-oss/pandera`; recorded licence: MIT.

### Cleanlab: adopt as a measurement provider

**What it contributes.** Label-error ranking, outlier/duplicate signals,
multi-annotator checks, and regression/classification data-quality analyses. This
fills a real gap after the governed pipeline can supply out-of-fold predictions.

**Import-time behavior.** Static inspection found local module imports and lazy
handling for optional `Datalab`; no network call. This assessment applies to the
open-source `cleanlab` package, not the separate cloud-oriented Studio client.
**Dependency weight:** NumPy, Pandas, and scikit-learn already exist; only `tqdm`
and `termcolor` are new core dependencies. **Migration cost:** adapt its outputs
to row-free `MeasurementRecord` summaries and keep any row-level issue ranking in
an internal immutable artifact. Never auto-delete labels.

Source: [Cleanlab manifest](https://github.com/cleanlab/cleanlab/blob/master/pyproject.toml).
Repository: `cleanlab/cleanlab`; recorded licence: Apache-2.0 in the repository
licence file at the time of review. Licence is recorded, not used as a gate in
this phase.

### skrub: adopt selected transforms inside the sandbox

**What it contributes.** Robust encoding of dirty categorical/text columns,
datetime transforms, table vectorization, fuzzy joins, and relational aggregation.
The strongest near-term use is a richer feature-engineering toolbox, not its
reporting surface.

**Import-time behavior.** Static inspection of the top-level imports and package
manifest found local registrations and no networking. The package depends on
`requests`, so the air-gap conclusion still requires a runtime test of the actual
selected transforms. **Dependency weight:** ADS already has NumPy, Pandas, and
scikit-learn; new core packages include SciPy, Jinja2, Matplotlib, Requests, and
Pydot. Put it in the sandbox image, not the host control plane.

**Migration cost.** Register an allowlisted subset as recipe operations. Every
adopted transform must be fitted inside each training fold, retain source-column
lineage, and be replayable by deterministic code. Fuzzy joins remain exploratory
until integration trials and human confirmation establish their semantics.

Source: [skrub manifest](https://github.com/skrub-data/skrub/blob/main/pyproject.toml).
Repository: `skrub-data/skrub`; recorded licence: BSD-3-Clause.

**Implementation contact result (2026-08-18).** Dirty-string and datetime
vectorization landed as a pinned, train-only helper in the no-network sandbox.
`AggJoiner` did not: the feature sandbox currently receives one ABT training
copy and one validation copy, not auxiliary tables with lineage and as-of
availability. Adding relational aggregation before that contract exists would
make leakage easy while presenting the operation as sanctioned. The Dockerfile
and live parity test are committed, but the image rebuild was not verified in
this block because Docker Desktop's WSL backend failed with `0xc00000fd`; it was
not restarted from the shell.

## Rejected or deferred

- **AutoGen Docker command executor:** reject. It executes blocks as new
  processes, defaults to pulling a missing image, exposes a local executor in the
  same family, and AutoGen is now in maintenance mode. The stateful Docker/Jupyter
  executor is explicitly experimental and runs a network-reachable Kernel Gateway
  on port 8888, adding Docker, websocket, HTTP, and AutoGen-core dependencies while
  fighting ADS's `--network=none` boundary. [Official executor docs](https://microsoft.github.io/autogen/stable/reference/python/autogen_ext.code_executors.docker_jupyter.html).
- **Jupyter Enterprise Gateway:** reject. It is a multi-user network service for
  remote Kubernetes/YARN/SSH kernels, not an embedded single-run isolation layer;
  its server and infrastructure dependency surface is much larger than the code it
  would replace.
- **E2B, Daytona, Modal, Azure dynamic sessions:** reject. Their normal execution
  path is a remote service. Self-hosting a control plane to recover air-gapped
  operation is heavier than the embedded executor and creates no evidence-layer
  benefit.
- **OpenHands, LangGraph, smolagents, CAMEL, Microsoft Agent Framework:** reject as
  the main runtime. They are broader application/orchestration systems, not a
  smaller substitute for the precise loop or executor seam; their conversational
  and UI affordances would need to be disabled to preserve typed publication.
- **Piston, local subprocess executors, embedded IPython:** reject. Process-level
  execution is not the required security boundary, and any local fallback violates
  an explicit invariant.
- **Pyodide/WASM-only Python executors:** reject for this workload. They cannot run
  the full native Pandas/Polars/DuckDB/scikit-learn stack and artifact workflow.
- **Great Expectations:** reject. It duplicates a much broader data-context,
  expectation, checkpoint, and documentation system; normal Data Context use has
  analytics enabled by default unless explicitly disabled. Pandera supplies the
  needed dataframe boundary checks with much less weight. [GX analytics behavior](https://docs.greatexpectations.io/docs/core/configure_project_settings/toggle_analytics_events/).
- **Deepchecks:** reject. Its package initializer calls a latest-version/usage
  function at import; that is incompatible with the air-gap rule even before its
  large plotting and model-validation dependency set is considered.
- **Evidently:** reject. It brings Plotly, Statsmodels, SciPy, NLTK, Litestar,
  Uvicorn, Watchdog, `iterative-telemetry`, and OpenTelemetry protocol packages for
  a monitoring/report platform. ADS needs selected measurements, not another
  server and report model. [Dependency manifest](https://github.com/evidentlyai/evidently/blob/main/pyproject.toml).
- **ydata-profiling:** reject. Its broad HTML-report stack includes SciPy,
  Matplotlib, Jinja2, visions, phik, Seaborn, Statsmodels, Wordcloud, Numba, and
  Requests; it would duplicate the row-free profiler while constraining NumPy
  versions. [Dependency manifest](https://github.com/ydataai/ydata-profiling/blob/develop/pyproject.toml).
- **Featuretools:** defer. Deep Feature Synthesis is relevant, but the reviewed
  release requires NumPy below 2, adds Woodwork/SciPy/Holidays, and executes
  installed initialization entry points on import. More importantly, its temporal
  cutoff semantics must be integrated into ADS lineage before generated features
  are safe.
- **FLAML:** defer. It is a credible sandbox-only model-search engine and its base
  import is local, but useful AutoML extras add LightGBM/XGBoost/SciPy. The current
  host-scored model-experiment seam should first prove governed promotion and exact
  replay.
- **Optuna:** defer. It is mature, but Alembic/SQLAlchemy are unnecessary weight
  for bounded local search, and it supplies optimization rather than models or
  data-science semantics.
- **Alibi Detect and NannyML:** defer until a real monitoring/drift stage exists.
  Their mature drift methods are valuable, especially beyond tabular data, but
  adopting a monitoring stack before the product has reference/current dataset
  semantics would create machinery without a governed consumer. NannyML also
  requires LightGBM. [Alibi Detect capabilities](https://github.com/SeldonIO/alibi-detect),
  [NannyML scope](https://github.com/NannyML/NannyML).
- **Giskard:** reject for this purpose. Current releases target evaluation of LLM
  agents rather than the tabular scientific pipeline, and its own documentation
  records optional usage analytics. It does not replace the assurance corpus.

## Adoption sequence

1. Finish the governed feature pipeline already at the top of `docs/BACKLOG.md`.
   Otherwise skrub, Cleanlab, and AutoML candidates have nowhere safe to land.
2. Run a one-investigator `pydantic-ai-slim` parity pilot. Delete the pilot if it
   cannot preserve attempted-call accounting, artifacts, and gate outcomes with a
   meaningfully smaller ADS loop.
3. Add Pandera at the accepted-feature recipe boundary.
4. Add Cleanlab and selected skrub operations as registered deterministic or
   exploratory providers, respectively, with executor-owned outputs.
5. Run the Microsandbox parity spike. Promote it only on measured isolation and
   lifecycle parity, not on its architecture claim.

This sequence borrows mature implementation where it removes maintenance cost,
but does not confuse framework adoption with product intelligence. The product's
domain value remains the governed relationship among proposals, measurements,
counter-tests, human decisions, and immutable evidence.

## Verification intentionally deferred

Source inspection is evidence of what the reviewed code says; it is not proof of
runtime behavior or a resolved dependency graph. Before adopting any candidate:

- resolve each candidate in a clean Python 3.12 environment and record the full
  transitive dependency delta;
- import it in a fresh process with outbound networking denied and detect attempted
  DNS/socket/HTTP access;
- repeat the network-denied test for the exact APIs ADS will call, not only the
  package initializer;
- for Pydantic AI, deliberately fail tools and prove every attempt consumes ADS's
  budget; compare artifacts and gate verdicts against the current loop;
- for Microsandbox, run the full backend parity and escape-boundary suite using a
  preloaded local image and no registry access;
- for data-science providers, use counter-datasets that prove fold locality,
  temporal cutoffs, lineage, and gate exclusion.
