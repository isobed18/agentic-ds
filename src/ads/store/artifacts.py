"""Content-addressed, immutable artifact store.

Design commitments from the architecture report, enforced here:

* **Immutable.** A stage never modifies an artifact; it writes a new one.
* **Content-addressed.** The id is a hash of the semantic payload, so an
  identical re-run is detectable and writes are idempotent. That property is
  what makes LangGraph's "node restarts from the top on resume" caveat a
  non-issue: re-executing a stage produces the same id and no duplicate state.
* **Queryable metadata.** Each artifact's small ``summary()`` projection lands
  in a relational index so the Gate Evaluator and run timeline never have to
  deserialize full payloads.

The index is SQLite for the MVP. All access goes through this class so the swap
to PostgreSQL is a driver change, not a refactor.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from ads.contracts.agents import AgentAudit
from ads.contracts.automation import AutomationExecutionPlan, NodeAttempt
from ads.contracts.base import Artifact, ArtifactType
from ads.contracts.comprehension import ComprehensionBrief
from ads.contracts.datacard import DataCard
from ads.contracts.dataflow import SplitManifest, TableAsset
from ads.contracts.documents import DocumentExtraction, DocumentTableReview
from ads.contracts.eda import EDAReport
from ads.contracts.evidence import MeasurementBundle
from ads.contracts.exploration import ExploratoryAnalysis
from ads.contracts.feature_experiment import FeatureExperiment
from ads.contracts.features import FeatureSpec
from ads.contracts.gates import CritiqueResult, GateDecision
from ads.contracts.integration import IntegrationPlan, IntegrationTrial
from ads.contracts.leakage import LeakageReport
from ads.contracts.model_experiment import ModelExperiment
from ads.contracts.problem import ProblemCandidateSet, ProblemDefinition
from ads.contracts.reporting import EvaluationReport
from ads.contracts.rlfe import RlFeatureReport
from ads.contracts.staging import GraphPatch, StagingReportArtifact, StagingWorkspace
from ads.contracts.training import EnhancedTrainingReport, TrainingReport
from ads.contracts.validation import ValidationStrategy, ValidationTrial

A = TypeVar("A", bound=Artifact)

# Fields excluded from the content hash. `created_at` is wall-clock metadata,
# not semantics — including it would make every re-run produce a new id and
# defeat idempotent writes.
_UNHASHED_FIELDS = frozenset({"created_at"})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id     TEXT NOT NULL,
    run_id          TEXT NOT NULL,
    stage_exec_id   TEXT,
    artifact_type   TEXT NOT NULL,
    schema_version  TEXT NOT NULL,
    type_key        TEXT NOT NULL,
    name            TEXT,
    summary_json    TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (artifact_id, run_id)
);
CREATE INDEX IF NOT EXISTS idx_artifacts_run  ON artifacts(run_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_type ON artifacts(run_id, artifact_type);
CREATE INDEX IF NOT EXISTS idx_artifacts_name ON artifacts(run_id, name);
"""

# Maps the on-disk type tag back to a model class for typed reads.
_TYPE_REGISTRY: dict[ArtifactType, type[Artifact]] = {
    ArtifactType.DATA_CARD: DataCard,
    ArtifactType.INTEGRATION_PLAN: IntegrationPlan,
    ArtifactType.INTEGRATION_TRIAL: IntegrationTrial,
    ArtifactType.PROBLEM_CANDIDATES: ProblemCandidateSet,
    ArtifactType.PROBLEM_DEFINITION: ProblemDefinition,
    ArtifactType.VALIDATION_STRATEGY: ValidationStrategy,
    ArtifactType.LEAKAGE_REPORT: LeakageReport,
    ArtifactType.TRAINED_MODEL: TrainingReport,
    ArtifactType.MODEL_EXPERIMENT: ModelExperiment,
    ArtifactType.CRITIQUE: CritiqueResult,
    ArtifactType.GATE_DECISION: GateDecision,
    ArtifactType.AGENT_AUDIT: AgentAudit,
    ArtifactType.MEASUREMENT_BUNDLE: MeasurementBundle,
    ArtifactType.COMPREHENSION_BRIEF: ComprehensionBrief,
    ArtifactType.STAGING_WORKSPACE: StagingWorkspace,
    ArtifactType.STAGING_REPORT: StagingReportArtifact,
    ArtifactType.DOCUMENT_EXTRACTION: DocumentExtraction,
    ArtifactType.DOCUMENT_TABLE_REVIEW: DocumentTableReview,
    ArtifactType.AUTOMATION_EXECUTION_PLAN: AutomationExecutionPlan,
    ArtifactType.GRAPH_PATCH: GraphPatch,
    ArtifactType.TABLE_ASSET: TableAsset,
    ArtifactType.SPLIT_MANIFEST: SplitManifest,
    ArtifactType.NODE_ATTEMPT: NodeAttempt,
    ArtifactType.EXPLORATORY_ANALYSIS: ExploratoryAnalysis,
    # These five were written by the pipeline but absent here, so `load()`
    # raised "No model registered" for artifacts that existed on disk. The API
    # never noticed because it serves stored payloads untyped; anything reading
    # an artifact by its type — a script, a test, a later stage — did not.
    ArtifactType.EDA_REPORT: EDAReport,
    ArtifactType.EVALUATION_REPORT: EvaluationReport,
    ArtifactType.FEATURE_SPEC: FeatureSpec,
    ArtifactType.FEATURE_EXPERIMENT: FeatureExperiment,
    ArtifactType.VALIDATION_TRIAL: ValidationTrial,
    ArtifactType.RL_FEATURE_REPORT: RlFeatureReport,
    ArtifactType.RL_ENHANCED_MODEL: EnhancedTrainingReport,
}


def register_artifact_type(model: type[Artifact]) -> None:
    """Register a model so :meth:`ArtifactStore.load` can resolve it untyped."""
    _TYPE_REGISTRY[model.artifact_type] = model


class ArtifactRef:
    """Lightweight handle to a stored artifact — metadata without the payload."""

    __slots__ = (
        "artifact_id",
        "run_id",
        "stage_exec_id",
        "artifact_type",
        "schema_version",
        "name",
        "summary",
        "created_at",
    )

    def __init__(
        self,
        artifact_id: str,
        run_id: str,
        stage_exec_id: str | None,
        artifact_type: ArtifactType,
        schema_version: str,
        name: str | None,
        summary: dict[str, Any],
        created_at: str,
    ) -> None:
        self.artifact_id = artifact_id
        self.run_id = run_id
        self.stage_exec_id = stage_exec_id
        self.artifact_type = artifact_type
        self.schema_version = schema_version
        self.name = name
        self.summary = summary
        self.created_at = created_at

    def __repr__(self) -> str:
        return (
            f"ArtifactRef({self.artifact_type.value} {self.artifact_id[:12]} "
            f"name={self.name!r} run={self.run_id})"
        )


class ArtifactNotFoundError(KeyError):
    pass


def compute_artifact_id(artifact: Artifact) -> str:
    """Hash the semantic payload of an artifact.

    Uses canonical JSON (sorted keys, no whitespace) so the id is stable across
    processes and Python versions. Prefixed with the type key so two different
    contracts that happen to serialize identically cannot collide.
    """
    payload = artifact.model_dump(mode="json")
    for field in _UNHASHED_FIELDS:
        payload.pop(field, None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(f"{artifact.type_key()}\x00{canonical}".encode()).hexdigest()
    return digest


class ArtifactStore:
    """Filesystem-backed artifact store with a SQLite metadata index."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.objects_dir = self.root / "objects"
        self.index_path = self.root / "index.sqlite"
        self.objects_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.index_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _payload_path(self, artifact_id: str) -> Path:
        return self.objects_dir / artifact_id[:2] / artifact_id / "payload.json"

    def type_of(self, artifact_id: str) -> ArtifactType | None:
        """The indexed type of one artifact, or None if it is not indexed.

        The preview endpoint reads a payload straight off disk and could only
        report a generic "artifact" type for anything it did not special-case,
        so unrecognised artifacts opened under a meaningless "ARTIFACT" eyebrow
        (#74). The index already records the real type.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT artifact_type FROM artifacts WHERE artifact_id=? LIMIT 1",
                (artifact_id,),
            ).fetchone()
        if row is None:
            return None
        return ArtifactType(row["artifact_type"])

    def blob_dir(self, artifact_id: str) -> Path:
        """Directory for large binary side-payloads (parquet, pickled pipelines)."""
        path = self.objects_dir / artifact_id[:2] / artifact_id / "blobs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def put(
        self,
        artifact: Artifact,
        *,
        run_id: str,
        stage_exec_id: str | None = None,
        name: str | None = None,
    ) -> ArtifactRef:
        """Persist an artifact. Idempotent: re-putting identical content is a no-op.

        ``name`` is an optional human/logical handle (e.g. the table name for a
        DataCard) that lets callers fetch without knowing the hash.
        """
        artifact_id = compute_artifact_id(artifact)
        payload_path = self._payload_path(artifact_id)

        with self._lock:
            if not payload_path.exists():
                payload_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = payload_path.with_suffix(".json.tmp")
                tmp.write_text(artifact.model_dump_json(indent=2), encoding="utf-8")
                tmp.replace(payload_path)

            summary = artifact.summary()
            created_at = datetime.now(UTC).isoformat()
            with self._connect() as conn:
                existing = conn.execute(
                    "SELECT created_at FROM artifacts WHERE artifact_id=? AND run_id=?",
                    (artifact_id, run_id),
                ).fetchone()
                if existing is not None:
                    created_at = existing["created_at"]
                conn.execute(
                    """
                    INSERT INTO artifacts (artifact_id, run_id, stage_exec_id, artifact_type,
                                           schema_version, type_key, name, summary_json,
                                           created_at)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(artifact_id, run_id) DO UPDATE SET
                        stage_exec_id=excluded.stage_exec_id,
                        name=COALESCE(excluded.name, artifacts.name),
                        summary_json=excluded.summary_json
                    """,
                    (
                        artifact_id,
                        run_id,
                        stage_exec_id,
                        artifact.artifact_type.value,
                        artifact.schema_version,
                        artifact.type_key(),
                        name,
                        json.dumps(summary, ensure_ascii=False),
                        created_at,
                    ),
                )

        return ArtifactRef(
            artifact_id=artifact_id,
            run_id=run_id,
            stage_exec_id=stage_exec_id,
            artifact_type=artifact.artifact_type,
            schema_version=artifact.schema_version,
            name=name,
            summary=summary,
            created_at=created_at,
        )

    def load(self, artifact_id: str, model: type[A] | None = None) -> A:
        """Read an artifact back. Pass ``model`` for a typed read."""
        payload_path = self._payload_path(artifact_id)
        if not payload_path.exists():
            raise ArtifactNotFoundError(f"No artifact payload for id={artifact_id}")
        raw = json.loads(payload_path.read_text(encoding="utf-8"))

        if model is None:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT artifact_type FROM artifacts WHERE artifact_id=? LIMIT 1",
                    (artifact_id,),
                ).fetchone()
            if row is None:
                raise ArtifactNotFoundError(f"No index entry for id={artifact_id}")
            resolved = _TYPE_REGISTRY.get(ArtifactType(row["artifact_type"]))
            if resolved is None:
                raise ArtifactNotFoundError(f"No model registered for type={row['artifact_type']}")
            model = resolved  # type: ignore[assignment]

        return model.model_validate(raw)  # type: ignore[union-attr,return-value]

    def list(
        self,
        run_id: str,
        *,
        artifact_type: ArtifactType | None = None,
        name: str | None = None,
    ) -> list[ArtifactRef]:
        """List artifact refs for a run, newest first."""
        sql = "SELECT * FROM artifacts WHERE run_id=?"
        params: list[Any] = [run_id]
        if artifact_type is not None:
            sql += " AND artifact_type=?"
            params.append(artifact_type.value)
        if name is not None:
            sql += " AND name=?"
            params.append(name)
        sql += " ORDER BY created_at DESC, rowid DESC"

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_ref(r) for r in rows]

    def list_all(self, artifact_type: ArtifactType) -> list[ArtifactRef]:
        """Every artifact of one type across every run, newest first.

        Deliberately run-free, matching `delete_artifact` and the download
        routes: from the outside a content-addressed artifact is one entity
        regardless of which runs indexed it. An artifact indexed by two runs
        appears once, because the id is what identifies it.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM artifacts WHERE artifact_type=? "
                "GROUP BY artifact_id ORDER BY created_at DESC, rowid DESC",
                (artifact_type.value,),
            ).fetchall()
        return [self._row_to_ref(r) for r in rows]

    def latest(
        self, run_id: str, artifact_type: ArtifactType, *, name: str | None = None
    ) -> ArtifactRef | None:
        refs = self.list(run_id, artifact_type=artifact_type, name=name)
        return refs[0] if refs else None

    def load_all(self, run_id: str, artifact_type: ArtifactType, model: type[A]) -> list[A]:
        """Load every artifact of a type for a run, typed."""
        refs = self.list(run_id, artifact_type=artifact_type)
        return [self.load(ref.artifact_id, model) for ref in refs]

    def exists(self, artifact_id: str) -> bool:
        return self._payload_path(artifact_id).exists()

    def delete_run(self, run_id: str) -> dict[str, int]:
        """Delete one run's index rows and identify payloads eligible for later GC.

        Objects are deliberately not deleted inline. Another process may be preparing
        to index the same content-addressed payload, and SQLite cannot make that
        filesystem race atomic. A separate quiescent garbage-collection operation can
        safely reclaim the reported orphans later.
        """
        if not run_id.strip():
            raise ValueError("run_id must not be empty")
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT artifact_id FROM artifacts WHERE run_id=?", (run_id,)
                ).fetchall()
                artifact_ids = {str(row["artifact_id"]) for row in rows}
                conn.execute("BEGIN IMMEDIATE")
                try:
                    conn.execute("DELETE FROM artifacts WHERE run_id=?", (run_id,))
                    orphaned = {
                        artifact_id
                        for artifact_id in artifact_ids
                        if conn.execute(
                            "SELECT 1 FROM artifacts WHERE artifact_id=? LIMIT 1",
                            (artifact_id,),
                        ).fetchone()
                        is None
                    }
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise

        return {
            "index_entries": len(rows),
            "payloads": 0,
            "orphaned_payloads": len(orphaned),
        }

    def delete_artifact(self, artifact_id: str) -> dict[str, int]:
        """Delete one artifact's index rows across every run it appears in.

        Scoped by id alone, matching the download routes, which also take no
        run_id: from the outside a content-addressed artifact is one entity
        regardless of which run(s) indexed it. As with `delete_run`, the
        payload is left for later garbage collection rather than removed here.
        """
        if not artifact_id.strip():
            raise ValueError("artifact_id must not be empty")
        with self._lock:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    deleted = conn.execute(
                        "DELETE FROM artifacts WHERE artifact_id=?", (artifact_id,)
                    ).rowcount
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
        return {"index_entries": deleted}

    @staticmethod
    def _row_to_ref(row: sqlite3.Row) -> ArtifactRef:
        return ArtifactRef(
            artifact_id=row["artifact_id"],
            run_id=row["run_id"],
            stage_exec_id=row["stage_exec_id"],
            artifact_type=ArtifactType(row["artifact_type"]),
            schema_version=row["schema_version"],
            name=row["name"],
            summary=json.loads(row["summary_json"]),
            created_at=row["created_at"],
        )
