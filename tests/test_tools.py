"""Permission, privacy, evidence, and isolation tests for deterministic agent tools."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from pydantic import BaseModel

from ads.agents import (
    AgentContext,
    AgentSpec,
    ToolEvidence,
    require_tool_evidence,
    run_agent,
)
from ads.contracts.gates import PermissionTier
from ads.intake import LoadedTable, ProfileOptions, profile_table
from ads.llm import LLMResponse, ModelProfile
from ads.sandbox import SandboxConfig, SandboxManager
from ads.tools import (
    PermissionBroker,
    SandboxUnavailableError,
    ToolDefinition,
    ToolPayload,
    ToolPermissionError,
    ToolRegistry,
    ToolRuntime,
    build_tool_registry,
)
from ads.tools.execute import STDOUT_CHAR_LIMIT, execute_python


@pytest.fixture
def runtime() -> ToolRuntime:
    frame = pd.DataFrame(
        {
            "customer_id": [1, 2, 3, 4],
            "email": ["ada@example.com", "bea@example.com", "cy@example.com", "dan@example.com"],
            "segment": ["enterprise", "consumer", "consumer", "enterprise"],
            "spend": [10.0, 20.0, 30.0, 40.0],
        }
    )
    card = profile_table(
        LoadedTable("customers", frame, "memory://customers", "csv"),
        ProfileOptions(include_samples=False),
    )
    return ToolRuntime.from_sources([card], {"customers": frame}, run_id="tools-test")


def _agent(
    *allowed_tools: str,
    tier: PermissionTier = PermissionTier.READ_DATA,
):
    return SimpleNamespace(
        id="test_agent",
        allowed_tools=frozenset(allowed_tools),
        max_tool_tier=tier,
    )


def test_allowlist_is_enforced_loudly_and_audited(runtime: ToolRuntime) -> None:
    broker = PermissionBroker(build_tool_registry())

    with pytest.raises(ToolPermissionError, match="not allowed"):
        broker.invoke(
            _agent("null_rate"),
            "value_counts",
            runtime,
            table="customers",
            column="segment",
        )

    event = broker.audit_log[-1]
    assert event.audit_tuple()[:4] == (
        "test_agent",
        "value_counts",
        PermissionTier.READ_DATA,
        "denied",
    )
    assert event.detail == "outside allowlist"


def test_permission_tier_is_enforced_before_execution(runtime: ToolRuntime) -> None:
    broker = PermissionBroker(build_tool_registry())

    with pytest.raises(ToolPermissionError, match="requires EXECUTE"):
        broker.invoke(
            _agent("execute_python", tier=PermissionTier.READ_DATA),
            "execute_python",
            runtime,
            code="raise AssertionError('must never execute')",
        )

    assert [event.decision for event in broker.audit_log] == ["denied"]


def test_mutate_source_is_denied_even_with_explicit_maximum_grant(runtime: ToolRuntime) -> None:
    called = False

    def mutate(_runtime, _arguments):
        nonlocal called
        called = True
        return ToolPayload(summary="mutated")

    registry = ToolRegistry()
    registry.register(
        ToolDefinition("mutate_source", PermissionTier.MUTATE_SOURCE, "forbidden", mutate)
    )
    broker = PermissionBroker(registry)

    with pytest.raises(ToolPermissionError, match="denied for every agent"):
        broker.invoke(
            _agent("mutate_source", tier=PermissionTier.MUTATE_SOURCE),
            "mutate_source",
            runtime,
        )

    assert not called
    assert broker.audit_log[-1].detail == "source mutation barred"


def test_value_counts_redacts_pii_values(runtime: ToolRuntime) -> None:
    broker = PermissionBroker(build_tool_registry())
    result = broker.invoke(
        _agent("value_counts"),
        "value_counts",
        runtime,
        table="customers",
        column="email",
    )

    rendered = json.dumps(result.data, sort_keys=True)
    assert result.data["redacted"] is True
    assert result.data["top_values"] == []
    assert "@example.com" not in rendered
    assert "@example.com" not in result.summary


def test_ds_registry_reuses_row_free_measurements(runtime: ToolRuntime) -> None:
    broker = PermissionBroker(build_tool_registry())
    agent = _agent("column_profile", "correlation", "candidate_keys")

    profile = broker.invoke(agent, "column_profile", runtime, table="customers", column="spend")
    correlation = broker.invoke(
        agent,
        "correlation",
        runtime,
        table="customers",
        left_column="customer_id",
        right_column="spend",
    )
    keys = broker.invoke(agent, "candidate_keys", runtime, table="customers")

    assert profile.data["numeric"]["mean"] == 25.0
    assert correlation.data["max_absolute_pearson_spearman"] == 1.0
    assert any(item["columns"] == ["customer_id"] for item in keys.data["candidates"])
    assert all(event.decision == "allowed" for event in broker.audit_log)


def test_execute_python_refuses_host_fallback_when_sandbox_unavailable(
    tmp_path: Path,
) -> None:
    class UnavailableManager:
        config = SimpleNamespace(artifacts_dir=tmp_path)

        def docker_available(self) -> bool:
            return False

        def image_available(self) -> bool:
            raise AssertionError("image check is short-circuited without Docker")

    runtime = ToolRuntime(sandbox_manager=UnavailableManager())  # type: ignore[arg-type]
    broker = PermissionBroker(build_tool_registry())

    with pytest.raises(SandboxUnavailableError, match="refusing to execute on the host"):
        broker.invoke(
            _agent("execute_python", tier=PermissionTier.EXECUTE),
            "execute_python",
            runtime,
            code="print('must not run on host')",
        )

    assert [event.decision for event in broker.audit_log] == ["allowed", "failed"]


class _EvidenceContract(BaseModel):
    claim: str


class _FakeLLM:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate_structured(
        self, *, system: str, prompt: str, json_schema: dict, profile: ModelProfile
    ) -> LLMResponse:
        self.prompts.append(prompt)
        return LLMResponse(
            text='{"claim":"measured"}',
            model=profile.name,
            latency_s=0.0,
            parsed={"claim": "measured"},
        )


def test_tool_evidence_enters_next_attempt_and_can_be_required() -> None:
    profile = ModelProfile(name="fake")
    spec = AgentSpec(
        id="evidence_agent",
        system_prompt="Use measurements.",
        output_contract=_EvidenceContract,
        profile=profile,
        validators=(require_tool_evidence("null_rate"),),
        max_attempts=2,
        allowed_tools=frozenset({"null_rate"}),
        max_tool_tier=PermissionTier.READ_DATA,
    )
    llm = _FakeLLM()

    def evidence_for(attempt: int, _context: AgentContext):
        if attempt == 2:
            return [
                ToolEvidence(
                    "null_rate",
                    "null_rate measured 0.25 over 100 rows",
                    PermissionTier.READ_DATA,
                )
            ]
        return []

    result = run_agent(
        spec,
        AgentContext(sections={"Task": "Make a measured claim."}),
        llm,
        evidence_provider=evidence_for,
    )

    assert result.succeeded
    assert result.n_attempts == 2
    assert "Measured tool evidence" not in llm.prompts[0]
    assert "null_rate measured 0.25" in llm.prompts[1]


def test_live_execute_python_is_isolated_and_suppresses_dataframes(tmp_path: Path) -> None:
    data = tmp_path / "data"
    artifacts = tmp_path / "artifacts"
    data.mkdir()
    artifacts.mkdir()
    manager = SandboxManager(SandboxConfig(data_dir=data, artifacts_dir=artifacts))
    if not manager.docker_available():
        pytest.skip("Docker daemon is unavailable")
    if not manager.image_available():
        pytest.skip("ads-sandbox:latest is not built")

    runtime = ToolRuntime(
        run_id="live-tool-test",
        sandbox_manager=manager,
        artifacts_dir=artifacts,
    )
    broker = PermissionBroker(build_tool_registry())
    agent = _agent("execute_python", tier=PermissionTier.EXECUTE)
    try:
        broker.invoke(agent, "execute_python", runtime, code="x = 41; print('ready')")
        result = broker.invoke(
            agent,
            "execute_python",
            runtime,
            code="import pandas as pd\nprint(x + 1)\npd.DataFrame({'secret': ['row']})",
        )
    finally:
        runtime.close()

    assert result.data["stdout"] == "42\n"
    assert result.data["suppressed_dataframe_outputs"] == 1
    assert "secret" not in json.dumps(result.data)


def test_stdout_is_capped_and_flagged() -> None:
    """A tool result must not be able to flood the caller with text.

    stdout is written by code the agent controls, so its size is bounded here
    rather than trusted. The flag matters as much as the cap: a silently
    shortened result would read as complete output.
    """

    class _Result:
        execution_count = 1
        outputs: list[object] = []
        errors: list[object] = []
        timed_out = False
        stdout = "x" * (STDOUT_CHAR_LIMIT + 500)

    class _Manager:
        config = SimpleNamespace(artifacts_dir=Path("."))

        def docker_available(self) -> bool:
            return True

        def image_available(self) -> bool:
            return True

        def create_session(self, run_id: str) -> str:
            return "session"

        def execute(self, session: str, code: str, timeout: float) -> _Result:
            return _Result()

    runtime = ToolRuntime(run_id="cap-test", sandbox_manager=_Manager())
    payload = execute_python(runtime, {"code": "print('x' * 5000)"})

    assert payload.data["stdout_truncated"] is True
    assert payload.data["stdout_chars"] == STDOUT_CHAR_LIMIT + 500
    assert len(payload.data["stdout"]) < STDOUT_CHAR_LIMIT + 300
    assert "truncated" in payload.data["stdout"]


def test_planner_agents_cannot_reach_execute_tier() -> None:
    """The property that actually enforces the two-plane separation.

    execute_python runs against a read-only mount of /data, so any agent that
    can call it can print source rows regardless of what the display layer
    suppresses. The separation holds because the agents that see DataCards are
    capped below EXECUTE. This test fails the moment that stops being true.
    """
    from ads.agents import problem_discovery, schema_discovery, validation_strategy

    for module in (problem_discovery, schema_discovery, validation_strategy):
        spec = module.build_spec()
        assert spec.max_tool_tier < PermissionTier.EXECUTE, (
            f"{spec.id} may reach {spec.max_tool_tier.name}; an agent that sees "
            "DataCards must not be able to execute code against /data."
        )
        assert "execute_python" not in spec.allowed_tools, (
            f"{spec.id} lists execute_python in its allowlist."
        )
