"""WorkflowSpec — the declarative graph that is this system's source of truth.

This is the architectural seam the whole design rests on. The report's central
build-vs-buy conclusion was: *own the domain layer, borrow the graph runtime*.
That only holds if the workflow is described by **our** document and merely
*compiled onto* whatever kernel executes it.

Three things follow from that, and all three are the point:

1. **Kernel replaceability.** Stages are plain callables `(RunState) -> StageResult`.
   Nothing in this package imports a graph library. The falsifiable test from the
   report is that swapping in a different kernel should take under two weeks; a
   design decision that breaks that test is the wrong decision.
2. **The visual editor's data model already exists.** A future canvas is a
   bidirectional view over this document, not a retrofit, because the runtime
   already executes arbitrary specs — it just happens to be handed one.
3. **Auditability.** Risk class, retry policy and gate behaviour are declared
   here and in `gates/default_policy.yaml`, so what the system will do is
   reviewable *before* a run starts rather than inferred from a transcript.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ads.contracts.base import ArtifactType


class EdgeCondition(StrEnum):
    """When an edge is followed.

    Deliberately a closed vocabulary keyed to gate verdicts rather than free
    expressions. An editable graph whose edges carry arbitrary code is not
    reviewable, and the gate already owns the decision.
    """

    ON_PROCEED = "on_proceed"
    ON_RETRY = "on_retry"
    ON_ESCALATE = "on_escalate"
    ALWAYS = "always"


@dataclass(frozen=True)
class StageDefinition:
    """One node: what runs, what it needs, what it produces."""

    id: str
    component: str
    """Name registered in the component registry, not an import path.

    Indirection is deliberate: a spec must be serialisable and reviewable
    without being executable, so it names capabilities rather than carrying code.
    """

    consumes: tuple[ArtifactType, ...] = ()
    produces: tuple[ArtifactType, ...] = ()
    description: str = ""
    optional: bool = False
    """Skipped when its inputs are absent, rather than failing the run."""

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("stage id must not be empty")


@dataclass(frozen=True)
class Edge:
    """A directed transition, taken when the gate verdict matches."""

    source: str
    target: str
    condition: EdgeCondition = EdgeCondition.ON_PROCEED

    def __post_init__(self) -> None:
        if self.source == self.target and self.condition is EdgeCondition.ON_PROCEED:
            raise ValueError(
                f"stage {self.source!r} proceeds to itself, which would loop forever; "
                "a self-edge is only meaningful for ON_RETRY"
            )


@dataclass(frozen=True)
class WorkflowSpec:
    """A complete, serialisable pipeline definition."""

    name: str
    version: str
    stages: tuple[StageDefinition, ...]
    edges: tuple[Edge, ...]
    entry: str

    def __post_init__(self) -> None:
        self.validate()

    # ------------------------------------------------------------------ graph

    def stage(self, stage_id: str) -> StageDefinition:
        for stage in self.stages:
            if stage.id == stage_id:
                return stage
        raise KeyError(f"unknown stage {stage_id!r}")

    def successors(self, stage_id: str, condition: EdgeCondition) -> tuple[str, ...]:
        return tuple(
            edge.target
            for edge in self.edges
            if edge.source == stage_id
            and edge.condition in (condition, EdgeCondition.ALWAYS)
        )

    def next_stage(self, stage_id: str, condition: EdgeCondition) -> str | None:
        """The single successor for a verdict, or None at the end of the graph."""
        targets = self.successors(stage_id, condition)
        return targets[0] if targets else None

    def stage_ids(self) -> tuple[str, ...]:
        return tuple(stage.id for stage in self.stages)

    # -------------------------------------------------------------- validation

    def validate(self) -> None:
        """Reject a malformed spec at construction, not mid-run.

        A graph error surfacing three stages into a four-hour run is the
        expensive way to learn about it.
        """
        ids = [stage.id for stage in self.stages]
        duplicates = {i for i in ids if ids.count(i) > 1}
        if duplicates:
            raise ValueError(f"duplicate stage ids: {sorted(duplicates)}")
        if self.entry not in ids:
            raise ValueError(f"entry stage {self.entry!r} is not defined")

        known = set(ids)
        for edge in self.edges:
            if edge.source not in known:
                raise ValueError(f"edge from unknown stage {edge.source!r}")
            if edge.target not in known:
                raise ValueError(f"edge to unknown stage {edge.target!r}")

        unreachable = known - self._reachable()
        if unreachable:
            raise ValueError(
                f"stages unreachable from {self.entry!r}: {sorted(unreachable)}"
            )

        self._check_inputs_are_produced()

    def _reachable(self) -> set[str]:
        seen = {self.entry}
        frontier = [self.entry]
        while frontier:
            current = frontier.pop()
            for edge in self.edges:
                if edge.source == current and edge.target not in seen:
                    seen.add(edge.target)
                    frontier.append(edge.target)
        return seen

    def _check_inputs_are_produced(self) -> None:
        """Every consumed artifact must be produced by some earlier stage.

        Catches the class of error where a stage is inserted before its
        dependency — which would otherwise fail at runtime with a missing
        artifact, after everything upstream has already run.
        """
        available: set[ArtifactType] = set()
        for stage in self._topological_order():
            missing = set(stage.consumes) - available
            if missing and not stage.optional:
                raise ValueError(
                    f"stage {stage.id!r} consumes {sorted(m.value for m in missing)} "
                    "which no earlier stage produces"
                )
            available.update(stage.produces)

    def _topological_order(self) -> list[StageDefinition]:
        """Stages in forward-edge order, ignoring retry edges.

        A cycle among non-retry edges is **rejected**, not tolerated. An earlier
        version fell back to declaration order, which is unsound: declaration
        order can make a later producer look available before the runtime's first
        visit to its consumer, so a spec with a genuinely missing input passed
        validation and failed at runtime with ``MissingArtifactError`` (Codex
        FINDING 14).

        Retreats are still expressible — they belong on ``ON_RETRY``, which is
        excluded here. Supporting unconditional forward cycles would need
        path-sensitive fixed-point analysis; declaration order is not evidence of
        availability.
        """
        forward = [
            edge
            for edge in self.edges
            if edge.condition is not EdgeCondition.ON_RETRY and edge.source != edge.target
        ]
        remaining = {stage.id: stage for stage in self.stages}
        incoming = {sid: 0 for sid in remaining}
        for edge in forward:
            incoming[edge.target] += 1

        ordered: list[StageDefinition] = []
        ready = [sid for sid, count in incoming.items() if count == 0]
        while ready:
            current = ready.pop(0)
            ordered.append(remaining[current])
            for edge in forward:
                if edge.source == current:
                    incoming[edge.target] -= 1
                    if incoming[edge.target] == 0:
                        ready.append(edge.target)

        if len(ordered) != len(self.stages):
            cyclic = sorted(set(remaining) - {s.id for s in ordered})
            raise ValueError(
                f"cycle among non-retry edges involving {cyclic}; a retreat must use "
                "an ON_RETRY edge so dependency validation stays sound"
            )
        return ordered

    # ---------------------------------------------------------- serialisation

    def to_dict(self) -> dict[str, Any]:
        """Round-trippable form. This is what a visual editor would read and write."""
        return {
            "workflow": self.name,
            "version": self.version,
            "entry": self.entry,
            "stages": [
                {
                    "id": s.id,
                    "component": s.component,
                    "consumes": [a.value for a in s.consumes],
                    "produces": [a.value for a in s.produces],
                    "description": s.description,
                    "optional": s.optional,
                }
                for s in self.stages
            ],
            "edges": [
                {"from": e.source, "to": e.target, "when": e.condition.value}
                for e in self.edges
            ],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> WorkflowSpec:
        stages = tuple(
            StageDefinition(
                id=s["id"],
                component=s["component"],
                consumes=tuple(ArtifactType(a) for a in s.get("consumes", ())),
                produces=tuple(ArtifactType(a) for a in s.get("produces", ())),
                description=s.get("description", ""),
                optional=bool(s.get("optional", False)),
            )
            for s in raw["stages"]
        )
        edges = tuple(
            Edge(
                source=e["from"],
                target=e["to"],
                condition=EdgeCondition(e.get("when", "on_proceed")),
            )
            for e in raw.get("edges", ())
        )
        return cls(
            name=raw["workflow"],
            version=str(raw["version"]),
            stages=stages,
            edges=edges,
            entry=raw["entry"],
        )


# --------------------------------------------------------------------- registry


@dataclass
class ComponentRegistry:
    """Maps the names a spec uses to the callables that implement them.

    The indirection is what lets a spec be data. It also means an unknown
    component fails at compile time with a list of what *is* available, rather
    than at runtime with an AttributeError.
    """

    components: dict[str, Callable[..., Any]] = field(default_factory=dict)

    def register(self, name: str, fn: Callable[..., Any]) -> None:
        if name in self.components:
            raise ValueError(f"component {name!r} is already registered")
        self.components[name] = fn

    def resolve(self, name: str) -> Callable[..., Any]:
        try:
            return self.components[name]
        except KeyError:
            raise KeyError(
                f"unknown component {name!r}; registered: {sorted(self.components)}"
            ) from None

    def validate_spec(self, spec: WorkflowSpec) -> None:
        missing = [s.component for s in spec.stages if s.component not in self.components]
        if missing:
            raise KeyError(
                f"spec {spec.name!r} names unregistered components: {sorted(set(missing))}"
            )


def linear_spec(
    name: str,
    version: str,
    stages: Sequence[StageDefinition],
    *,
    retry_stages: Sequence[str] = (),
) -> WorkflowSpec:
    """Build a straight-line spec, with optional retry self-edges.

    A convenience for the MVP's fixed pipeline. The runtime does not know this
    spec is linear — branching is a property of the document, not the engine.
    """
    edges: list[Edge] = []
    for current, following in zip(stages, stages[1:], strict=False):
        edges.append(Edge(current.id, following.id, EdgeCondition.ON_PROCEED))
    for stage_id in retry_stages:
        edges.append(Edge(stage_id, stage_id, EdgeCondition.ON_RETRY))
    return WorkflowSpec(
        name=name,
        version=version,
        stages=tuple(stages),
        edges=tuple(edges),
        entry=stages[0].id,
    )


__all__ = [
    "ComponentRegistry",
    "Edge",
    "EdgeCondition",
    "StageDefinition",
    "WorkflowSpec",
    "linear_spec",
]
