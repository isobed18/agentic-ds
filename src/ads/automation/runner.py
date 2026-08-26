"""Sequential, restart-safe runner for compiled graph automation plans."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from ads.contracts.automation import (
    AutomationExecutionPlan,
    AutomationPlanNode,
    NodeAttempt,
    NodeInputBinding,
    NodeOutputBinding,
)
from ads.contracts.base import Artifact, ArtifactType
from ads.contracts.registry import contract_definition
from ads.store import ArtifactStore


@dataclass(frozen=True)
class NodeExecutionContext:
    run_id: str
    plan: AutomationExecutionPlan
    node: AutomationPlanNode
    attempt: int
    input_bindings: tuple[NodeInputBinding, ...]
    store: ArtifactStore

    def artifact_ids(self, port_id: str) -> tuple[str, ...]:
        return tuple(
            artifact_id
            for binding in self.input_bindings
            if binding.port_id == port_id
            for artifact_id in binding.artifact_ids
        )


@dataclass(frozen=True)
class NodeExecutionResult:
    outputs: Mapping[str, Artifact | Sequence[Artifact]] = field(default_factory=dict)
    release_outputs: bool = True
    awaiting_review: bool = False
    gate_decision_artifact_id: str | None = None


@dataclass(frozen=True)
class AutomationRunResult:
    status: Literal["completed", "paused", "awaiting_review", "failed"]
    attempts: tuple[NodeAttempt, ...]
    stopped_after_component: str | None = None


AutomationExecutor = Callable[[NodeExecutionContext], NodeExecutionResult]


class ExecutorRegistry:
    def __init__(self) -> None:
        self._executors: dict[str, AutomationExecutor] = {}

    def register(self, executor_id: str, executor: AutomationExecutor) -> None:
        if executor_id in self._executors:
            raise ValueError(f"executor {executor_id!r} is already registered")
        self._executors[executor_id] = executor

    def require(self, executor_id: str) -> AutomationExecutor:
        try:
            return self._executors[executor_id]
        except KeyError as exc:
            raise ValueError(f"no executor registered for {executor_id!r}") from exc


class AutomationRunner:
    """Execute ready DAG nodes in stable topological order using exact bindings."""

    def __init__(self, store: ArtifactStore, executors: ExecutorRegistry) -> None:
        self.store = store
        self.executors = executors

    def run(self, *, run_id: str, plan: AutomationExecutionPlan) -> AutomationRunResult:
        history = self._history(run_id)
        latest = self._latest_final_by_component(history)
        released = self._released_outputs(latest.values())
        completed_this_call: list[NodeAttempt] = []
        nodes = {node.instance_id: node for node in plan.nodes}

        for component_id in plan.node_order:
            node = nodes[component_id]
            prior = latest.get(component_id)
            if (
                prior
                and prior.plan_id == plan.plan_id
                and prior.plan_revision == plan.plan_revision
            ):
                if prior.status in {"succeeded", "cached", "paused"}:
                    continue
                if prior.status == "awaiting_review":
                    return AutomationRunResult(
                        status="awaiting_review",
                        attempts=tuple(completed_this_call),
                        stopped_after_component=component_id,
                    )

            input_bindings = self._resolve_inputs(node, released)
            execution_key = self._execution_key(node, input_bindings)
            reusable = self._reusable_attempt(history, node.instance_id, execution_key)
            attempt_number = self._next_attempt(history, node.instance_id)
            started_at = datetime.now(UTC)
            if reusable is not None:
                cached = NodeAttempt(
                    plan_id=plan.plan_id,
                    plan_revision=plan.plan_revision,
                    scope_id=node.scope_id,
                    component_instance_id=node.instance_id,
                    catalog_id=node.catalog_id,
                    attempt=attempt_number,
                    started_at=started_at,
                    ended_at=datetime.now(UTC),
                    status="cached",
                    input_bindings=input_bindings,
                    output_bindings=reusable.output_bindings,
                    gate_decision_artifact_id=reusable.gate_decision_artifact_id,
                    execution_key=execution_key,
                )
                self._persist(run_id, cached)
                history.append(cached)
                latest[component_id] = cached
                self._release(released, component_id, cached.output_bindings)
                completed_this_call.append(cached)
                continue

            running = NodeAttempt(
                plan_id=plan.plan_id,
                plan_revision=plan.plan_revision,
                scope_id=node.scope_id,
                component_instance_id=node.instance_id,
                catalog_id=node.catalog_id,
                attempt=attempt_number,
                started_at=started_at,
                status="running",
                input_bindings=input_bindings,
                execution_key=execution_key,
            )
            self._persist(run_id, running)
            try:
                executor = self.executors.require(node.executor_id)
                result = executor(
                    NodeExecutionContext(
                        run_id=run_id,
                        plan=plan,
                        node=node,
                        attempt=attempt_number,
                        input_bindings=tuple(input_bindings),
                        store=self.store,
                    )
                )
                outputs = self._persist_outputs(run_id, node, attempt_number, result)
                status = (
                    "awaiting_review"
                    if result.awaiting_review
                    else "paused"
                    if node.control.execution == "pause_after"
                    else "succeeded"
                )
                finished = running.model_copy(
                    update={
                        "ended_at": datetime.now(UTC),
                        "status": status,
                        "output_bindings": outputs,
                        "gate_decision_artifact_id": result.gate_decision_artifact_id,
                    }
                )
            except Exception as exc:
                finished = running.model_copy(
                    update={"ended_at": datetime.now(UTC), "status": "failed", "error": str(exc)}
                )
                self._persist(run_id, finished)
                completed_this_call.append(finished)
                return AutomationRunResult(
                    status="failed",
                    attempts=tuple(completed_this_call),
                    stopped_after_component=component_id,
                )

            self._persist(run_id, finished)
            history.append(finished)
            latest[component_id] = finished
            self._release(released, component_id, finished.output_bindings)
            completed_this_call.append(finished)
            if finished.status == "awaiting_review":
                return AutomationRunResult(
                    status="awaiting_review",
                    attempts=tuple(completed_this_call),
                    stopped_after_component=component_id,
                )
            if finished.status == "paused":
                return AutomationRunResult(
                    status="paused",
                    attempts=tuple(completed_this_call),
                    stopped_after_component=component_id,
                )

        return AutomationRunResult(status="completed", attempts=tuple(completed_this_call))

    def _resolve_inputs(
        self,
        node: AutomationPlanNode,
        released: Mapping[tuple[str, str], tuple[str, ...]],
    ) -> list[NodeInputBinding]:
        resolved: list[NodeInputBinding] = []
        for declaration in node.input_bindings:
            key = (declaration.source_component, declaration.source_port)
            artifact_ids = list(released.get(key, ()))
            if not artifact_ids:
                raise ValueError(
                    f"{node.instance_id}.{declaration.target_port} is waiting for released "
                    f"output {declaration.source_component}.{declaration.source_port}"
                )
            resolved.append(
                NodeInputBinding(
                    port_id=declaration.target_port,
                    contract_id=declaration.contract_id,
                    source_component=declaration.source_component,
                    source_port=declaration.source_port,
                    artifact_ids=artifact_ids,
                )
            )
        return resolved

    def _persist_outputs(
        self,
        run_id: str,
        node: AutomationPlanNode,
        attempt: int,
        result: NodeExecutionResult,
    ) -> list[NodeOutputBinding]:
        declared = {output.port_id: output for output in node.output_contracts}
        unknown = set(result.outputs) - set(declared)
        if unknown:
            raise ValueError(f"executor returned undeclared output ports: {sorted(unknown)}")
        bindings: list[NodeOutputBinding] = []
        for port_id, contract in declared.items():
            raw = result.outputs.get(port_id, ())
            artifacts = [raw] if isinstance(raw, Artifact) else list(raw)
            expected = contract_definition(contract.contract_id).artifact_type
            if expected is not None and any(
                artifact.artifact_type is not expected for artifact in artifacts
            ):
                raise ValueError(
                    f"{node.instance_id}.{port_id} requires {expected.value}, "
                    "but its executor returned another artifact type"
                )
            artifact_ids = [
                self.store.put(
                    artifact,
                    run_id=run_id,
                    stage_exec_id=f"{node.instance_id}:attempt:{attempt}",
                ).artifact_id
                for artifact in artifacts
            ]
            availability = (
                "blocked"
                if artifact_ids and not result.release_outputs
                else "released"
                if artifact_ids
                else "not_produced"
            )
            bindings.append(
                NodeOutputBinding(
                    port_id=port_id,
                    contract_id=contract.contract_id,
                    artifact_ids=artifact_ids,
                    availability=availability,
                )
            )
        return bindings

    @staticmethod
    def _execution_key(node: AutomationPlanNode, inputs: Sequence[NodeInputBinding]) -> str:
        payload = {
            "catalog_id": node.catalog_id,
            "catalog_version": node.catalog_version,
            "executor_id": node.executor_id,
            "settings": node.resolved_settings,
            "gate_policy_id": node.gate_policy_id,
            "inputs": [binding.model_dump(mode="json") for binding in inputs],
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def _history(self, run_id: str) -> list[NodeAttempt]:
        return self.store.load_all(run_id, ArtifactType.NODE_ATTEMPT, NodeAttempt)

    @staticmethod
    def _latest_final_by_component(history: Sequence[NodeAttempt]) -> dict[str, NodeAttempt]:
        final = [attempt for attempt in history if attempt.status != "running"]
        ordered = sorted(final, key=lambda item: (item.attempt, item.ended_at or item.started_at))
        return {attempt.component_instance_id: attempt for attempt in ordered}

    @staticmethod
    def _released_outputs(
        attempts: Sequence[NodeAttempt],
    ) -> dict[tuple[str, str], tuple[str, ...]]:
        released: dict[tuple[str, str], tuple[str, ...]] = {}
        for attempt in attempts:
            AutomationRunner._release(
                released, attempt.component_instance_id, attempt.output_bindings
            )
        return released

    @staticmethod
    def _release(
        released: dict[tuple[str, str], tuple[str, ...]],
        component_id: str,
        outputs: Sequence[NodeOutputBinding],
    ) -> None:
        for output in outputs:
            if output.availability == "released":
                released[(component_id, output.port_id)] = tuple(output.artifact_ids)

    @staticmethod
    def _reusable_attempt(
        history: Sequence[NodeAttempt], component_id: str, execution_key: str
    ) -> NodeAttempt | None:
        for attempt in history:
            if (
                attempt.component_instance_id == component_id
                and attempt.execution_key == execution_key
                and attempt.status in {"succeeded", "cached", "paused"}
                and all(output.availability == "released" for output in attempt.output_bindings)
            ):
                return attempt
        return None

    @staticmethod
    def _next_attempt(history: Sequence[NodeAttempt], component_id: str) -> int:
        return (
            max(
                (
                    attempt.attempt
                    for attempt in history
                    if attempt.component_instance_id == component_id
                ),
                default=0,
            )
            + 1
        )

    def _persist(self, run_id: str, attempt: NodeAttempt) -> None:
        self.store.put(
            attempt,
            run_id=run_id,
            stage_exec_id=f"{attempt.component_instance_id}:attempt:{attempt.attempt}",
        )


__all__ = [
    "AutomationExecutor",
    "AutomationRunResult",
    "AutomationRunner",
    "ExecutorRegistry",
    "NodeExecutionContext",
    "NodeExecutionResult",
]
