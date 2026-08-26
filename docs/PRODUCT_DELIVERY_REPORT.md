# Agentic DS — Current Product Delivery Report

**Delivery snapshot:** 2026-08-26

**Code baseline:** `8862fa5`

**Branch/remote:** `codex/graph-automation` on private `agentic-ds-dev`

## Delivered product shape

The main product is now a guided data-science journey rather than a blank automation editor.

```text
Home → Data project → Upload → Intake → Understand → Review ML inputs
     → Review/accept plan → Guided base pipeline → Results
```

Advanced graph authoring is retained behind **Advanced editor · Experimental**.

## Delivered capabilities

### Guided onboarding and staging

- Home identifies the recommended next action.
- Mixed files upload into one traceable source group.
- Intake is the first visible phase.
- Every file remains visible under Structured, Documents, or Needs review.
- Structured and document branches progress independently.
- Running, waiting, complete, and failed states are visible without fake percentages.
- Document extraction shows the selected engine, version, OCR mode, file/page progress, candidate
  counts, duration, warnings, and artifacts.
- Staging failures stop the run and create a visible error state.

### Durable understanding

- Intake/schema/document outputs, relationship explanations, reports, Planner chat, graph/layout,
  runtime recommendation, and errors are persisted as immutable staging artifacts.
- Optional fingerprint cache reuse is off by default.
- Measured relationships and agent interpretation are shown separately.
- English and Turkish companions are generated for persisted artifacts in one response.

### Document processing

- Docling, Unstructured, Marker, MinerU, and text-layer adapters share a normalized output contract.
- PDF-only staging actually executes extraction and synthesis.
- Per-file partial failures are retained.
- Candidate identifiers are sanitized independently of unsafe filename characters.
- Human review and promotion endpoints create provenance-bound `TableAsset` artifacts.
- Candidate PDF tables remain untrusted until reviewed and promoted.

### Planner and ML handoff

- Planner may recommend creating, deferring, or refusing an ML pipeline.
- The proposal shows which structured files enter ML and which documents are context only.
- Base table, grain, objective, execution scope, checkpoints, and rationale are visible before run.
- Accepting the proposal materializes a guided projection of the established base ML pipeline.
- A no/deferred recommendation does not expose a misleading primary Run action.

### Execution transparency

- Guided stages derive status from the actual workflow/run APIs.
- Artifact chips attach to the stage/group that produced them.
- Stage inspection opens measured outputs and artifact previews.
- Run, continue, cooperative pause-after-current-stage, retry-from-intake, and results navigation
  are available.
- Cooperative pause preserves the current run and resumes from the next stage.

### Advanced baseline

- Registered component catalog with typed versioned ports and closed settings.
- Persisted blueprint and UI-only layout.
- Graph validation and immutable compilation.
- Restart-safe graph-native sequential runner with exact bindings and semantic cache keys.
- Advanced editor with React Flow, ELK layout, component inspector, artifacts, Planner, branching,
  and per-node review controls.

This is a baseline, not arbitrary n8n-equivalent execution. The default ML template still runs
through the established gated pipeline; not every visual component has a graph-native executor.

## Runtime used by the private deployment

The deployment is configured for `claude_cli`, model `haiku`, effort `low`, timeout 90 seconds.
It uses the authenticated Claude Code subscription and strips API/provider variables. No Anthropic
API key is used. This backend is remote inference and is enabled only for the private test
deployment. Ollama remains the default self-hosted backend.

## Verification performed

- Full Python suite: passed.
- Ruff over `src` and `tests`: passed.
- Frontend: 5 test files / 17 tests passed.
- TypeScript and Vite production build: passed.
- Public-server health on `127.0.0.1:8077`: 200.
- Unauthenticated API request: 401.
- Latest implementation commits pushed to `agentic-ds-dev`.

## Open product gaps

| Gap | User impact | Next implementation boundary |
|---|---|---|
| Narrative TXT is treated as a table | wrong route for reports/notes | source-content classifier |
| PDF review/promotion UI incomplete | promoted data cannot be completed from guided canvas | review cards + input-plan refresh |
| Figure/chart values are candidates only | chart data cannot enter training safely | chart extraction + reconciliation contract |
| Source roles not first-class artifacts | Planner revisions may diverge from derived UI labels | durable source-use decision contract |
| Immediate hard stop absent | long stage finishes before pause | cancellable worker/process protocol |
| Partial graph executor coverage | advanced graphs may not run component by component | production executor adapters |
| Stale EDA/leakage ids in guided groups | two live stage statuses/artifacts can be omitted from the guided projection | correct id map + workflow-coverage test |
| Single-account deployment | unsuitable for real multi-user service | identity, authorization, audit |

## Recommended next delivery

Complete the source-to-ML trust transition:

1. content-aware routing for ambiguous TXT;
2. durable per-source ML/context/review/excluded decisions;
3. candidate PDF table review and promotion in the guided canvas;
4. automatic refresh of the proposed ML input set after promotion;
5. an end-to-end mixed CSV + PDF + TXT test that accepts a plan and completes the base pipeline.

The technical design for each part is in
[`SYSTEM_ARCHITECTURE_REPORT.md`](SYSTEM_ARCHITECTURE_REPORT.md).
