"""Separate Prefect-backed A/B UI without replacing the owned workflow or base UI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ads.api import ControlPlane, create_app
from ads.integrations import build_prefect_flow
from ads.store import ArtifactStore


def prefect_runner(spec, registry, state, **kwargs):
    """Run one complete owned workflow as one observable Prefect flow."""
    return build_prefect_flow(spec, registry, state, **kwargs)()


def create_prefect_app(
    artifacts_dir: str | Path = "data/artifacts",
    *,
    plane: ControlPlane | None = None,
) -> Any:
    """Build Experiment B while reusing the exact control-plane API and data safety."""
    from fastapi.responses import HTMLResponse

    if plane is None:
        plane = ControlPlane(
            store=ArtifactStore(artifacts_dir),
            workflow_runner=prefect_runner,
        )
    elif plane.workflow_runner is None:
        plane.workflow_runner = prefect_runner

    app = create_app(plane=plane)
    app.title = "Agentic DS — Prefect experiment"
    # Drop the React shell *and* its SPA fallback. The fallback is declared as
    # "/{full_path:path}", which also matches "/", so removing only the literal
    # index route would leave the catch-all serving this experiment's page.
    app.router.routes = [
        route
        for route in app.router.routes
        if getattr(route, "name", None) not in {"index", "spa_fallback"}
    ]

    @app.get("/", response_class=HTMLResponse)
    def experiment_index() -> str:
        return Path(__file__).with_name("dashboard.html").read_text(encoding="utf-8")

    return app


app = create_prefect_app()

__all__ = ["app", "create_prefect_app", "prefect_runner"]
