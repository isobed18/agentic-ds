# Workflow framework decision

The fixed local workflow UI and `WorkflowSpec`/`run_workflow` boundary remain the
project's owned surfaces. No framework UI is embedded, and no framework defines the
product interaction model.

## License and fit assessment

This assessment concerns the runtime components we would import, not hosted commercial
offerings or every optional integration in their ecosystems.

- **Prefect:** the upstream core repository publishes an Apache License 2.0. Its flow
  model can add scheduling, outer-run retry, and external observability around one Python
  call without translating our domain graph. It is the best fit for a narrow adapter.
- **Dagster:** the upstream core repository also publishes Apache-2.0. It is a credible
  self-hosted alternative, but its asset/op vocabulary would not add a tested benefit to
  this fixed MVP boundary and risks duplicating the owned graph.
- **n8n:** n8n uses its Sustainable Use License for the main product. That is a
  source-available/fair-code license with use restrictions, not an OSI-approved
  permissive open-source license. It is therefore unsuitable as a core dependency here,
  independently of its UI fit.

Before a production deployment enables the optional Prefect extra, its complete locked
transitive dependency set still needs a license/SBOM scan. The Apache-2.0 statement about
Prefect core is not a claim that every connector, hosted service, or transitively installed
package shares that license.

Primary license references:

- Prefect core: <https://github.com/PrefectHQ/prefect/blob/main/LICENSE>
- Dagster core: <https://github.com/dagster-io/dagster/blob/master/LICENSE>
- n8n Sustainable Use License: <https://github.com/n8n-io/n8n/blob/master/LICENSE.md>

## Implemented proof of concept

`ads.integrations.prefect.build_prefect_flow` is a tested optional adapter:

```text
Prefect scheduling / outer observability
                  |
                  v
WorkflowSpec + ComponentRegistry + RunState
        -> run_workflow(..., on_event=...) -> RunOutcome
        -> ArtifactStore + ControlPlane projections
```

The adapter lazily imports Prefect and wraps exactly one complete `run_workflow` call. It
passes the original spec, registry, state, policy, rubrics, critic, step limit, and event
callback through unchanged. It does **not** turn stages into Prefect tasks or duplicate
retry state, checkpoints, artifacts, gate decisions, or human resume. Normal installs and
the UI import no Prefect code.

Install only when the scheduling/observability benefit is wanted:

```powershell
.venv\Scripts\python.exe -m pip install -e ".[workflow-prefect]"
```

This proves the adapter seam with no heavy default dependency. A real Prefect deployment
remains an operational choice; the project does not claim a configured server or framework
UI integration.
