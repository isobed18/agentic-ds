"""Local workflow control plane and deliberately data-safe UI API.

The browser receives workflow metadata, DataCard-style schema statistics, run
events, gate decisions, and artifact summaries. It never receives source rows.
The runtime registry accelerates live polling; a redacted progress snapshot is
also written beside the artifact store so completed and interrupted runs remain
inspectable after the server restarts.
"""

import json
import re
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

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
from ads.intake import load_directory, profile_tables
from ads.llm import LARGE, OllamaClient, StructuredLLM
from ads.orchestration import RunState, resume_workflow, run_workflow
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
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


@dataclass
class ControlPlane:
    """Framework-free application service behind the local FastAPI UI."""

    store: ArtifactStore
    source_roots: tuple[Path, ...] = ()
    upload_root: Path | None = None
    llm_factory: Callable[[], StructuredLLM] | None = None
    workflow_runner: Callable[..., Any] | None = None
    _runtime_runs: dict[str, _RuntimeRun] = field(default_factory=dict)
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
            mode = progress.get("configuration", {}).get("mode", "manual")
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
                    "order": index,
                    "kind": "planner_agent" if stage["id"] in _PLANNER_AGENTS else "deterministic",
                    "owner": (
                        "Local planning agent"
                        if stage["id"] in _PLANNER_AGENTS
                        else "Python executor"
                    ),
                    "status": status,
                    "attempt_count": len(own),
                    "retry_count": sum(item.get("verdict") == "retry" for item in own),
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
            and run_status in {"queued", "running"}
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
                    "label": "Agent-planned",
                    "description": "Planner agents discover schema, problem, and validation.",
                },
                {
                    "value": "manual",
                    "label": "Manual plan",
                    "description": "Use the problem and validation choices configured here.",
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
                )
            )
        return sorted(summaries, key=lambda item: item.last_activity, reverse=True)

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
                if child.is_dir() and not self._reserved_path(child) and any(child.iterdir()):
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
                            column["sensitivity"] == "pii"
                            for column in columns
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

    def source_profile(self, source_id: str) -> dict[str, Any]:
        """Profile source files into a schema-only browser projection."""
        cards = profile_tables(load_directory(self.source_path(source_id)))
        if not cards:
            raise ValueError("source contains no supported data files")
        return {
            "source_id": source_id,
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

    # --------------------------------------------------------------- execution

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
        supervision = self._normalise_supervision(supervision or {})
        profile = BUILTIN_PROFILES["full_auto"].model_copy(
            update={"checkpoint_stages": supervision["checkpoint_stages"]}
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

        def event(name: str, payload: dict[str, Any]) -> None:
            with self._lock:
                runtime.events.append({"event": name, "at": _now(), **payload})
                runtime.updated_at = _now()
                if name == "stage_started":
                    runtime.current_stage = payload.get("stage")
            self._persist_runtime(runtime)

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

        def event(name: str, payload: dict[str, Any]) -> None:
            with self._lock:
                runtime.events.append({"event": name, "at": _now(), **payload})
                runtime.updated_at = _now()
                if name == "stage_started":
                    runtime.current_stage = payload.get("stage")
            self._persist_runtime(runtime)

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
        return (
            IntegrationPlan(
                base_table=str(body["base_table"]),
                base_grain=list(grain or []),
                grain_description=str(
                    body.get("grain_description") or "One analytical row per selected grain."
                ),
            ),
            ProblemDefinition(
                task_type=TaskType(body["task_type"]),
                target_column=body.get("target_column") or None,
                primary_metric=Metric(body["primary_metric"]),
                title=str(body.get("problem_title") or "Configured analysis")[:120],
                description=str(
                    body.get("problem_description")
                    or "Problem configured through the local workflow UI."
                )[:1000],
                excluded_columns=list(body.get("excluded_columns") or []),
                confirmed_by="human",
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
    _IN_FLIGHT = frozenset({"queued", "running"})

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
        title = (name or artifact_type).replace("_", " ").title()
        description = "A durable result produced by this stage."
        preferred: list[tuple[str, str]] = []
        if artifact_type == "data_card":
            title = f"Profiled table: {summary.get('table_name', name or 'dataset')}"
            description = "Schema, size, and quality statistics; no source rows are shown."
            preferred = [
                ("Rows", "n_rows"),
                ("Columns", "n_columns"),
                ("Candidate keys", "n_candidate_keys"),
            ]
        elif artifact_type == "integration_plan":
            title = "Data integration plan"
            description = "How source tables are aggregated and joined into one analytical table."
            preferred = [
                ("Base table", "base_table"),
                ("Joins", "n_joins"),
                ("Aggregations", "n_aggregations"),
            ]
        elif artifact_type == "integration_trial":
            title = "Integration plan trial"
            description = (
                "Measured result of executing the proposed joins and aggregations on a copy."
            )
            preferred = [
                ("Base rows", "base_rows"),
                ("Result rows", "result_rows"),
                ("Grain preserved", "grain_preserved"),
                ("Result columns", "n_columns"),
            ]
        elif artifact_type == "problem_candidates":
            title = "Candidate analysis problems"
            description = "Problems proposed by the planner and checked against measured support."
            preferred = [("Candidates", "n_candidates"), ("Viable", "n_viable")]
        elif artifact_type == "problem_definition":
            title = str(summary.get("title") or "Selected problem")
            description = "The target, task, and success metric used downstream."
            preferred = [
                ("Task", "task_type"),
                ("Target", "target_column"),
                ("Metric", "primary_metric"),
            ]
        elif artifact_type == "validation_strategy":
            title = "Validation plan"
            description = "How training and holdout data are separated to keep evaluation honest."
            preferred = [
                ("Strategy", "strategy"),
                ("Folds", "n_folds"),
                ("Group", "group_column"),
                ("Time", "time_column"),
            ]
        elif artifact_type == "leakage_report":
            title = "Leakage audit"
            description = (
                "Features checked for information that would make model results "
                "unrealistically good."
            )
            preferred = [
                ("Features checked", "n_features_checked"),
                ("Findings", "n_findings"),
                ("Blocking", "n_blocking"),
                ("Clean", "is_clean"),
            ]
        elif artifact_type == "trained_model":
            title = "Model comparison"
            description = (
                "Candidate models compared by cross-validation and untouched holdout performance."
            )
            preferred = [
                ("Winner", "winner_id"),
                ("Metric", "primary_metric"),
                ("Holdout score", "winner_holdout_score"),
                ("Training rows", "training_row_count"),
            ]
        elif artifact_type == "model_experiment":
            title = str(summary.get("title") or "Agent-authored model experiment")
            description = (
                "An isolated development experiment scored by the host on withheld labels."
            )
            preferred = [
                ("Model family", "model_family"),
                ("Metric", "metric"),
                ("Score", "score"),
                ("Baseline", "baseline_score"),
                ("Evaluation rows", "evaluation_rows"),
            ]
        elif artifact_type == "evaluation_report":
            title = "Evaluation result"
            description = (
                "The selected model compared with its baseline, including alerts "
                "and decision history."
            )
            preferred = [
                ("Winner", "winner_id"),
                ("Metric", "primary_metric"),
                ("Holdout score", "winner_holdout_score"),
                ("Baseline improvement", "baseline_delta"),
                ("Alerts", "n_alerts"),
            ]
        elif artifact_type == "final_report":
            title = "Final auditable report"
            description = (
                "The human-readable handoff tying conclusions to the exact evaluation evidence."
            )
            preferred = [("Report length", "n_characters")]
        elif artifact_type == "agent_audit":
            title = "Agent execution audit"
            description = (
                "Contract validation, panel agreement, and deterministic tool provenance."
            )
            preferred = [
                ("Agent", "agent_id"),
                ("Panel", "panel_size"),
                ("Valid", "valid_members"),
                ("Agreement", "agreement"),
                ("Pydantic", "pydantic_validated"),
                ("Tools", "tool_count"),
                ("Skills", "skill_count"),
            ]
        elif artifact_type == "eda_report":
            title = "Exploratory data findings"
            description = (
                "Measured distributions, missingness, and relationships relevant to the problem."
            )
            preferred = [(key.replace("_", " ").title(), key) for key in summary][:4]
        elif artifact_type == "exploratory_analysis":
            title = str(summary.get("title") or "Agent-authored exploratory analysis")
            description = "Validated exploratory output produced by locally executed code."
            preferred = [
                ("Evidence class", "evidence_class"),
                ("Chart", "chart_kind"),
                ("Tool calls", "tool_calls"),
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
            story["suggestion"] = (
                f"Use {payload['base_table']} at one row per "
                f"{', '.join(payload['base_grain'])}; apply {len(payload['aggregations'])} "
                f"aggregation(s) before {len(payload['joins'])} join(s)."
            )
            story["warnings"] = payload.get("warnings", [])
            # The join plan as a picture. The brief asks that the schema not be
            # something only the agent understands.
            story["schema_graph"] = schema_graph(payload)
        elif artifact_type == "integration_trial":
            story["suggestion"] = (
                "The proposed plan was executed by the deterministic integration engine "
                "before it was accepted."
            )
            story["warnings"] = payload.get("warnings", [])
        elif artifact_type == "problem_candidates":
            story["suggestion"] = "The planner ranked these analysis problems."
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
            story["suggestion"] = (
                f"Proceed with “{payload.get('title')}” as a {payload.get('task_type')} "
                f"problem, predicting {payload.get('target_column')} and measuring "
                f"{payload.get('primary_metric')}."
            )
            story["rationale"] = payload.get("description")
            story["excluded_columns"] = payload.get("excluded_columns", [])
        elif artifact_type == "validation_strategy":
            story["panels"] = validation_panels(payload)
            story["suggestion"] = (
                f"Use {payload.get('strategy')} validation with {payload.get('n_folds')} folds"
                + (
                    f", grouped by {payload.get('group_column')}"
                    if payload.get("group_column")
                    else ""
                )
                + (
                    f", ordered by {payload.get('time_column')}"
                    if payload.get("time_column")
                    else ""
                )
                + "."
            )
            story["rationale"] = payload.get("rationale")
        elif artifact_type == "leakage_report":
            blocking = [item for item in payload.get("findings", []) if item.get("blocking")]
            story["panels"] = leakage_panels(payload)
            story["suggestion"] = (
                "Do not train yet; correct or exclude the blocking features."
                if blocking
                else "No blocking leakage was detected; training may proceed."
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
                f"Start by understanding {target_name}: inspect its distribution, then "
                "review missing fields and the strongest measured relationships before "
                "accepting any modelling direction."
                if target_name
                else "Review coverage, missingness, correlations, and outliers before modelling."
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
                "Target distribution measured": bool(target_name is None or target),
                "Every feature covered": set(payload.get("feature_columns", []))
                .issubset(payload.get("covered_columns", [])),
                "Missingness measured": {
                    item.get("column") for item in missingness
                }.issuperset(
                    set(payload.get("covered_columns", []))
                    | ({target_name} if target_name else set())
                ),
            }
            warnings = []
            high_missing = [
                item for item in missingness if (item.get("null_rate") or 0.0) >= 0.1
            ]
            if high_missing:
                warnings.append(
                    f"{len(high_missing)} column(s) have at least 10% missing values; "
                    "confirm how they should be handled."
                )
            strong = [
                item
                for item in relationships
                if abs(item.get("pearson_correlation") or 0.0) >= 0.9
            ]
            if strong:
                warnings.append(
                    f"{len(strong)} feature(s) have |correlation| at or above 0.90; "
                    "treat these as leakage candidates until audited."
                )
            story["warnings"] = warnings
        elif artifact_type == "exploratory_analysis":
            story["panels"] = [
                exploratory_panel(payload, linked_interpretations or [])
            ]
            story["suggestion"] = (
                "Treat this as a proposed extension to the mandatory EDA, not as gate evidence."
            )
        elif artifact_type == "trained_model":
            story["panels"] = training_panels(payload)
            story["suggestion"] = (
                f"Select {payload.get('winner_id')} from {len(payload.get('results', []))} "
                f"compared candidates using {payload.get('primary_metric')}."
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
            story["panels"] = [
                model_experiment_panel(payload, linked_interpretations or [])
            ]
            story["suggestion"] = (
                "Review this as a proposed development experiment; it did not use the "
                "final holdout and cannot change the selected model."
            )
        elif artifact_type == "evaluation_report":
            story["panels"] = evaluation_panels(payload)
            story["suggestion"] = (
                f"The selected {payload.get('winner_display_name')} scored "
                f"{payload.get('winner_holdout_score')} on holdout; baseline improvement "
                f"was {payload.get('baseline_delta')}."
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
                item["detail"]
                for item in story["history_alerts"]
                if not item["resolved"]
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
            story["suggestion"] = "The final report is ready for review and export."
            story["report_markdown"] = payload.get("markdown")
        elif artifact_type == "agent_audit":
            story["suggestion"] = (
                "Review panel agreement and validation evidence before trusting this "
                "agent-authored contract."
            )
            row_access = bool(payload.get("raw_rows_shared", False))
            row_access_authorized = (
                not row_access or payload.get("agent_id") == "eda_investigator"
            )
            story["quality_checks"] = [
                {
                    "label": "Pydantic output contract",
                    "passed": payload.get("pydantic_contract_enforced", False),
                    "detail": payload.get("output_contract"),
                },
                {
                    "label": "Row access matches the agent role",
                    "passed": row_access_authorized,
                    "detail": (
                        "Local investigator may read a read-only copy; output remains typed."
                        if row_access
                        else "Planner context contains schema and aggregate measurements only."
                    ),
                },
                {
                    "label": "Evidence tools were allowlisted",
                    "passed": set(payload.get("evidence_tools", []))
                    <= set(payload.get("allowed_tools", [])),
                    "detail": ", ".join(payload.get("evidence_tools", [])) or "none",
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
            mode = progress.get("configuration", {}).get("mode", "manual")
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
                linked_interpretations=interpretations_by_source.get(
                    artifact["artifact_id"], []
                ),
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
                "description": definition.description,
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
                    "Your decision is needed"
                    if active_question
                    else "In progress"
                    if stage_status == "running"
                    else "No action needed"
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
                    "deletable": summary.status
                    not in {"queued", "running", "awaiting_human"},
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
        if status in {"queued", "running", "awaiting_human"}:
            raise ValueError(f"cannot delete an active run with status {status!r}")
        with self._lock:
            runtime = self._runtime_runs.get(run_id)
            if runtime and runtime.status in {"queued", "running", "awaiting_human"}:
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


def create_app(artifacts_dir: str | Path = "data/artifacts", *, plane: ControlPlane | None = None):
    """Build the lightweight local FastAPI application."""
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import HTMLResponse, Response
    from fastapi.staticfiles import StaticFiles

    store = ArtifactStore(artifacts_dir)
    plane = plane or ControlPlane(store=store)
    app = FastAPI(title="Agentic DS workflow", version="0.2.0")

    # Password gate. Installed only when a credential is configured, so the
    # loopback launcher and the test suite are unaffected; the public launcher
    # refuses to start without one. See ads/api/auth.py.
    auth_config = config_from_env()
    if auth_config is not None and auth_config.enabled:
        install_auth(app, auth_config)

    @app.get("/api/health", include_in_schema=False)
    def health() -> dict[str, Any]:
        """Unauthenticated liveness probe. Reports no run or artifact data."""
        return {"status": "ok", "auth": auth_config is not None}

    static_dir = Path(__file__).with_name("static")
    if static_dir.is_dir():
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
            return (
                "<h1>UI not built</h1><p>Run <code>npm --prefix web run build</code>.</p>"
            )

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
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return {"run_id": run_id}

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
