"""Tests for agent behavior analytics across persisted runs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ads.contracts.agents import AgentAudit, AgentMemberAudit
from ads.reporting.agent_analytics import analyze_run_records, format_agent_analytics_report
from ads.store import ArtifactStore


@pytest.fixture
def sample_analytics_store(tmp_path: Path) -> ArtifactStore:
    store = ArtifactStore(tmp_path / "artifacts")
    run_id = "run-test-analytics-1"
    audit = AgentAudit(
        stage_id="schema_discovery",
        agent_id="schema_investigator",
        output_contract="IntegrationPlanProposal",
        panel_size=1,
        valid_members=1,
        agreement=1.0,
        verbatim_agreement=1.0,
        allowed_tools=["candidate_keys", "trial_integration_plan"],
        evidence_tools=["candidate_keys", "trial_integration_plan"],
        validator_count=4,
        members=[
            AgentMemberAudit(
                member=1,
                model="qwen3.6:27b",
                attempts=3,
                accepted=True,
                validation_failures=["unaggregated_fan_out", "untested_plan"],
                repairs=[],
                latency_s=12.4,
            )
        ],
    )
    store.put(audit, run_id=run_id, stage_exec_id="schema_discovery", name="schema_discovery_audit")

    # Write a run snapshot in run-state
    run_state_dir = tmp_path / "run-state"
    run_state_dir.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "run_id": run_id,
        "status": "completed",
        "configuration": {"mode": "agent", "agent_panel_size": 1},
        "attempts": [
            {
                "stage_id": "intake",
                "attempt": 1,
                "started_at": "2026-08-18T10:00:00Z",
                "ended_at": "2026-08-18T10:00:02Z",
                "decision": None,
                "error": None,
            },
            {
                "stage_id": "schema_discovery",
                "attempt": 1,
                "started_at": "2026-08-18T10:00:02Z",
                "ended_at": "2026-08-18T10:00:15Z",
                "decision": None,
                "error": None,
            },
        ],
    }
    (run_state_dir / f"{run_id}.json").write_text(json.dumps(snapshot), encoding="utf-8")
    return store


def test_analyze_run_records_measures_attempts_and_failures(
    sample_analytics_store: ArtifactStore,
) -> None:
    store = sample_analytics_store
    run_state_root = store.root.parent / "run-state"

    metrics = analyze_run_records(store=store, run_state_root=run_state_root)

    assert metrics.total_runs == 1
    assert "schema_discovery" in metrics.stage_metrics
    stage = metrics.stage_metrics["schema_discovery"]
    assert stage.total_turns == 3
    assert stage.accepted_count == 1
    assert stage.validation_failure_counts["unaggregated_fan_out"] == 1
    assert stage.validation_failure_counts["untested_plan"] == 1
    assert stage.total_latency_s == pytest.approx(12.4, rel=1e-2)

    report_text = format_agent_analytics_report(metrics)
    assert "schema_discovery" in report_text
    assert "unaggregated_fan_out" in report_text
    assert "qwen3.6:27b" in report_text
