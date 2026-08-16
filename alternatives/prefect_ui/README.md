# Experiment B: Prefect-backed UI

This directory is intentionally separate from `src/ads/api`: it is an A/B experiment,
not a replacement for the pipeline-first base UI.

It reuses the existing safe FastAPI control-plane endpoints and submits each complete ADS
run through the tested `build_prefect_flow` boundary. Prefect does not own or translate
individual stages; `WorkflowSpec`, `run_workflow`, gates, human resume, bindings, and the
artifact store remain authoritative.

Install the optional Apache-2.0 framework dependency and run on the separate port 8078:

```powershell
.venv\Scripts\python.exe -m pip install -e ".[api,workflow-prefect]"
.venv\Scripts\python.exe -m alternatives.prefect_ui.serve
```

Compare with the base UI at `http://127.0.0.1:8077`. Experiment B is at
`http://127.0.0.1:8078`. The experiment does not expose Prefect's own dashboard or adopt
its visual language, keeping product-design comparison separate from runtime comparison.
