"""Optional Prefect wrapper around one complete owned workflow run.

This adapter intentionally does not translate :class:`WorkflowSpec` stages into
Prefect tasks. Retry semantics, exact input bindings, gates, artifacts, and human
resume remain owned by ``ads``. Prefect may schedule and observe the outer run;
removing it changes neither the spec nor the execution result.
"""

from __future__ import annotations

from collections.abc import Callable

from ads.gates import GatePolicy
from ads.orchestration import (
    ComponentRegistry,
    RubricRegistry,
    RunOutcome,
    RunState,
    WorkflowSpec,
    run_workflow,
)


class PrefectUnavailableError(RuntimeError):
    """Raised when the optional adapter is used without Prefect installed."""


def build_prefect_flow(
    spec: WorkflowSpec,
    registry: ComponentRegistry,
    state: RunState,
    *,
    policy: GatePolicy | None = None,
    rubrics: RubricRegistry | None = None,
    critic_llm: object | None = None,
    max_total_steps: int = 100,
    on_event: Callable[[str, dict], None] | None = None,
) -> Callable[[], RunOutcome]:
    """Return a Prefect flow that invokes the existing run boundary exactly once.

    Prefect is imported only when this function is called. Core installs and the
    local workflow UI therefore do not acquire a Prefect dependency.
    """
    try:
        from prefect import flow
    except ImportError as exc:
        raise PrefectUnavailableError(
            "Prefect is optional; install the 'workflow-prefect' extra to build this flow."
        ) from exc

    @flow(name=f"ads-{spec.name}", flow_run_name=state.run_id)
    def execute() -> RunOutcome:
        return run_workflow(
            spec,
            registry,
            state,
            policy=policy,
            rubrics=rubrics,
            critic_llm=critic_llm,
            max_total_steps=max_total_steps,
            on_event=on_event,
        )

    return execute


__all__ = ["PrefectUnavailableError", "build_prefect_flow"]
