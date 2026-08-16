"""The optional framework seam wraps, but never translates, our workflow."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from ads.contracts.gates import BUILTIN_PROFILES
from ads.integrations import PrefectUnavailableError, build_prefect_flow
from ads.orchestration import ComponentRegistry, RunState, StageDefinition, linear_spec
from ads.store import ArtifactStore


def _owned_boundary(tmp_path):
    spec = linear_spec(
        "owned-workflow",
        "7",
        [StageDefinition(id="only", component="fixture.only")],
    )
    registry = ComponentRegistry()
    registry.register("fixture.only", lambda state, correction=None: None)
    state = RunState(
        "run-prefect",
        ArtifactStore(tmp_path / "artifacts"),
        BUILTIN_PROFILES["full_auto"],
    )
    return spec, registry, state


def test_adapter_is_lazy_when_prefect_is_not_installed(tmp_path, monkeypatch) -> None:
    spec, registry, state = _owned_boundary(tmp_path)
    monkeypatch.setitem(sys.modules, "prefect", None)

    with pytest.raises(PrefectUnavailableError, match="workflow-prefect"):
        build_prefect_flow(spec, registry, state)


def test_prefect_wraps_one_run_without_translating_stages(tmp_path, monkeypatch) -> None:
    spec, registry, state = _owned_boundary(tmp_path)
    decoration = {}
    invocation = {}

    fake_prefect = ModuleType("prefect")

    def flow(**kwargs):
        decoration.update(kwargs)

        def decorate(fn):
            return fn

        return decorate

    fake_prefect.flow = flow
    monkeypatch.setitem(sys.modules, "prefect", fake_prefect)

    expected = SimpleNamespace(status="completed")

    def fake_run(actual_spec, actual_registry, actual_state, **kwargs):
        invocation.update(
            spec=actual_spec,
            registry=actual_registry,
            state=actual_state,
            kwargs=kwargs,
        )
        return expected

    monkeypatch.setattr("ads.integrations.prefect.run_workflow", fake_run)
    observed_events = []
    wrapped = build_prefect_flow(
        spec,
        registry,
        state,
        max_total_steps=17,
        on_event=lambda name, payload: observed_events.append((name, payload)),
    )

    assert wrapped() is expected
    assert decoration == {"name": "ads-owned-workflow", "flow_run_name": "run-prefect"}
    assert invocation["spec"] is spec
    assert invocation["registry"] is registry
    assert invocation["state"] is state
    assert invocation["kwargs"]["max_total_steps"] == 17
    assert invocation["kwargs"]["on_event"] is not None
    assert len(spec.stages) == 1, "the adapter must not replace or expand the owned graph"
