"""Analyze and summarize agent behavior across persisted runs."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ads.contracts.agents import AgentAudit
from ads.contracts.base import ArtifactType
from ads.store import ArtifactStore


@dataclass
class StageAgentMetrics:
    stage_id: str
    agent_id: str = "unknown"
    models: set[str] = field(default_factory=set)
    total_invocations: int = 0
    total_turns: int = 0
    accepted_count: int = 0
    rejected_count: int = 0
    validation_failure_counts: Counter[str] = field(default_factory=Counter)
    repairs_count: Counter[str] = field(default_factory=Counter)
    tools_called_counts: Counter[str] = field(default_factory=Counter)
    total_latency_s: float = 0.0
    wall_clock_seconds: list[float] = field(default_factory=list)

    @property
    def acceptance_rate(self) -> float:
        total = self.accepted_count + self.rejected_count
        return (self.accepted_count / total) if total > 0 else 0.0

    @property
    def avg_latency_s(self) -> float:
        return (self.total_latency_s / self.total_turns) if self.total_turns > 0 else 0.0

    @property
    def avg_wall_clock_s(self) -> float:
        return (
            (sum(self.wall_clock_seconds) / len(self.wall_clock_seconds))
            if self.wall_clock_seconds
            else 0.0
        )


@dataclass
class RunAnalyticsSummary:
    total_runs: int = 0
    run_ids: list[str] = field(default_factory=list)
    stage_metrics: dict[str, StageAgentMetrics] = field(default_factory=dict)
    rejection_layers: Counter[str] = field(default_factory=Counter)


def _categorize_failure_layer(code: str) -> str:
    if code in {"invalid_json", "invalid_action", "pydantic_validation_error"}:
        return "schema"
    if code.startswith("tool_") or code.startswith("investigation_error"):
        return "tool"
    if code in {"unsupported_join", "unaggregated_fan_out", "missing_base_grain", "unknown_column"}:
        return "semantic"
    if code in {
        "untested_plan",
        "trial_grain_not_preserved",
        "leakage_detected",
        "holdout_overfit",
    }:
        return "evidence"
    return "gate"


def analyze_run_records(
    store: ArtifactStore | None = None,
    run_state_root: Path | None = None,
) -> RunAnalyticsSummary:
    """Extract and aggregate empirical agent behavior from SQLite and run-state snapshots."""
    store = store or ArtifactStore(Path("data/artifacts"))
    run_state_root = run_state_root or Path("data/run-state")

    summary = RunAnalyticsSummary()
    known_run_ids: set[str] = set()

    # 1. Read all AgentAudit artifacts across all runs in the artifact store
    try:
        with store._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM artifacts WHERE artifact_type=? ORDER BY created_at DESC",
                (ArtifactType.AGENT_AUDIT.value,),
            ).fetchall()
            audit_refs = [store._row_to_ref(r) for r in rows]
    except Exception:
        audit_refs = []
    for ref in audit_refs:
        known_run_ids.add(ref.run_id)
        try:
            audit = store.load(ref.artifact_id, AgentAudit)
        except Exception:
            continue

        stage = summary.stage_metrics.setdefault(
            audit.stage_id,
            StageAgentMetrics(stage_id=audit.stage_id, agent_id=audit.agent_id),
        )
        stage.agent_id = audit.agent_id
        stage.total_invocations += 1
        for tool in audit.evidence_tools:
            stage.tools_called_counts[tool] += 1

        for member in audit.members:
            if member.model:
                stage.models.add(member.model)
            stage.total_turns += member.attempts
            if member.accepted:
                stage.accepted_count += 1
            else:
                stage.rejected_count += 1

            for fail in member.validation_failures:
                stage.validation_failure_counts[fail] += 1
                layer = _categorize_failure_layer(fail)
                summary.rejection_layers[layer] += 1

            for repair in member.repairs:
                stage.repairs_count[repair] += 1

            stage.total_latency_s += member.latency_s

    # 2. Read wall-clock durations from run-state snapshots if present
    if run_state_root.exists():
        for path in run_state_root.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                run_id = data.get("run_id", path.stem)
                known_run_ids.add(run_id)
                for attempt in data.get("attempts", []):
                    stage_id = attempt.get("stage_id")
                    started = attempt.get("started_at")
                    ended = attempt.get("ended_at")
                    if stage_id and started and ended:
                        try:
                            t0 = datetime.fromisoformat(started.replace("Z", "+00:00"))
                            t1 = datetime.fromisoformat(ended.replace("Z", "+00:00"))
                            dur = (t1 - t0).total_seconds()
                            if dur >= 0:
                                stage = summary.stage_metrics.setdefault(
                                    stage_id, StageAgentMetrics(stage_id=stage_id)
                                )
                                stage.wall_clock_seconds.append(dur)
                        except Exception:
                            pass
            except Exception:
                pass

    summary.total_runs = len(known_run_ids)
    summary.run_ids = sorted(known_run_ids)
    return summary


def format_agent_analytics_report(summary: RunAnalyticsSummary) -> str:
    """Format measured metrics into a clear markdown and console table."""
    header = (
        "| Stage | Agent ID | Model(s) | Turns | Accepted | Rejected | "
        "Accept % | Avg Turn Latency | Avg Stage Wall-Clock |"
    )
    lines: list[str] = [
        "# Agent Behavior & Pipeline Run Analytics",
        "",
        f"**Analyzed Runs:** {summary.total_runs} run(s)",
        "",
        "## Per-Stage Agent Execution & Durations",
        "",
        header,
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]

    for stage_id, m in sorted(summary.stage_metrics.items()):
        models_str = ", ".join(sorted(m.models)) or "—"
        acc_pct = (
            f"{m.acceptance_rate * 100:.1f}%" if (m.accepted_count + m.rejected_count) > 0 else "—"
        )
        avg_lat = f"{m.avg_latency_s:.2f}s" if m.total_turns > 0 else "—"
        avg_wall = f"{m.avg_wall_clock_s:.2f}s" if m.wall_clock_seconds else "—"
        lines.append(
            f"| `{stage_id}` | `{m.agent_id}` | {models_str} | {m.total_turns} | "
            f"{m.accepted_count} | {m.rejected_count} | {acc_pct} | {avg_lat} | {avg_wall} |"
        )

    lines.extend(["", "## Validation Rejections by Layer", ""])
    if summary.rejection_layers:
        lines.extend(["| Layer | Rejection Count |", "|---|---:|"])
        for layer, count in summary.rejection_layers.most_common():
            lines.append(f"| `{layer}` | {count} |")
    else:
        lines.append("No validation rejections recorded.")

    lines.extend(["", "## Top Validation Failure Reasons", ""])
    all_failures: Counter[str] = Counter()
    for m in summary.stage_metrics.values():
        all_failures.update(m.validation_failure_counts)

    if all_failures:
        lines.extend(["| Reason Code | Occurrences |", "|---|---:|"])
        for code, count in all_failures.most_common(10):
            lines.append(f"| `{code}` | {count} |")
    else:
        lines.append("No specific failure codes recorded.")

    lines.extend(["", "## Tool Invocations by Stage", ""])
    for stage_id, m in sorted(summary.stage_metrics.items()):
        if m.tools_called_counts:
            tools_str = ", ".join(f"`{k}` ({v})" for k, v in m.tools_called_counts.most_common())
            lines.append(f"- **{stage_id}**: {tools_str}")

    return "\n".join(lines)


__all__ = [
    "RunAnalyticsSummary",
    "StageAgentMetrics",
    "analyze_run_records",
    "format_agent_analytics_report",
]
