"""Local workflow control plane and deliberately data-safe UI API.

The browser receives workflow metadata, DataCard-style schema statistics, run
events, gate decisions, and artifact summaries. It never receives source rows.
The runtime registry accelerates live polling; a redacted progress snapshot is
also written beside the artifact store so completed and interrupted runs remain
inspectable after the server restarts.
"""

import hashlib
import json
import re
import threading
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from ads.agents.runtime import DEFAULT_AGENT_RUNTIME_POLICY, AgentRuntimePolicy
from ads.api import i18n
from ads.api.auth import config_from_env, install_auth
from ads.api.panels import (
    eda_panels,
    evaluation_panels,
    exploratory_panel,
    leakage_panels,
    model_experiment_panel,
    schema_graph,
    source_panels,
    training_panels,
    validation_panels,
)
from ads.contracts.base import ArtifactType
from ads.contracts.gates import BUILTIN_PROFILES
from ads.contracts.integration import IntegrationPlan
from ads.contracts.problem import METRICS_BY_TASK, Metric, ProblemDefinition, TaskType
from ads.contracts.validation import SplitStrategy, ValidationStrategy
from ads.gates import GatePolicy
from ads.intake import detect_relationships, load_directory, profile_tables
from ads.intake.loaders import CSV_SUFFIXES, EXCEL_SUFFIXES, PARQUET_SUFFIXES
from ads.llm import LARGE, OllamaClient, StructuredLLM
from ads.orchestration import (
    EdgeCondition,
    RunOutcome,
    RunState,
    RunStatus,
    resume_workflow,
    run_workflow,
)
from ads.pipeline import (
    build_default_registry,
    build_default_spec,
    build_full_spec,
    build_full_spec_definition,
    build_pipeline_rubrics,
    configure_full_pipeline_state,
    configure_pipeline_state,
)
from ads.sandbox import SandboxConfig, SandboxManager
from ads.store import ArtifactStore

_UPLOAD_ID = re.compile(r"^upload:([0-9a-f]{12})$")
_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9_.-]+$")
_UPLOAD_SUFFIXES = {".csv", ".tsv", ".txt", ".xlsx", ".xlsm", ".xls", ".parquet", ".pq"}
_PLANNER_AGENTS = {"schema_discovery", "problem_discovery", "validation_strategy"}
_PIPELINE_STAGES = {
    "intake",
    "schema_discovery",
    "integration",
    "problem_discovery",
    "validation_strategy",
    "eda",
    "leakage_audit",
    "feature_pipeline",
    "splitting",
    "training",
    "evaluation",
    "report",
}


class _PlannerChatReply(BaseModel):
    """Grammar-constrained, UI-safe response from the planner chat."""

    reply: str
    configuration_patch: dict[str, Any] = Field(default_factory=dict)
    rules_to_remember: list[str] = Field(default_factory=list)
    checkpoint_stages: list[str] = Field(default_factory=list)
    auto_proceed_stages: list[str] = Field(default_factory=list)
    max_retries_by_stage: dict[str, int] = Field(default_factory=dict)
    focus_stage: str | None = None
    proposed_decision: Literal["approve", "retry", "abort"] | None = None
    decision_instructions: list[str] = Field(default_factory=list)
    #: stage_id -> instructions the planner is passing on to that stage's agent.
    #: This is the indirect route: a person describes what they want to the
    #: planner, and the planner decides which stage it belongs to. The direct
    #: route is the instruction box on the stage itself.
    stage_directives: dict[str, list[str]] = Field(default_factory=dict)


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class RunSummary:
    """One row in the runs list."""

    run_id: str
    artifact_count: int
    stages: list[str]
    status: str
    last_activity: str
    pending_question: dict[str, Any] | None = None
    #: Set when this run was branched from another to try a different problem.
    #: A branch is a separate run rather than a fork inside the engine: the
    #: problem changes, so every later stage reasons from a different premise
    #: and needs its own agents. Sharing an engine would only share state that
    #: is no longer true for both sides.
    parent_run_id: str | None = None
    branch_label: str | None = None
    #: Which dataset this run is working on. The frontend has been reading a
    #: `dataset` field off this object since it was written; nothing ever put
    #: one there, so every screen that needed the source got `undefined`.
    source_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "parent_run_id": self.parent_run_id,
            "branch_label": self.branch_label,
            "source_id": self.source_id,
            "dataset": self.source_id,
            "artifact_count": self.artifact_count,
            "stages": self.stages,
            "status": self.status,
            "last_activity": self.last_activity,
            "pending_question": self.pending_question,
        }


@dataclass
class _RuntimeRun:
    run_id: str
    source_id: str
    state: RunState
    configuration: dict[str, Any]
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    status: str = "queued"
    current_stage: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    outcome: Any | None = None
    error: str | None = None
    #: Continues a staged run from where it stopped. Held rather than rebuilt
    #: so the second half executes against the same state and the artifacts
    #: intake and schema discovery already produced are not recomputed.
    resume: Any | None = None


@dataclass
class ControlPlane:
    """Framework-free application service behind the local FastAPI UI."""

    store: ArtifactStore
    source_roots: tuple[Path, ...] = ()
    upload_root: Path | None = None
    llm_factory: Callable[[], StructuredLLM] | None = None
    workflow_runner: Callable[..., Any] | None = None
    _runtime_runs: dict[str, _RuntimeRun] = field(default_factory=dict)
    #: source_id -> (file fingerprint, profile). Invalidated by the fingerprint
    #: rather than by a timer, so a changed file is re-profiled immediately and
    #: an unchanged one is never re-read.
    _profile_cache: dict[str, tuple[str, dict[str, Any]]] = field(default_factory=dict)
    #: source_id -> (file fingerprint, cached staged outcome data).
    #: Staging runs Intake and Schema Discovery (which takes ~7 min on local 27B model).
    #: When unchanged data is staged again, the staged state & artifacts are reused.
    _staged_cache: dict[str, tuple[str, dict[str, Any]]] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def __post_init__(self) -> None:
        self.source_roots = (
            tuple(path.resolve() for path in self.source_roots)
            if self.source_roots
            else (Path("data").resolve(),)
        )
        if self.upload_root is None:
            self.upload_root = self.store.root.parent / "uploads"
        self.upload_root = self.upload_root.resolve()
        self.upload_root.mkdir(parents=True, exist_ok=True)
        self._run_state_root.mkdir(parents=True, exist_ok=True)

    @property
    def _run_state_root(self) -> Path:
        return self.store.root.parent / "run-state"

    # ---------------------------------------------------------------- workflow

    def workflow_graph(self, run_id: str | None = None, *, mode: str = "agent") -> dict[str, Any]:
        """Return the fixed pipeline graph, optionally annotated with run state."""
        progress = self.progress(run_id) if run_id else None
        if progress:
            mode = self._mode_of(progress)
        spec = build_full_spec_definition() if mode == "agent" else build_default_spec()
        raw = spec.to_dict()
        attempts = progress["attempts"] if progress else []
        current = progress.get("current_stage") if progress else None
        run_status = progress.get("status") if progress else None

        nodes = []
        for index, stage in enumerate(raw["stages"], start=1):
            own = [item for item in attempts if item["stage_id"] == stage["id"]]
            latest = own[-1] if own else None
            status = self._stage_status(latest, current, run_status)
            nodes.append(
                {
                    **stage,
                    "description": i18n.t(stage["description"]),
                    "order": index,
                    "kind": "planner_agent" if stage["id"] in _PLANNER_AGENTS else "deterministic",
                    "owner": (
                        i18n.t("Local planning agent")
                        if stage["id"] in _PLANNER_AGENTS
                        else i18n.t("Python executor")
                    ),
                    "status": status,
                    "attempt_count": len(own),
                    "retry_count": sum(item.get("verdict") == "retry" for item in own),
                    # Wall time across every attempt of this stage, and the
                    # moment the last one started. A stage that took four
                    # minutes and one that took four seconds looked identical
                    # before this, which is most of what a person wants to know
                    # while watching a run they cannot otherwise see inside.
                    "elapsed_seconds": self._elapsed(own),
                    "started_at": own[0]["started_at"] if own else None,
                    "ended_at": own[-1]["ended_at"] if own else None,
                }
            )
        return {
            "workflow": raw["workflow"],
            "version": raw["version"],
            "entry": raw["entry"],
            "nodes": nodes,
            "edges": [
                {"source": edge["from"], "target": edge["to"], "condition": edge["when"]}
                for edge in raw["edges"]
            ],
            "run_id": run_id,
            "run_status": run_status,
        }

    @staticmethod
    def _stage_status(
        latest: dict[str, Any] | None, current: str | None, run_status: str | None
    ) -> str:
        if latest is None:
            return "pending"
        stage_id = latest["stage_id"]
        if (
            current == stage_id
            and run_status in {"queued", "running", "staging"}
            and not latest.get("ended_at")
        ):
            return "running"
        if latest.get("error"):
            return "failed"
        verdict = latest.get("verdict")
        if verdict == "retry":
            return "retry"
        if verdict == "escalate":
            return "blocked"
        if verdict == "abort":
            return "failed"
        if verdict == "auto_proceed":
            return "succeeded"
        return "running" if run_status == "running" and current == stage_id else "failed"

    def run_options(self) -> dict[str, Any]:
        """Closed configuration vocabulary derived from executable contracts."""
        return {
            "task_types": [item.value for item in TaskType],
            "metrics_by_task": {
                task.value: sorted(metric.value for metric in metrics)
                for task, metrics in METRICS_BY_TASK.items()
            },
            "split_strategies": [item.value for item in SplitStrategy],
            "defaults": {"n_folds": 5, "test_size": 0.2, "candidate_limit": 3},
            "agent_panel_sizes": [1, 2, 3],
            "execution_modes": [
                {
                    "value": "agent",
                    "label": i18n.t("Agent-planned"),
                    "description": i18n.t(
                        "Planner agents discover schema, problem, and validation."
                    ),
                },
                {
                    "value": "manual",
                    "label": i18n.t("Manual plan"),
                    "description": i18n.t(
                        "Use the problem and validation choices configured here."
                    ),
                },
            ],
        }

    def planner_chat(
        self,
        *,
        message: str,
        configuration: dict[str, Any] | None = None,
        history: list[dict[str, str]] | None = None,
        source_id: str | None = None,
        run_id: str | None = None,
        stage_id: str | None = None,
    ) -> dict[str, Any]:
        """Let the local planner explain and propose UI/run changes without mutating a run."""
        if not message.strip():
            raise ValueError("planner message cannot be empty")
        llm = self.llm_factory() if self.llm_factory else OllamaClient()
        if isinstance(llm, OllamaClient) and not llm.is_available():
            llm.close()
            raise ValueError("Planner chat needs the local Ollama service to be running")

        safe_source: dict[str, Any] | None = None
        if source_id:
            safe_source = self.source_profile(source_id)
        run_context: dict[str, Any] | None = None
        if run_id:
            try:
                progress = self.progress(run_id)
                run_context = {
                    "run_id": run_id,
                    "status": progress.get("status"),
                    "current_stage": progress.get("current_stage"),
                    "events": progress.get("events", []),
                    "gate_decisions": self.gate_decisions(run_id),
                    "attempts": [
                        {
                            "stage_id": item.get("stage_id"),
                            "attempt": item.get("attempt"),
                            "verdict": item.get("verdict"),
                            "error": item.get("error"),
                        }
                        for item in progress.get("attempts", [])
                    ],
                }
            except KeyError:
                run_context = {"run_id": run_id, "status": "unknown"}
        stage_context: dict[str, Any] | None = None
        if run_id and stage_id:
            try:
                detail = self.stage_detail(run_id, stage_id)
                stage_context = {
                    "stage": detail["stage"],
                    "status": detail["status"],
                    "human_view": detail["human_view"],
                    "outputs": [
                        {
                            "type": item["type"],
                            "summary": item["summary"],
                            "story": item["story"],
                        }
                        for item in detail["outputs"][:4]
                    ],
                    "gate_decisions": detail["gate_decisions"][-4:],
                }
            except KeyError:
                stage_context = {"stage_id": stage_id, "status": "unknown"}

        system = (
            "You are the Planner/Orchestrator control layer for a fixed agentic data-science "
            "workflow. Help a human understand an unfamiliar dataset and configure the run. "
            "Be concise, explain trade-offs, and never claim a change was applied unless it is "
            "present in configuration_patch. Only propose a gate decision when the run is "
            "actually awaiting human input. Configuration keys you may set are: base_table, "
            "base_grain, target_column, task_type, primary_metric, problem_title, "
            "problem_description, excluded_columns, validation_strategy, n_folds, test_size, "
            "group_column, time_column, holdout_cutoff, candidate_limit, instructions. "
            "Valid stages are intake, schema_discovery, integration, problem_discovery, "
            "validation_strategy, eda, leakage_audit, splitting, training, evaluation, report. "
            "Rules such as approval preferences or retry limits belong in rules_to_remember. "
            "When the human asks for something an agent should do differently at a "
            "specific stage -- a model family to prefer, a metric to report, a column "
            "to leave alone -- put it in stage_directives keyed by that stage id. It "
            "reaches that stage's agent the next time it runs. Do not use it for "
            "anything the deterministic layer decides; it is an instruction to an "
            "agent, not a setting. "
            "Also map enforceable supervision requests: use checkpoint_stages when the human "
            "must approve a clean stage, auto_proceed_stages to remove such a checkpoint (hard "
            "safety gates still apply), and max_retries_by_stage for retry limits from 0 to 9. "
            "Treat gate history as authoritative: an escalation followed by a later passing "
            "attempt is a resolved intervention, not uninterrupted auto-proceed. Never claim "
            "how an issue was fixed unless the supplied evidence says how; label inferences. "
            "Do not expose or ask for raw rows, names, emails, licence numbers, or other PII."
        )
        prompt = json.dumps(
            {
                "message": message.strip(),
                "configuration": configuration or {},
                "remembered_conversation": (history or [])[-8:],
                "safe_source_profile": safe_source,
                "run_context": run_context,
                "selected_stage_evidence": stage_context,
            },
            default=str,
        )
        try:
            response = llm.generate_structured(
                system=system,
                prompt=prompt,
                json_schema=_PlannerChatReply.model_json_schema(),
                profile=LARGE,
            )
            if response.parsed is None:
                raise ValueError(response.parse_error or "planner returned no structured response")
            reply = _PlannerChatReply.model_validate(response.parsed)
        finally:
            if isinstance(llm, OllamaClient):
                llm.close()

        allowed = {
            "base_table",
            "base_grain",
            "target_column",
            "task_type",
            "primary_metric",
            "problem_title",
            "problem_description",
            "excluded_columns",
            "validation_strategy",
            "n_folds",
            "test_size",
            "group_column",
            "time_column",
            "holdout_cutoff",
            "candidate_limit",
            "instructions",
        }
        patch = {key: value for key, value in reply.configuration_patch.items() if key in allowed}
        result = reply.model_dump()
        result["configuration_patch"] = patch
        result["stage_directives"] = {
            stage: [line.strip() for line in lines if line.strip()]
            for stage, lines in reply.stage_directives.items()
            if stage in _PIPELINE_STAGES and any(line.strip() for line in lines)
        }
        result["checkpoint_stages"] = [
            stage for stage in reply.checkpoint_stages if stage in _PIPELINE_STAGES
        ]
        result["auto_proceed_stages"] = [
            stage for stage in reply.auto_proceed_stages if stage in _PIPELINE_STAGES
        ]
        result["max_retries_by_stage"] = {
            stage: max(0, min(9, retries))
            for stage, retries in reply.max_retries_by_stage.items()
            if stage in _PIPELINE_STAGES
        }
        # Applied here rather than returned for the UI to apply, so the planner
        # route and the direct route end in the same place. A directive the user
        # never sees applied is the failure mode worth avoiding: they asked the
        # planner, the planner agreed, and nothing reached the agent.
        applied: dict[str, list[str]] = {}
        if run_id and result["stage_directives"]:
            for stage, lines in result["stage_directives"].items():
                for line in lines:
                    try:
                        applied[stage] = self.direct_stage(run_id, stage, line)
                    except ValueError:
                        # A finished or evicted run cannot take directives. The
                        # planner still answers; it just could not act.
                        continue
        result["stage_directives_applied"] = applied

        result["model"] = response.model
        result["latency_s"] = response.latency_s
        return result

    # ------------------------------------------------------------------- runs

    def list_runs(self) -> list[RunSummary]:
        summaries: list[RunSummary] = []
        run_ids = (
            set(self._artifact_run_ids()) | set(self._snapshot_run_ids()) | set(self._runtime_runs)
        )
        for run_id in run_ids:
            refs = self.store.list(run_id)
            decisions = self.gate_decisions(run_id)
            try:
                progress = self.progress(run_id)
            except KeyError:
                progress = None
            pending = next(
                (item for item in reversed(decisions) if item["verdict"] == "escalate"), None
            )
            status = (
                progress["status"]
                if progress
                else decisions[-1]["verdict"]
                if decisions
                else "no_gate_record"
            )
            runtime = self._runtime_runs.get(run_id)
            configuration = (
                runtime.configuration if runtime else (progress or {}).get("configuration", {})
            )
            stages = {ref.stage_exec_id for ref in refs if ref.stage_exec_id}
            if progress:
                stages.update(item["stage_id"] for item in progress["attempts"])
            summaries.append(
                RunSummary(
                    run_id=run_id,
                    artifact_count=len(refs),
                    stages=sorted(stages),
                    status="awaiting_human" if pending and status != "completed" else status,
                    last_activity=(
                        progress.get("updated_at", "")
                        if progress
                        else refs[0].created_at
                        if refs
                        else ""
                    ),
                    pending_question=pending,
                    parent_run_id=configuration.get("parent_run_id"),
                    branch_label=configuration.get("branch_label"),
                    source_id=(runtime.source_id if runtime else configuration.get("source_id")),
                )
            )
        return sorted(summaries, key=lambda item: item.last_activity, reverse=True)

    @staticmethod
    def _mode_of(progress: dict[str, Any]) -> str:
        """Which spec this run is executing.

        Staging records `agent` when it starts, because that is the spec it
        builds. The fallback covers a run whose snapshot predates that: it had
        no mode, every reader inferred `manual`, and the rail drew a nine-stage
        pipeline for a run that had already executed schema discovery -- a
        stage that pipeline does not contain -- while the stage endpoint
        answered 404 for whatever the person was looking at.
        """
        configured = progress.get("configuration") or {}
        if configured.get("mode"):
            return str(configured["mode"])
        staged_states = {"staging", "staged"}
        has_agent_stage = any(
            attempt.get("stage_id") in _PLANNER_AGENTS for attempt in progress.get("attempts") or []
        )
        if has_agent_stage or progress.get("status") in staged_states:
            return "agent"
        return "manual"

    @staticmethod
    def _elapsed(attempts: list[dict[str, Any]]) -> float | None:
        """Seconds spent in a stage, summed over its attempts.

        Summed rather than measured end to end, because a retried stage sits
        idle between attempts while the gate and any human decide, and counting
        that as execution time would report a stage as slow when it was waiting.
        A running attempt has no end yet and is measured against now.
        """
        total = 0.0
        counted = False
        for attempt in attempts:
            started = attempt.get("started_at")
            if not started:
                continue
            begin = datetime.fromisoformat(started)
            ended = attempt.get("ended_at")
            finish = datetime.fromisoformat(ended) if ended else datetime.now(UTC)
            total += max(0.0, (finish - begin).total_seconds())
            counted = True
        return round(total, 1) if counted else None

    def _artifact_run_ids(self) -> list[str]:
        with self.store._connect() as conn:  # noqa: SLF001 - read-only index projection
            rows = conn.execute("SELECT DISTINCT run_id FROM artifacts").fetchall()
        return [row["run_id"] for row in rows]

    def _snapshot_run_ids(self) -> list[str]:
        return [path.stem for path in self._run_state_root.glob("*.json")]

    # ---------------------------------------------------------------- sources

    def data_sources(self) -> list[dict[str, Any]]:
        sources: list[dict[str, Any]] = []
        for root in self.source_roots:
            if not root.exists():
                continue
            for child in sorted(root.iterdir()):
                if (
                    child.is_dir()
                    and not self._reserved_path(child)
                    and self._holds_loadable_files(child)
                ):
                    sources.append({"source_id": child.name, "label": child.name})
        if self.upload_root is not None and self.upload_root.exists():
            for child in sorted(self.upload_root.iterdir()):
                files = sorted(path.name for path in child.iterdir() if path.is_file())
                if child.is_dir() and files:
                    sources.append(
                        {
                            "source_id": f"upload:{child.name}",
                            "label": f"upload ({len(files)} file{'s' if len(files) != 1 else ''})",
                            "files": files,
                        }
                    )
        return sources

    def dataset_catalog(self) -> list[dict[str, Any]]:
        """Row-free summaries backing the Datasets product surface."""
        catalog: list[dict[str, Any]] = []
        for source in self.data_sources():
            item = dict(source)
            try:
                profile = self.source_profile(source["source_id"])
                tables = profile["tables"]
                columns = [column for table in tables for column in table["columns"]]
                item.update(
                    {
                        "tables": len(tables),
                        "rows": sum(table["rows"] for table in tables),
                        "columns": sum(table["columns_count"] for table in tables),
                        "candidate_keys": sum(len(table["candidate_keys"]) for table in tables),
                        "quality_issues": sum(len(table["issues"]) for table in tables),
                        "sensitive_columns": sum(
                            column["sensitivity"] == "pii" for column in columns
                        ),
                        "table_summaries": [
                            {
                                "name": table["name"],
                                "format": table["format"],
                                "rows": table["rows"],
                                "columns": table["columns_count"],
                                "candidate_keys": len(table["candidate_keys"]),
                                "issues": table["issues"],
                            }
                            for table in tables
                        ],
                        "privacy": profile["privacy"],
                    }
                )
            except (KeyError, OSError, ValueError) as exc:
                item["profile_error"] = str(exc)
            catalog.append(item)
        return catalog

    def source_path(self, source_id: str) -> Path:
        candidates = [root / source_id for root in self.source_roots]
        match = _UPLOAD_ID.fullmatch(source_id)
        if match and self.upload_root is not None:
            candidates.insert(0, self.upload_root / match.group(1))
        for candidate in candidates:
            resolved = candidate.resolve()
            if self._reserved_path(resolved):
                continue
            in_source = any(root in resolved.parents for root in self.source_roots)
            in_upload = self.upload_root is not None and self.upload_root in resolved.parents
            if (in_source or in_upload) and resolved.is_dir() and any(resolved.iterdir()):
                return resolved
        raise KeyError(source_id)

    @staticmethod
    def _holds_loadable_files(directory: Path) -> bool:
        """Whether this folder is a dataset rather than a folder that exists.

        `data/` also holds working directories -- another run's artifact store,
        a sandbox, an export probe -- and offering them as datasets put five
        rows on the chooser of which three answered "source contains no
        supported data files" when clicked. A directory the loader cannot read
        anything from is not a dataset, whatever else it is.
        """
        supported = CSV_SUFFIXES | EXCEL_SUFFIXES | PARQUET_SUFFIXES
        return any(
            path.is_file() and path.suffix.lower() in supported for path in directory.rglob("*")
        )

    def _reserved_path(self, path: Path) -> bool:
        resolved = path.resolve()
        reserved = {self.store.root.resolve(), self._run_state_root.resolve()}
        if any(resolved == item or item in resolved.parents for item in reserved):
            return True
        return self.upload_root is not None and resolved == self.upload_root.resolve()

    def upload(
        self, filename: str, content: bytes, *, source_id: str | None = None
    ) -> dict[str, Any]:
        """Persist one file, optionally appending it to an existing upload group."""
        safe_name = Path(filename).name
        if not safe_name or safe_name in {".", ".."}:
            raise ValueError("filename must contain a file name")
        if Path(safe_name).suffix.lower() not in _UPLOAD_SUFFIXES:
            raise ValueError("supported uploads are CSV/TSV, Excel, or Parquet")
        if not content:
            raise ValueError("uploaded file is empty")

        if source_id is None:
            token = uuid.uuid4().hex[:12]
            target_dir = self.upload_root / token  # type: ignore[operator]
            target_dir.mkdir(parents=True, exist_ok=False)
        else:
            match = _UPLOAD_ID.fullmatch(source_id)
            if match is None:
                raise ValueError("source_id is not a valid upload group")
            target_dir = self.upload_root / match.group(1)  # type: ignore[operator]
            if not target_dir.is_dir():
                raise ValueError("upload group does not exist")
            token = match.group(1)

        target = target_dir / safe_name
        if target.exists():
            raise ValueError(f"upload group already contains {safe_name!r}")
        target.write_bytes(content)
        files = sorted(path.name for path in target_dir.iterdir() if path.is_file())
        return {
            "source_id": f"upload:{token}",
            "label": f"upload ({len(files)} file{'s' if len(files) != 1 else ''})",
            "files": files,
        }

    def _source_fingerprint(self, source_id: str) -> str:
        """Identify a source by what its files are, not by when we last looked.

        Name, size and modification time of every file. Content hashing would be
        stronger and would mean reading 121 MB to answer "has this changed",
        which is the cost the cache exists to avoid. A file edited within the
        same mtime tick and to the identical byte length would be missed; that
        is the accepted gap and it is written down rather than assumed away.
        """
        root = Path(self.source_path(source_id))
        parts = []
        for path in sorted(root.rglob("*")):
            if path.is_file():
                stat = path.stat()
                parts.append(f"{path.relative_to(root)}:{stat.st_size}:{stat.st_mtime_ns}")
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()

    def source_profile(self, source_id: str) -> dict[str, Any]:
        """Profile source files into a schema-only browser projection.

        Cached on the source's file fingerprint. Profiling re-reads and
        re-measures every file, and the dataset list calls this once per source
        on every request: measured through the tunnel, listing three sources took
        5.22s, of which 4.91s was one 121 MB dataset being re-profiled for a
        screen that had already shown it.
        """
        fingerprint = self._source_fingerprint(source_id)
        with self._lock:
            cached = self._profile_cache.get(source_id)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]

        loaded = load_directory(self.source_path(source_id))
        cards = profile_tables(loaded)
        if not cards:
            raise ValueError("source contains no supported data files")
        # Measured before any run exists. This is what makes the pre-run screen
        # honest: the relationships shown are the same ones schema discovery
        # will reason over, not a picture drawn from column names.
        relationships = [
            {
                "from_table": candidate.from_table,
                "from_columns": list(candidate.from_columns),
                "to_table": candidate.to_table,
                "to_columns": list(candidate.to_columns),
                "overlap_rate": candidate.overlap_rate,
                "orphan_rate": candidate.orphan_rate,
                "parent_coverage": candidate.parent_coverage,
                "cardinality": candidate.cardinality,
                "name_affinity": candidate.name_affinity,
            }
            for candidate in detect_relationships(
                cards, {table.name: table.frame for table in loaded}
            )
        ]
        profile: dict[str, Any] = {
            "source_id": source_id,
            "relationships": relationships,
            "tables": [
                {
                    "name": card.table_name,
                    "format": card.source_format,
                    "rows": card.n_rows,
                    "columns_count": card.n_columns,
                    "candidate_keys": card.candidate_primary_keys,
                    "issues": [issue.code for issue in card.issues],
                    "columns": [
                        {
                            "name": column.name,
                            "dtype": column.dtype,
                            "semantic_type": column.semantic_type.value,
                            "sensitivity": column.sensitivity.value,
                            "null_rate": column.null_rate,
                            "unique_rate": column.unique_rate,
                            "is_unique": column.is_unique,
                            "candidate_target": column.is_usable_target,
                        }
                        for column in card.columns
                    ],
                }
                for card in cards
            ],
            "privacy": "Schema and aggregate statistics only; source rows and values are omitted.",
        }
        with self._lock:
            self._profile_cache[source_id] = (fingerprint, profile)
        return profile

    # --------------------------------------------------------------- execution

    #: Staging stops here. Intake measures the data and schema discovery reads
    #: what it means; between them they produce everything a person needs on
    #: screen before deciding anything, and neither depends on a stated problem.
    STAGE_UNTIL = "schema_discovery"

    def stage_run(
        self, source_id: str, configuration: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Start a run and stop it after intake and schema discovery.

        This is a real run from its first moment -- same id, same state, same
        artifact lineage -- that has been asked to stop early. It was a
        reservation holding a cached profile before, which meant the pipeline
        showed nothing until the user had already committed to a configuration,
        and the intake they were looking at was not the intake the run would
        later perform. Continuing it resumes the same state at the next stage,
        so nothing measured here is measured twice.
        """
        source_path = self.source_path(source_id)
        fingerprint = self._source_fingerprint(source_id)
        with self._lock:
            cached_staged = self._staged_cache.get(source_id)
        if cached_staged is not None and cached_staged[0] == fingerprint:
            cached_data = cached_staged[1]
            run_id = f"run-{uuid.uuid4().hex[:8]}"
            state = RunState(
                run_id=run_id,
                store=self.store,
                profile=BUILTIN_PROFILES["full_auto"],
            )
            llm: StructuredLLM | None = self.llm_factory() if self.llm_factory else OllamaClient()
            if isinstance(llm, OllamaClient) and not llm.is_available():
                llm.close()
                raise ValueError(
                    "Reading the schema needs Ollama at http://localhost:11434. "
                    "Start Ollama and choose the dataset again."
                )
            spec, registry = build_full_spec(llm, panel_size=1)
            sandbox_root = self.store.root.parent / "sandbox" / run_id
            (sandbox_root / "data").mkdir(parents=True, exist_ok=True)
            (sandbox_root / "artifacts").mkdir(parents=True, exist_ok=True)
            configure_full_pipeline_state(
                state,
                source_path=source_path,
                execution_backend=SandboxManager(
                    SandboxConfig(
                        data_dir=sandbox_root / "data",
                        artifacts_dir=sandbox_root / "artifacts",
                    )
                ),
                agent_runtime_policy=DEFAULT_AGENT_RUNTIME_POLICY,
            )
            for k, v in cached_data.get("blackboard", {}).items():
                state.blackboard[k] = v

            for item in cached_data.get("artifacts", []):
                try:
                    art = self.store.load(item["artifact_id"])
                    state.put(art, stage_id=item.get("stage_id") or "intake", name=item.get("name"))
                except Exception:
                    pass

            for att_data in cached_data.get("attempts", []):
                new_att = state.begin_attempt(att_data["stage_id"])
                new_att.started_at = att_data.get("started_at")
                new_att.ended_at = att_data.get("ended_at")
                new_att.input_bindings = dict(att_data.get("input_bindings", {}))
                new_att.artifact_ids = list(att_data.get("artifact_ids", []))
                new_att.decision = att_data.get("decision")
                new_att.critique = att_data.get("critique")
                new_att.error = att_data.get("error")
                state.active_attempt = None

            runtime = _RuntimeRun(
                run_id=run_id,
                source_id=source_id,
                state=state,
                configuration={
                    "source_id": source_id,
                    "mode": "agent",
                    **(configuration or {}),
                },
                status="staged",
            )
            runtime.outcome = RunOutcome(
                run_id=run_id,
                status=RunStatus.STAGED,
                final_stage=self.STAGE_UNTIL,
                decisions=list(cached_data.get("decisions", [])),
            )
            runtime.events = [
                {"event": "run_staged", "at": _now(), "source": source_id},
                *[dict(e) for e in cached_data.get("events", []) if e.get("event") != "run_staged"],
            ]
            runtime.resume = (spec, registry, state, llm)
            with self._lock:
                self._runtime_runs[run_id] = runtime
            self._persist_runtime(runtime)
            return {
                "run_id": run_id,
                "status": "staged",
                "profile": self.source_profile(source_id),
            }

        run_id = f"run-{uuid.uuid4().hex[:8]}"
        state = RunState(
            run_id=run_id,
            store=self.store,
            profile=BUILTIN_PROFILES["full_auto"],
        )
        llm = self.llm_factory() if self.llm_factory else OllamaClient()
        if isinstance(llm, OllamaClient) and not llm.is_available():
            llm.close()
            raise ValueError(
                "Reading the schema needs Ollama at http://localhost:11434. "
                "Start Ollama and choose the dataset again."
            )
        spec, registry = build_full_spec(llm, panel_size=1)
        sandbox_root = self.store.root.parent / "sandbox" / run_id
        (sandbox_root / "data").mkdir(parents=True, exist_ok=True)
        (sandbox_root / "artifacts").mkdir(parents=True, exist_ok=True)
        configure_full_pipeline_state(
            state,
            source_path=source_path,
            execution_backend=SandboxManager(
                SandboxConfig(
                    data_dir=sandbox_root / "data",
                    artifacts_dir=sandbox_root / "artifacts",
                )
            ),
            agent_runtime_policy=DEFAULT_AGENT_RUNTIME_POLICY,
        )
        runtime = _RuntimeRun(
            run_id=run_id,
            source_id=source_id,
            state=state,
            configuration={
                "source_id": source_id,
                # Staging builds and runs the agent spec, so the run is an
                # agent run from this moment. Leaving it unset meant every
                # reader inferred "manual" and looked the run up in a
                # nine-stage pipeline that does not contain the stage it had
                # just executed.
                "mode": "agent",
                **(configuration or {}),
            },
            status="staging",
        )
        with self._lock:
            self._runtime_runs[run_id] = runtime
            runtime.events.append({"event": "run_staged", "at": _now(), "source": source_id})
        self._persist_runtime(runtime)

        event = self._event_recorder(runtime)
        runner = self.workflow_runner or run_workflow

        def execute() -> None:
            try:
                runtime.outcome = runner(
                    spec,
                    registry,
                    state,
                    rubrics=build_pipeline_rubrics(),
                    policy=GatePolicy.load(),
                    on_event=event,
                    stop_after=self.STAGE_UNTIL,
                )
                with self._lock:
                    runtime.status = runtime.outcome.status
                    runtime.error = runtime.outcome.error
                    runtime.updated_at = _now()
                    if runtime.outcome.status in {RunStatus.STAGED, "staged"}:
                        from ads.pipeline.stages import (  # noqa: PLC0415
                            LOADED_TABLES_KEY,
                            SOURCE_CARDS_KEY,
                            SOURCE_FRAMES_KEY,
                        )

                        self._staged_cache[source_id] = (
                            fingerprint,
                            {
                                "attempts": [
                                    {
                                        "stage_id": att.stage_id,
                                        "attempt": att.attempt,
                                        "started_at": att.started_at,
                                        "ended_at": att.ended_at,
                                        "input_bindings": dict(att.input_bindings),
                                        "artifact_ids": list(att.artifact_ids),
                                        "decision": att.decision,
                                        "critique": att.critique,
                                        "error": att.error,
                                    }
                                    for att in state.attempts
                                ],
                                "artifacts": [
                                    {
                                        "artifact_id": a.artifact_id,
                                        "stage_id": a.stage_exec_id,
                                        "name": a.name,
                                    }
                                    for a in self.store.list(run_id=state.run_id)
                                ],
                                "blackboard": {
                                    k: v
                                    for k, v in state.blackboard.items()
                                    if k
                                    in {
                                        SOURCE_CARDS_KEY,
                                        SOURCE_FRAMES_KEY,
                                        LOADED_TABLES_KEY,
                                    }
                                },
                                "decisions": list(runtime.outcome.decisions),
                                "events": list(runtime.events),
                            },
                        )
            except Exception as exc:  # noqa: BLE001 - recorded in durable UI state
                with self._lock:
                    runtime.status = "failed"
                    runtime.error = f"{type(exc).__name__}: {exc}"
                    runtime.updated_at = _now()
            self._persist_runtime(runtime)

        # Held so the second half runs on this state rather than a fresh one.
        runtime.resume = (spec, registry, state, llm)
        threading.Thread(target=execute, name=f"ads-stage-{run_id}", daemon=True).start()
        return {
            "run_id": run_id,
            "status": "staging",
            "profile": self.source_profile(source_id),
        }

    def update_staged_run(self, run_id: str, configuration: dict[str, Any]) -> dict[str, Any]:
        """Amend a staged run's configuration before it is started.

        Accepted while the first stages are still executing as well as after:
        a person reading the schema and setting a target at the same time is
        the normal case, and making them wait would be an artificial race.
        """
        runtime = self._runtime_runs.get(run_id)
        if runtime is None or runtime.status not in {"staging", "staged"}:
            raise ValueError("this run is not staged")
        with self._lock:
            runtime.configuration.update(configuration)
            runtime.updated_at = _now()
        self._persist_runtime(runtime)
        return dict(runtime.configuration)

    def start_staged_run(self, run_id: str, configuration: dict[str, Any] | None = None) -> str:
        """Continue a staged run through the rest of the pipeline.

        The run keeps its id. It was replaced by a freshly started one before,
        which threw away the intake and schema discovery the person had just
        spent their attention reading and re-ran both against the same files.
        """
        runtime = self._runtime_runs.get(run_id)
        if runtime is None or runtime.status != "staged":
            raise ValueError("this run is not staged")
        if runtime.resume is None:
            raise ValueError(
                "this staged run did not survive a restart and cannot be continued; "
                "choose the dataset again"
            )
        body = {**runtime.configuration, **(configuration or {})}
        spec, registry, state, llm = runtime.resume

        run_mode = str(body.get("run_mode", "auto"))
        if run_mode not in {"auto", "manual"}:
            raise ValueError("run_mode must be 'auto' or 'manual'")
        supervision = self._normalise_supervision(body.get("supervision") or {})
        checkpoints = list(supervision["checkpoint_stages"])
        if run_mode == "manual":
            checkpoints = sorted(set(checkpoints) | _PIPELINE_STAGES)
        # The problem has not been chosen yet at this point in the pipeline --
        # the agent chooses it two stages from here -- so the checkpoint that
        # start_run derives from an unstated problem applies here always.
        if "problem_discovery" not in checkpoints:
            checkpoints.append("problem_discovery")
        state.profile = BUILTIN_PROFILES["full_auto"].model_copy(
            update={"checkpoint_stages": sorted(checkpoints)}
        )
        intent = _as_intent(body.get("instructions"))
        preferences = self._staged_preferences(body)
        state.user_intent = "\n\n".join(part for part in (intent, preferences) if part) or None

        runtime.configuration.update(
            {
                **body,
                "supervision": supervision,
                "run_mode": run_mode,
                "instructions": state.user_intent,
            }
        )
        with self._lock:
            runtime.status = "running"
            runtime.updated_at = _now()
        self._persist_runtime(runtime)

        event = self._event_recorder(runtime)
        runner = self.workflow_runner or run_workflow
        resume_at = spec.next_stage(self.STAGE_UNTIL, EdgeCondition.ON_PROCEED)

        def execute() -> None:
            try:
                runtime.outcome = runner(
                    spec,
                    registry,
                    state,
                    rubrics=build_pipeline_rubrics(),
                    policy=self._policy_for_supervision(supervision),
                    on_event=event,
                    start_at=resume_at,
                )
                with self._lock:
                    runtime.status = runtime.outcome.status
                    runtime.error = runtime.outcome.error
                    runtime.current_stage = getattr(runtime.outcome, "final_stage", None)
                    runtime.updated_at = _now()
            except Exception as exc:  # noqa: BLE001 - recorded in durable UI state
                with self._lock:
                    runtime.status = "failed"
                    runtime.error = f"{type(exc).__name__}: {exc}"
                    runtime.updated_at = _now()
            finally:
                if isinstance(llm, OllamaClient):
                    llm.close()
            self._persist_runtime(runtime)

        threading.Thread(target=execute, name=f"ads-run-{run_id}", daemon=True).start()
        return run_id

    @staticmethod
    def _staged_preferences(body: dict[str, Any]) -> str | None:
        """Render whatever the person chose on the staged screen as guidance.

        These are preferences, not contracts: the agent still discovers the
        problem from measured evidence, and stating a target it cannot support
        must not silently override what the data says.
        """
        stated = {
            "target": body.get("target_column"),
            "task": body.get("task_type"),
            "primary metric": body.get("primary_metric"),
            "base table": body.get("base_table"),
            "excluded columns": body.get("excluded_columns") or None,
        }
        chosen = [f"{name} {value!r}" for name, value in stated.items() if value]
        if not chosen:
            return None
        return (
            "User-configured planning preferences (honour when compatible with "
            "measured evidence): " + "; ".join(chosen) + "."
        )

    def _event_recorder(self, runtime: _RuntimeRun) -> Callable[[str, dict[str, Any]], None]:
        """Record a stage event on the run and persist it."""

        def event(name: str, payload: dict[str, Any]) -> None:
            with self._lock:
                runtime.events.append({"event": name, "at": _now(), **payload})
                runtime.updated_at = _now()
                if name == "stage_started":
                    runtime.current_stage = payload.get("stage")
            self._persist_runtime(runtime)

        return event

    def discard_staged_run(self, run_id: str) -> None:
        """Drop a staged run from memory *and* from disk.

        Staging persists a snapshot so a reserved run survives a restart, which
        means forgetting the in-memory copy alone does not discard anything --
        the run reappears in the list from `run-state/` the next time it is
        read. Observed on the deployment: a discarded staged run was still
        there, still `staged`, after the API said `discarded`.
        """
        with self._lock:
            runtime = self._runtime_runs.get(run_id)
            if runtime is not None and runtime.status != "staged":
                raise ValueError(f"run {run_id!r} is not staged; it is {runtime.status}")
            self._runtime_runs.pop(run_id, None)
            if _SAFE_RUN_ID.fullmatch(run_id) is None:
                raise ValueError("run_id contains unsafe characters")
            snapshot = (self._run_state_root / f"{run_id}.json").resolve()
            if self._run_state_root.resolve() not in snapshot.parents:
                raise RuntimeError("refusing to remove a snapshot outside run-state")
            snapshot.unlink(missing_ok=True)

    def start_run(
        self,
        *,
        source_id: str,
        integration_plan: IntegrationPlan,
        problem: ProblemDefinition,
        validation_strategy: ValidationStrategy,
        candidate_limit: int | None = None,
        instructions: str | None = None,
        mode: str = "manual",
        supervision: dict[str, Any] | None = None,
        agent_panel_size: int = 1,
        eda_agent: bool = True,
        run_mode: str = "auto",
        parent_run_id: str | None = None,
        branch_label: str | None = None,
    ) -> str:
        source_path = self.source_path(source_id)
        if mode not in {"agent", "manual"}:
            raise ValueError("mode must be 'agent' or 'manual'")
        if not 1 <= agent_panel_size <= 3:
            raise ValueError("agent_panel_size must be between 1 and 3")
        if mode == "manual":
            self._validate_configuration(source_id, integration_plan, problem, validation_strategy)
        run_id = f"ui-{uuid.uuid4().hex[:12]}"
        run_intent = instructions.strip() if instructions and instructions.strip() else None
        if mode == "agent":
            preferences = (
                "User-configured planning preferences (honour when compatible with measured "
                f"evidence): base table {integration_plan.base_table!r}; target "
                f"{problem.target_column!r}; task {problem.task_type.value!r}; primary metric "
                f"{problem.primary_metric.value!r}; validation "
                f"{validation_strategy.strategy.value!r}; excluded columns "
                f"{problem.excluded_columns!r}."
            )
            run_intent = f"{run_intent}\n\n{preferences}" if run_intent else preferences
        if run_mode not in {"auto", "manual"}:
            raise ValueError("run_mode must be 'auto' or 'manual'")
        supervision = self._normalise_supervision(supervision or {})
        checkpoints = list(supervision["checkpoint_stages"])
        # Manual is not a second control path: it is every stage declared a
        # checkpoint, so it runs through the same gate as everything else and
        # cannot accidentally weaken a hard rule. Auto leaves the gate to decide
        # on its own signals, which is what it did before this existed.
        if run_mode == "manual":
            checkpoints = sorted(set(checkpoints) | _PIPELINE_STAGES)
        # If the caller did not state what should be predicted, the discovery
        # stage is choosing the entire project on their behalf. That is a
        # decision a person has to see before the rest of the pipeline is built
        # on top of it, so the checkpoint is not optional here — it is implied
        # by the absence of a stated problem, not by a supervision preference.
        if problem.confirmed_by == "auto" and "problem_discovery" not in checkpoints:
            checkpoints.append("problem_discovery")
        profile = BUILTIN_PROFILES["full_auto"].model_copy(
            update={"checkpoint_stages": sorted(checkpoints)}
        )
        # Switching the EDA investigator off leaves the fixed profiler running;
        # it trades an agent-authored analysis for roughly a minute of latency.
        agent_runtime_policy = (
            DEFAULT_AGENT_RUNTIME_POLICY
            if eda_agent
            else AgentRuntimePolicy(investigators_disabled=frozenset({"eda_investigation"}))
        )
        policy = self._policy_for_supervision(supervision)
        state = RunState(
            run_id=run_id,
            store=self.store,
            profile=profile,
            user_intent=run_intent,
        )
        llm: StructuredLLM | None = None
        if mode == "agent":
            llm = self.llm_factory() if self.llm_factory else OllamaClient()
            if isinstance(llm, OllamaClient) and not llm.is_available():
                llm.close()
                raise ValueError(
                    "Agent-planned mode needs Ollama at http://localhost:11434. "
                    "Start Ollama or choose Manual plan."
                )
            spec, registry = build_full_spec(llm, panel_size=agent_panel_size)
            sandbox_root = self.store.root.parent / "sandbox" / run_id
            sandbox_data = sandbox_root / "data"
            sandbox_outputs = sandbox_root / "artifacts"
            sandbox_data.mkdir(parents=True, exist_ok=True)
            sandbox_outputs.mkdir(parents=True, exist_ok=True)
            configure_full_pipeline_state(
                state,
                source_path=source_path,
                candidate_limit=candidate_limit,
                validation_folds=validation_strategy.n_folds,
                execution_backend=SandboxManager(
                    SandboxConfig(
                        data_dir=sandbox_data,
                        artifacts_dir=sandbox_outputs,
                    )
                ),
                agent_runtime_policy=agent_runtime_policy,
            )
        else:
            spec, registry = build_default_spec(), build_default_registry()
            configure_pipeline_state(
                state,
                source_path=source_path,
                integration_plan=integration_plan,
                problem=problem,
                validation_strategy=validation_strategy,
                candidate_limit=candidate_limit,
            )
        configuration = {
            "mode": mode,
            "source_id": source_id,
            "base_table": integration_plan.base_table,
            "base_grain": integration_plan.base_grain,
            "target_column": problem.target_column,
            "task_type": problem.task_type.value,
            "primary_metric": problem.primary_metric.value,
            "problem_title": problem.title,
            "validation": validation_strategy.summary(),
            "excluded_columns": problem.excluded_columns,
            "candidate_limit": candidate_limit,
            "instructions": state.user_intent,
            "supervision": supervision,
            "agent_panel_size": agent_panel_size if mode == "agent" else 0,
            "run_mode": run_mode,
            "parent_run_id": parent_run_id,
            "branch_label": branch_label,
        }
        runtime = _RuntimeRun(
            run_id=run_id,
            source_id=source_id,
            state=state,
            configuration=configuration,
        )
        with self._lock:
            self._runtime_runs[run_id] = runtime
        self._persist_runtime(runtime)

        event = self._event_recorder(runtime)

        def execute() -> None:
            with self._lock:
                runtime.status = "running"
                runtime.updated_at = _now()
            self._persist_runtime(runtime)
            try:
                runner = self.workflow_runner or run_workflow
                runtime.outcome = runner(
                    spec,
                    registry,
                    state,
                    rubrics=build_pipeline_rubrics(),
                    policy=policy,
                    on_event=event,
                )
                with self._lock:
                    runtime.status = runtime.outcome.status
                    runtime.error = runtime.outcome.error
                    runtime.current_stage = getattr(runtime.outcome, "final_stage", None)
                    runtime.updated_at = _now()
            except Exception as exc:  # noqa: BLE001 - preserve failure in durable UI state
                with self._lock:
                    runtime.status = "failed"
                    runtime.error = f"{type(exc).__name__}: {exc}"
                    runtime.updated_at = _now()
            finally:
                if isinstance(llm, OllamaClient):
                    llm.close()
            self._persist_runtime(runtime)

        threading.Thread(target=execute, name=f"ads-ui-{run_id}", daemon=True).start()
        return run_id

    @staticmethod
    def _normalise_supervision(raw: dict[str, Any]) -> dict[str, Any]:
        checkpoints = {
            str(stage) for stage in raw.get("checkpoint_stages", []) if stage in _PIPELINE_STAGES
        }
        for stage in raw.get("auto_proceed_stages", []):
            checkpoints.discard(str(stage))
        retries = {
            str(stage): max(0, min(9, int(value)))
            for stage, value in (raw.get("max_retries_by_stage") or {}).items()
            if stage in _PIPELINE_STAGES
        }
        return {
            "checkpoint_stages": sorted(checkpoints),
            "auto_proceed_stages": sorted(
                str(stage)
                for stage in raw.get("auto_proceed_stages", [])
                if stage in _PIPELINE_STAGES
            ),
            "max_retries_by_stage": retries,
        }

    @staticmethod
    def _policy_for_supervision(supervision: dict[str, Any]) -> GatePolicy:
        policy = GatePolicy.load()
        stages = dict(policy.stages)
        for stage_id, retries in supervision.get("max_retries_by_stage", {}).items():
            current = policy.stage(stage_id)
            stages[stage_id] = replace(current, max_attempts=int(retries) + 1)
        return replace(policy, stages=stages)

    def apply_sensitivity_overrides(self, run_id: str, overrides: dict[str, str]) -> dict[str, str]:
        """Let a person correct the machine's PII classification before it is used.

        The classifier decides which columns are dropped from the feature pool.
        It is a heuristic, it is wrong in both directions, and the person looking
        at the screen usually knows which columns are personal. This applies
        their correction to the profiled cards in place, so the decision that
        follows is made on what they said rather than on what a name-matcher
        guessed.

        Deliberately narrow: it can only move a column between `pii` and
        `internal`. It cannot rename, drop, or otherwise reshape a card.
        """
        runtime = self._runtime_runs.get(run_id)
        if runtime is None:
            raise ValueError("this run is not resident; it cannot be adjusted")

        from ads.contracts.datacard import Sensitivity
        from ads.pipeline.stages import SOURCE_CARDS_KEY

        allowed = {"pii": Sensitivity.PII, "internal": Sensitivity.INTERNAL}
        cards = runtime.state.blackboard.get(SOURCE_CARDS_KEY) or []
        known = {column.name for card in cards for column in card.columns}

        applied: dict[str, str] = {}
        for name, value in overrides.items():
            target = allowed.get(str(value).lower())
            if target is None:
                raise ValueError(f"sensitivity must be one of {sorted(allowed)}")
            if name not in known:
                raise ValueError(f"unknown column {name!r}")
            applied[name] = target.value

        if not applied:
            return {}

        # Cards are frozen contracts, so this rebuilds rather than mutates.
        rebuilt = []
        for card in cards:
            columns = [
                col.model_copy(update={"sensitivity": allowed[applied[col.name]]})
                if col.name in applied
                else col
                for col in card.columns
            ]
            rebuilt.append(card.model_copy(update={"columns": columns}))
        runtime.state.blackboard[SOURCE_CARDS_KEY] = rebuilt

        with self._lock:
            runtime.events.append(
                {
                    "event": "sensitivity_overridden",
                    "at": _now(),
                    "stage": runtime.current_stage,
                    "columns": applied,
                }
            )
            runtime.updated_at = _now()
        self._persist_runtime(runtime)
        return applied

    def direct_stage(self, run_id: str, stage_id: str, instruction: str) -> list[str]:
        """Address an instruction to the agent working a specific stage.

        This is not a gate answer and not a retry correction. A correction says
        the last attempt was wrong; a directive says what the person wants, and
        applies every time that stage runs including the first. Keeping them
        separate matters because an agent that cannot tell them apart reads a
        preference as a failure and starts trying to fix something that was not
        broken.

        The instruction survives until the run ends, so it can be left for a
        stage that has not started yet.
        """
        runtime = self._runtime_runs.get(run_id)
        if runtime is None:
            raise ValueError("this run is not resident; it cannot be directed")
        if stage_id not in _PIPELINE_STAGES:
            raise ValueError(f"unknown stage {stage_id!r}")
        text = instruction.strip()
        if not text:
            raise ValueError("instruction cannot be empty")

        from ads.pipeline.stages import STAGE_DIRECTIVES_KEY

        directives = dict(runtime.state.blackboard.get(STAGE_DIRECTIVES_KEY) or {})
        directives[stage_id] = [*directives.get(stage_id, []), text]
        runtime.state.blackboard[STAGE_DIRECTIVES_KEY] = directives

        with self._lock:
            runtime.events.append(
                {
                    "event": "stage_directed",
                    "at": _now(),
                    "stage": stage_id,
                    "instruction": text,
                }
            )
            runtime.updated_at = _now()
        self._persist_runtime(runtime)
        return directives[stage_id]

    def stage_directives(self, run_id: str) -> dict[str, list[str]]:
        runtime = self._runtime_runs.get(run_id)
        if runtime is None:
            return {}
        from ads.pipeline.stages import STAGE_DIRECTIVES_KEY

        return dict(runtime.state.blackboard.get(STAGE_DIRECTIVES_KEY) or {})

    def answer_run(
        self, run_id: str, *, decision: str, instructions: list[str] | None = None
    ) -> None:
        """Apply a human gate answer and resume the in-memory run asynchronously."""
        runtime = self._runtime_runs.get(run_id)
        if runtime is None:
            raise ValueError("this archived run cannot resume after the server restarted")
        if runtime.status != "awaiting_human":
            raise ValueError("this run is not awaiting a human decision")
        offered = runtime.outcome.pending_question if runtime.outcome else None
        allowed = (
            {option.option_id for option in offered.human_prompt.options}
            if offered and offered.human_prompt
            else set()
        )
        if decision not in allowed:
            raise ValueError(f"decision must be one of {sorted(allowed)}")

        with self._lock:
            runtime.events.append(
                {
                    "event": "human_decision_recorded",
                    "at": _now(),
                    "stage": runtime.current_stage,
                    "decision": decision,
                    "instruction_count": len(instructions or []),
                }
            )
            runtime.updated_at = _now()
        self._persist_runtime(runtime)

        mode = runtime.configuration.get("mode", "manual")
        policy = self._policy_for_supervision(
            self._normalise_supervision(runtime.configuration.get("supervision") or {})
        )
        llm: StructuredLLM | None = None
        if mode == "agent":
            llm = self.llm_factory() if self.llm_factory else OllamaClient()
            spec, registry = build_full_spec(
                llm,
                panel_size=int(runtime.configuration.get("agent_panel_size", 1)),
            )
        else:
            spec, registry = build_default_spec(), build_default_registry()

        event = self._event_recorder(runtime)

        def execute() -> None:
            with self._lock:
                runtime.status = "running"
                runtime.updated_at = _now()
            self._persist_runtime(runtime)
            try:
                runtime.outcome = resume_workflow(
                    spec,
                    registry,
                    runtime.state,
                    decision=decision,
                    instructions=instructions or [],
                    rubrics=build_pipeline_rubrics(),
                    policy=policy,
                    on_event=event,
                )
                with self._lock:
                    runtime.status = runtime.outcome.status
                    runtime.error = runtime.outcome.error
                    runtime.current_stage = runtime.outcome.final_stage
                    runtime.updated_at = _now()
            except Exception as exc:  # noqa: BLE001 - present resume failures in the UI
                with self._lock:
                    runtime.status = "failed"
                    runtime.error = f"{type(exc).__name__}: {exc}"
                    runtime.updated_at = _now()
            finally:
                if isinstance(llm, OllamaClient):
                    llm.close()
            self._persist_runtime(runtime)

        threading.Thread(target=execute, name=f"ads-ui-resume-{run_id}", daemon=True).start()

    def _validate_configuration(
        self,
        source_id: str,
        plan: IntegrationPlan,
        problem: ProblemDefinition,
        strategy: ValidationStrategy,
    ) -> None:
        profile = self.source_profile(source_id)
        tables = {item["name"]: item for item in profile["tables"]}
        if plan.base_table not in tables:
            raise ValueError(f"base_table {plan.base_table!r} is not present in the source")
        columns = {item["name"] for item in tables[plan.base_table]["columns"]}
        missing_grain = set(plan.base_grain) - columns
        if missing_grain:
            raise ValueError(f"base_grain columns are missing: {sorted(missing_grain)}")
        if problem.target_column and problem.target_column not in columns:
            raise ValueError(f"target_column {problem.target_column!r} is not in the base table")
        for label, value in (
            ("group_column", strategy.group_column),
            ("time_column", strategy.time_column),
        ):
            if value and value not in columns:
                raise ValueError(f"{label} {value!r} is not in the base table")

    def contracts_from_body(
        self, body: dict[str, Any]
    ) -> tuple[IntegrationPlan, ProblemDefinition, ValidationStrategy]:
        """Accept the structured UI shape and the prior explicit-contract API shape."""
        if {"integration_plan", "problem", "validation_strategy"} <= body.keys():
            return (
                IntegrationPlan.model_validate(body["integration_plan"]),
                ProblemDefinition.model_validate(body["problem"]),
                ValidationStrategy.model_validate(body["validation_strategy"]),
            )
        grain = body.get("base_grain")
        if isinstance(grain, str):
            grain = [grain]
        validation = body.get("validation") or {}

        # A caller may legitimately not know what it wants predicted yet — that
        # is the whole premise of agent mode, where problem_discovery decides.
        # These fields are therefore optional, and when they are absent the
        # problem is marked `auto` rather than `human`.
        #
        # Recording an unspecified problem as human-confirmed was not harmless:
        # the UI silently defaulted to the first table's first candidate target,
        # that placeholder was written to the record as the human's intent, and
        # the run built an entire project around it.
        stated = {"task_type", "primary_metric", "target_column"} & body.keys()
        task_type = TaskType(body["task_type"]) if body.get("task_type") else TaskType.REGRESSION
        metric = Metric(body["primary_metric"]) if body.get("primary_metric") else Metric.RMSE
        return (
            IntegrationPlan(
                base_table=str(body.get("base_table") or ""),
                base_grain=list(grain or []),
                grain_description=str(
                    body.get("grain_description") or "One analytical row per selected grain."
                ),
            ),
            ProblemDefinition(
                task_type=task_type,
                target_column=body.get("target_column") or None,
                primary_metric=metric,
                title=str(
                    body.get("problem_title")
                    or ("Configured analysis" if stated else "Awaiting problem discovery")
                )[:120],
                description=str(
                    body.get("problem_description")
                    or (
                        "Problem configured through the local workflow UI."
                        if stated
                        else "Placeholder. No problem was stated at launch; the "
                        "discovery stage proposes one and a human confirms it."
                    )
                )[:1000],
                excluded_columns=list(body.get("excluded_columns") or []),
                confirmed_by="human" if stated else "auto",
            ),
            ValidationStrategy(
                strategy=SplitStrategy(validation.get("strategy", "random")),
                n_folds=int(validation.get("n_folds", 5)),
                test_size=float(validation.get("test_size", 0.2)),
                group_column=validation.get("group_column") or None,
                time_column=validation.get("time_column") or None,
                holdout_cutoff=validation.get("holdout_cutoff") or None,
                rationale=str(
                    validation.get("rationale")
                    or "Validation strategy selected in the local workflow UI."
                )[:800],
            ),
        )

    def _attempts(self, runtime: _RuntimeRun) -> list[dict[str, Any]]:
        return [
            {
                "stage_id": attempt.stage_id,
                "attempt": attempt.attempt,
                "started_at": attempt.started_at.isoformat(),
                "ended_at": attempt.ended_at.isoformat() if attempt.ended_at else None,
                "artifact_ids": list(attempt.artifact_ids),
                "input_bindings": dict(attempt.input_bindings),
                "verdict": attempt.decision.verdict.value if attempt.decision else None,
                "error": attempt.error,
                "critique": attempt.critique.model_dump(mode="json") if attempt.critique else None,
            }
            for attempt in runtime.state.attempts
        ]

    def _runtime_snapshot(self, runtime: _RuntimeRun) -> dict[str, Any]:
        with self._lock:
            return {
                "run_id": runtime.run_id,
                "source_id": runtime.source_id,
                "configuration": dict(runtime.configuration),
                "created_at": runtime.created_at,
                "updated_at": runtime.updated_at,
                "status": runtime.status,
                "current_stage": runtime.current_stage,
                "events": list(runtime.events),
                "error": runtime.error,
                "attempts": self._attempts(runtime),
                # The question the run stopped to ask. It lived only on the
                # in-memory outcome, so `progress` -- the endpoint the run
                # screen polls -- reported `awaiting_human` and carried nothing
                # to show, and a run that had stopped for a person displayed no
                # way to answer it.
                "pending_question": _pending_question(runtime),
            }

    def _persist_runtime(self, runtime: _RuntimeRun) -> None:
        snapshot = self._runtime_snapshot(runtime)
        target = self._run_state_root / f"{runtime.run_id}.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
        temporary.replace(target)

    #: Snapshot states that only a live worker in this process can be making
    #: progress on. `awaiting_human` is deliberately absent: it is durable by
    #: design and is meant to be resumed after a restart.
    _IN_FLIGHT = frozenset({"queued", "running", "staging"})

    def progress(self, run_id: str) -> dict[str, Any]:
        runtime = self._runtime_runs.get(run_id)
        if runtime is not None:
            return self._runtime_snapshot(runtime)
        target = self._run_state_root / f"{run_id}.json"
        if target.exists():
            snapshot = json.loads(target.read_text(encoding="utf-8"))
            return self._reconcile_orphan(snapshot)
        return self._legacy_progress(run_id)

    def _reconcile_orphan(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        """Report an abandoned run as interrupted rather than still running.

        Runs execute in worker threads owned by this process, so a snapshot
        left in an in-flight state with no matching in-memory runtime belongs
        to a process that is gone — a restart, or a crash. Nothing will ever
        advance it.

        Reporting it as `running` is wrong in three ways that compound: the run
        list shows perpetual activity, a polling client refreshes it forever,
        and `delete_run` refuses to remove it as "active" — so the stale run
        cannot be cleared by any means short of deleting files by hand.
        """
        if snapshot.get("status") not in self._IN_FLIGHT:
            return snapshot
        return {
            **snapshot,
            "status": "interrupted",
            "error": snapshot.get("error")
            or (
                "The process running this stage exited before it finished. No "
                "worker is advancing it; start a new run or delete this one."
            ),
        }

    def _legacy_progress(self, run_id: str) -> dict[str, Any]:
        """Synthesize a stable read model for runs created before UI snapshots."""
        artifacts = self.artifacts(run_id)
        if not artifacts:
            raise KeyError(run_id)
        decisions = self.gate_decisions(run_id)
        by_stage: dict[str, list[dict[str, Any]]] = {}
        for artifact in artifacts:
            if artifact["stage"]:
                by_stage.setdefault(artifact["stage"], []).append(artifact)
        decision_by_stage = {item["stage_id"]: item for item in decisions}
        stages = list(by_stage)
        attempts = []
        for stage_id in stages:
            outputs = by_stage[stage_id]
            decision = decision_by_stage.get(stage_id)
            attempts.append(
                {
                    "stage_id": stage_id,
                    "attempt": decision.get("attempt", 1) if decision else 1,
                    "started_at": outputs[-1]["created_at"],
                    "ended_at": outputs[0]["created_at"],
                    "artifact_ids": [item["artifact_id"] for item in outputs],
                    "input_bindings": {},
                    "verdict": decision.get("verdict") if decision else None,
                    "error": None,
                    "critique": None,
                }
            )
        status = decisions[-1]["verdict"] if decisions else "archived"
        if status == "escalate":
            status = "awaiting_human"
        uses_agents = bool(_PLANNER_AGENTS & set(stages))
        last_activity = max(item["created_at"] for item in artifacts)
        return {
            "run_id": run_id,
            "source_id": None,
            "configuration": {"mode": "agent" if uses_agents else "manual"},
            "created_at": min(item["created_at"] for item in artifacts),
            "updated_at": last_activity,
            "status": status,
            "current_stage": stages[-1] if stages else None,
            "events": [
                {
                    "event": "recorded_stage",
                    "at": by_stage[stage_id][0]["created_at"],
                    "stage": stage_id,
                }
                for stage_id in stages
            ],
            "error": None,
            "attempts": attempts,
            "legacy_snapshot": True,
        }

    # ------------------------------------------------------------- artifacts

    def artifacts(self, run_id: str) -> list[dict[str, Any]]:
        """Artifact metadata and summaries only. No source frame is serialised."""
        return [
            {
                "artifact_id": ref.artifact_id,
                "type": ref.artifact_type.value,
                "name": ref.name,
                "stage": ref.stage_exec_id,
                "created_at": ref.created_at,
                "summary": ref.summary,
                "presentation": self._artifact_presentation(
                    ref.artifact_type.value, ref.name, ref.summary
                ),
            }
            for ref in self.store.list(run_id)
        ]

    @staticmethod
    def _artifact_presentation(
        artifact_type: str, name: str | None, summary: dict[str, Any]
    ) -> dict[str, Any]:
        """Turn compact machine summaries into stable, non-technical UI copy."""
        title = i18n.t((name or artifact_type).replace("_", " ").title())
        description = i18n.t("A durable result produced by this stage.")
        preferred: list[tuple[str, str]] = []
        if artifact_type == "data_card":
            title = i18n.t(
                "Profiled table: {name}",
                name=summary.get("table_name", name or i18n.t("dataset")),
            )
            description = i18n.t("Schema, size, and quality statistics; no source rows are shown.")
            preferred = [
                (i18n.t("Rows"), "n_rows"),
                (i18n.t("Columns"), "n_columns"),
                (i18n.t("Candidate keys"), "n_candidate_keys"),
            ]
        elif artifact_type == "integration_plan":
            title = i18n.t("Data integration plan")
            description = i18n.t(
                "How source tables are aggregated and joined into one analytical table."
            )
            preferred = [
                (i18n.t("Base table"), "base_table"),
                (i18n.t("Joins"), "n_joins"),
                (i18n.t("Aggregations"), "n_aggregations"),
            ]
        elif artifact_type == "integration_trial":
            title = i18n.t("Integration plan trial")
            description = i18n.t(
                "Measured result of executing the proposed joins and aggregations on a copy."
            )
            preferred = [
                (i18n.t("Base rows"), "base_rows"),
                (i18n.t("Result rows"), "result_rows"),
                (i18n.t("Grain preserved"), "grain_preserved"),
                (i18n.t("Result columns"), "n_columns"),
            ]
        elif artifact_type == "problem_candidates":
            title = i18n.t("Candidate analysis problems")
            description = i18n.t(
                "Problems proposed by the planner and checked against measured support."
            )
            preferred = [(i18n.t("Candidates"), "n_candidates"), (i18n.t("Viable"), "n_viable")]
        elif artifact_type == "problem_definition":
            title = str(summary.get("title") or i18n.t("Selected problem"))
            description = i18n.t("The target, task, and success metric used downstream.")
            preferred = [
                (i18n.t("Task"), "task_type"),
                (i18n.t("Target"), "target_column"),
                (i18n.t("Metric"), "primary_metric"),
            ]
        elif artifact_type == "validation_strategy":
            title = i18n.t("Validation plan")
            description = i18n.t(
                "How training and holdout data are separated to keep evaluation honest."
            )
            preferred = [
                (i18n.t("Strategy"), "strategy"),
                (i18n.t("Folds"), "n_folds"),
                (i18n.t("Group"), "group_column"),
                (i18n.t("Time"), "time_column"),
            ]
        elif artifact_type == "leakage_report":
            title = i18n.t("Leakage audit")
            description = i18n.t(
                "Features checked for information that would make model results "
                "unrealistically good."
            )
            preferred = [
                (i18n.t("Features checked"), "n_features_checked"),
                (i18n.t("Findings"), "n_findings"),
                (i18n.t("Blocking"), "n_blocking"),
                (i18n.t("Clean"), "is_clean"),
            ]
        elif artifact_type == "trained_model":
            title = i18n.t("Model comparison")
            description = i18n.t(
                "Candidate models compared by cross-validation and untouched holdout performance."
            )
            preferred = [
                (i18n.t("Winner"), "winner_id"),
                (i18n.t("Metric"), "primary_metric"),
                (i18n.t("Holdout score"), "winner_holdout_score"),
                (i18n.t("Training rows"), "training_row_count"),
            ]
        elif artifact_type == "model_experiment":
            title = str(summary.get("title") or i18n.t("Agent-authored model experiment"))
            description = i18n.t(
                "An isolated development experiment scored by the host on withheld labels."
            )
            preferred = [
                (i18n.t("Model family"), "model_family"),
                (i18n.t("Metric"), "metric"),
                (i18n.t("Score"), "score"),
                (i18n.t("Baseline"), "baseline_score"),
                (i18n.t("Evaluation rows"), "evaluation_rows"),
            ]
        elif artifact_type == "evaluation_report":
            title = i18n.t("Evaluation result")
            description = i18n.t(
                "The selected model compared with its baseline, including alerts "
                "and decision history."
            )
            preferred = [
                (i18n.t("Winner"), "winner_id"),
                (i18n.t("Metric"), "primary_metric"),
                (i18n.t("Holdout score"), "winner_holdout_score"),
                (i18n.t("Baseline improvement"), "baseline_delta"),
                (i18n.t("Alerts"), "n_alerts"),
            ]
        elif artifact_type == "final_report":
            title = i18n.t("Final auditable report")
            description = i18n.t(
                "The human-readable handoff tying conclusions to the exact evaluation evidence."
            )
            preferred = [(i18n.t("Report length"), "n_characters")]
        elif artifact_type == "agent_audit":
            title = i18n.t("Agent execution audit")
            description = i18n.t(
                "Contract validation, panel agreement, and deterministic tool provenance."
            )
            preferred = [
                (i18n.t("Agent"), "agent_id"),
                (i18n.t("Panel"), "panel_size"),
                (i18n.t("Valid"), "valid_members"),
                (i18n.t("Agreement"), "agreement"),
                (i18n.t("Pydantic"), "pydantic_validated"),
                (i18n.t("Tools"), "tool_count"),
                (i18n.t("Skills"), "skill_count"),
            ]
        elif artifact_type == "eda_report":
            title = i18n.t("Exploratory data findings")
            description = i18n.t(
                "Measured distributions, missingness, and relationships relevant to the problem."
            )
            preferred = [(i18n.t(key.replace("_", " ").title()), key) for key in summary][:4]
        elif artifact_type == "exploratory_analysis":
            title = str(summary.get("title") or i18n.t("Agent-authored exploratory analysis"))
            description = i18n.t(
                "Validated exploratory output produced by locally executed code."
            )
            preferred = [
                (i18n.t("Evidence class"), "evidence_class"),
                (i18n.t("Chart"), "chart_kind"),
                (i18n.t("Tool calls"), "tool_calls"),
            ]
        facts = [
            {"label": label, "value": summary[key]}
            for label, key in preferred
            if key in summary and summary[key] is not None
        ]
        return {"title": title, "description": description, "facts": facts}

    def _artifact_story(
        self,
        artifact: dict[str, Any],
        *,
        linked_interpretations: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """A selective, row-free narrative projection for one artifact card."""
        artifact_type = artifact["type"]
        story: dict[str, Any] = {
            "headline": artifact["presentation"]["title"],
            "explanation": artifact["presentation"]["description"],
            "facts": artifact["presentation"]["facts"],
        }
        if artifact_type == "data_card":
            return story
        payload = self.artifact_payload(artifact["artifact_id"])
        if artifact_type == "integration_plan":
            story["suggestion"] = i18n.t(
                "Use {base_table} at one row per {base_grain}; apply {n_aggs} "
                "aggregation(s) before {n_joins} join(s).",
                base_table=payload["base_table"],
                base_grain=", ".join(payload["base_grain"]),
                n_aggs=len(payload["aggregations"]),
                n_joins=len(payload["joins"]),
            )
            story["warnings"] = payload.get("warnings", [])
            # The join plan as a picture. The brief asks that the schema not be
            # something only the agent understands.
            story["schema_graph"] = schema_graph(payload)
        elif artifact_type == "comprehension_brief":
            # The agent's own explanations. They were produced and stored from
            # the beginning and never rendered, so the half of the product that
            # exists to help a person understand their data was invisible.
            items = payload.get("items", [])
            # Both languages are stored; the reader's choice picks one here, and
            # falls back to English when the model did not produce the Turkish
            # half rather than showing an empty card.
            turkish = i18n.current() == "tr"

            def _pick(item: dict[str, Any], field: str) -> Any:
                if turkish and item.get(f"{field}_tr"):
                    return item[f"{field}_tr"]
                return item.get(field)

            story["insights"] = [
                {
                    "kind": item.get("kind"),
                    "interpretation": _pick(item, "interpretation"),
                    "why_it_matters": _pick(item, "why_it_matters"),
                    "verification_question": (
                        item.get("verification_question_tr")
                        if turkish and item.get("verification_question_tr")
                        else (item.get("verification") or {}).get("question")
                        if isinstance(item.get("verification"), dict)
                        else item.get("verification")
                    ),
                    "confidence": item.get("confidence"),
                    "epistemic_state": item.get("epistemic_state", "proposed"),
                    "subjects": [
                        f"{subject.get('table')}.{subject.get('column')}"
                        if subject.get("column")
                        else subject.get("table")
                        for subject in item.get("subjects", [])
                    ],
                }
                for item in items
            ]
            if payload.get("degraded"):
                # Say that the agent could not produce an explanation, rather
                # than showing an empty section that reads as "nothing to say".
                story["warnings"] = [
                    str(
                        payload.get("degradation_reason")
                        or i18n.t("Interpretation was not produced.")
                    )
                ]
        elif artifact_type == "integration_trial":
            story["suggestion"] = i18n.t(
                "The proposed plan was executed by the deterministic integration engine "
                "before it was accepted."
            )
            story["warnings"] = payload.get("warnings", [])
        elif artifact_type == "problem_candidates":
            story["suggestion"] = i18n.t("The planner ranked these analysis problems.")
            story["choices"] = [
                {
                    "title": item.get("title"),
                    "target": item.get("target_column"),
                    "task": item.get("task_type"),
                    "metric": item.get("primary_metric"),
                    "rationale": item.get("business_rationale"),
                    "viable": not item.get("support", {}).get("blocking_reasons"),
                }
                for item in payload.get("candidates", [])
            ]
        elif artifact_type == "problem_definition":
            story["suggestion"] = i18n.t(
                "Proceed with “{title}” as a {task_type} problem, predicting "
                "{target_column} and measuring {primary_metric}.",
                title=payload.get("title"),
                task_type=payload.get("task_type"),
                target_column=payload.get("target_column"),
                primary_metric=payload.get("primary_metric"),
            )
            story["rationale"] = payload.get("description")
            story["excluded_columns"] = payload.get("excluded_columns", [])
        elif artifact_type == "validation_strategy":
            story["panels"] = validation_panels(payload)
            story["suggestion"] = i18n.t(
                "Use {strategy} validation with {n_folds} folds"
                + (
                    ", grouped by {group_column}"
                    if payload.get("group_column")
                    else ""
                )
                + (
                    ", ordered by {time_column}"
                    if payload.get("time_column")
                    else ""
                )
                + ".",
                strategy=payload.get("strategy"),
                n_folds=payload.get("n_folds"),
                group_column=payload.get("group_column"),
                time_column=payload.get("time_column"),
            )
            story["rationale"] = payload.get("rationale")
        elif artifact_type == "leakage_report":
            blocking = [item for item in payload.get("findings", []) if item.get("blocking")]
            story["panels"] = leakage_panels(payload)
            story["suggestion"] = (
                i18n.t("Do not train yet; correct or exclude the blocking features.")
                if blocking
                else i18n.t("No blocking leakage was detected; training may proceed.")
            )
            story["findings"] = [
                {
                    "column": item.get("column"),
                    "kind": item.get("kind"),
                    "detail": item.get("detail"),
                    "action": item.get("suggested_action"),
                    "blocking": item.get("blocking"),
                }
                for item in payload.get("findings", [])
            ]
        elif artifact_type == "eda_report":
            target = payload.get("target_distribution") or {}
            numeric = target.get("numeric") or {}
            relationships = sorted(
                payload.get("target_relationships", []),
                key=lambda item: max(
                    abs(item.get("pearson_correlation") or 0.0),
                    item.get("adjusted_mutual_information") or 0.0,
                ),
                reverse=True,
            )
            missingness = sorted(
                payload.get("missingness", []),
                key=lambda item: item.get("null_rate", 0.0),
                reverse=True,
            )
            outliers = sorted(
                payload.get("outliers", []),
                key=lambda item: item.get("outlier_rate", 0.0),
                reverse=True,
            )
            target_name = payload.get("target_column")
            story["suggestion"] = (
                i18n.t(
                    "Start by understanding {target}: inspect its distribution, then "
                    "review missing fields and the strongest measured relationships before "
                    "accepting any modelling direction.",
                    target=target_name,
                )
                if target_name
                else i18n.t(
                    "Review coverage, missingness, correlations, and outliers before modelling."
                )
            )
            story["visuals"] = {
                "target": {
                    "column": target.get("target_column"),
                    "kind": target.get("kind"),
                    "total_count": target.get("total_count"),
                    "non_null_count": target.get("non_null_count"),
                    "null_rate": target.get("null_rate"),
                    "values": target.get("values", []),
                    "numeric": numeric,
                },
                "missingness": missingness,
                "relationships": relationships[:16],
                "outliers": outliers[:16],
                "correlation": payload.get("correlation_matrix") or {},
            }
            # The analysis strip the design specifies: one panel per measured
            # analysis, each with its own chart, severity and insights.
            story["panels"] = eda_panels(payload)
            story["criteria"] = {
                i18n.t("Target distribution measured"): bool(target_name is None or target),
                i18n.t("Every feature covered"): set(
                    payload.get("feature_columns", [])
                ).issubset(payload.get("covered_columns", [])),
                i18n.t("Missingness measured"): {
                    item.get("column") for item in missingness
                }.issuperset(
                    set(payload.get("covered_columns", []))
                    | ({target_name} if target_name else set())
                ),
            }
            warnings = []
            high_missing = [item for item in missingness if (item.get("null_rate") or 0.0) >= 0.1]
            if high_missing:
                warnings.append(
                    i18n.t(
                        "{count} column(s) have at least 10% missing values; "
                        "confirm how they should be handled.",
                        count=len(high_missing),
                    )
                )
            strong = [
                item for item in relationships if abs(item.get("pearson_correlation") or 0.0) >= 0.9
            ]
            if strong:
                warnings.append(
                    i18n.t(
                        "{count} feature(s) have |correlation| at or above 0.90; "
                        "treat these as leakage candidates until audited.",
                        count=len(strong),
                    )
                )
            story["warnings"] = warnings
        elif artifact_type == "exploratory_analysis":
            story["panels"] = [exploratory_panel(payload, linked_interpretations or [])]
            story["suggestion"] = i18n.t(
                "Treat this as a proposed extension to the mandatory EDA, not as gate evidence."
            )
        elif artifact_type == "trained_model":
            story["panels"] = training_panels(payload)
            story["suggestion"] = i18n.t(
                "Select {winner} from {count} compared candidates using {metric}.",
                winner=payload.get("winner_id"),
                count=len(payload.get("results", [])),
                metric=payload.get("primary_metric"),
            )
            story["model_comparison"] = [
                {
                    "candidate": item.get("display_name") or item.get("candidate_id"),
                    "selected": item.get("candidate_id") == payload.get("winner_id"),
                    "baseline": item.get("is_baseline", False),
                    "cv_mean": next(
                        (
                            score.get("cv_mean")
                            for score in item.get("metrics", [])
                            if score.get("metric") == payload.get("primary_metric")
                        ),
                        None,
                    ),
                    "cv_std": next(
                        (
                            score.get("cv_std")
                            for score in item.get("metrics", [])
                            if score.get("metric") == payload.get("primary_metric")
                        ),
                        None,
                    ),
                    "holdout": next(
                        (
                            score.get("holdout_score")
                            for score in item.get("metrics", [])
                            if score.get("metric") == payload.get("primary_metric")
                        ),
                        None,
                    ),
                }
                for item in payload.get("results", [])
            ]
        elif artifact_type == "model_experiment":
            story["panels"] = [model_experiment_panel(payload, linked_interpretations or [])]
            story["suggestion"] = i18n.t(
                "Review this as a proposed development experiment; it did not use the "
                "final holdout and cannot change the selected model."
            )
        elif artifact_type == "evaluation_report":
            story["panels"] = evaluation_panels(payload)
            story["suggestion"] = i18n.t(
                "The selected {winner} scored {score} on holdout; baseline improvement "
                "was {delta}.",
                winner=payload.get("winner_display_name"),
                score=payload.get("winner_holdout_score"),
                delta=payload.get("baseline_delta"),
            )
            gate_history = payload.get("gate_history", [])
            story["history_alerts"] = [
                {
                    "detail": item.get("detail"),
                    "resolved": any(
                        gate.get("stage_id") == item.get("stage_id")
                        and gate.get("verdict") == "auto_proceed"
                        and (gate.get("attempt") or 0) > 1
                        for gate in gate_history
                    ),
                }
                for item in payload.get("alerts", [])
            ]
            story["warnings"] = [
                item["detail"] for item in story["history_alerts"] if not item["resolved"]
            ]
            story["model_comparison"] = [
                {
                    "candidate": item.get("display_name") or item.get("candidate_id"),
                    "selected": item.get("selected", False),
                    "baseline": item.get("is_baseline", False),
                    "cv_mean": item.get("cv_mean"),
                    "cv_std": item.get("cv_std"),
                    "holdout": item.get("holdout_score"),
                }
                for item in payload.get("candidate_comparisons", [])
            ]
            story["holdout_metrics"] = payload.get("holdout_metrics", [])
        elif artifact_type == "final_report":
            story["suggestion"] = i18n.t("The final report is ready for review and export.")
            story["report_markdown"] = payload.get("markdown")
        elif artifact_type == "agent_audit":
            story["suggestion"] = i18n.t(
                "Review panel agreement and validation evidence before trusting this "
                "agent-authored contract."
            )
            row_access = bool(payload.get("raw_rows_shared", False))
            row_access_authorized = not row_access or payload.get("agent_id") == "eda_investigator"
            story["quality_checks"] = [
                {
                    "label": i18n.t("Pydantic output contract"),
                    "passed": payload.get("pydantic_contract_enforced", False),
                    "detail": payload.get("output_contract"),
                },
                {
                    "label": i18n.t("Row access matches the agent role"),
                    "passed": row_access_authorized,
                    "detail": (
                        i18n.t(
                            "Local investigator may read a read-only copy; "
                            "output remains typed."
                        )
                        if row_access
                        else i18n.t(
                            "Planner context contains schema and "
                            "aggregate measurements only."
                        )
                    ),
                },
                {
                    "label": i18n.t("Evidence tools were allowlisted"),
                    "passed": set(payload.get("evidence_tools", []))
                    <= set(payload.get("allowed_tools", [])),
                    "detail": ", ".join(payload.get("evidence_tools", [])) or i18n.t("none"),
                },
            ]
            story["panel"] = payload.get("members", [])
        return story

    def artifact_payload(self, artifact_id: str) -> dict[str, Any]:
        path = self.store._payload_path(artifact_id)  # noqa: SLF001 - typed artifact payload
        if not path.exists():
            raise KeyError(artifact_id)
        return json.loads(path.read_text(encoding="utf-8"))

    # ---------------------------------------------------------------- gates

    def gate_decisions(self, run_id: str) -> list[dict[str, Any]]:
        refs = self.store.list(run_id, artifact_type=ArtifactType.GATE_DECISION)
        decisions: list[dict[str, Any]] = []
        for ref in refs:
            payload = self.artifact_payload(ref.artifact_id)
            decisions.append(
                {
                    "artifact_id": ref.artifact_id,
                    "created_at": ref.created_at,
                    "stage_id": payload.get("stage_id"),
                    "attempt": payload.get("attempt"),
                    "verdict": payload.get("verdict"),
                    "reason_code": payload.get("reason_code"),
                    "triggered_rules": payload.get("triggered_rules", []),
                    "human_prompt": payload.get("human_prompt"),
                    "correction_instructions": payload.get("correction_instructions", []),
                }
            )
        decisions.sort(key=lambda item: (item["created_at"], item.get("attempt") or 0))
        return decisions

    def stage_detail(self, run_id: str, stage_id: str) -> dict[str, Any]:
        try:
            try:
                progress = self.progress(run_id)
            except KeyError:
                progress = {"events": [], "attempts": [], "status": None, "current_stage": None}
            mode = self._mode_of(progress)
            spec = build_full_spec_definition() if mode == "agent" else build_default_spec()
            definition = spec.stage(stage_id)
        except KeyError:
            raise KeyError(stage_id) from None
        attempts = [item for item in progress["attempts"] if item["stage_id"] == stage_id]
        all_artifacts = self.artifacts(run_id)
        attempted_ids = {
            artifact_id for attempt in attempts for artifact_id in attempt["artifact_ids"]
        }
        artifacts = [
            item
            for item in all_artifacts
            if item["type"] != "gate_decision"
            and (item["stage"] == stage_id or item["artifact_id"] in attempted_ids)
        ]
        interpretations_by_source: dict[str, list[dict[str, Any]]] = {}
        for item in artifacts:
            if item["type"] != ArtifactType.COMPREHENSION_BRIEF.value:
                continue
            brief = self.artifact_payload(item["artifact_id"])
            for interpretation in brief.get("items", []):
                for citation in interpretation.get("citations", []):
                    source_id = citation.get("source_artifact_id")
                    if isinstance(source_id, str):
                        interpretations_by_source.setdefault(source_id, []).append(
                            {
                                "interpretation": interpretation.get("interpretation"),
                                "why_it_matters": interpretation.get("why_it_matters"),
                                "verification_question": (
                                    interpretation.get("verification") or {}
                                ).get("question"),
                                "confidence": interpretation.get("confidence"),
                                "epistemic_state": interpretation.get("epistemic_state"),
                                "measurement_id": citation.get("measurement_id"),
                            }
                        )
        for artifact in artifacts:
            artifact["story"] = self._artifact_story(
                artifact,
                linked_interpretations=interpretations_by_source.get(artifact["artifact_id"], []),
            )
        decisions = [item for item in self.gate_decisions(run_id) if item["stage_id"] == stage_id]
        questions = [decision["human_prompt"] for decision in decisions if decision["human_prompt"]]
        primary_outputs = next(
            (
                [item for item in artifacts if item["type"] == kind.value]
                for kind in definition.produces
                if any(item["type"] == kind.value for item in artifacts)
            ),
            [],
        )
        stage_panels: list[dict[str, Any]] = []
        data_cards = [item for item in artifacts if item["type"] == "data_card"]
        if len(data_cards) > 1:
            stage_panels = source_panels(
                [
                    {"name": item["name"], "payload": self.artifact_payload(item["artifact_id"])}
                    for item in data_cards
                ]
            )
        latest_story = (primary_outputs or artifacts)[0]["story"] if artifacts else None
        stage_status = self._stage_status(
            attempts[-1] if attempts else None,
            progress.get("current_stage"),
            progress.get("status"),
        )
        active_question = questions[-1] if questions and stage_status == "blocked" else None
        return {
            "run_id": run_id,
            "stage": {
                "id": definition.id,
                "description": i18n.t(definition.description),
                "consumes": [item.value for item in definition.consumes],
                "produces": [item.value for item in definition.produces],
            },
            "status": stage_status,
            "attempts": attempts,
            "events": [item for item in progress["events"] if item.get("stage") == stage_id],
            "inputs": [
                {"artifact_type": kind, "artifact_id": artifact_id}
                for attempt in attempts
                for kind, artifact_id in attempt.get("input_bindings", {}).items()
            ],
            "outputs": artifacts,
            "measurements": [
                {"artifact_type": item["type"], "name": item["name"], "values": item["summary"]}
                for item in artifacts
            ],
            "panels": stage_panels,
            "gate_decisions": decisions,
            "corrections": [
                correction
                for decision in decisions
                for correction in decision["correction_instructions"]
            ],
            "human_questions": questions,
            "human_view": {
                "needs_human": active_question is not None,
                "state_label": (
                    i18n.t("Your decision is needed")
                    if active_question
                    else i18n.t("In progress")
                    if stage_status == "running"
                    else i18n.t("No action needed")
                ),
                "suggestion": latest_story.get("suggestion") if latest_story else None,
                "question": active_question,
                "can_resume": run_id in self._runtime_runs,
            },
        }

    def run_detail(self, run_id: str) -> dict[str, Any]:
        detail: dict[str, Any] = {
            "run_id": run_id,
            "artifacts": self.artifacts(run_id),
            "decisions": self.gate_decisions(run_id),
        }
        try:
            detail["progress"] = self.progress(run_id)
        except KeyError:
            pass
        return detail

    def experiment_catalog(self) -> list[dict[str, Any]]:
        """Runs enriched with safe configuration, progress, and gate summaries."""
        catalog: list[dict[str, Any]] = []
        for summary in self.list_runs():
            try:
                progress = self.progress(summary.run_id)
            except KeyError:
                progress = {}
            decisions = self.gate_decisions(summary.run_id)
            attempts = progress.get("attempts", [])
            catalog.append(
                {
                    **summary.to_dict(),
                    "configuration": progress.get("configuration", {}),
                    "completed_stages": sum(
                        item.get("verdict") == "auto_proceed" for item in attempts
                    ),
                    "attempts": len(attempts),
                    "retries": sum(item.get("verdict") == "retry" for item in attempts),
                    "human_stops": sum(item.get("verdict") == "escalate" for item in attempts),
                    "latest_gate": decisions[-1] if decisions else None,
                    "deletable": summary.status not in {"queued", "running", "staging"},
                }
            )
        return catalog

    def model_catalog(self) -> list[dict[str, Any]]:
        """Saved trained-model artifacts across runs, with no training rows."""
        models: list[dict[str, Any]] = []
        for run in self.list_runs():
            for artifact in self.artifacts(run.run_id):
                if artifact["type"] != ArtifactType.TRAINED_MODEL.value:
                    continue
                payload = self.artifact_payload(artifact["artifact_id"])
                winner_id = payload.get("winner_id")
                winner = next(
                    (
                        item
                        for item in payload.get("results", [])
                        if item.get("candidate_id") == winner_id
                    ),
                    {},
                )
                metric = next(
                    (
                        item
                        for item in winner.get("metrics", [])
                        if item.get("metric") == payload.get("primary_metric")
                    ),
                    {},
                )
                models.append(
                    {
                        "artifact_id": artifact["artifact_id"],
                        "run_id": run.run_id,
                        "created_at": artifact["created_at"],
                        "winner_id": winner_id,
                        "display_name": winner.get("display_name", winner_id),
                        "estimator": winner.get("estimator_class"),
                        "metric": payload.get("primary_metric"),
                        "holdout_score": metric.get("holdout_score"),
                        "cv_mean": metric.get("cv_mean"),
                        "cv_std": metric.get("cv_std"),
                        "saved": bool(payload.get("model_blob")),
                        "candidate_count": len(payload.get("results", [])),
                        "training_rows": payload.get("training_row_count"),
                    }
                )
        return models

    def report_catalog(self) -> list[dict[str, Any]]:
        """Final report artifacts with safe previews and export ids."""
        reports: list[dict[str, Any]] = []
        for run in self.list_runs():
            for artifact in self.artifacts(run.run_id):
                if artifact["type"] != ArtifactType.FINAL_REPORT.value:
                    continue
                payload = self.artifact_payload(artifact["artifact_id"])
                markdown = str(payload.get("markdown", ""))
                preview = " ".join(
                    line.lstrip("# ") for line in markdown.splitlines() if line.strip()
                )[:320]
                reports.append(
                    {
                        "artifact_id": artifact["artifact_id"],
                        "run_id": run.run_id,
                        "created_at": artifact["created_at"],
                        "characters": len(markdown),
                        "preview": preview,
                        "evaluation_artifact_id": payload.get("evaluation_artifact_id"),
                    }
                )
        return reports

    def hardening_status(self) -> dict[str, Any]:
        """Actual, code-backed controls shown in Settings and agent stage cards."""
        data_root = self.source_roots[0]
        sandbox = SandboxConfig(data_dir=data_root, artifacts_dir=self.store.root)
        return {
            "contracts": {
                "engine": "Pydantic v2",
                "extra_fields": "forbidden",
                "artifacts": "immutable and schema-versioned",
                "model_output": "grammar-constrained then Pydantic validated",
            },
            "agents": {
                "planning_stages": sorted(_PLANNER_AGENTS),
                "panel_sizes": [1, 2, 3],
                "selection": "exact validated-contract majority",
                "semantic_validators": True,
                "deterministic_auto_repair": True,
                "tool_allowlists": True,
                "raw_rows_shared": True,
                "raw_row_access_scope": (
                    "Read-only ABT copy, on demand, local EDA investigator only"
                ),
            },
            "orchestration": {
                "attempt_input_pinning": True,
                "versioned_rubrics": True,
                "deterministic_gates": True,
                "human_resume": True,
                "durable_progress": True,
            },
            "sandbox": sandbox.security_policy(),
        }

    def delete_run(self, run_id: str, *, confirmation: str) -> dict[str, Any]:
        """Remove one terminal run after exact-id confirmation."""
        if confirmation != run_id:
            raise ValueError("confirmation must exactly match run_id")
        if _SAFE_RUN_ID.fullmatch(run_id) is None:
            raise ValueError("run_id contains unsafe characters")
        known = set(self._artifact_run_ids()) | set(self._snapshot_run_ids())
        if run_id not in known:
            raise KeyError(run_id)
        try:
            status = self.progress(run_id).get("status", "archived")
        except KeyError:
            status = "archived"
        # `progress` has already downgraded an abandoned in-flight run to
        # `interrupted`, so what reaches here as active really is active.
        #
        # `awaiting_human` is deliberately not in this set. A parked run has no
        # worker thread -- the one that raised the question returned, and
        # answering spawns a fresh one -- so nothing is mutating its artifacts
        # underneath the delete. Refusing it meant a run whose question nobody
        # intends to answer could never be cleared from the product, which is
        # the exact dead end this guard exists to prevent for crashed runs.
        # Typing the run id back is the deliberate act that protects it.
        if status in {"queued", "running", "staging"}:
            raise ValueError(f"cannot delete an active run with status {status!r}")
        with self._lock:
            runtime = self._runtime_runs.get(run_id)
            if runtime and runtime.status in {"queued", "running", "staging"}:
                raise ValueError("cannot delete a live in-memory run")
            self._runtime_runs.pop(run_id, None)
            deleted = self.store.delete_run(run_id)
            snapshot = (self._run_state_root / f"{run_id}.json").resolve()
            if self._run_state_root.resolve() not in snapshot.parents:
                raise RuntimeError("refusing to remove a snapshot outside run-state")
            snapshot_deleted = snapshot.is_file()
            if snapshot_deleted:
                snapshot.unlink()
        return {"run_id": run_id, **deleted, "snapshot": int(snapshot_deleted)}


def _pending_question(runtime: Any) -> dict[str, Any] | None:
    """The escalation a run stopped on, ready for the wire."""
    question = getattr(getattr(runtime, "outcome", None), "pending_question", None)
    return question.model_dump(mode="json") if question is not None else None


def _as_intent(value: Any) -> str | None:
    """Normalise run instructions to the single free-text intent start_run takes."""
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    if isinstance(value, list):
        joined = "\n".join(str(item) for item in value if str(item).strip())
        return joined or None
    raise ValueError("instructions must be a string or a list of strings")


def create_app(
    artifacts_dir: str | Path = "data/artifacts",
    *,
    plane: ControlPlane | None = None,
    source_roots: Sequence[str | Path] | None = None,
):
    """Build the lightweight local FastAPI application.

    `source_roots` is worth passing explicitly whenever the process is not
    launched from the repository. It defaults to `data/` *relative to the
    working directory*, so a server started from a deployment checkout looked
    for datasets inside that checkout -- where `data/` is gitignored and
    therefore absent -- and served an empty list with no error anywhere.
    """
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import HTMLResponse, Response
    from fastapi.staticfiles import StaticFiles

    store = ArtifactStore(artifacts_dir)
    plane = plane or ControlPlane(
        store=store,
        source_roots=tuple(Path(root) for root in source_roots or ()),
    )
    app = FastAPI(title="Agentic DS workflow", version="0.2.0")

    # Password gate. Installed only when a credential is configured, so the
    # loopback launcher and the test suite are unaffected; the public launcher
    # refuses to start without one. See ads/api/auth.py.
    auth_config = config_from_env()
    if auth_config is not None and auth_config.enabled:
        install_auth(app, auth_config)

    @app.middleware("http")
    async def select_language(request: Request, call_next):
        """Bind the reader's language for the duration of one request.

        Explicit `?lang=` wins over the browser's Accept-Language, so a shared
        link can carry its own language and a switch in the UI takes effect
        without depending on browser settings.
        """
        chosen = request.query_params.get("lang") or request.headers.get("accept-language")
        with i18n.using(chosen):
            return await call_next(request)

    @app.get("/api/languages")
    def languages() -> dict[str, Any]:
        return {"supported": list(i18n.SUPPORTED), "default": i18n.DEFAULT}

    @app.get("/api/health", include_in_schema=False)
    def health() -> dict[str, Any]:
        """Unauthenticated liveness probe. Reports no run or artifact data."""
        return {"status": "ok", "auth": auth_config is not None}

    static_dir = Path(__file__).with_name("static")
    if (static_dir / "assets").is_dir():
        # Serve the built React app. Hashed asset filenames make the bundle
        # cacheable; index.html is the SPA fallback so client-side routes
        # survive a hard refresh.
        app.mount(
            "/assets",
            StaticFiles(directory=static_dir / "assets"),
            name="assets",
        )

        @app.get("/", response_class=HTMLResponse)
        def index() -> str:
            return (static_dir / "index.html").read_text(encoding="utf-8")
    else:

        @app.get("/", response_class=HTMLResponse)
        def index_missing() -> str:
            return "<h1>UI not built</h1><p>Run <code>npm --prefix web run build</code>.</p>"

    @app.get("/api/workflow")
    def workflow(run_id: str | None = None, mode: str = "agent") -> dict[str, Any]:
        try:
            return plane.workflow_graph(run_id, mode=mode)
        except (KeyError, ValueError):
            raise HTTPException(status_code=404, detail=f"unknown run {run_id!r}") from None

    @app.get("/api/run-options")
    def run_options() -> dict[str, Any]:
        return plane.run_options()

    @app.post("/api/planner/chat")
    def planner_chat(body: dict[str, Any]) -> dict[str, Any]:
        try:
            return plane.planner_chat(
                message=str(body["message"]),
                configuration=body.get("configuration"),
                history=body.get("history"),
                source_id=body.get("source_id"),
                run_id=body.get("run_id"),
                stage_id=body.get("stage_id"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.get("/api/runs")
    def runs() -> list[dict[str, Any]]:
        return [summary.to_dict() for summary in plane.list_runs()]

    @app.get("/api/data-sources")
    def data_sources() -> list[dict[str, Any]]:
        return plane.data_sources()

    @app.get("/api/catalog/datasets")
    def dataset_catalog() -> list[dict[str, Any]]:
        return plane.dataset_catalog()

    @app.get("/api/catalog/experiments")
    def experiment_catalog() -> list[dict[str, Any]]:
        return plane.experiment_catalog()

    @app.get("/api/catalog/models")
    def model_catalog() -> list[dict[str, Any]]:
        return plane.model_catalog()

    @app.get("/api/catalog/reports")
    def report_catalog() -> list[dict[str, Any]]:
        return plane.report_catalog()

    @app.get("/api/hardening")
    def hardening_status() -> dict[str, Any]:
        return plane.hardening_status()

    @app.get("/api/data-sources/{source_id}/profile")
    def source_profile(source_id: str) -> dict[str, Any]:
        try:
            return plane.source_profile(source_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown source") from None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.post("/api/uploads/{filename}")
    async def upload(
        filename: str, request: Request, source_id: str | None = None
    ) -> dict[str, Any]:
        try:
            return plane.upload(filename, await request.body(), source_id=source_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.post("/api/runs")
    def start_run(body: dict[str, Any]) -> dict[str, str]:
        try:
            integration_plan, problem, validation_strategy = plane.contracts_from_body(body)
            run_id = plane.start_run(
                source_id=str(body["source_id"]),
                integration_plan=integration_plan,
                problem=problem,
                validation_strategy=validation_strategy,
                candidate_limit=body.get("candidate_limit"),
                # This endpoint takes free-text intent while /answer takes a
                # list of correction lines, and the UI had no way to know which
                # was which. A list arriving here used to reach .strip() and
                # raise AttributeError, which is not in the caught tuple below,
                # so a malformed body returned a 500 stack trace rather than a
                # 400. Accept either shape and normalise.
                instructions=_as_intent(body.get("instructions")),
                mode=str(body.get("mode", "manual")),
                supervision=body.get("supervision"),
                agent_panel_size=int(body.get("agent_panel_size", 1)),
                eda_agent=bool(body.get("eda_agent", True)),
                run_mode=str(body.get("run_mode", "auto")),
                parent_run_id=body.get("parent_run_id"),
                branch_label=body.get("branch_label"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return {"run_id": run_id}

    @app.post("/api/runs/{run_id}/stages/{stage_id}/direct")
    def direct_stage(run_id: str, stage_id: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            directives = plane.direct_stage(run_id, stage_id, str(body.get("instruction", "")))
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return {"run_id": run_id, "stage_id": stage_id, "directives": directives}

    @app.get("/api/runs/{run_id}/directives")
    def run_directives(run_id: str) -> dict[str, Any]:
        return {"run_id": run_id, "directives": plane.stage_directives(run_id)}

    @app.post("/api/runs/{run_id}/sensitivity")
    def override_sensitivity(run_id: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            applied = plane.apply_sensitivity_overrides(run_id, dict(body.get("columns") or {}))
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return {"run_id": run_id, "applied": applied}

    @app.post("/api/runs/staged")
    def stage_run(body: dict[str, Any]) -> dict[str, Any]:
        try:
            return plane.stage_run(str(body["source_id"]), body.get("configuration"))
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.patch("/api/runs/{run_id}/staged")
    def update_staged(run_id: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            return {"configuration": plane.update_staged_run(run_id, body)}
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.post("/api/runs/{run_id}/start")
    def start_staged(run_id: str, body: dict[str, Any]) -> dict[str, str]:
        try:
            started = plane.start_staged_run(run_id, body or None)
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return {"run_id": started, "status": "running"}

    @app.post("/api/runs/{run_id}/discard")
    def discard_staged(run_id: str) -> dict[str, str]:
        try:
            plane.discard_staged_run(run_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        return {"run_id": run_id, "status": "discarded"}

    @app.post("/api/runs/{run_id}/answer")
    def answer_run(run_id: str, body: dict[str, Any]) -> dict[str, str]:
        try:
            raw_instructions = body.get("instructions") or []
            instructions = (
                [line.strip() for line in raw_instructions.splitlines() if line.strip()]
                if isinstance(raw_instructions, str)
                else [str(item) for item in raw_instructions]
            )
            plane.answer_run(
                run_id,
                decision=str(body["decision"]),
                instructions=instructions,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return {"run_id": run_id, "status": "resuming"}

    @app.post("/api/runs/{run_id}/delete")
    def delete_run(run_id: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            return plane.delete_run(run_id, confirmation=str(body.get("confirmation", "")))
        except KeyError:
            raise HTTPException(status_code=404, detail=f"unknown run {run_id!r}") from None
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None

    @app.get("/api/runs/{run_id}/progress")
    def progress(run_id: str) -> dict[str, Any]:
        try:
            return plane.progress(run_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"unknown run {run_id!r}") from None

    @app.get("/api/runs/{run_id}/stages/{stage_id}")
    def stage_detail(run_id: str, stage_id: str) -> dict[str, Any]:
        if run_id not in set(plane._artifact_run_ids()) | set(plane._snapshot_run_ids()):
            raise HTTPException(status_code=404, detail=f"unknown run {run_id!r}")
        try:
            return plane.stage_detail(run_id, stage_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"unknown stage {stage_id!r}") from None

    @app.get("/api/runs/{run_id}")
    def run_detail(run_id: str) -> dict[str, Any]:
        detail = plane.run_detail(run_id)
        if not detail["artifacts"] and "progress" not in detail:
            raise HTTPException(status_code=404, detail=f"unknown run {run_id!r}")
        return detail

    @app.get("/api/artifacts/{artifact_id}")
    def artifact(artifact_id: str) -> dict[str, Any]:
        try:
            return plane.artifact_payload(artifact_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown artifact") from None

    @app.get("/api/reports/{artifact_id}/download")
    def download_report(artifact_id: str) -> Response:
        try:
            payload = plane.artifact_payload(artifact_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown report") from None
        if "markdown" not in payload or "evaluation_artifact_id" not in payload:
            raise HTTPException(status_code=404, detail="artifact is not a final report")
        return Response(
            str(payload["markdown"]),
            media_type="text/markdown",
            headers={
                "Content-Disposition": f'attachment; filename="ads-report-{artifact_id[:12]}.md"'
            },
        )

    if static_dir.is_dir():
        # Registered last, deliberately: a catch-all declared before the API
        # routes would swallow every one of them. Client-side routes like
        # /datasets must survive a hard refresh, so anything that is not an
        # API path or a built asset returns the SPA shell.
        @app.get("/{full_path:path}", response_class=HTMLResponse)
        def spa_fallback(full_path: str) -> str:
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="unknown endpoint")
            return (static_dir / "index.html").read_text(encoding="utf-8")

    return app


__all__ = ["ControlPlane", "RunSummary", "create_app"]
