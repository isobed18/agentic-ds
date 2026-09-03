"""Local workflow control plane and deliberately data-safe UI API.

The browser receives workflow metadata, DataCard-style schema statistics, run
events, gate decisions, and artifact summaries. It never receives source rows.
The runtime registry accelerates live polling; a redacted progress snapshot is
also written beside the artifact store so completed and interrupted runs remain
inspectable after the server restarts.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from ads.agents.runtime import DEFAULT_AGENT_RUNTIME_POLICY, AgentRuntimePolicy
from ads.api import i18n
from ads.api.auth import config_from_env, install_auth
from ads.api.panels import (
    eda_panels,
    evaluation_panels,
    exploratory_panel,
    leakage_panels,
    model_experiment_panel,
    rl_feature_panels,
    schema_graph,
    source_panels,
    training_panels,
    validation_panels,
)
from ads.api.teams import (
    OWNERSHIP_FILE,
    OwnershipStore,
    Teams,
    load_teams,
    may_view,
)
from ads.automation import (
    AutomationRevisionConflict,
    AutomationStore,
    PlannerGraphEditRejected,
    add_problem_branches,
    apply_planner_graph_operations,
    automation_component_catalog,
    compile_automation_plan,
    instantiate_component,
)
from ads.contracts.automation_definition import AutomationInputFile
from ads.contracts.base import ArtifactType, is_diagnostic_artifact
from ads.contracts.dataflow import TableAsset
from ads.contracts.documents import (
    DocumentExtraction,
    DocumentExtractionSummary,
    DocumentTableReview,
    aligned_turkish,
)
from ads.contracts.gates import BUILTIN_PROFILES
from ads.contracts.integration import IntegrationPlan
from ads.contracts.problem import METRICS_BY_TASK, Metric, ProblemDefinition, TaskType
from ads.contracts.project import (
    PROJECT_VISIBILITIES,
    may_view_project,
    may_write_project,
)
from ads.contracts.staging import (
    GraphPatch,
    LocalizedText,
    PipelineBlueprint,
    PipelineLayout,
    PipelineOutputReference,
    PlannerOverrideProposal,
    PromotedDocumentTable,
    RelationshipExplanation,
    RuntimeConfigurationPlan,
    StagingMessage,
    StagingReport,
    StagingReportArtifact,
    StagingWorkspace,
)
from ads.contracts.validation import SplitStrategy, ValidationStrategy
from ads.documents import (
    PDF_SUFFIXES,
    CandidateNotPromotable,
    DocumentExtractionError,
    create_document_table_review,
    document_extraction_prompt_context,
    extract_document_directory,
    load_pdf_directory,
    pdf_prompt_context,
    promote_reviewed_document_tables,
)
from ads.gates import GatePolicy
from ads.intake import (
    TooManyTablesForPairwiseDetection,
    detect_relationships,
    load_directory_with_failures,
    profile_tables,
)
from ads.intake.loaders import CSV_SUFFIXES, EXCEL_SUFFIXES, PARQUET_SUFFIXES
from ads.llm import (
    DEFAULT_CLAUDE_TIMEOUT,
    DEFAULT_DEEPSEEK_MODEL,
    DEFAULT_DEEPSEEK_TIMEOUT,
    DEFAULT_REQUESTS_PER_MINUTE,
    LARGE,
    MAX_RUN_SEED,
    ClaudeCliClient,
    DeepSeekClient,
    OllamaClient,
    RateLimiter,
    StructuredLLM,
    derive_agent_seed,
)
from ads.llm.budget import (
    BudgetedLLM,
    PerUserRateLimiter,
    bind_user,
    multipliers_from_env,
    start_worker,
)
from ads.orchestration import (
    EdgeCondition,
    MissingArtifactError,
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
from ads.pipeline.stages import (
    CANDIDATE_LIMIT_KEY,
    QUICK_PROBLEM_KEY,
    RUN_LANGUAGE_KEY,
    STAGE_DIRECTIVES_KEY,
    VALIDATION_FOLDS_KEY,
)
from ads.projects import ProjectRevisionConflict, ProjectStore
from ads.sandbox import SandboxConfig, SandboxManager
from ads.skills import render_skills, select_skills
from ads.staging import (
    StagingWorkspaceConflict,
    apply_component_updates,
    build_default_blueprint,
    document_engine_catalog,
    validate_executable_blueprint,
)
from ads.store import ArtifactNotFoundError, ArtifactRef, ArtifactStore
from ads.tools.activity import FEED as tool_activity_feed

# Content-based file detection is optional. When its dependency is unavailable,
# the source profile is still produced without detection measurements.
try:
    from ads.file_detection.router import inventory as _file_inventory
except ImportError:  # pragma: no cover - ekstranin kurulu olmadigi ortam
    _file_inventory = None

# Map the content detector's flow vocabulary to the extension-based `route`
# vocabulary. This is used only to expose disagreements (see source_profile).
_DETECTED_FLOW_TO_ROUTE = {"tablo": "structured", "belge": "documents"}
#: Run states that mean more work is still coming, and a live view is worth
#: polling for (#411). `interrupted` is excluded for the same reason the web
#: side excludes it: its whole purpose is to end a poll that would never stop.
_ACTIVE_RUN_STATUSES = frozenset(
    {"queued", "running", "resuming", "staging", "branches_running"}
)

def _detect_flow_from_content(filename: str, content: bytes) -> str | None:
    """Bir dosyanin akisini ICERIGINDEN olc; uzantiya hic bakma.

    Yukleme kapisi uzantiyla karar veriyordu, yani uzantisi olmayan gecerli bir
    tablo icerigine hic bakilmadan reddediliyordu -- keşif'in var olma sebebi
    tam olarak buydu. Kesif kurulu degilse None doner ve cagiran taraf eski
    uzanti kuralina duser.
    """
    if _file_inventory is None:
        return None
    import tempfile

    with tempfile.TemporaryDirectory() as gecici:
        yol = Path(gecici) / (Path(filename).name or "dosya")
        yol.write_bytes(content)
        try:
            env = _file_inventory(Path(gecici), ocr=False)
        except Exception:  # olcum basarisizsa karar eski kurala kalir
            return None
    kararlar = env.get("kararlar") or []
    if not kararlar:
        return None
    karar = kararlar[0]
    return karar.akis.value if karar.deterministik else None


# Olculen format -> yukleyicinin anladigi uzanti. Magika'nin etiketi ile
# `ads.intake.loaders` kumeleri ayni dili konusmuyor; eslemeyi burada tek yerde
# tutuyoruz.
_MEASURED_FORMAT_SUFFIX = {
    "csv": ".csv", "tsv": ".tsv", "txt": ".txt",
    "xlsx": ".xlsx", "xlsm": ".xlsm",
    "parquet": ".parquet",
}
_LOADABLE_SUFFIXES = CSV_SUFFIXES | EXCEL_SUFFIXES | PARQUET_SUFFIXES


def _file_detection_inventory(source_root: Path) -> tuple[Any, str | None]:
    """Bir klasoru BIR KEZ olc; sonucu hem yukleme hem raporlama kullanir.

    Basarisizlik profili DUSURMEZ. Hatanin TURU geri veriliyor cunku
    "olculemedi" tek basina teshis edilemez -- hangi hatanin oldugunu bilmek
    ile bilmemek arasindaki fark, bir kullanicinin bildirdigi sorunu bulup
    bulamamak oluyor.
    """
    if _file_inventory is None:
        return None, None
    try:
        return _file_inventory(source_root, ocr=False), None
    except Exception as error:  # Detection must never take down the profile.
        return None, type(error).__name__


def _measured_table_formats(source_root: Path, env) -> dict[str, str]:
    """Uzantisi kullanilamayan ama ICERIGI tablo olarak olculen dosyalar.

    Yukleme kapisi bu dosyalari zaten kabul ediyor (#103); tarayicinin onlari
    gormemesi, kabul edilen bir dosyanin sessizce kaybolmasi demekti (#208).
    Uzantisi ZATEN destekleniyorsa dokunulmaz -- uzanti dogru oldugu surece
    olcum devreye girmez.
    """
    if env is None:
        return {}
    olculen: dict[str, str] = {}
    for karar in env.get("kararlar", []):
        yol = Path(karar.yol)
        if yol.suffix.lower() in _LOADABLE_SUFFIXES:
            continue
        if not karar.deterministik or karar.akis.value != "tablo":
            continue
        uzanti = _MEASURED_FORMAT_SUFFIX.get(karar.format)
        if uzanti:
            olculen[yol.name] = uzanti
    return olculen


def _measure_file_detection(
    source_root: Path,
    source_files: list[dict[str, Any]],
    env: Any = None,
    error_name: str | None = None,
) -> dict[str, Any]:
    """Her kaynak dosyanin turunu ICERIKTEN olc ve `source_files`'i zenginlestir.

    Bu ek bilgidir, karar degil: `route` alanina DOKUNULMAZ. Amac, uzantiya
    bakarak verilen mevcut kararin nerede yaniltici oldugunu gorunur kilmak --
    ornegin `.csv` adli bir PDF, ya da hicbir uzantisi olmadigi icin
    "unsupported" sayilan gecerli bir tablo.

    Kesif kurulu degilse ya da olcum sirasinda bir sey ters giderse profil
    kesif alanlari olmadan doner; cagiran taraf icin bu bir hata degildir.
    """
    if _file_inventory is None:
        return {"used": False, "reason": "file detection extra is not installed"}
    if env is None and error_name is None:
        env, error_name = _file_detection_inventory(source_root)
    if env is None:
        return {"used": False, "reason": f"detection failed: {error_name}"}

    girdiler = {row["name"]: row for row in source_files}
    uyusmazlik = 0

    for karar in env["kararlar"]:
        try:
            ad = Path(karar.yol).resolve().relative_to(source_root).as_posix()
        except ValueError:
            continue
        satir = girdiler.get(ad)
        if satir is None:
            continue

        akis = karar.akis.value
        satir["detected_flow"] = akis
        satir["detection_deterministic"] = karar.deterministik
        if karar.kanitlar:
            baslik, detay = karar.kanitlar[0]
            satir["detection_evidence"] = f"{baslik}: {detay}"
        if not karar.deterministik and karar.yargi_sebebi:
            satir["detection_reason"] = karar.yargi_sebebi

        # Uyusmazlik yalnizca kesif KESIN konustugunda ve uzantinin GERCEKTEN
        # baska bir sey iddia ettigi durumda one surulur. Kararsizsa sessiz
        # kalir.
        #
        # `unsupported` bir iddia DEGILDIR: dosyanin uzantisi yok ya da
        # taninmiyor demektir, yani ortada celisilecek bir sav yok. Bunu
        # celiski saymak, kapinin ICERIGE bakarak kabul ettigi uzantisiz bir
        # tabloyu hemen ardindan karantinaya atiyordu -- iki yarim birbirini
        # yiyordu (#208).
        olculen_rota = _DETECTED_FLOW_TO_ROUTE.get(akis)
        uzanti_iddia_ediyor = satir["route"] not in {"unsupported", "needs_review"}
        celisiyor = (
            karar.deterministik
            and olculen_rota is not None
            and uzanti_iddia_ediyor
            and olculen_rota != satir["route"]
        )
        # Uzanti susuyorsa olcum rotayi VERIR; kapinin dosyayi kabul etme
        # gerekcesi zaten buydu.
        if karar.deterministik and olculen_rota is not None and not uzanti_iddia_ediyor:
            satir["route"] = olculen_rota
            satir["detection_supplied_route"] = True
        satir["detection_conflicts_with_extension"] = celisiyor
        if celisiyor:
            uyusmazlik += 1

    return {
        "used": True,
        "file_count": env["dosya_sayisi"],
        "deterministic_count": env["deterministik"],
        "adjudication_required_count": env["yargi_gerektiren"],
        "extension_conflict_count": uyusmazlik,
    }


def _translated(text: str, language: str, **params: Any) -> str:
    """Render one catalogue key without changing the request's language."""
    with i18n.using(language):
        return i18n.t(text, **params)


def _file_profile_insights(
    source_files: list[dict[str, Any]],
    tables: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach one bounded, row-free explanation to every routed file.

    These sentences use only DataCard measurements already safe for agent
    context: table/row counts, candidate key names, issue counts and measured
    relationship participation. Source values never enter the payload.
    """

    tables_by_file: dict[str, list[dict[str, Any]]] = {}
    for table in tables:
        tables_by_file.setdefault(str(table.get("source_file") or ""), []).append(table)
    documents_by_name = {str(item.get("name") or ""): item for item in documents}
    related_tables = {
        str(relationship.get(side) or "")
        for relationship in relationships
        for side in ("from_table", "to_table")
    }

    enriched: list[dict[str, Any]] = []
    for source_file in source_files:
        item = dict(source_file)
        name = str(item.get("name") or "")
        file_tables = tables_by_file.get(name, [])
        route = str(item.get("route") or "unsupported")

        if route == "structured" and file_tables:
            table_count = len(file_tables)
            row_count = sum(int(table.get("rows") or 0) for table in file_tables)
            issue_count = sum(len(table.get("issues") or []) for table in file_tables)
            key_labels: list[str] = []
            for table in file_tables:
                for columns in table.get("candidate_keys") or []:
                    joined = "+".join(str(column) for column in columns)
                    if joined:
                        key_labels.append(
                            joined if table_count == 1 else f"{table.get('name')}.{joined}"
                        )

            if table_count > 1:
                role_key = "multi-table source"
            elif any(str(table.get("name") or "") in related_tables for table in file_tables):
                role_key = "joinable table"
            elif key_labels:
                role_key = "keyed table"
            else:
                role_key = "analysis table"

            shown_keys = key_labels[:2]
            remaining_keys = len(key_labels) - len(shown_keys)
            suffix = f" +{remaining_keys}" if remaining_keys else ""
            key_template = (
                "key candidate: {keys}" if len(key_labels) == 1 else "key candidates: {keys}"
            )
            insight: dict[str, str] = {}
            role: dict[str, str] = {}
            for language in ("en", "tr"):
                role[language] = _translated(role_key, language)
                keys = (
                    _translated(
                        key_template,
                        language,
                        keys=f"{', '.join(shown_keys)}{suffix}",
                    )
                    if shown_keys
                    else _translated("no key candidate", language)
                )
                quality = (
                    _translated("no quality notes", language)
                    if issue_count == 0
                    else _translated("{count} quality notes", language, count=issue_count)
                )
                insight[language] = _translated(
                    "{tables} {table_unit} · {rows} {row_unit} · {role} · {keys} · {quality}",
                    language,
                    tables=table_count,
                    table_unit=_translated(
                        "table" if table_count == 1 else "tables", language
                    ),
                    rows=f"{row_count:,}",
                    row_unit=_translated("row" if row_count == 1 else "rows", language),
                    role=role[language],
                    keys=keys,
                    quality=quality,
                )
            item.update(
                {
                    "origin": "measured",
                    "rows": row_count,
                    "tables": table_count,
                    "candidate_keys": key_labels,
                    "quality_issues": issue_count,
                    "schema_role": role,
                    "insight": insight,
                }
            )
        elif route == "documents" and name in documents_by_name:
            document = documents_by_name[name]
            pages = int(document.get("pages") or 0)
            ready = document.get("understanding_status") == "text_ready"
            role = {
                language: _translated("document context", language)
                for language in ("en", "tr")
            }
            insight = {
                language: _translated(
                    "{pages} {page_unit} · {role} · {readiness}",
                    language,
                    pages=pages,
                    page_unit=_translated("page" if pages == 1 else "pages", language),
                    role=role[language],
                    readiness=_translated(
                        "text layer ready" if ready else "OCR or vision needed", language
                    ),
                )
                for language in ("en", "tr")
            }
            item.update(
                {
                    "origin": "measured",
                    "rows": 0,
                    "tables": 0,
                    "candidate_keys": [],
                    "quality_issues": len(document.get("issues") or []),
                    "schema_role": role,
                    "insight": insight,
                }
            )
        else:
            reason = item.get("reason")
            if isinstance(reason, dict) and reason.get("en") and reason.get("tr"):
                item["origin"] = "measured"
                item["insight"] = {"en": str(reason["en"]), "tr": str(reason["tr"])}
        enriched.append(item)
    return enriched


_UPLOAD_ID = re.compile(r"^upload:([0-9a-f]{12})$")
_AUTOMATION_INPUT_ID = re.compile(r"^automation-input:([0-9a-f]{12})$")
_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9_.-]+$")
_PROFILE_CACHE_SCHEMA_VERSION = 2
_UPLOAD_SUFFIXES = {".csv", ".tsv", ".txt", ".xlsx", ".xlsm", ".xls", ".parquet", ".pq", ".pdf"}
# Windows' classic MAX_PATH. A host can lift it with LongPathsEnabled, but that
# is a per-machine opt-in outside this application's control, so the budget is
# spent here either way rather than assumed away because one deployment happens
# to have it switched on (#63).
_MAX_UPLOAD_PATH = 260
# The longest single name component NTFS and ext4 accept, long paths or not.
# Counted in UTF-8 bytes because that is what the limit counts on Linux, and
# Turkish file names are routine here: 'ğ' costs two of the 255.
_MAX_UPLOAD_NAME_BYTES = 255
# Below this many characters left for the file name, the upload root itself is
# what is wrong, not the file: `2026-yili-calisma-takvimi.xlsx` is 30.
_MIN_UPLOAD_NAME_BUDGET = 32
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
    "rl_feature_engineering",
    "training",
    "evaluation",
    "report",
}


def _signed(value: Any) -> str:
    """A metric change with its sign kept, so "no change" cannot read as "+0.00"."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return "—"
    return f"{value:+.3f}"

# Agent calls outside the executable workflow reserve stable virtual stage
# ordinals so they share the same run-level derivation without colliding with
# actual workflow nodes.
_STAGING_ANALYSIS_STAGE_ORDINAL = 100
_PLANNER_CHAT_STAGE_ORDINAL = 101


def _new_run_seed() -> int:
    return uuid.uuid4().int % (MAX_RUN_SEED + 1)


def _coerce_run_seed(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("run_seed must be an integer")
    try:
        seed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("run_seed must be an integer") from exc
    if not 0 <= seed <= MAX_RUN_SEED:
        raise ValueError(f"run_seed must be between 0 and {MAX_RUN_SEED}")
    return seed


class _PlannerLocalizedText(BaseModel):
    """A bilingual fragment emitted in one local-model response."""

    en: str = Field(min_length=1, max_length=20_000)
    tr: str = Field(min_length=1, max_length=20_000)


class _PlannerRelationshipExplanation(BaseModel):
    from_table: str
    from_columns: list[str] = Field(min_length=1)
    to_table: str
    to_columns: list[str] = Field(min_length=1)
    explanation_en: str = Field(min_length=1, max_length=20_000)
    explanation_tr: str = Field(min_length=1, max_length=20_000)
    why_it_matters_en: str = Field(min_length=1, max_length=20_000)
    why_it_matters_tr: str = Field(min_length=1, max_length=20_000)
    verification_question_en: str = Field(min_length=1, max_length=20_000)
    verification_question_tr: str = Field(min_length=1, max_length=20_000)


class _PlannerReport(BaseModel):
    component_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]*$")
    title_en: str = Field(min_length=1, max_length=500)
    title_tr: str = Field(min_length=1, max_length=500)
    summary_en: str = Field(min_length=1, max_length=20_000)
    summary_tr: str = Field(min_length=1, max_length=20_000)
    findings: list[_PlannerLocalizedText] = Field(default_factory=list)
    verification_questions: list[_PlannerLocalizedText] = Field(default_factory=list)


class _PlannerProblemRecommendation(BaseModel):
    """A measured, non-mutating shortlist item for ordinary planner advice."""

    rank: int = Field(ge=1, le=5)
    problem_title: str = Field(min_length=1, max_length=200)
    target_column: str = Field(min_length=1, max_length=500)
    task_type: Literal[
        "binary_classification",
        "multiclass_classification",
        "regression",
        "anomaly_detection",
    ]
    primary_metric: Literal[
        "roc_auc",
        "average_precision",
        "f1",
        "balanced_accuracy",
        "accuracy",
        "rmse",
        "mae",
        "r2",
        "mape",
        "silhouette",
    ]
    evidence: list[str] = Field(min_length=1, max_length=4)
    caveats: list[str] = Field(default_factory=list, max_length=3)


class _PlannerProblemBranch(BaseModel):
    branch_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$", max_length=40)
    title: str = Field(min_length=1, max_length=200)
    target_column: str = Field(min_length=1, max_length=500)
    task_type: Literal[
        "binary_classification",
        "multiclass_classification",
        "regression",
        "anomaly_detection",
    ]
    primary_metric: Literal[
        "roc_auc",
        "average_precision",
        "f1",
        "balanced_accuracy",
        "accuracy",
        "rmse",
        "mae",
        "r2",
        "mape",
        "silhouette",
    ]


class _PlannerComponentAddition(BaseModel):
    catalog_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    component_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    settings: dict[str, Any] = Field(default_factory=dict)
    control: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    branch_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]*$")
    group_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]*$")


class _PlannerConnection(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]+$")
    source_component: str
    source_port: str
    target_component: str
    target_port: str


class _PlannerChatReply(BaseModel):
    """Grammar-constrained, UI-safe response from the planner chat."""

    reply: str
    relationship_explanations: list[_PlannerRelationshipExplanation] = Field(default_factory=list)
    reports: list[_PlannerReport] = Field(default_factory=list)
    plan_rationale: list[_PlannerLocalizedText] = Field(default_factory=list)
    configuration_patch: dict[str, Any] = Field(default_factory=dict)
    rules_to_remember: list[str] = Field(default_factory=list)
    checkpoint_stages: list[str] = Field(default_factory=list)
    auto_proceed_stages: list[str] = Field(default_factory=list)
    max_retries_by_stage: dict[str, int] = Field(default_factory=dict)
    focus_stage: str | None = None
    #: stage_id -> instructions the planner is passing on to that stage's agent.
    #: This is the indirect route: a person describes what they want to the
    #: planner, and the planner decides which stage it belongs to. The direct
    #: route is the instruction box on the stage itself.
    stage_directives: dict[str, list[str]] = Field(default_factory=dict)
    #: Bounded updates to known component settings. The host validates these
    #: against the persisted blueprint; this is not arbitrary graph execution.
    pipeline_component_updates: dict[str, dict[str, Any]] = Field(default_factory=dict)
    pipeline_component_additions: list[_PlannerComponentAddition] = Field(
        default_factory=list, max_length=12
    )
    pipeline_connections: list[_PlannerConnection] = Field(default_factory=list, max_length=24)
    pipeline_component_disables: list[str] = Field(default_factory=list, max_length=12)
    problem_recommendations: list[_PlannerProblemRecommendation] = Field(
        default_factory=list, max_length=5
    )
    problem_branches: list[_PlannerProblemBranch] = Field(default_factory=list, max_length=3)


class _StagingAnalysisReport(BaseModel):
    kind: Literal["structured", "documents", "synthesis"]
    title_en: str
    title_tr: str
    summary_en: str
    summary_tr: str
    findings_en: list[str] = Field(default_factory=list, max_length=4)
    findings_tr: list[str] = Field(default_factory=list, max_length=4)
    verification_questions_en: list[str] = Field(default_factory=list, max_length=3)
    verification_questions_tr: list[str] = Field(default_factory=list, max_length=3)


class _StagingAnalysisReply(BaseModel):
    """Minimal automatic synthesis; interactive chat owns graph editing."""

    reply: str
    pipeline_decision: Literal["create_pipeline", "defer_pipeline", "no_pipeline"]
    decision_reason_en: str
    decision_reason_tr: str
    reports: list[_StagingAnalysisReport] = Field(default_factory=list, max_length=3)
    rationale_en: list[str] = Field(default_factory=list, max_length=4)
    rationale_tr: list[str] = Field(default_factory=list, max_length=4)


def _check_upload_path_fits(target: Path) -> None:
    """Refuse an upload whose path would not survive the write, before writing.

    `Path(filename).name` stops traversal but caps nothing, so a long enough
    name reached `write_bytes` and came back as a bare `OSError` naming a path
    the uploader never typed. The limit is applied here instead, and applied
    whether or not the host has Windows long-path support: that switch belongs
    to whoever built the machine, and code that assumes it works everywhere it
    was tested (#63).

    The two failures are told apart on purpose. A name that overruns the budget
    is the uploader's to fix; a root so deep that a reasonable name cannot fit
    under it is the operator's, and saying "file name is too long" there would
    send the wrong person looking.
    """
    directory = len(str(target.parent.resolve()))
    # The separator the name will be joined with, plus the terminating NUL that
    # MAX_PATH counts but `len` does not.
    budget = _MAX_UPLOAD_PATH - directory - 2
    if budget < _MIN_UPLOAD_NAME_BUDGET:
        raise ValueError(
            "the configured upload directory is too deep to store files safely; "
            "move it closer to the drive root"
        )
    if len(target.name) > budget:
        raise ValueError(f"file name is too long (limit {budget} characters here)")


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
    automation_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "parent_run_id": self.parent_run_id,
            "branch_label": self.branch_label,
            "source_id": self.source_id,
            "automation_id": self.automation_id,
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
    #: Ya duz metin (eski kayitlar, ve tanimadigimiz hatalar) ya da iki dilli
    #: {"en":..,"tr":..}. Okuyan taraf ikisini de kaldirmali (#263, #265).
    error: str | dict[str, str] | None = None
    #: Continues a staged run from where it stopped. Held rather than rebuilt
    #: so the second half executes against the same state and the artifacts
    #: intake and schema discovery already produced are not recomputed.
    resume: Any | None = None
    #: Cooperative pause requested by the UI. The current stage is allowed to
    #: finish so its artifacts remain valid; the event boundary then returns
    #: the run to STAGED and continuation resumes from the next stage.
    pause_requested: bool = False


class _RunPauseRequested(Exception):
    """Internal control-flow signal raised only at a completed stage boundary."""


class PlannerResponseError(RuntimeError):
    """The planner's model returned a response the service could not use.

    #242: ``json.JSONDecodeError`` subclasses ``ValueError``, so a malformed
    model reply used to be caught by the planner route's ``except ValueError``
    and reported to the client as an HTTP 400 whose entire body was the JSON
    parser's own offset text -- ``Expecting ',' delimiter: line 1 column 5151
    (char 5150)``. That status blames the request (which was fine) and the
    message names a character in a payload the user never wrote and cannot see.
    A model that returns unparseable output is an upstream failure, not a bad
    request, so this is raised distinctly, mapped to 502 with a readable
    message, while the raw parser detail is preserved for the logs only.
    """

    def __init__(self, message: str, *, cause: str | None = None) -> None:
        super().__init__(message)
        self.cause = cause


@dataclass
class ControlPlane:
    """Framework-free application service behind the local FastAPI UI."""

    store: ArtifactStore
    source_roots: tuple[Path, ...] = ()
    upload_root: Path | None = None
    llm_factory: Callable[[], StructuredLLM] | None = None
    workflow_runner: Callable[..., Any] | None = None
    automation_store: AutomationStore | None = None
    project_store: ProjectStore | None = None
    _runtime_runs: dict[str, _RuntimeRun] = field(default_factory=dict)
    #: source_id -> (file fingerprint, profile). Invalidated by the fingerprint
    #: rather than by a timer, so a changed file is re-profiled immediately and
    #: an unchanged one is never re-read.
    _profile_cache: dict[str, tuple[str, dict[str, Any]]] = field(default_factory=dict)
    #: source_id -> (file fingerprint, cached staged outcome data).
    #: Staging runs Intake and Schema Discovery (which takes ~7 min on local 27B model).
    #: When unchanged data is staged again, the staged state & artifacts are reused.
    _staged_cache: dict[str, tuple[str, dict[str, Any]]] = field(default_factory=dict)
    #: Where the profile cache survives a restart. The in-memory dict above is
    #: per-process, so every deploy, crash or restart previously re-profiled
    #: every source from scratch -- the docstring on `source_profile` records a
    #: single 121 MB dataset costing 4.91s of a 5.22s page load, and that cost
    #: was paid again on every boot. Keyed by the same fingerprint, so a changed
    #: file still invalidates immediately.
    profile_cache_dir: Path | None = None
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
        if self.automation_store is None:
            self.automation_store = AutomationStore(self.store.root.parent / "automations")
        if self.project_store is None:
            self.project_store = ProjectStore(self.store.root.parent / "projects")
        self._automation_input_root.mkdir(parents=True, exist_ok=True)

    @property
    def _run_state_root(self) -> Path:
        return self.store.root.parent / "run-state"

    @property
    def _automation_input_root(self) -> Path:
        return self.store.root.parent / "automation-inputs"

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
        human_approved_stages = {
            event.get("stage")
            for event in (progress.get("events", []) if progress else [])
            if event.get("event") == "human_decision_recorded"
            and event.get("decision") == "approve"
        }

        nodes = []
        for index, stage in enumerate(raw["stages"], start=1):
            own = [item for item in attempts if item["stage_id"] == stage["id"]]
            latest = own[-1] if own else None
            status = self._stage_status(
                latest,
                current,
                run_status,
                human_approved=stage["id"] in human_approved_stages,
            )
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
                    # A short, already-translated line the canvas card shows
                    # under its description, with the tone it should read in.
                    # Composed here rather than in the browser for the same
                    # reason panel severity is: what counts as a warning is a
                    # judgment about the run, and the two sides disagreeing
                    # about it is worse than either being slightly wrong.
                    "note": self._stage_note(stage["id"], run_id),
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

    def _stage_note(self, stage_id: str, run_id: str | None) -> dict[str, str] | None:
        """One translated line for a stage's canvas card, or None for most stages.

        Only the external feature search has one so far: it is the one stage
        whose result a person needs on the card itself, because "the API was
        unreachable" is otherwise invisible until someone opens the panel.
        """
        if stage_id != "rl_feature_engineering" or run_id is None:
            return None
        try:
            ref = self.store.latest(run_id, ArtifactType.RL_FEATURE_REPORT)
        except (KeyError, ValueError):
            return None
        if ref is None:
            return None
        payload = self.artifact_payload(ref.artifact_id)
        status = str(payload.get("status") or "")
        if status == "unavailable":
            return {
                "text": i18n.t("Feature engineering service unreachable; step skipped"),
                "tone": "warn",
            }
        if status != "applicable":
            return {"text": i18n.t("Feature engineering does not apply here"), "tone": "neutral"}
        return {
            "text": i18n.t(
                "+{added} features, −{removed} · {metric} {delta}",
                added=len(payload.get("generated_features") or []),
                removed=len(payload.get("removed_features") or []),
                metric=payload.get("primary_metric") or "",
                delta=_signed(payload.get("api_score_improvement")),
            ),
            "tone": "neutral",
        }

    @staticmethod
    def _stage_status(
        latest: dict[str, Any] | None,
        current: str | None,
        run_status: str | None,
        *,
        human_approved: bool = False,
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
        # An escalation describes the automated gate result, not the terminal
        # stage state. Once a person explicitly accepts it, execution resumes
        # along the proceed edge and the stage is complete. The original
        # escalation remains in gate_decisions/events as the audit trail.
        if verdict == "escalate" and human_approved:
            return "succeeded"
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
            "document_engines": document_engine_catalog(),
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

    # ----------------------------------------------------------- automations

    def _visible_project(
        self, project_id: str, viewer: str | None, *, write: bool = False
    ) -> Any:
        """The one ownership check every project-scoped route goes through (#206).

        A viewer who may not see the project gets `KeyError`, which the routes
        answer as 404 -- not 403, because a 403 confirms the id exists and that
        is exactly what a hidden project must not reveal. `write=True` also
        demands the stronger right; someone who can already see the project has
        no secret left to protect, so that refusal is an honest 403.
        """
        assert self.project_store is not None
        project = self.project_store.get(project_id)
        if not may_view_project(project, viewer):
            raise KeyError(project_id)
        if write and not may_write_project(project, viewer):
            raise PermissionError("only the owner can change this project")
        return project

    @staticmethod
    def _project_payload(project: Any, viewer: str | None) -> dict[str, Any]:
        """One project on the wire, plus whether the reader owns it.

        `mine` is computed per request rather than stored: it answers "may I
        change who sees this?", which is a fact about the reader, not about the
        project. The UI needs it to render the visibility control as a real
        toggle or as a read-only mark -- a control that silently 403s is worse
        than one that says why it is disabled (#207).
        """
        payload = project.model_dump(mode="json")
        payload["mine"] = project.owner is not None and project.owner == viewer
        return payload

    def list_projects(self, *, viewer: str | None = None) -> list[dict[str, Any]]:
        assert self.project_store is not None
        return [
            self._project_payload(item, viewer)
            for item in self.project_store.list(viewer=viewer)
        ]

    def project(self, project_id: str, *, viewer: str | None = None) -> dict[str, Any]:
        return self._project_payload(self._visible_project(project_id, viewer), viewer)

    def set_project_visibility(
        self, project_id: str, *, visibility: str, viewer: str | None = None
    ) -> dict[str, Any]:
        """Publish a project to every signed-in account, or take it back (#207).

        Only the owner may do this, and the refusal is 403 rather than 404: a
        non-owner asking this question can already see the project, so
        pretending it does not exist would be a lie they can disprove. Same
        reasoning, same shape, as the data-source route this copies.

        Taking a project back to private takes effect immediately. There is no
        grandfathering: the next request from anyone still holding it open 404s,
        and it leaves their project list on the next load.
        """
        assert self.project_store is not None
        if visibility not in PROJECT_VISIBILITIES:
            raise ValueError(f"visibility must be one of {PROJECT_VISIBILITIES}")
        project = self._visible_project(project_id, viewer)
        if project.owner is None:
            raise ValueError("this project has no recorded owner")
        if project.owner != viewer:
            raise PermissionError(
                f"only {project.owner} can change who sees this project"
            )
        saved = self.project_store.set_visibility(project_id, visibility)
        return self._project_payload(saved, viewer)

    def create_project(self, name: str, *, owner: str | None = None) -> dict[str, Any]:
        assert self.project_store is not None
        return self.project_store.create(name, owner=owner).model_dump(mode="json")

    def update_project(
        self,
        project_id: str,
        *,
        expected_revision: int,
        changes: dict[str, Any],
        viewer: str | None = None,
    ) -> dict[str, Any]:
        assert self.project_store is not None
        self._visible_project(project_id, viewer, write=True)
        return self.project_store.update(
            project_id, expected_revision=expected_revision, changes=changes
        ).model_dump(mode="json")

    def delete_project(
        self, project_id: str, *, viewer: str | None = None
    ) -> dict[str, Any]:
        """Delete a project and its automations, keeping data and run history.

        #181: the promise automation deletion (`delete_automation`) makes, one
        level up. Only someone who may change the project may remove it
        (`write=True`); the warned dialog covers the accidental click. Each
        child automation is removed through that same path, so its runs stay
        readable and its private input snapshot is dropped only when nothing
        executed. The project's uploaded data sources are reusable and shared,
        so they are left in the data library untouched.
        """
        assert self.project_store is not None
        assert self.automation_store is not None
        project = self._visible_project(project_id, viewer, write=True)
        removed_automations = 0
        for automation_id in list(project.automation_ids):
            try:
                self.delete_automation(automation_id)
                removed_automations += 1
            except KeyError:
                # A stale id that names no definition must not block the delete.
                continue
        removed = self.project_store.delete(project_id)
        return {"project_id": project_id, "automations": removed_automations, **removed}

    def add_project_source(
        self, project_id: str, source_id: str, *, viewer: str | None = None
    ) -> dict[str, Any]:
        assert self.project_store is not None
        self._visible_project(project_id, viewer, write=True)
        self.source_path(source_id)
        return self.project_store.add_source(
            project_id, source_id=source_id
        ).model_dump(mode="json")

    def project_data(
        self, project_id: str, *, viewer: str | None = None
    ) -> list[dict[str, Any]]:
        assert self.project_store is not None
        project = self._visible_project(project_id, viewer)
        # Filtered by the same viewer as the Data library: a source its owner
        # kept private stays private inside someone else's project, so a shared
        # project can legitimately list fewer files than its owner sees.
        listed = {item["source_id"]: item for item in self.data_sources(viewer=viewer)}
        data: list[dict[str, Any]] = []
        for source_id in project.source_ids:
            source = listed.get(source_id)
            if source is None:
                continue
            item = self._dataset_summary(source)
            item["files"] = [
                path.relative_to(self.source_path(source_id)).as_posix()
                for path in sorted(self.source_path(source_id).rglob("*"))
                if path.is_file()
            ]
            data.append(item)
        return data

    def project_automations(
        self, project_id: str, *, viewer: str | None = None
    ) -> list[dict[str, Any]]:
        assert self.project_store is not None
        assert self.automation_store is not None
        project = self._visible_project(project_id, viewer)
        records = []
        for automation_id in project.automation_ids:
            try:
                records.append(self.automation_store.get(automation_id).model_dump(mode="json"))
            except KeyError:
                continue
        return records

    def create_project_automation(
        self, project_id: str, name: str, *, viewer: str | None = None
    ) -> dict[str, Any]:
        assert self.project_store is not None
        assert self.automation_store is not None
        self._visible_project(project_id, viewer, write=True)
        created = self.automation_store.create(name)
        try:
            self.project_store.add_automation(
                project_id, automation_id=created.automation_id
            )
        except Exception:
            self.automation_store.delete(created.automation_id)
            raise
        return created.model_dump(mode="json")

    def select_automation_inputs(
        self,
        automation_id: str,
        *,
        expected_revision: int,
        selections: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Snapshot exact project files so later pool growth cannot alter a run."""
        assert self.project_store is not None
        assert self.automation_store is not None
        automation = self.automation_store.get(automation_id)
        if automation.revision != expected_revision:
            raise AutomationRevisionConflict(
                f"automation revision changed from {expected_revision} to {automation.revision}"
            )
        parents = [
            project
            for project in self.project_store.list(unfiltered=True)
            if automation_id in project.automation_ids
        ]
        if len(parents) != 1:
            raise ValueError("automation must belong to exactly one project")
        project = parents[0]
        chosen = tuple(AutomationInputFile.model_validate(item) for item in selections)
        if not chosen:
            raise ValueError("select at least one project file")
        if len({(item.source_id, item.path) for item in chosen}) != len(chosen):
            raise ValueError("selected project files must be unique")

        token = uuid.uuid4().hex[:12]
        snapshot_id = f"automation-input:{token}"
        snapshot_root = self._automation_input_root / token
        snapshot_root.mkdir(parents=True, exist_ok=False)
        try:
            for index, item in enumerate(chosen):
                if item.source_id not in project.source_ids:
                    raise ValueError("automation inputs must come from its project data pool")
                source_root = self.source_path(item.source_id)
                source_file = (source_root / item.path).resolve()
                if source_root not in source_file.parents or not source_file.is_file():
                    raise ValueError(f"unknown project file {item.path!r}")
                # #157: the source loader reads files at the source root. The
                # first implementation nested each selection under ``0000/``,
                # so the UI successfully selected data and then profiling said
                # the snapshot contained no supported files. Prefixing at the
                # root preserves collision safety without hiding the files.
                destination = snapshot_root / f"{index:04d}-{Path(item.path).name}"
                shutil.copy2(source_file, destination)
            saved = self.automation_store.update(
                automation_id,
                expected_revision=expected_revision,
                changes={"source_id": snapshot_id, "selected_files": chosen},
            )
        except Exception:
            shutil.rmtree(snapshot_root, ignore_errors=True)
            raise
        old_match = _AUTOMATION_INPUT_ID.fullmatch(automation.source_id or "")
        if old_match:
            shutil.rmtree(self._automation_input_root / old_match.group(1), ignore_errors=True)
        return saved.model_dump(mode="json")

    def list_automations(self) -> list[dict[str, Any]]:
        assert self.automation_store is not None
        return [item.model_dump(mode="json") for item in self.automation_store.list()]

    def automation(self, automation_id: str) -> dict[str, Any]:
        assert self.automation_store is not None
        return self.automation_store.get(automation_id).model_dump(mode="json")

    def create_automation(self, name: str) -> dict[str, Any]:
        assert self.automation_store is not None
        return self.automation_store.create(name).model_dump(mode="json")

    def delete_automation(self, automation_id: str) -> dict[str, Any]:
        """Delete a saved graph without deleting its independently audited runs.

        The parent project is detached first (#185). Both names are only
        reachable through the definition being deleted, and the project card
        counts this id list, so a stale entry counts a deleted draft forever.

        A never-executed automation also takes its private input snapshot with
        it: nothing else can name that directory afterwards. One that did run
        keeps the snapshot, because its runs still resolve their source bytes
        through that path and the deletion promises the history stays readable.
        """
        assert self.automation_store is not None
        assert self.project_store is not None
        automation = self.automation_store.get(automation_id)
        for project in self.project_store.list(unfiltered=True):
            if automation_id in project.automation_ids:
                self.project_store.remove_automation(
                    project.project_id, automation_id=automation_id
                )
        removed = self.automation_store.delete(automation_id)
        snapshot = _AUTOMATION_INPUT_ID.fullmatch(automation.source_id or "")
        if snapshot and not automation.execution_ids:
            shutil.rmtree(
                self._automation_input_root / snapshot.group(1), ignore_errors=True
            )
        return {"automation_id": automation_id, **removed}

    def update_automation(
        self,
        automation_id: str,
        *,
        expected_revision: int,
        changes: dict[str, Any],
    ) -> dict[str, Any]:
        assert self.automation_store is not None
        return self.automation_store.update(
            automation_id,
            expected_revision=expected_revision,
            changes=changes,
        ).model_dump(mode="json")

    def _project_automation_records(
        self, project_id: str, *, viewer: str | None = None
    ) -> tuple[Any, list[Any]]:
        """Load a project and only the automation records it still owns.

        #157 made the durable project store authoritative. Missing child files
        are skipped because deleting a draft must not make its parent unreadable.
        """
        assert self.project_store is not None
        assert self.automation_store is not None
        project = self._visible_project(project_id, viewer)
        automations = []
        for automation_id in project.automation_ids:
            try:
                automations.append(self.automation_store.get(automation_id))
            except KeyError:
                continue
        return project, automations

    def automation_contents(
        self, automation_id: str, *, viewer: str | None = None
    ) -> dict[str, Any]:
        """Return one child automation's private inputs and outputs."""
        assert self.project_store is not None
        assert self.automation_store is not None
        automation = self.automation_store.get(automation_id)
        # Filtered, because this payload embeds the parent project record: an
        # automation whose only parent is hidden from the viewer reads as
        # missing rather than as a project they may not open.
        parents = [
            project
            for project in self.project_store.list(viewer=viewer)
            if automation_id in project.automation_ids
        ]
        if not parents:
            raise KeyError(automation_id)
        if len(parents) != 1:
            raise ValueError("automation must belong to exactly one project")
        execution_ids = set(automation.execution_ids)
        executions = [run for run in self.list_runs() if run.run_id in execution_ids]
        return {
            "automation": automation.model_dump(mode="json"),
            "project": parents[0].model_dump(mode="json"),
            "data": [item.model_dump(mode="json") for item in automation.selected_files],
            "executions": [self._experiment_summary(run) for run in executions],
            "models": [model for run in executions for model in self._model_summaries(run)],
            "reports": [report for run in executions for report in self._report_summaries(run)],
        }

    def project_contents(
        self, project_id: str, *, viewer: str | None = None
    ) -> dict[str, Any]:
        """Aggregate every child output and retain its automation provenance."""
        project, automations = self._project_automation_records(project_id, viewer=viewer)
        runs = {run.run_id: run for run in self.list_runs()}
        executions: list[dict[str, Any]] = []
        models: list[dict[str, Any]] = []
        reports: list[dict[str, Any]] = []
        for automation in automations:
            provenance = {
                "automation_id": automation.automation_id,
                "automation_name": automation.name,
            }
            for run_id in automation.execution_ids:
                run = runs.get(run_id)
                if run is None:
                    continue
                executions.append({**self._experiment_summary(run), **provenance})
                models.extend(
                    {**model, **provenance} for model in self._model_summaries(run)
                )
                reports.extend(
                    {**report, **provenance} for report in self._report_summaries(run)
                )
        return {
            "project": self._project_payload(project, viewer),
            "data": self.project_data(project_id, viewer=viewer),
            "automations": [item.model_dump(mode="json") for item in automations],
            "executions": executions,
            "models": models,
            "reports": reports,
        }

    @staticmethod
    def _project_state(statuses: list[str], project_status: str) -> str:
        """Collapse a project's execution statuses into one headline state.

        Precedence answers the question the home page asks first -- "does
        anything need me?" -- so live work outranks a waiting prompt, which
        outranks a failure to review, which outranks a finished result. A
        project that never ran but whose start failed before it saved a
        workspace reads as failed too (#82), not as an untouched draft.
        """
        seen = set(statuses)
        if seen & {"running", "queued", "staging", "resuming"}:
            return "running"
        if "awaiting_human" in seen:
            return "awaiting_human"
        if seen & {"interrupted", "failed", "error"} or (
            not statuses and project_status == "error"
        ):
            return "failed"
        if "completed" in seen:
            return "completed"
        return "idle"

    def home_overview(
        self, *, search: str | None = None, viewer: str | None = None
    ) -> dict[str, Any]:
        """Assemble the project-first home: every project's state and the work
        projects have produced, so a person sees what is running, what is
        waiting on them, and can rediscover an output whose project they forgot.

        The global catalogues used to carry the browsing job; #111 removes them
        as destinations, so that job moves here. Search spans project names and
        the labels of recent project-owned models and reports, which is the case
        the catalogues served -- finding a thing whose project is forgotten.
        """
        assert self.project_store is not None
        assert self.automation_store is not None
        query = (search or "").strip().lower()
        runs = {run.run_id: run for run in self.list_runs()}
        owner_of: dict[str, tuple[str, str, str, str]] = {}
        projects: list[dict[str, Any]] = []
        for project in self.project_store.list(viewer=viewer):
            _, automations = self._project_automation_records(
                project.project_id, viewer=viewer
            )
            statuses: list[str] = []
            execution_ids: list[str] = []
            for automation in automations:
                for run_id in automation.execution_ids:
                    execution_ids.append(run_id)
                    owner_of[run_id] = (
                        project.project_id,
                        project.name,
                        automation.automation_id,
                        automation.name,
                    )
                    run = runs.get(run_id)
                    if run is not None:
                        statuses.append(run.status)
            project_status = (
                "error" if any(item.status == "error" for item in automations) else "saved"
            )
            state = self._project_state(statuses, project_status)
            latest_run = next(
                (rid for rid in reversed(execution_ids) if rid in runs),
                None,
            )
            updated_at = max(
                [project.updated_at, *(automation.updated_at for automation in automations)]
            )
            projects.append(
                {
                    "project_id": project.project_id,
                    "name": project.name,
                    "visibility": project.visibility,
                    "mine": project.owner is not None and project.owner == viewer,
                    "status": project_status,
                    "source_id": project.source_ids[0] if project.source_ids else None,
                    "execution_count": len(execution_ids),
                    "updated_at": updated_at.isoformat(),
                    "state": state,
                    "needs_attention": state in {"awaiting_human", "failed"},
                    "latest_run_id": latest_run,
                }
            )
        totals = {
            "projects": len(projects),
            "executions": sum(item["execution_count"] for item in projects),
            "running": sum(item["state"] == "running" for item in projects),
            "awaiting_human": sum(item["state"] == "awaiting_human" for item in projects),
            "failed": sum(item["state"] == "failed" for item in projects),
            "completed": sum(item["state"] == "completed" for item in projects),
        }
        recent: list[dict[str, Any]] = []
        for run in runs.values():
            owner = owner_of.get(run.run_id)
            project_id = owner[0] if owner else None
            project_name = owner[1] if owner else None
            automation_id = owner[2] if owner else None
            automation_name = owner[3] if owner else None
            for model in self._model_summaries(run):
                recent.append(
                    {
                        "kind": "model",
                        "project_id": project_id,
                        "project_name": project_name,
                        "automation_id": automation_id,
                        "automation_name": automation_name,
                        "run_id": run.run_id,
                        "artifact_id": model["artifact_id"],
                        "label": model["display_name"] or "Trained model",
                        "created_at": model["created_at"],
                    }
                )
            for report in self._report_summaries(run):
                recent.append(
                    {
                        "kind": "report",
                        "project_id": project_id,
                        "project_name": project_name,
                        "automation_id": automation_id,
                        "automation_name": automation_name,
                        "run_id": run.run_id,
                        "artifact_id": report["artifact_id"],
                        "label": report["preview"] or "Evaluation report",
                        "created_at": report["created_at"],
                    }
                )
        recent.sort(key=lambda item: item["created_at"] or "", reverse=True)
        if query:
            projects = [item for item in projects if query in item["name"].lower()]
            recent = [
                item
                for item in recent
                if query in item["label"].lower()
                or query in (item["project_name"] or "").lower()
                or query in item["run_id"].lower()
            ]
        # Needs-attention projects first, then most recently touched: the home
        # page leads with what a person has to act on, not an alphabetical wall.
        # Two stable passes -- recency within each attention group.
        projects.sort(key=lambda item: item["updated_at"], reverse=True)
        projects.sort(key=lambda item: not item["needs_attention"])
        return {
            "projects": projects,
            "totals": totals,
            "recent": recent[:20],
        }

    def attach_automation_execution(
        self,
        automation_id: str,
        *,
        run_id: str,
        source_id: str,
    ) -> None:
        assert self.automation_store is not None
        self.automation_store.attach_execution(
            automation_id,
            run_id=run_id,
            source_id=source_id,
        )

    def _sync_automation_workspace(
        self,
        runtime: _RuntimeRun,
        workspace: StagingWorkspace,
        artifact_id: str,
    ) -> None:
        automation_id = runtime.configuration.get("automation_id")
        if not automation_id:
            return
        assert self.automation_store is not None
        self.automation_store.sync_workspace(
            str(automation_id),
            source_id=runtime.source_id,
            workspace_artifact_id=artifact_id,
            pipeline_blueprint=workspace.pipeline_blueprint,
            pipeline_layout=workspace.pipeline_layout,
        )

    def _mark_automation_error(self, runtime: _RuntimeRun) -> None:
        """Move a failed run's automation out of `draft` so the library can tell
        a run that failed apart from one that was never started (#82)."""
        automation_id = runtime.configuration.get("automation_id")
        if not automation_id or self.automation_store is None:
            return
        self.automation_store.mark_error(str(automation_id))

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
        """Explain staged evidence and safely author the executable automation graph."""
        if not message.strip():
            raise ValueError("planner message cannot be empty")
        llm = self.llm_factory() if self.llm_factory else OllamaClient()
        if isinstance(llm, OllamaClient) and not llm.is_available():
            llm.close()
            raise ValueError("Planner chat needs the local Ollama service to be running")

        run_progress: dict[str, Any] | None = None
        if run_id:
            try:
                run_progress = self.progress(run_id)
            except KeyError:
                run_progress = {"run_id": run_id, "status": "unknown"}
        if not source_id and run_progress:
            inferred = run_progress.get("source_id") or run_progress.get("dataset")
            if not inferred and isinstance(run_progress.get("configuration"), dict):
                inferred = run_progress["configuration"].get("source_id")
            source_id = str(inferred) if inferred else None

        safe_source: dict[str, Any] | None = None
        document_context: list[dict[str, Any]] | None = None
        if source_id:
            safe_source = self.source_profile(source_id)
            if safe_source.get("documents"):
                extraction_ref = (
                    self.store.latest(run_id, ArtifactType.DOCUMENT_EXTRACTION) if run_id else None
                )
                if extraction_ref is not None:
                    extraction = self.store.load(extraction_ref.artifact_id, DocumentExtraction)
                    document_context = document_extraction_prompt_context(extraction, message)
                else:
                    documents = load_pdf_directory(self.source_path(source_id))
                    document_context = pdf_prompt_context(documents, message)
        run_context: dict[str, Any] | None = None
        planner_run_seed: int | None = None
        if run_id:
            if run_progress and run_progress.get("status") != "unknown":
                configured_seed = (run_progress.get("configuration") or {}).get("run_seed")
                if configured_seed is not None:
                    planner_run_seed = _coerce_run_seed(configured_seed)
                run_context = {
                    "run_id": run_id,
                    "status": run_progress.get("status"),
                    "current_stage": run_progress.get("current_stage"),
                    "events": run_progress.get("events", []),
                    "gate_decisions": self.gate_decisions(run_id),
                    "attempts": [
                        {
                            "stage_id": item.get("stage_id"),
                            "attempt": item.get("attempt"),
                            "verdict": item.get("verdict"),
                            "error": item.get("error"),
                        }
                        for item in run_progress.get("attempts", [])
                    ],
                }
            else:
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

        gate_action_available = bool(
            (run_context or {}).get("status") == "awaiting_human"
            or ((stage_context or {}).get("human_view") or {}).get("needs_human")
        )

        # The pre-pipeline planner needs one coherent understanding surface,
        # not whichever half of intake/schema discovery happens to be selected
        # in the rail. Both projections are row-free artifact stories.
        understanding_context: dict[str, Any] | None = None
        if run_id and stage_id in {None, "intake", "schema_discovery"}:
            understanding_context = {}
            for understanding_stage in ("intake", "schema_discovery"):
                try:
                    detail = self.stage_detail(run_id, understanding_stage)
                except KeyError:
                    continue
                understanding_context[understanding_stage] = {
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
                    "gate_decisions": detail["gate_decisions"][-2:],
                }

        stored_history: list[dict[str, str]] = []
        automation_context: dict[str, Any] | None = None
        if run_id:
            existing = self._latest_staging_workspace(run_id)
            if existing is not None:
                stored_history = [
                    {"role": item.role, "content": item.content.en}
                    for item in existing.chat_history[-8:]
                ]
                if existing.pipeline_blueprint is not None:
                    automation_context = {
                        "blueprint": existing.pipeline_blueprint.model_dump(mode="json"),
                        "component_outputs": [
                            item.model_dump(mode="json") for item in existing.component_outputs
                        ],
                        "catalog": [
                            {
                                "catalog_id": item.catalog_id,
                                "category": item.category,
                                "kind": item.kind,
                                "title": item.title.en,
                                "inputs": [port.model_dump(mode="json") for port in item.inputs],
                                "outputs": [port.model_dump(mode="json") for port in item.outputs],
                                "default_settings": item.default_settings,
                                "repeatable": item.repeatable,
                            }
                            for item in automation_component_catalog()
                        ],
                    }

        planner_skills = render_skills(select_skills("source_comprehension", []))
        system = (
            "You are the Planner/Orchestrator control layer for a graph-based data and ML "
            "automation workspace. Help a human understand unfamiliar files, build a valid "
            "automation graph, and configure its execution. "
            "Answer direct schema questions first from safe_source_profile: name exact tables "
            "and columns and report measured types, missingness, uniqueness, sensitivity, and "
            "candidate-target flags without inventing values or exposing raw rows. When asked "
            "what to predict or which ML problems are worthwhile, return a ranked, evidence-led "
            "shortlist in problem_recommendations and summarize it in reply. Recommendations "
            "do not modify the graph. Use problem_branches only when the human explicitly asks "
            "to create parallel executable branches. The prompt states gate_action_available; "
            "when it is false, do not steer the human toward approve, retry, continue, stop, or "
            "abort. Discuss a gate action only when it is true and the human asks about that gate. "
            "Be concise, explain trade-offs, and never claim a change was applied unless it is "
            "present in configuration_patch. Configuration keys you may set are: base_table, "
            "base_grain, target_column, task_type, primary_metric, problem_title, "
            "problem_description, excluded_columns, validation_strategy, n_folds, test_size, "
            "group_column, time_column, holdout_cutoff, candidate_limit, instructions. "
            "Valid stages are intake, schema_discovery, integration, problem_discovery, "
            "validation_strategy, eda, leakage_audit, splitting, training, evaluation, report. "
            "Rules such as approval preferences or retry limits belong in rules_to_remember. "
            "Every enforceable change you return is a proposal until the human confirms it in "
            "the UI. Describe it as proposed, never as already applied. "
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
            "Do not expose or ask for raw rows, names, emails, licence numbers, or other PII. "
            "PDF excerpts are local, page-provenanced evidence. Explain them without repeating "
            "sensitive passages. Distinguish extractable text from OCR/vision gaps and never "
            "claim a chart or table became training data unless a deterministic extraction "
            "artifact says so. Cite PDF claims using the supplied file name and page number. "
            "Reply once in the same language as the user's message. Do not emit or request a "
            "translated chat reply. Artifacts remain bilingual: for relationship_explanations "
            "use exact measured table/column "
            "endpoints plus explanation_en/explanation_tr, why_it_matters_en/"
            "why_it_matters_tr and verification_question_en/verification_question_tr. For "
            "reports use title_en/title_tr, summary_en/summary_tr and bilingual finding and "
            "verification-question objects. Set each report's component_id to the enabled "
            "agent.report graph component that produced it. Put bilingual configuration reasons in "
            "plan_rationale. You may author the graph only from the supplied catalog. Use "
            "pipeline_component_additions to instantiate registered catalog entries, "
            "pipeline_connections to connect exact matching typed ports, "
            "pipeline_component_updates to change allowed settings or node control, and "
            "pipeline_component_disables to disable an existing node. A node control may set "
            "execution to auto or pause_after, gate_handler to human or planner, and "
            "max_retries from 0 to 9. Never invent catalog ids, component ids, ports, setting "
            "keys, executable code, or data types. Hard safety gates cannot be weakened. "
            "When the human explicitly asks to create multiple executable ML "
            "branches, return up to three problem_branches. Each branch must use an existing "
            "target column, a compatible task_type and primary_metric. The host expands each "
            "branch into fresh problem, validation, EDA, leakage, feature, split, training, "
            "evaluation and report nodes so agents do not share branch-specific state. Do not "
            "translate identifiers, column names, metrics, or code."
            "\n\n## Runtime data-understanding skills\n" + planner_skills
        )
        prompt = json.dumps(
            {
                "message": message.strip(),
                "configuration": configuration or {},
                "remembered_conversation": ((history or []) + stored_history)[-8:],
                "safe_source_profile": safe_source,
                "local_pdf_context": document_context,
                "intake_and_schema_discovery": understanding_context,
                "run_context": run_context,
                "selected_stage_evidence": stage_context,
                "automation_graph": automation_context,
                "gate_action_available": gate_action_available,
            },
            default=str,
        )
        try:
            planner_profile = LARGE
            if planner_run_seed is not None:
                planner_profile = LARGE.with_seed(
                    derive_agent_seed(
                        planner_run_seed,
                        stage_ordinal=_PLANNER_CHAT_STAGE_ORDINAL,
                        attempt=1,
                        call_ordinal=len((history or []) + stored_history),
                    )
                )
            response = llm.generate_structured(
                system=system,
                prompt=prompt,
                json_schema=_PlannerChatReply.model_json_schema(),
                profile=planner_profile,
            )
            if response.parsed is None:
                # #242: parse_error is the model's problem (often a raw JSON
                # decode offset), not the caller's. Keep it for the logs but
                # hand the client a readable, actionable message via a 502.
                raise PlannerResponseError(
                    "The planner could not produce a usable response: the model "
                    "returned malformed output. Please try again.",
                    cause=response.parse_error,
                )
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
        known_columns = {
            column["name"]
            for table in (safe_source or {}).get("tables", [])
            for column in table.get("columns", [])
        }
        seen_targets: set[str] = set()
        result["problem_recommendations"] = []
        for item in sorted(reply.problem_recommendations, key=lambda candidate: candidate.rank):
            if item.target_column in seen_targets or item.target_column not in known_columns:
                continue
            if Metric(item.primary_metric) not in METRICS_BY_TASK[TaskType(item.task_type)]:
                continue
            result["problem_recommendations"].append(item.model_dump())
            seen_targets.add(item.target_column)
        if run_id:
            workspace = self._latest_staging_workspace(run_id)
            if workspace and workspace.pipeline_blueprint:
                # #193: a refused edit costs its graph change, not the whole
                # turn. The planner still answered, and the answer is often the
                # part that was wanted; the graph simply stays as it was, which
                # is the state that still runs.
                try:
                    updated_blueprint = apply_planner_graph_operations(
                        workspace.pipeline_blueprint,
                        additions=[
                            item.model_dump() for item in reply.pipeline_component_additions
                        ],
                        connections=[item.model_dump() for item in reply.pipeline_connections],
                        updates=result["pipeline_component_updates"],
                        disable_components=result["pipeline_component_disables"],
                        base_revision=workspace.pipeline_blueprint.revision,
                    )
                    valid_branches = [
                        item.model_dump()
                        for item in reply.problem_branches
                        if item.target_column in known_columns
                        and Metric(item.primary_metric) in METRICS_BY_TASK[TaskType(item.task_type)]
                    ]
                    updated_blueprint = add_problem_branches(
                        updated_blueprint, valid_branches, configured_by="planner"
                    )
                    if updated_blueprint.revision > workspace.pipeline_blueprint.revision:
                        patch_ref = self.store.put(
                            GraphPatch(
                                base_revision=workspace.pipeline_blueprint.revision,
                                resulting_revision=updated_blueprint.revision,
                                actor="planner",
                                additions=[
                                    item.model_dump() for item in reply.pipeline_component_additions
                                ],
                                connections=[
                                    item.model_dump()
                                    for item in reply.pipeline_connections
                                ],
                                updates=result["pipeline_component_updates"],
                                disabled_components=result["pipeline_component_disables"],
                                problem_branches=valid_branches,
                            ),
                            run_id=run_id,
                            stage_exec_id="planner-graph-patch",
                            name="graph_patch",
                        )
                        result["graph_patch_artifact_id"] = patch_ref.artifact_id
                    if updated_blueprint.revision > workspace.pipeline_blueprint.revision:
                        result["pipeline_blueprint"] = updated_blueprint.model_dump(mode="json")
                except PlannerGraphEditRejected as exc:
                    result["graph_edit_rejected"] = str(exc)
        proposed_blueprint = (
            PipelineBlueprint.model_validate(result["pipeline_blueprint"])
            if isinstance(result.get("pipeline_blueprint"), dict)
            else None
        )
        pending_override = PlannerOverrideProposal(
            configuration_patch=dict(result["configuration_patch"]),
            stage_directives={
                stage: list(lines) for stage, lines in result["stage_directives"].items()
            },
            checkpoint_stages=list(result["checkpoint_stages"]),
            auto_proceed_stages=list(result["auto_proceed_stages"]),
            max_retries_by_stage=dict(result["max_retries_by_stage"]),
            pipeline_blueprint=proposed_blueprint,
        )
        if not pending_override.has_changes:
            pending_override = None
        else:
            result["override_proposal"] = pending_override.model_dump(mode="json")

        # Directives and supervision change execution, so chat may only stage
        # them. The explicit apply route below is the sole place they reach the
        # runtime or accepted plan (#328).
        applied: dict[str, list[str]] = {}
        if run_id and pending_override is None and result["stage_directives"]:
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
        if run_id and run_id in self._runtime_runs:
            self._persist_staging_workspace(
                self._runtime_runs[run_id],
                planner_result=result,
                user_message=message.strip(),
                pending_override=pending_override,
                preserve_effects=True,
            )
        return result

    @staticmethod
    def _localized(en: Any, tr: Any = None) -> LocalizedText:
        english = str(en or "").strip() or "No explanation was returned."
        turkish = str(tr or "").strip() or english
        return LocalizedText(en=english, tr=turkish)

    def _latest_staging_workspace(self, run_id: str) -> StagingWorkspace | None:
        ref = self.store.latest(run_id, ArtifactType.STAGING_WORKSPACE)
        return self.store.load(ref.artifact_id, StagingWorkspace) if ref else None

    def _staging_report_artifact_ids(self, run_id: str) -> dict[str, list[str]]:
        attached: dict[str, list[str]] = {}
        for report_ref in self.store.list(run_id, artifact_type=ArtifactType.STAGING_REPORT):
            report = self.store.load(report_ref.artifact_id, StagingReportArtifact)
            attached.setdefault(report.producer_component_id, []).append(report_ref.artifact_id)
        return attached

    def staging_workspace(self, run_id: str) -> dict[str, Any]:
        ref = self.store.latest(run_id, ArtifactType.STAGING_WORKSPACE)
        if ref is None:
            raise KeyError(run_id)
        workspace = self.store.load(ref.artifact_id, StagingWorkspace)
        branch_outputs = self._branch_component_outputs(run_id, workspace)
        branch_component_ids = {
            component.id
            for component in (
                workspace.pipeline_blueprint.components if workspace.pipeline_blueprint else []
            )
            if component.branch_id
        }
        projected_outputs = [
            item
            for item in workspace.component_outputs
            if item.component_id not in branch_component_ids
        ]
        projected_outputs.extend(branch_outputs)
        return {
            "artifact_id": ref.artifact_id,
            "run_id": run_id,
            **workspace.model_dump(mode="json", exclude={"component_outputs"}),
            "component_outputs": [item.model_dump(mode="json") for item in projected_outputs],
        }

    def apply_planner_override(self, run_id: str, proposal_id: str) -> dict[str, Any]:
        """Apply exactly one validated Planner proposal after a human click."""
        previous = self._latest_staging_workspace(run_id)
        if previous is None or previous.pending_override is None:
            raise ValueError("there is no pending Planner override")
        pending = previous.pending_override
        if pending.proposal_id != proposal_id:
            raise ValueError("the Planner override changed; review the latest proposal")
        old_plan = previous.recommended_plan or RuntimeConfigurationPlan(
            pipeline_recommendation="create_pipeline"
        )
        configuration = dict(old_plan.configuration)
        configuration.update(pending.configuration_patch)
        if previous.recommended_plan is None:
            for artifact_id in previous.schema_artifact_ids:
                try:
                    integration_plan = self.store.load(artifact_id, IntegrationPlan)
                except Exception:
                    continue
                configuration.setdefault("base_table", integration_plan.base_table)
                configuration.setdefault("base_grain", integration_plan.base_grain)
                break
            configuration.setdefault("candidate_limit", 2)
            configuration.setdefault("n_folds", 5)
        directives = {stage: list(lines) for stage, lines in old_plan.stage_directives.items()}
        for stage, lines in pending.stage_directives.items():
            directives[stage] = [*directives.get(stage, []), *lines]
        checkpoints = set(old_plan.checkpoint_stages)
        checkpoints.update(pending.checkpoint_stages)
        checkpoints.difference_update(pending.auto_proceed_stages)
        auto_proceed = set(old_plan.auto_proceed_stages)
        auto_proceed.update(pending.auto_proceed_stages)
        auto_proceed.difference_update(pending.checkpoint_stages)
        retries = dict(old_plan.max_retries_by_stage)
        retries.update(pending.max_retries_by_stage)
        plan = old_plan.model_copy(
            update={
                "configuration": configuration,
                "stage_directives": directives,
                "checkpoint_stages": sorted(checkpoints),
                "auto_proceed_stages": sorted(auto_proceed),
                "max_retries_by_stage": retries,
            }
        )
        blueprint = pending.pipeline_blueprint or previous.pipeline_blueprint
        if blueprint is None:
            raise ValueError("the proposed workflow has no materializable blueprint")
        blueprint.validate_connections()

        for stage, lines in pending.stage_directives.items():
            for instruction in lines:
                self.direct_stage(run_id, stage, instruction)

        saved = previous.model_copy(
            update={
                "recommended_plan": plan,
                "pipeline_blueprint": blueprint,
                "pending_override": None,
                "component_outputs": self._staging_component_outputs(
                    blueprint,
                    intake_ids=previous.intake_artifact_ids,
                    schema_ids=previous.schema_artifact_ids,
                    document_ids=[
                        artifact_id
                        for output in previous.component_outputs
                        if output.component_id == "understand-documents"
                        for artifact_id in output.artifact_ids
                    ],
                    document_summary=(
                        previous.document_extractions[-1]
                        if previous.document_extractions
                        else None
                    ),
                    reports_ready=bool(previous.reports),
                    report_artifact_ids=self._staging_report_artifact_ids(run_id),
                    plan_ready=True,
                    plan_accepted=self._plan_is_accepted(plan),
                ),
            }
        )
        ref = self.store.put(
            saved,
            run_id=run_id,
            stage_exec_id="planner-override",
            name="data_understanding",
        )
        runtime = self._runtime_runs.get(run_id)
        if runtime is not None:
            self._sync_automation_workspace(runtime, saved, ref.artifact_id)
        return self.staging_workspace(run_id)

    def discard_planner_override(self, run_id: str, proposal_id: str) -> dict[str, Any]:
        """Discard a proposal without changing the accepted plan or runtime."""
        previous = self._latest_staging_workspace(run_id)
        if previous is None or previous.pending_override is None:
            raise ValueError("there is no pending Planner override")
        if previous.pending_override.proposal_id != proposal_id:
            raise ValueError("the Planner override changed; review the latest proposal")
        saved = previous.model_copy(update={"pending_override": None})
        ref = self.store.put(
            saved,
            run_id=run_id,
            stage_exec_id="planner-override-discard",
            name="data_understanding",
        )
        runtime = self._runtime_runs.get(run_id)
        if runtime is not None:
            self._sync_automation_workspace(runtime, saved, ref.artifact_id)
        return self.staging_workspace(run_id)

    def _branch_component_outputs(
        self, parent_run_id: str, workspace: StagingWorkspace
    ) -> list[PipelineOutputReference]:
        if workspace.pipeline_blueprint is None:
            return []
        children = {
            item.branch_label: item
            for item in self.list_runs()
            if item.parent_run_id == parent_run_id and item.branch_label
        }
        output_types: dict[str, set[ArtifactType]] = {
            "agent.define_problem": {ArtifactType.PROBLEM_DEFINITION},
            "agent.plan_validation": {ArtifactType.VALIDATION_STRATEGY},
            "analysis.eda": {ArtifactType.EDA_REPORT, ArtifactType.EXPLORATORY_ANALYSIS},
            "analysis.leakage": {ArtifactType.LEAKAGE_REPORT},
            "data.features": {ArtifactType.FEATURE_SPEC},
            "ml.train": {ArtifactType.TRAINED_MODEL, ArtifactType.MODEL_EXPERIMENT},
            "ml.evaluate": {ArtifactType.EVALUATION_REPORT},
            "report.final": {ArtifactType.FINAL_REPORT},
        }
        projected: list[PipelineOutputReference] = []
        for component in workspace.pipeline_blueprint.components:
            if not component.branch_id:
                continue
            child = children.get(component.branch_id)
            refs = self.store.list(child.run_id) if child else []
            allowed_types = output_types.get(component.catalog_id or "", set())
            artifact_ids = [
                item.artifact_id for item in refs if item.artifact_type in allowed_types
            ]
            if artifact_ids:
                status = "ready"
            elif child and child.status in {"failed", "aborted"}:
                status = "unavailable"
            elif child:
                status = "pending"
            else:
                status = "not_started"
            for port in component.outputs:
                projected.append(
                    PipelineOutputReference(
                        component_id=component.id,
                        port_id=port.id,
                        data_type=port.data_type,
                        status=status,
                        artifact_ids=artifact_ids,
                        summary=LocalizedText(
                            en=f"Independent branch {component.branch_id}: "
                            f"{status.replace('_', ' ')}.",
                            tr=(f"Bağımsız dal {component.branch_id}: {status.replace('_', ' ')}."),
                        ),
                    )
                )
        return projected

    def compile_automation(self, run_id: str) -> dict[str, Any]:
        """Freeze the saved visual graph into an immutable execution plan."""
        workspace_ref = self.store.latest(run_id, ArtifactType.STAGING_WORKSPACE)
        if workspace_ref is None:
            raise KeyError(run_id)
        workspace = self.store.load(workspace_ref.artifact_id, StagingWorkspace)
        if workspace.pipeline_blueprint is None:
            raise ValueError("the staging workspace has no automation graph")
        plan = compile_automation_plan(workspace.pipeline_blueprint)
        plan_ref = self.store.put(
            plan,
            run_id=run_id,
            stage_exec_id="automation",
            name="execution_plan",
        )
        runtime = self._runtime_runs.get(run_id)
        if runtime is not None:
            runtime.configuration.update(
                {
                    "automation_execution_plan_id": plan_ref.artifact_id,
                    "automation_blueprint_fingerprint": plan.blueprint_fingerprint,
                    "pipeline_blueprint": workspace.pipeline_blueprint.model_dump(mode="json"),
                }
            )
            runtime.updated_at = _now()
            self._persist_runtime(runtime)
        return {
            "artifact_id": plan_ref.artifact_id,
            "workspace_artifact_id": workspace_ref.artifact_id,
            "plan": plan.model_dump(mode="json"),
        }

    def start_automation_branches(self, run_id: str) -> list[dict[str, str]]:
        """Launch each configured ML problem branch as an isolated agent run."""
        workspace = self._latest_staging_workspace(run_id)
        if workspace is None or workspace.pipeline_blueprint is None:
            raise KeyError(run_id)
        validate_executable_blueprint(workspace.pipeline_blueprint)
        integration_plan: IntegrationPlan | None = None
        for artifact_id in workspace.schema_artifact_ids:
            try:
                integration_plan = self.store.load(artifact_id, IntegrationPlan)
                break
            except (KeyError, TypeError, ValueError):
                continue
        if integration_plan is None:
            raise ValueError("problem branches need a completed integration plan")

        plan = workspace.recommended_plan
        supervision = (
            {
                "checkpoint_stages": plan.checkpoint_stages,
                "auto_proceed_stages": plan.auto_proceed_stages,
                "max_retries_by_stage": plan.max_retries_by_stage,
            }
            if plan
            else {}
        )
        branch_runs: list[dict[str, str]] = []
        problem_nodes = [
            item
            for item in workspace.pipeline_blueprint.components
            if item.enabled and item.branch_id and item.catalog_id == "agent.define_problem"
        ]
        for node in sorted(problem_nodes, key=lambda item: item.branch_id or ""):
            branch_id = str(node.branch_id)
            settings = node.settings
            task_type = TaskType(str(settings.get("task_type")))
            metric = Metric(str(settings.get("primary_metric")))
            if metric not in METRICS_BY_TASK[task_type]:
                raise ValueError(f"metric {metric.value!r} is incompatible with {task_type.value}")
            problem = ProblemDefinition(
                task_type=task_type,
                target_column=str(settings.get("target_column") or "") or None,
                primary_metric=metric,
                title=str(settings.get("problem_title") or branch_id),
                description=(
                    f"Independent automation branch {branch_id!r}, configured before execution."
                ),
                excluded_columns=list(
                    (plan.configuration.get("excluded_columns") if plan else []) or []
                ),
                confirmed_by="auto",
            )
            validation_node = next(
                (
                    item
                    for item in workspace.pipeline_blueprint.components
                    if item.enabled
                    and item.branch_id == branch_id
                    and item.catalog_id == "agent.plan_validation"
                ),
                None,
            )
            n_folds = int(
                (validation_node.settings.get("n_folds") if validation_node else None)
                or (plan.configuration.get("n_folds") if plan else 5)
                or 5
            )
            validation = ValidationStrategy(
                strategy=SplitStrategy.STRATIFIED,
                n_folds=n_folds,
                test_size=0.2,
                rationale="Initial branch preference; the branch agent must validate it.",
            )
            directives = (
                "\n".join(
                    f"{stage}: {line}"
                    for stage, lines in plan.stage_directives.items()
                    for line in lines
                )
                if plan
                else None
            )
            child_run_id = self.start_run(
                source_id=workspace.source_id,
                integration_plan=integration_plan,
                problem=problem,
                validation_strategy=validation,
                candidate_limit=int(plan.configuration.get("candidate_limit", 2)) if plan else 2,
                instructions=directives,
                mode="agent",
                supervision=supervision,
                agent_panel_size=1,
                run_mode="fully_auto",
                parent_run_id=run_id,
                branch_label=branch_id,
            )
            branch_runs.append({"branch_id": branch_id, "run_id": child_run_id})
        if not branch_runs:
            raise ValueError("the automation graph has no enabled ML problem branches")
        return branch_runs

    def default_staging_pipeline(self, source_id: str) -> dict[str, Any]:
        profile = self.source_profile(source_id)
        return build_default_blueprint(
            has_tables=bool(profile.get("tables")),
            has_documents=bool(profile.get("documents")),
        ).model_dump(mode="json")

    def update_staging_pipeline(
        self,
        run_id: str,
        raw_blueprint: dict[str, Any],
        *,
        base_artifact_id: str,
    ) -> dict[str, Any]:
        """Persist a human-edited component graph as a new immutable snapshot."""
        runtime = self._runtime_runs.get(run_id)
        if runtime is None or runtime.status not in {"staging", "staged"}:
            raise _cannot_stage_error(runtime)
        latest_ref = self.store.latest(run_id, ArtifactType.STAGING_WORKSPACE)
        if latest_ref is None:
            raise ValueError("the staging workspace is not ready")
        if latest_ref.artifact_id != base_artifact_id:
            raise StagingWorkspaceConflict(
                "the staging workspace changed; reload before saving this pipeline"
            )
        previous = self.store.load(latest_ref.artifact_id, StagingWorkspace)
        if previous.pipeline_blueprint is None:
            raise ValueError("the staging pipeline is not ready")
        candidate = PipelineBlueprint.model_validate(raw_blueprint)
        candidate.validate_connections()
        blueprint = self._validate_human_blueprint(previous.pipeline_blueprint, candidate)
        previous_document_ids = list(
            dict.fromkeys(
                artifact_id
                for output in previous.component_outputs
                if output.component_id == "understand-documents"
                for artifact_id in output.artifact_ids
            )
        )
        previous_document_summary = (
            previous.document_extractions[-1] if previous.document_extractions else None
        )
        saved = previous.model_copy(
            update={
                "pipeline_blueprint": blueprint,
                "component_outputs": self._staging_component_outputs(
                    blueprint,
                    intake_ids=previous.intake_artifact_ids,
                    schema_ids=previous.schema_artifact_ids,
                    document_ids=previous_document_ids,
                    document_summary=previous_document_summary,
                    reports_ready=bool(previous.reports),
                    report_artifact_ids=self._staging_report_artifact_ids(run_id),
                    plan_ready=previous.recommended_plan is not None,
                    plan_accepted=self._plan_is_accepted(previous.recommended_plan),
                ),
            }
        )
        ref = self.store.put(
            saved,
            run_id=run_id,
            stage_exec_id="staging",
            name="data_understanding",
        )
        runtime.configuration["pipeline_blueprint"] = blueprint.model_dump(mode="json")
        runtime.updated_at = _now()
        self._persist_runtime(runtime)
        self._sync_automation_workspace(runtime, saved, ref.artifact_id)
        return {"artifact_id": ref.artifact_id, **saved.model_dump(mode="json")}

    def update_staging_layout(
        self,
        run_id: str,
        raw_layout: dict[str, Any],
        *,
        base_artifact_id: str,
    ) -> dict[str, Any]:
        """Persist UI layout without changing graph semantics or its fingerprint."""
        latest_ref = self.store.latest(run_id, ArtifactType.STAGING_WORKSPACE)
        if latest_ref is None:
            raise ValueError("the staging workspace is not ready")
        if latest_ref.artifact_id != base_artifact_id:
            raise StagingWorkspaceConflict(
                "the staging workspace changed; reload before saving layout"
            )
        previous = self.store.load(latest_ref.artifact_id, StagingWorkspace)
        layout = PipelineLayout.model_validate(raw_layout)
        component_ids = {
            component.id
            for component in (
                previous.pipeline_blueprint.components if previous.pipeline_blueprint else []
            )
        }
        layout_ids = [node.component_id for node in layout.nodes]
        if len(layout_ids) != len(set(layout_ids)):
            raise ValueError("pipeline layout component ids must be unique")
        unknown = sorted(set(layout_ids) - component_ids)
        if unknown:
            raise ValueError(f"pipeline layout references unknown components: {unknown}")
        saved = previous.model_copy(update={"pipeline_layout": layout})
        ref = self.store.put(
            saved,
            run_id=run_id,
            stage_exec_id="staging-layout",
            name="data_understanding",
        )
        runtime = self._runtime_runs.get(run_id)
        if runtime is not None:
            self._sync_automation_workspace(runtime, saved, ref.artifact_id)
        return {"artifact_id": ref.artifact_id, **saved.model_dump(mode="json")}

    @staticmethod
    def _plan_is_accepted(plan: RuntimeConfigurationPlan | None) -> bool:
        return bool(plan and (plan.status == "accepted" or plan.accepted))

    def accept_staging_plan(
        self,
        run_id: str,
        *,
        base_artifact_id: str,
    ) -> dict[str, Any]:
        """Freeze a human-approved proposal and compile its exact saved blueprint."""
        latest_ref = self.store.latest(run_id, ArtifactType.STAGING_WORKSPACE)
        if latest_ref is None:
            raise ValueError("the staging workspace is not ready")
        if latest_ref.artifact_id != base_artifact_id:
            raise StagingWorkspaceConflict(
                "the staging workspace changed; review the latest proposal"
            )
        previous = self.store.load(latest_ref.artifact_id, StagingWorkspace)
        if previous.recommended_plan is None:
            raise ValueError("the Planner has not proposed a workflow")
        if previous.pipeline_blueprint is None:
            raise ValueError("the proposed workflow has no materializable blueprint")
        validate_executable_blueprint(previous.pipeline_blueprint)
        accepted_plan = previous.recommended_plan.model_copy(
            update={
                "status": "accepted",
                "accepted": True,
                "accepted_at": datetime.now(UTC),
                "accepted_by": "human",
            }
        )
        saved = previous.model_copy(
            update={
                "recommended_plan": accepted_plan,
                "component_outputs": self._staging_component_outputs(
                    previous.pipeline_blueprint,
                    intake_ids=previous.intake_artifact_ids,
                    schema_ids=previous.schema_artifact_ids,
                    document_ids=[
                        artifact_id
                        for output in previous.component_outputs
                        if output.component_id == "understand-documents"
                        for artifact_id in output.artifact_ids
                    ],
                    document_summary=(
                        previous.document_extractions[-1] if previous.document_extractions else None
                    ),
                    reports_ready=bool(previous.reports),
                    report_artifact_ids=self._staging_report_artifact_ids(run_id),
                    plan_ready=True,
                    plan_accepted=True,
                ),
            }
        )
        accepted_ref = self.store.put(
            saved,
            run_id=run_id,
            stage_exec_id="plan-acceptance",
            name="data_understanding",
        )
        runtime = self._runtime_runs.get(run_id)
        if runtime is not None:
            self._sync_automation_workspace(runtime, saved, accepted_ref.artifact_id)
        compiled = self.compile_automation(run_id)
        return {
            "artifact_id": accepted_ref.artifact_id,
            # #166: StagingWorkspace has no run_id field, and staging_workspace()
            # injects one into its response while this path did not. The editor
            # reads next.run_id after accepting to keep the run selected; without
            # it the URL became run=undefined and the screen fell back to the
            # empty upload state -- the "accepting the plan lands somewhere
            # unrelated" defect. Return the same run_id the workspace read did.
            "run_id": run_id,
            **saved.model_dump(mode="json"),
            "execution_plan_artifact_id": compiled["artifact_id"],
        }

    @staticmethod
    def _validate_human_blueprint(
        baseline: PipelineBlueprint,
        candidate: PipelineBlueprint,
    ) -> PipelineBlueprint:
        """Accept graph composition while keeping component capabilities host-owned."""
        baseline_by_id = {component.id: component for component in baseline.components}
        candidate_by_id = {component.id: component for component in candidate.components}
        if "data-source" not in candidate_by_id:
            raise ValueError("the uploaded-data source component cannot be removed")
        if sum(component.kind == "data_source" for component in candidate.components) != 1:
            raise ValueError("an automation must contain exactly one uploaded-data source")

        structural_fields = (
            "kind",
            "title",
            "description",
            "inputs",
            "outputs",
            "optional",
            "evidence_layer",
            "catalog_id",
            "branch_id",
            "group_id",
        )
        existing_updates = {
            component.id: {
                "enabled": component.enabled,
                "settings": component.settings,
                "control": component.control,
            }
            for component in candidate.components
            if component.id in baseline_by_id
        }
        validated_existing = apply_component_updates(
            baseline,
            existing_updates,
            configured_by="human",
        )
        validated_by_id = {component.id: component for component in validated_existing.components}

        for component in candidate.components:
            original = baseline_by_id.get(component.id)
            if original is not None:
                for field_name in structural_fields:
                    if getattr(component, field_name) != getattr(original, field_name):
                        raise ValueError(
                            f"component capability {component.id}.{field_name} cannot be changed"
                        )
                continue

            if component.catalog_id is None:
                raise ValueError(f"new component {component.id!r} must come from the catalog")
            canonical = instantiate_component(
                component.catalog_id,
                component.id,
                branch_id=component.branch_id,
                group_id=component.group_id,
                configured_by="human",
            )
            for field_name in structural_fields:
                if getattr(component, field_name) != getattr(canonical, field_name):
                    raise ValueError(
                        f"new component {component.id!r} does not match its catalog definition"
                    )
            one_node = PipelineBlueprint(
                name=candidate.name,
                components=[canonical],
                connections=[],
            )
            validated_new = apply_component_updates(
                one_node,
                {
                    component.id: {
                        "enabled": component.enabled,
                        "settings": component.settings,
                        "control": component.control,
                    }
                },
                configured_by="human",
            )
            validated_by_id[component.id] = validated_new.components[0]

        validated = candidate.model_copy(
            update={
                "components": [validated_by_id[item.id] for item in candidate.components],
                "revision": baseline.revision + 1,
            }
        )
        validated.validate_connections()
        return validated

    def run_document_understanding(self, run_id: str) -> dict[str, Any]:
        """Execute the selected document node and persist its normalized outputs."""
        runtime = self._runtime_runs.get(run_id)
        if runtime is None or runtime.status not in {"staging", "staged"}:
            raise ValueError("this run is not available for document understanding")
        self._execute_document_understanding(runtime)
        return self.staging_workspace(run_id)

    def review_document_tables(self, run_id: str, decisions: dict[str, str]) -> dict[str, Any]:
        """Persist human candidate decisions without promoting any values yet."""
        extraction_ref = self.store.latest(run_id, ArtifactType.DOCUMENT_EXTRACTION)
        if extraction_ref is None:
            raise ValueError("this run has no document extraction to review")
        extraction = self.store.load(extraction_ref.artifact_id, DocumentExtraction)
        review = create_document_table_review(
            extraction,
            extraction_artifact_id=extraction_ref.artifact_id,
            decisions=decisions,
        )
        reference = self.store.put(
            review,
            run_id=run_id,
            stage_exec_id="document-table-review",
        )
        return {"artifact_id": reference.artifact_id, **review.summary()}

    def promote_document_tables(self, run_id: str, review_artifact_id: str) -> dict[str, Any]:
        """Promote only accepted reviewed candidates into immutable TableAssets."""
        run_artifacts = {reference.artifact_id for reference in self.store.list(run_id)}
        if review_artifact_id not in run_artifacts:
            raise ValueError("document review artifact does not belong to this run")
        review = self.store.load(review_artifact_id, DocumentTableReview)
        promoted = promote_reviewed_document_tables(
            self.store,
            run_id=run_id,
            review=review,
        )
        # #361: promotion wrote table assets and left every reader describing
        # the state before it. The plan panel reads the staging workspace and
        # the source profile, and the profile is a walk of the uploaded files --
        # a promoted table is not a file, so nothing there could ever change.
        # Record the promotion on the workspace, which the client already
        # re-reads on this callback.
        self._record_promoted_document_tables(run_id, review, promoted)
        return {
            "review_artifact_id": review_artifact_id,
            "table_assets": [
                {
                    "artifact_id": reference.artifact_id,
                    "rows": asset.row_count,
                    "columns": asset.column_count,
                    "fingerprint": asset.fingerprint,
                    "provenance": asset.provenance.model_dump(mode="json"),
                }
                for asset, reference in promoted
            ],
        }

    def _record_promoted_document_tables(
        self,
        run_id: str,
        review: DocumentTableReview,
        promoted: list[tuple[TableAsset, ArtifactRef]],
    ) -> None:
        """Write the promotion onto the staging workspace so readers can see it.

        ``promote_reviewed_document_tables`` walks ``review.decisions`` in order
        and skips everything that is not accepted, so zipping the accepted
        decisions against its result pairs each asset with the candidate whose
        provenance it already verified.
        """
        previous = self._latest_staging_workspace(run_id)
        if previous is None:
            return
        accepted = [item for item in review.decisions if item.decision == "accepted"]
        already = {item.candidate_id for item in previous.promoted_document_tables}
        additions = [
            PromotedDocumentTable(
                candidate_id=decision.candidate_id,
                artifact_id=reference.artifact_id,
                source_file=decision.source_file,
                page_number=decision.page_number,
                row_count=asset.row_count,
                column_count=asset.column_count,
            )
            for decision, (asset, reference) in zip(accepted, promoted, strict=True)
            if decision.candidate_id not in already
        ]
        if not additions:
            return
        saved = previous.model_copy(
            update={
                "promoted_document_tables": [*previous.promoted_document_tables, *additions],
            }
        )
        reference = self.store.put(
            saved,
            run_id=run_id,
            stage_exec_id="document-table-promotion",
            name="data_understanding",
        )
        runtime = self._runtime_runs.get(run_id)
        if runtime is not None:
            self._sync_automation_workspace(runtime, saved, reference.artifact_id)

    def _measured_relationship_explanations(self, source_id: str) -> list[RelationshipExplanation]:
        explanations: list[RelationshipExplanation] = []
        for item in self.source_profile(source_id).get("relationships", []):
            left = ", ".join(item["from_columns"])
            right = ", ".join(item["to_columns"])
            overlap = float(item["overlap_rate"])
            orphan = float(item["orphan_rate"])
            explanations.append(
                RelationshipExplanation(
                    from_table=item["from_table"],
                    from_columns=list(item["from_columns"]),
                    to_table=item["to_table"],
                    to_columns=list(item["to_columns"]),
                    cardinality=str(item["cardinality"]),
                    overlap_rate=overlap,
                    orphan_rate=orphan,
                    explanation=LocalizedText(
                        en=(
                            f"{item['from_table']}.{left} overlaps {item['to_table']}.{right} "
                            f"for {overlap:.1%} of dependent rows."
                        ),
                        tr=(
                            f"{item['from_table']}.{left}, bağımlı satırların %{overlap * 100:.1f} "
                            f"kadarı için {item['to_table']}.{right} ile örtüşüyor."
                        ),
                    ),
                    why_it_matters=LocalizedText(
                        en=f"A join would leave {orphan:.1%} of dependent rows unmatched.",
                        tr=(
                            f"Birleştirme, bağımlı satırların %{orphan * 100:.1f} kadarını "
                            "eşleşmeden bırakır."
                        ),
                    ),
                    verification_question=LocalizedText(
                        en="Do these columns represent the same business identifier?",
                        tr="Bu sütunlar aynı iş tanımlayıcısını mı temsil ediyor?",
                    ),
                )
            )
        return explanations

    def _document_tables_await_review(self, run_id: str) -> bool:
        """Whether extracted table candidates are still waiting on a human (#316).

        A recorded review settles the question whichever way it went -- rejecting
        every candidate is a completed decision, not a pending one -- so this
        asks whether one exists, not whether anything was promoted.
        """
        reference = self.store.latest(run_id, ArtifactType.DOCUMENT_EXTRACTION)
        if reference is None:
            return False
        try:
            extraction = self.store.load(reference.artifact_id, DocumentExtraction)
        except Exception:
            return False
        if not any(document.tables for document in extraction.documents):
            return False
        return self.store.latest(run_id, ArtifactType.DOCUMENT_TABLE_REVIEW) is None

    def _persist_staging_workspace(
        self,
        runtime: _RuntimeRun,
        *,
        planner_result: dict[str, Any] | None = None,
        user_message: str | None = None,
        planner_error: str | None = None,
        pending_override: PlannerOverrideProposal | None = None,
        preserve_effects: bool = False,
    ) -> StagingWorkspace:
        """Append an immutable snapshot of staging evidence, chat, and plan."""
        previous = self._latest_staging_workspace(runtime.run_id)
        pending = previous.pending_override if previous else None
        if pending_override is not None:
            pending = pending_override
        intake_ids = [
            artifact_id
            for attempt in runtime.state.attempts
            if attempt.stage_id == "intake"
            for artifact_id in attempt.artifact_ids
        ]
        schema_ids = [
            artifact_id
            for attempt in runtime.state.attempts
            if attempt.stage_id == "schema_discovery"
            for artifact_id in attempt.artifact_ids
        ]
        relationships = (
            list(previous.relationship_explanations)
            if previous
            else self._measured_relationship_explanations(runtime.source_id)
        )
        reports = list(previous.reports) if previous else []
        report_artifact_ids = self._staging_report_artifact_ids(runtime.run_id)
        history = list(previous.chat_history) if previous else []
        plan = previous.recommended_plan if previous else None
        model = previous.planner_model if previous else None
        profile = self.source_profile(runtime.source_id)
        document_ref = self.store.latest(runtime.run_id, ArtifactType.DOCUMENT_EXTRACTION)
        document_ids: list[str] = []
        document_summaries: list[DocumentExtractionSummary] = []
        document_summary: DocumentExtractionSummary | None = None
        if document_ref is not None:
            extraction = self.store.load(document_ref.artifact_id, DocumentExtraction)
            document_ids = [document_ref.artifact_id]
            document_summary = extraction.extraction_summary().model_copy(
                update={"artifact_id": document_ref.artifact_id}
            )
            document_summaries = [document_summary]
        elif runtime.configuration.get("document_extraction_error"):
            selected_engine = str(
                runtime.configuration.get("document_extraction_engine") or "docling"
            )
            document_summary = DocumentExtractionSummary(
                engine=selected_engine,
                ocr_mode=str(runtime.configuration.get("document_extraction_ocr") or "auto"),
                status="failed",
                document_count=0,
                page_count=0,
                text_characters=0,
                table_candidates=0,
                figure_candidates=0,
                duration_seconds=float(
                    runtime.configuration.get("document_extraction_duration_seconds") or 0.0
                ),
                warnings=[str(runtime.configuration["document_extraction_error"])[:1_000]],
            )
            document_summaries = [document_summary]
        configured_blueprint = runtime.configuration.get("pipeline_blueprint")
        blueprint = previous.pipeline_blueprint if previous else None
        if blueprint is None and isinstance(configured_blueprint, dict):
            configured = PipelineBlueprint.model_validate(configured_blueprint)
            configured.validate_connections()
            default = build_default_blueprint(
                has_tables=bool(profile.get("tables")),
                has_documents=bool(profile.get("documents")),
            )
            blueprint = self._validate_human_blueprint(default, configured)
        if blueprint is None:
            blueprint = build_default_blueprint(
                has_tables=bool(profile.get("tables")),
                has_documents=bool(profile.get("documents")),
            )

        if planner_result is not None:
            measured = {
                (
                    item.from_table,
                    tuple(item.from_columns),
                    item.to_table,
                    tuple(item.to_columns),
                ): item
                for item in relationships
            }
            interpreted: list[RelationshipExplanation] = []
            for raw in planner_result.get("relationship_explanations", []):
                key = (
                    str(raw.get("from_table", "")),
                    tuple(raw.get("from_columns") or []),
                    str(raw.get("to_table", "")),
                    tuple(raw.get("to_columns") or []),
                )
                fact = measured.get(key)
                if fact is None:
                    continue
                interpreted.append(
                    fact.model_copy(
                        update={
                            "explanation": self._localized(
                                raw.get("explanation_en"), raw.get("explanation_tr")
                            ),
                            "why_it_matters": self._localized(
                                raw.get("why_it_matters_en"), raw.get("why_it_matters_tr")
                            ),
                            "verification_question": self._localized(
                                raw.get("verification_question_en"),
                                raw.get("verification_question_tr"),
                            ),
                        }
                    )
                )
            if interpreted:
                explained_keys = {
                    (x.from_table, tuple(x.from_columns), x.to_table, tuple(x.to_columns))
                    for x in interpreted
                }
                relationships = interpreted + [
                    item for key, item in measured.items() if key not in explained_keys
                ]

            parsed_reports: list[StagingReport] = []
            report_components = {
                item.id for item in blueprint.components if item.enabled and item.kind == "report"
            }
            for raw in planner_result.get("reports", []):
                if not raw.get("title_en") or not raw.get("summary_en"):
                    continue
                parsed = StagingReport(
                    title=self._localized(raw.get("title_en"), raw.get("title_tr")),
                    summary=self._localized(raw.get("summary_en"), raw.get("summary_tr")),
                    findings=[
                        self._localized(item.get("en"), item.get("tr"))
                        for item in raw.get("findings", [])
                        if isinstance(item, dict) and item.get("en")
                    ],
                    verification_questions=[
                        self._localized(item.get("en"), item.get("tr"))
                        for item in raw.get("verification_questions", [])
                        if isinstance(item, dict) and item.get("en")
                    ],
                )
                parsed_reports.append(parsed)
                if not report_components:
                    continue
                producer = str(raw.get("component_id") or "understanding-synthesis")
                if producer not in report_components:
                    producer = (
                        "understanding-synthesis"
                        if "understanding-synthesis" in report_components
                        else sorted(report_components)[0]
                    )
                report_ref = self.store.put(
                    StagingReportArtifact(
                        producer_component_id=producer,
                        title=parsed.title,
                        summary_text=parsed.summary,
                        findings=parsed.findings,
                        verification_questions=parsed.verification_questions,
                    ),
                    run_id=runtime.run_id,
                    stage_exec_id="staging-reports",
                    name=producer,
                )
                report_artifact_ids.setdefault(producer, []).append(report_ref.artifact_id)
            if parsed_reports:
                reports = parsed_reports

            plan_configuration = dict(planner_result.get("configuration_patch") or {})
            if not (
                isinstance(plan_configuration.get("base_grain"), list)
                and all(
                    isinstance(item, str) and item.strip()
                    for item in plan_configuration["base_grain"]
                )
            ):
                plan_configuration.pop("base_grain", None)
            if plan_configuration.get("validation_strategy") not in {
                item.value for item in SplitStrategy
            }:
                plan_configuration.pop("validation_strategy", None)
            if plan_configuration.get("task_type") not in {item.value for item in TaskType}:
                plan_configuration.pop("task_type", None)
            pipeline_recommendation = _resolved_pipeline_recommendation(
                planner_result.get("pipeline_recommendation"),
                plan,
                self._document_tables_await_review(runtime.run_id),
            )
            if pipeline_recommendation == "create_pipeline":
                for artifact_id in schema_ids:
                    try:
                        integration_plan = self.store.load(artifact_id, IntegrationPlan)
                    except Exception:
                        continue
                    plan_configuration.setdefault("base_table", integration_plan.base_table)
                    plan_configuration.setdefault("base_grain", integration_plan.base_grain)
                    break
                plan_configuration.setdefault("candidate_limit", 2)
                plan_configuration.setdefault("n_folds", 5)
            else:
                plan_configuration = {}
            plan = RuntimeConfigurationPlan(
                pipeline_recommendation=pipeline_recommendation,
                decision_summary=(
                    self._localized(
                        (planner_result.get("decision_summary") or {}).get("en"),
                        (planner_result.get("decision_summary") or {}).get("tr"),
                    )
                    if isinstance(planner_result.get("decision_summary"), dict)
                    else None
                ),
                configuration=plan_configuration,
                stage_directives={
                    key: list(value)
                    for key, value in (planner_result.get("stage_directives") or {}).items()
                },
                checkpoint_stages=list(planner_result.get("checkpoint_stages") or []),
                auto_proceed_stages=list(planner_result.get("auto_proceed_stages") or []),
                max_retries_by_stage=dict(planner_result.get("max_retries_by_stage") or {}),
                rationale=[
                    self._localized(item.get("en"), item.get("tr"))
                    for item in planner_result.get("plan_rationale", [])
                    if isinstance(item, dict) and item.get("en")
                ],
            )
            model = str(planner_result.get("model") or "") or model
            raw_blueprint = planner_result.get("pipeline_blueprint")
            if isinstance(raw_blueprint, dict):
                blueprint = PipelineBlueprint.model_validate(raw_blueprint)
                blueprint.validate_connections()
            if user_message:
                history.append(
                    StagingMessage(
                        role="user",
                        content=LocalizedText(en=user_message, tr=user_message),
                    )
                )
            history.append(
                StagingMessage(
                    role="planner",
                    content=self._localized(
                        planner_result.get("reply"), planner_result.get("reply")
                    ),
                    model=model,
                )
            )

        if preserve_effects:
            plan = previous.recommended_plan if previous else None
            if previous and previous.pipeline_blueprint is not None:
                blueprint = previous.pipeline_blueprint

        workspace = StagingWorkspace(
            source_id=runtime.source_id,
            source_fingerprint=self._source_fingerprint(runtime.source_id),
            intake_artifact_ids=intake_ids,
            schema_artifact_ids=schema_ids,
            cache_reused=bool(runtime.configuration.get("reuse_cache", False)),
            relationship_explanations=relationships,
            reports=reports,
            pipeline_blueprint=blueprint,
            pipeline_layout=(previous.pipeline_layout if previous else PipelineLayout()),
            component_outputs=self._staging_component_outputs(
                blueprint,
                intake_ids=intake_ids,
                schema_ids=schema_ids,
                document_ids=document_ids,
                document_summary=document_summary,
                reports_ready=bool(reports),
                report_artifact_ids=report_artifact_ids,
                plan_ready=plan is not None,
                plan_accepted=self._plan_is_accepted(plan),
            ),
            document_extractions=document_summaries,
            recommended_plan=plan,
            pending_override=pending,
            chat_history=history,
            planner_model=model,
            planner_error=planner_error,
        )
        ref = self.store.put(
            workspace,
            run_id=runtime.run_id,
            stage_exec_id="staging",
            name="data_understanding",
        )
        self._sync_automation_workspace(runtime, workspace, ref.artifact_id)
        return workspace

    @staticmethod
    def _staging_component_outputs(
        blueprint: PipelineBlueprint,
        *,
        intake_ids: list[str],
        schema_ids: list[str],
        document_ids: list[str],
        document_summary: DocumentExtractionSummary | None,
        reports_ready: bool,
        report_artifact_ids: dict[str, list[str]],
        plan_ready: bool,
        plan_accepted: bool,
    ) -> list[PipelineOutputReference]:
        """Project artifact lineage onto exact component output ports."""
        ready: dict[tuple[str, str], tuple[str, list[str], LocalizedText]] = {
            ("data-source", "structured_files"): (
                "ready",
                [],
                LocalizedText(
                    en="Uploaded structured files are available.",
                    tr="Yüklenen yapısal dosyalar hazır.",
                ),
            ),
            ("data-source", "documents"): (
                "ready",
                [],
                LocalizedText(
                    en="Uploaded documents are available.", tr="Yüklenen belgeler hazır."
                ),
            ),
            ("intake", "table_profiles"): (
                "ready" if intake_ids else "pending",
                intake_ids,
                LocalizedText(en="Measured table profiles.", tr="Ölçülen tablo profilleri."),
            ),
            ("schema-discovery", "relationship_graph"): (
                "ready" if schema_ids else "pending",
                schema_ids,
                LocalizedText(en="Measured relationship evidence.", tr="Ölçülen ilişki kanıtı."),
            ),
        }
        for report_component in (
            "structured-brief",
            "document-brief",
            "understanding-synthesis",
        ):
            ready[(report_component, "reports")] = (
                "ready" if reports_ready else "pending",
                report_artifact_ids.get(report_component, []),
                LocalizedText(
                    en=(
                        "Agent-created understanding reports and runtime recommendations."
                        if plan_ready
                        else "Agent-created understanding reports."
                    ),
                    tr=(
                        "Ajan tarafından oluşturulan anlama raporları ve çalışma önerileri."
                        if plan_ready
                        else "Ajan tarafından oluşturulan anlama raporları."
                    ),
                ),
            )
        outputs: list[PipelineOutputReference] = []
        for component in blueprint.components:
            for port in component.outputs:
                status, artifact_ids, summary = ready.get(
                    (component.id, port.id),
                    (
                        "not_started" if component.enabled else "unavailable",
                        [],
                        LocalizedText(
                            en="This output has not been created yet.",
                            tr="Bu çıktı henüz oluşturulmadı.",
                        ),
                    ),
                )
                if component.id == "understand-documents" and component.enabled:
                    engine = str(component.settings.get("engine", "docling"))
                    available = next(
                        (
                            item["available"]
                            for item in document_engine_catalog()
                            if item["id"] == engine
                        ),
                        False,
                    )
                    if document_summary is not None:
                        status = "ready" if document_summary.status == "ready" else "unavailable"
                        artifact_ids = document_ids
                        if document_summary.status == "ready":
                            summary_en = (
                                f"{engine} produced {document_summary.table_candidates} table and "
                                f"{document_summary.figure_candidates} figure candidates."
                            )
                            summary_tr = (
                                f"{engine}, {document_summary.table_candidates} tablo ve "
                                f"{document_summary.figure_candidates} şekil adayı üretti."
                            )
                        else:
                            summary_en = (
                                f"{engine} failed; inspect the document output and try an "
                                "alternative."
                            )
                            summary_tr = (
                                f"{engine} başarısız oldu; belge çıktısını inceleyip alternatif "
                                "deneyin."
                            )
                    else:
                        status = "not_started" if available else "unavailable"
                        summary_en = (
                            f"{engine} is ready to run."
                            if available
                            else f"{engine} must be installed before this output can run."
                        )
                        summary_tr = (
                            f"{engine} çalıştırılmaya hazır."
                            if available
                            else f"Bu çıktı çalışmadan önce {engine} kurulmalıdır."
                        )
                    summary = LocalizedText(en=summary_en, tr=summary_tr)
                outputs.append(
                    PipelineOutputReference(
                        component_id=component.id,
                        port_id=port.id,
                        data_type=port.data_type,
                        status=status,
                        artifact_ids=artifact_ids,
                        summary=summary,
                    )
                )
        return outputs

    def _execute_document_understanding(self, runtime: _RuntimeRun) -> None:
        """Run the extractor without letting one engine failure erase schema evidence."""
        workspace = self._latest_staging_workspace(runtime.run_id)
        if workspace is None or workspace.pipeline_blueprint is None:
            workspace = self._persist_staging_workspace(runtime)
        component = next(
            (
                item
                for item in workspace.pipeline_blueprint.components
                if item.id == "understand-documents"
            ),
            None,
        )
        if component is None or not component.enabled:
            return
        if not self.source_profile(runtime.source_id).get("documents"):
            return
        engine = str(component.settings.get("engine") or "docling")
        allowed = {item["id"] for item in document_engine_catalog()}
        if engine not in allowed:
            raise ValueError(f"unknown document engine {engine!r}")
        existing_ref = self.store.latest(runtime.run_id, ArtifactType.DOCUMENT_EXTRACTION)
        if existing_ref is not None:
            existing = self.store.load(existing_ref.artifact_id, DocumentExtraction)
            if existing.engine == engine:
                return
        started = datetime.now(UTC)
        runtime.configuration["document_extraction_engine"] = engine
        runtime.configuration["document_extraction_ocr"] = str(
            component.settings.get("ocr") or "auto"
        )
        runtime.events.append(
            {
                "event": "document_understanding_started",
                "at": _now(),
                "engine": engine,
                "ocr_mode": runtime.configuration["document_extraction_ocr"],
            }
        )
        self._persist_runtime(runtime)

        # #64: reuse a prior extraction of byte-identical documents rather than
        # paying the full (minutes-long) OCR cost again. Keyed on content, so a
        # re-upload with a fresh source_id and mtime — which `_source_fingerprint`
        # could never recognise — still hits.
        content_fingerprint = self._document_content_fingerprint(runtime.source_id)
        cache_path = (
            self._document_cache_path(content_fingerprint, engine, dict(component.settings))
            if content_fingerprint is not None
            else None
        )
        if cache_path is not None:
            cached = self._reuse_cached_extraction(cache_path, engine)
            if cached is not None:
                ref = self.store.put(
                    cached,
                    run_id=runtime.run_id,
                    stage_exec_id="document_understanding",
                    name=f"document_{engine}",
                )
                runtime.configuration.pop("document_extraction_error", None)
                runtime.configuration["document_extraction_artifact_id"] = ref.artifact_id
                runtime.configuration["document_extraction_duration_seconds"] = (
                    cached.duration_seconds
                )
                runtime.events.append(
                    {
                        "event": "document_understanding_ready",
                        "at": _now(),
                        "engine": engine,
                        "artifact_id": ref.artifact_id,
                        "documents": len(cached.documents),
                        "pages": sum(item.page_count for item in cached.documents),
                        "reused_cache": True,
                    }
                )
                runtime.updated_at = _now()
                self._persist_runtime(runtime)
                self._persist_staging_workspace(runtime)
                return

        def record_file_progress(name: str, payload: dict[str, Any]) -> None:
            with self._lock:
                runtime.events.append(
                    {"event": f"document_{name}", "at": _now(), "engine": engine, **payload}
                )
                runtime.updated_at = _now()
            self._persist_runtime(runtime)

        try:
            extraction = extract_document_directory(
                self.source_path(runtime.source_id),
                source_id=runtime.source_id,
                source_fingerprint=self._source_fingerprint(runtime.source_id),
                engine=engine,
                settings=dict(component.settings),
                output_dir=(
                    self.store.root.parent
                    / "document-assets"
                    / runtime.run_id
                    / uuid.uuid4().hex[:8]
                ),
                on_progress=record_file_progress,
            )
            ref = self.store.put(
                extraction,
                run_id=runtime.run_id,
                stage_exec_id="document_understanding",
                name=f"document_{engine}",
            )
            runtime.configuration.pop("document_extraction_error", None)
            runtime.configuration["document_extraction_artifact_id"] = ref.artifact_id
            runtime.configuration["document_extraction_duration_seconds"] = (
                extraction.duration_seconds
            )
            failed_files = [item for item in extraction.file_results if item.status == "failed"]
            if failed_files:
                failure_detail = "; ".join(
                    warning for item in failed_files for warning in item.warnings
                ) or ", ".join(item.source_file for item in failed_files)
                runtime.configuration["document_extraction_error"] = (
                    f"Document extraction failed for {len(failed_files)} file(s): "
                    f"{failure_detail[:800]}"
                )
                runtime.events.append(
                    {
                        "event": "document_understanding_failed",
                        "at": _now(),
                        "engine": engine,
                        "artifact_id": ref.artifact_id,
                        "failed_files": [item.source_file for item in failed_files],
                        "error": runtime.configuration["document_extraction_error"],
                    }
                )
            else:
                # Only a clean extraction is cached: a partial failure may be
                # transient, so a re-upload should get a fresh attempt rather
                # than a memoised error.
                if cache_path is not None:
                    self._write_document_cache(cache_path, ref.artifact_id)
                runtime.events.append(
                    {
                        "event": "document_understanding_ready",
                        "at": _now(),
                        "engine": engine,
                        "artifact_id": ref.artifact_id,
                        "documents": len(extraction.documents),
                        "pages": sum(item.page_count for item in extraction.documents),
                    }
                )
        except Exception as exc:  # noqa: BLE001 - persisted so another engine can be chosen
            elapsed = (datetime.now(UTC) - started).total_seconds()
            runtime.configuration["document_extraction_error"] = (
                f"{type(exc).__name__}: {str(exc)[:900]}"
            )
            runtime.configuration["document_extraction_duration_seconds"] = elapsed
            runtime.events.append(
                {
                    "event": "document_understanding_failed",
                    "at": _now(),
                    "engine": engine,
                    "error": runtime.configuration["document_extraction_error"],
                }
            )
        runtime.updated_at = _now()
        self._persist_runtime(runtime)
        self._persist_staging_workspace(runtime)

    def _generate_staging_analysis(self, runtime: _RuntimeRun) -> None:
        """Create reports and a ready-to-accept plan with a compact planner call.

        The interactive Planner can edit the whole graph, but automatic source
        understanding cannot. Sending its catalog and graph-editing grammar
        here made a two-PDF synthesis spend minutes on irrelevant context.
        """
        self._persist_staging_workspace(runtime)
        self._execute_document_understanding(runtime)
        document_error = runtime.configuration.get("document_extraction_error")
        if document_error:
            raise DocumentExtractionError(str(document_error))
        with self._lock:
            runtime.current_stage = "staging_analysis"
            runtime.events.append({"event": "staging_analysis_started", "at": _now()})
            runtime.updated_at = _now()
        self._persist_runtime(runtime)
        llm = self.llm_factory() if self.llm_factory else OllamaClient()
        usable = isinstance(llm, StructuredLLM)
        if not usable:
            if isinstance(llm, OllamaClient):
                llm.close()
            # #365: this used to be a bare `return`. The caller then marked the
            # run `staged` and emitted `staging_analysis_ready`, so the run
            # reported success while carrying no plan -- and the canvas, which
            # has only "pending" and "failed" for the proposal node, drew it as
            # still working. The file was accepted, nothing went red, and the
            # flow never advanced. Say what happened instead; the status is
            # left alone, because the understanding that did run is real and a
            # deployment with no planner configured is a configuration fact
            # rather than a failed run.
            with self._lock:
                runtime.events.append(
                    {
                        "event": "staging_analysis_skipped",
                        "at": _now(),
                        "reason": {
                            "en": (
                                "No planner model is available, so no plan could be proposed "
                                "for these files. Understanding itself completed."
                            ),
                            "tr": (
                                "Kullanilabilir bir planlayici model yok, bu yuzden bu dosyalar "
                                "icin plan onerilemedi. Veri anlama asamasi tamamlandi."
                            ),
                        },
                    }
                )
                runtime.updated_at = _now()
            self._persist_runtime(runtime)
            return
        profile = self.source_profile(runtime.source_id)
        safe_tables = [
            {
                "name": table.get("name"),
                "source_file": table.get("source_file"),
                "rows": table.get("rows"),
                "columns_count": table.get("columns_count"),
                "candidate_keys": table.get("candidate_keys", []),
                "issues": table.get("issues", []),
                "columns": [
                    {
                        "name": column.get("name"),
                        "dtype": column.get("dtype"),
                        "semantic_type": column.get("semantic_type"),
                        "candidate_target": column.get("candidate_target"),
                    }
                    for column in table.get("columns", [])[:80]
                ],
            }
            for table in profile.get("tables", [])[:24]
        ]
        document_context: list[dict[str, Any]] = []
        extraction_ref = self.store.latest(runtime.run_id, ArtifactType.DOCUMENT_EXTRACTION)
        if extraction_ref is not None:
            extraction = self.store.load(extraction_ref.artifact_id, DocumentExtraction)
            document_context = document_extraction_prompt_context(
                extraction,
                (
                    "Summarize each source, distinguish extracted evidence from interpretation, "
                    "and recommend the bounded next workflow."
                ),
                character_budget=12_000,
            )
        prompt = json.dumps(
            {
                "source_files": profile.get("source_files", []),
                "structured_tables": safe_tables,
                "measured_relationships": profile.get("relationships", [])[:24],
                "document_extraction": document_context,
            },
            default=str,
        )
        reply_language = i18n.normalise(str(runtime.configuration.get("language") or ""))
        reply_instruction = (
            "Write reply only in Turkish."
            if reply_language == "tr"
            else "Write reply only in English."
        )
        system = (
            "Create the automatic pre-pipeline data-understanding synthesis. Return two or three "
            "short bilingual report artifacts and a bounded runtime rationale. "
            f"{reply_instruction} Artifacts remain bilingual: use English and Turkish in "
            "artifact fields. "
            "Explain each modality "
            "and cross-source relationships. Measured/extracted evidence must be verbally "
            "distinct from interpretation. Cite document evidence with file name and page. "
            "Candidate PDF tables remain untrusted until human review and must never be "
            "described as training data. Use report kind structured, documents, or synthesis. "
            "Do not invent columns, relationships, files, metrics, or completed actions. Explain "
            "high-value measured relationships inside report findings. Choose exactly one neutral "
            "pipeline_decision: create_pipeline when the evidence supports an executable ML "
            "objective and suitable inputs; defer_pipeline when a named review or missing fact "
            "must resolve viability; no_pipeline when the understood source does not warrant an "
            "ML workflow. Evaluate all three options and state the evidence-based reason. Keep "
            "every field concise."
        )
        try:
            response = None
            reply = None
            raw_result: dict[str, Any] = {}
            last_error: Exception | None = None
            for _attempt in range(2):
                try:
                    run_seed = _coerce_run_seed(runtime.configuration["run_seed"])
                    response = llm.generate_structured(
                        system=system,
                        prompt=prompt,
                        json_schema=_StagingAnalysisReply.model_json_schema(),
                        profile=LARGE.with_seed(
                            derive_agent_seed(
                                run_seed,
                                stage_ordinal=_STAGING_ANALYSIS_STAGE_ORDINAL,
                                attempt=1,
                                call_ordinal=_attempt,
                            )
                        ),
                    )
                    if response.parsed is None:
                        raise ValueError(
                            response.parse_error or "planner returned no structured response"
                        )
                    raw_result = dict(response.parsed)
                    normalized_reports = []
                    for raw_report in raw_result.get("reports", []):
                        if "kind" in raw_report:
                            normalized_reports.append(raw_report)
                            continue
                        component_id = raw_report.get("component_id")
                        kind = {
                            "structured-brief": "structured",
                            "document-brief": "documents",
                            "understanding-synthesis": "synthesis",
                        }.get(component_id, "synthesis")
                        findings = raw_report.get("findings") or []
                        questions = raw_report.get("verification_questions") or []
                        normalized_reports.append(
                            {
                                **raw_report,
                                "kind": kind,
                                "findings_en": [
                                    item.get("en", "")
                                    for item in findings
                                    if isinstance(item, dict)
                                ],
                                "findings_tr": [
                                    item.get("tr", item.get("en", ""))
                                    for item in findings
                                    if isinstance(item, dict)
                                ],
                                "verification_questions_en": [
                                    item.get("en", "")
                                    for item in questions
                                    if isinstance(item, dict)
                                ],
                                "verification_questions_tr": [
                                    item.get("tr", item.get("en", ""))
                                    for item in questions
                                    if isinstance(item, dict)
                                ],
                            }
                        )
                    rationale = raw_result.get("plan_rationale") or []
                    reply = _StagingAnalysisReply.model_validate(
                        {
                            **raw_result,
                            "pipeline_decision": raw_result.get("pipeline_decision")
                            or ("create_pipeline" if safe_tables else "defer_pipeline"),
                            "decision_reason_en": raw_result.get("decision_reason_en")
                            or "The legacy planner response did not include a pipeline decision.",
                            "decision_reason_tr": raw_result.get("decision_reason_tr")
                            or "Eski planlayıcı yanıtı bir işlem hattı kararı içermiyordu.",
                            "reports": normalized_reports,
                            "rationale_en": raw_result.get("rationale_en")
                            or [item.get("en", "") for item in rationale if isinstance(item, dict)],
                            "rationale_tr": raw_result.get("rationale_tr")
                            or [
                                item.get("tr", item.get("en", ""))
                                for item in rationale
                                if isinstance(item, dict)
                            ],
                        }
                    )
                    # #65: a model that returns a verdict but no words for it left
                    # the UI showing generic boilerplate. Reject the unexplained
                    # verdict and re-roll (the retry uses a different seed) rather
                    # than papering over it. Only when the model gave no decision
                    # at all is the legacy default above the intended answer, so
                    # that path is left to pass on the first try.
                    provided_decision = raw_result.get("pipeline_decision")
                    explained = bool(
                        (raw_result.get("decision_reason_en") or "").strip()
                        and (raw_result.get("decision_reason_tr") or "").strip()
                    )
                    if provided_decision and not explained and _attempt == 0:
                        raise ValueError(
                            "planner returned a pipeline decision with no explanation"
                        )
                    break
                except (TimeoutError, subprocess.TimeoutExpired):
                    raise
                except Exception as exc:
                    last_error = exc
            if response is None or reply is None:
                assert last_error is not None
                raise last_error
            component_by_kind = {
                "structured": "structured-brief",
                "documents": "document-brief",
                "synthesis": "understanding-synthesis",
            }
            reports = []
            for item in reply.reports:
                findings = [
                    {"en": english, "tr": turkish}
                    for english, turkish in zip(item.findings_en, item.findings_tr, strict=False)
                ]
                questions = [
                    {"en": english, "tr": turkish}
                    for english, turkish in zip(
                        item.verification_questions_en,
                        item.verification_questions_tr,
                        strict=False,
                    )
                ]
                reports.append(
                    {
                        "component_id": component_by_kind[item.kind],
                        "title_en": item.title_en,
                        "title_tr": item.title_tr,
                        "summary_en": item.summary_en,
                        "summary_tr": item.summary_tr,
                        "findings": findings,
                        "verification_questions": questions,
                    }
                )
            allowed = {
                "base_table",
                "base_grain",
                "target_column",
                "task_type",
                "primary_metric",
                "validation_strategy",
                "n_folds",
                "candidate_limit",
                "instructions",
            }
            plan_configuration: dict[str, Any] = (
                {"candidate_limit": 2, "n_folds": 5}
                if reply.pipeline_decision == "create_pipeline"
                else {}
            )
            plan_configuration.update(
                {
                    key: value
                    for key, value in (raw_result.get("configuration_patch") or {}).items()
                    if key in allowed
                }
            )
            if safe_tables:
                plan_configuration.setdefault("base_table", safe_tables[0]["name"])
                candidate_keys = safe_tables[0].get("candidate_keys") or []
                if candidate_keys and "base_grain" not in plan_configuration:
                    first_key = candidate_keys[0]
                    plan_configuration["base_grain"] = (
                        list(first_key) if isinstance(first_key, (list, tuple)) else [first_key]
                    )
            stage_directives = {
                stage: [line.strip() for line in lines if line.strip()]
                for stage, lines in (raw_result.get("stage_directives") or {}).items()
                if stage in _PIPELINE_STAGES and any(line.strip() for line in lines)
            }
            max_retries = {
                stage: max(0, min(9, retries))
                for stage, retries in (raw_result.get("max_retries_by_stage") or {}).items()
                if stage in _PIPELINE_STAGES
            }
            max_retries.setdefault("schema_discovery", 1)
            result = {
                "reply": reply.reply,
                "pipeline_recommendation": reply.pipeline_decision,
                "decision_summary": {
                    "en": reply.decision_reason_en,
                    "tr": reply.decision_reason_tr,
                },
                "reports": reports,
                "plan_rationale": [
                    {"en": english, "tr": turkish}
                    for english, turkish in zip(
                        reply.rationale_en, reply.rationale_tr, strict=False
                    )
                ],
                "configuration_patch": plan_configuration,
                "checkpoint_stages": [],
                "auto_proceed_stages": [],
                "max_retries_by_stage": max_retries,
                "stage_directives": stage_directives,
            }
            workspace = self._latest_staging_workspace(runtime.run_id)
            graph_updates = raw_result.get("pipeline_component_updates") or {}
            graph_disables = raw_result.get("pipeline_component_disables") or []
            if workspace and workspace.pipeline_blueprint and (graph_updates or graph_disables):
                # #193: the same refusal here would fail the whole staging plan
                # over a disable the planner should not have asked for. Keep the
                # plan and drop the edit; the default graph it started from runs.
                try:
                    updated_blueprint = apply_planner_graph_operations(
                        workspace.pipeline_blueprint,
                        additions=[],
                        connections=[],
                        updates=graph_updates,
                        disable_components=graph_disables,
                        base_revision=workspace.pipeline_blueprint.revision,
                    )
                    result["pipeline_blueprint"] = updated_blueprint.model_dump(mode="json")
                except PlannerGraphEditRejected as exc:
                    result["graph_edit_rejected"] = str(exc)
            result["model"] = response.model
            result["latency_s"] = response.latency_s
            self._persist_staging_workspace(runtime, planner_result=result)
        except Exception as exc:
            self._persist_staging_workspace(
                runtime,
                planner_error=f"{type(exc).__name__}: {exc}",
            )
            raise RuntimeError("Planner could not create a staging plan") from exc
        finally:
            if isinstance(llm, OllamaClient):
                llm.close()

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
                    automation_id=configuration.get("automation_id"),
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

    def _teams(self) -> Teams:
        """Read the configuration on each use rather than caching it.

        Parsing a short string is far cheaper than the confusion of a team
        change that appears to have been ignored because the process is still
        holding the value it read at boot.
        """
        return load_teams()

    def _ownership(self) -> OwnershipStore:
        root = self.upload_root if self.upload_root is not None else Path("data/uploads")
        return OwnershipStore(root / OWNERSHIP_FILE)

    @staticmethod
    def _upload_label(files: list[str]) -> str:
        """A single-file upload is named after its file, not "upload (1 file)".

        With several unnamed uploads side by side in the picker or /datasets,
        the one distinguishing fact -- the file's own name -- was already in
        hand and thrown away, so every singleton read identically. Groups of
        two or more keep the generic count, which no single name can convey.
        """
        if len(files) == 1:
            return files[0]
        return f"upload ({len(files)} files)"

    def data_sources(self, *, viewer: str | None = None) -> list[dict[str, Any]]:
        """Sources this person may see: their own, plus their team's.

        `viewer` of None means unfiltered, which is what an internal caller with
        no request behind it gets. Filtering happens here rather than in the
        route so every listing path agrees on one rule.
        """
        teams = self._teams()
        ownership = self._ownership()
        sources: list[dict[str, Any]] = []
        for root in self.source_roots:
            if not root.exists():
                continue
            for child in sorted(root.iterdir()):
                if (
                    child.is_dir()
                    and not self._reserved_path(child)
                    # Automation selections are copied to an immutable private
                    # snapshot so later project uploads cannot alter old runs.
                    # When ``data/`` is a source root, that implementation
                    # directory is also its direct child; never offer it as a
                    # second user-visible dataset.
                    and not self._is_automation_input_path(child)
                    and self._holds_loadable_files(child)
                ):
                    sources.append({"source_id": child.name, "label": child.name})
        if self.upload_root is not None and self.upload_root.exists():
            for child in sorted(self.upload_root.iterdir()):
                # `is_dir()` first. Listing the children of a *file* raises
                # NotADirectoryError, so a stray .DS_Store, Thumbs.db or stray
                # download in the upload root took down the whole Data library
                # listing rather than being ignored.
                if not child.is_dir() or child.name.startswith("."):
                    continue
                files = sorted(path.name for path in child.iterdir() if path.is_file())
                if not files:
                    continue
                source_id = f"upload:{child.name}"
                owner = ownership.owner_of(source_id)
                visibility = ownership.visibility_of(source_id)
                # `viewer is None` means no request is behind this call, so
                # there is nobody to filter for. Running the rules anyway made
                # every source invisible once teams were configured, because
                # nobody shares a team with nobody -- background work would have
                # silently skipped everything.
                if viewer is not None and not may_view(
                    viewer=viewer, owner=owner, visibility=visibility, teams=teams
                ):
                    continue
                sources.append(
                    {
                        "source_id": source_id,
                        "label": self._upload_label(files),
                        "files": files,
                        "owner": owner,
                        "visibility": visibility,
                        "mine": bool(viewer and owner == viewer),
                    }
                )
        return sources

    def require_source_view(self, source_id: str, *, viewer: str | None) -> None:
        """Hide a private source from callers that do not own or share it.

        Unowned built-in sources retain their historical shared behaviour. A
        denied source answers like an unknown id so the endpoint does not also
        disclose that another account's private source exists.
        """
        ownership = self._ownership()
        if not may_view(
            viewer=viewer,
            owner=ownership.owner_of(source_id),
            visibility=ownership.visibility_of(source_id),
            teams=self._teams(),
        ):
            raise KeyError(source_id)

    #: Cap on `page_size` so a caller cannot ask the server to profile an
    #: unbounded slice in one request and undo the reason pagination exists.
    _DATASET_PAGE_MAX = 100

    def dataset_catalog(
        self,
        *,
        search: str | None = None,
        page: int = 1,
        page_size: int = 25,
        viewer: str | None = None,
    ) -> dict[str, Any]:
        """Paginated, searchable row-free summaries backing /datasets.

        Profiling is the cost here: `source_profile()` re-measures a source,
        and the old catalog profiled *every* source on *every* load, so the
        page slowed without bound as datasets accumulated (#72) -- a single
        121 MB source alone once cost 4.91s of a 5.22s load. Listing sources
        and searching their labels is cheap; only the page actually shown is
        profiled, so the per-load cost tracks page size, not the total.
        """
        page_size = max(1, min(page_size, self._DATASET_PAGE_MAX))
        page = max(1, page)
        sources = self.data_sources(viewer=viewer)
        needle = (search or "").strip().casefold()
        if needle:
            sources = [
                source
                for source in sources
                if needle in str(source.get("label", "")).casefold()
            ]
        # Stable order so a given page holds the same rows across loads;
        # data_sources() already sorts within each root but not across them.
        sources.sort(key=lambda source: str(source.get("label", "")).casefold())
        total = len(sources)
        start = (page - 1) * page_size
        window = sources[start : start + page_size]
        return {
            "items": [self._dataset_summary(source) for source in window],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def _dataset_summary(self, source: dict[str, Any]) -> dict[str, Any]:
        """Profile one source into its row-free catalog entry."""
        item = dict(source)
        try:
            profile = self.source_profile(source["source_id"])
            tables = profile["tables"]
            documents = profile["documents"]
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
                    # A PDF-only source has no tables, so every table-derived
                    # field above is zero and the row read as empty/broken
                    # (#69). Carry a document summary too so the surface can
                    # show what such a source actually contains.
                    "documents": len(documents),
                    "document_pages": sum(
                        document.get("pages", 0) for document in documents
                    ),
                    "document_summaries": [
                        {
                            "name": document["name"],
                            "format": document.get("format", "pdf"),
                            "pages": document.get("pages", 0),
                        }
                        for document in documents
                    ],
                    # The project Data tab lists physical files, not only the
                    # source-level totals above. Preserve each file's bounded,
                    # row-free profile explanation so that view and Intake
                    # describe the same evidence (#329).
                    "file_summaries": [dict(file) for file in profile["source_files"]],
                    "privacy": profile["privacy"],
                }
            )
        except (KeyError, OSError, ValueError) as exc:
            item["profile_error"] = str(exc)
        return item

    def source_path(self, source_id: str) -> Path:
        candidates = [root / source_id for root in self.source_roots]
        match = _UPLOAD_ID.fullmatch(source_id)
        if match and self.upload_root is not None:
            candidates.insert(0, self.upload_root / match.group(1))
        input_match = _AUTOMATION_INPUT_ID.fullmatch(source_id)
        if input_match:
            candidates.insert(0, self._automation_input_root / input_match.group(1))
        for candidate in candidates:
            resolved = candidate.resolve()
            if self._reserved_path(resolved):
                continue
            in_source = any(root in resolved.parents for root in self.source_roots)
            in_upload = self.upload_root is not None and self.upload_root in resolved.parents
            in_automation_input = self._is_automation_input_path(resolved)
            if (
                (in_source or in_upload or in_automation_input)
                and resolved.is_dir()
                and any(resolved.iterdir())
            ):
                return resolved
        raise KeyError(source_id)

    def _is_automation_input_path(self, path: Path) -> bool:
        resolved = path.resolve()
        root = self._automation_input_root.resolve()
        return resolved == root or root in resolved.parents

    @staticmethod
    def _holds_loadable_files(directory: Path) -> bool:
        """Whether this folder is a dataset rather than a folder that exists.

        `data/` also holds working directories -- another run's artifact store,
        a sandbox, an export probe -- and offering them as datasets put five
        rows on the chooser of which three answered "source contains no
        supported data files" when clicked. A directory the loader cannot read
        anything from is not a dataset, whatever else it is.
        """
        supported = CSV_SUFFIXES | EXCEL_SUFFIXES | PARQUET_SUFFIXES | PDF_SUFFIXES
        return any(
            path.is_file() and path.suffix.lower() in supported for path in directory.rglob("*")
        )

    def _reserved_path(self, path: Path) -> bool:
        resolved = path.resolve()
        reserved = {self.store.root.resolve(), self._run_state_root.resolve()}
        if any(resolved == item or item in resolved.parents for item in reserved):
            return True
        return self.upload_root is not None and resolved == self.upload_root.resolve()

    def _matching_single_file_upload(
        self, content: bytes, *, owner: str | None
    ) -> tuple[str, list[str]] | None:
        """Find an owned singleton whose bytes are already stored."""
        if self.upload_root is None:
            return None
        expected = hashlib.sha256(content).digest()
        ownership = self._ownership()
        for directory in sorted(self.upload_root.iterdir()):
            if not directory.is_dir() or directory.name.startswith("."):
                continue
            source_id = f"upload:{directory.name}"
            # A cross-owner hit would disclose that another person's private
            # upload exists, and reusing its source id would bypass visibility.
            if ownership.owner_of(source_id) != owner:
                continue
            files = sorted(path for path in directory.iterdir() if path.is_file())
            if len(files) != 1 or files[0].stat().st_size != len(content):
                continue
            digest = hashlib.sha256()
            with files[0].open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.digest() == expected:
                return source_id, [files[0].name]
        return None

    def upload(
        self,
        filename: str,
        content: bytes,
        *,
        source_id: str | None = None,
        owner: str | None = None,
    ) -> dict[str, Any]:
        """Persist one file, optionally appending it to an existing upload group."""
        safe_name = Path(filename).name
        if not safe_name or safe_name in {".", ".."}:
            raise ValueError("filename must contain a file name")
        if len(safe_name.encode("utf-8")) > _MAX_UPLOAD_NAME_BYTES:
            raise ValueError("file name is too long")
        if not content:
            raise ValueError("uploaded file is empty")
        if Path(safe_name).suffix.lower() not in _UPLOAD_SUFFIXES:
            # Uzanti bir IDDIA, olcum degil. Reddetmeden once icerige bak:
            # uzantisiz ya da yanlis adlandirilmis gecerli bir tablo, hicbir
            # hata verilmeden kaybediliyordu.
            if _detect_flow_from_content(safe_name, content) not in {"tablo", "belge"}:
                raise ValueError(
                    "supported uploads are CSV/TSV, Excel, Parquet, or PDF; this file's "
                    "content could not be measured as a table or a document either"
                )

        if source_id is None:
            matching = self._matching_single_file_upload(content, owner=owner)
            if matching is not None:
                matched_source_id, files = matching
                return {
                    "source_id": matched_source_id,
                    "label": self._upload_label(files),
                    "files": files,
                    "reused": True,
                }
            token = uuid.uuid4().hex[:12]
            target_dir = self.upload_root / token  # type: ignore[operator]
            new_group = True
        else:
            match = _UPLOAD_ID.fullmatch(source_id)
            if match is None:
                raise ValueError("source_id is not a valid upload group")
            if self._source_has_execution_history(source_id):
                # #111: a reused source may feed several independent projects.
                # Mutating its bytes after any run would silently change the
                # data shown beside immutable outputs in every one of them.
                raise ValueError(
                    "a source used by an execution is immutable; upload the files "
                    "as a new source"
                )
            target_dir = self.upload_root / match.group(1)  # type: ignore[operator]
            if not target_dir.is_dir():
                raise ValueError("upload group does not exist")
            token = match.group(1)
            new_group = False

        target = target_dir / safe_name
        # Before anything is created, so a name that cannot be written does not
        # leave an empty upload group and an ownership record behind it.
        _check_upload_path_fits(target)
        if new_group:
            target_dir.mkdir(parents=True, exist_ok=False)
            # Recorded for the first file only. Appending to an existing group
            # must not transfer it to whoever added the latest file.
            self._ownership().record(f"upload:{token}", owner=owner)

        if target.exists():
            raise ValueError(f"upload group already contains {safe_name!r}")
        target.write_bytes(content)
        files = sorted(path.name for path in target_dir.iterdir() if path.is_file())
        return {
            "source_id": f"upload:{token}",
            "label": self._upload_label(files),
            "files": files,
        }

    def install_pdf_demo(self, *, owner: str | None = None) -> dict[str, Any]:
        """Materialize the bundled PDF + CSV oracle as one reusable source.

        The bytes are packaged with the application, so this is offline. An
        owner gets one copy: repeated clicks verify and reuse it instead of
        producing duplicate data sources.
        """
        from ads.testing.pdf_demo import PDF_DEMO_FILES, pdf_demo_fixture_dir  # noqa: PLC0415

        if self.upload_root is None:
            raise ValueError("uploads are not configured")
        fixture = pdf_demo_fixture_dir()
        expected = {name: (fixture / name).read_bytes() for name in PDF_DEMO_FILES}
        ownership = self._ownership()
        for source in self.data_sources(viewer=owner):
            source_id = str(source.get("source_id", ""))
            if not source_id.startswith("upload:") or ownership.owner_of(source_id) != owner:
                continue
            if sorted(source.get("files", [])) != sorted(expected):
                continue
            try:
                directory = self.source_path(source_id)
            except KeyError:
                continue
            matches = all(
                (directory / name).read_bytes() == content
                for name, content in expected.items()
            )
            if matches:
                return {**source, "reused": True}

        token = uuid.uuid4().hex[:12]
        target_dir = self.upload_root / token
        for name in expected:
            _check_upload_path_fits(target_dir / name)
        target_dir.mkdir(parents=True, exist_ok=False)
        source_id = f"upload:{token}"
        ownership.record(source_id, owner=owner)
        try:
            for name, content in expected.items():
                (target_dir / name).write_bytes(content)
        except Exception:
            shutil.rmtree(target_dir, ignore_errors=True)
            ownership.forget(source_id)
            raise
        files = sorted(expected)
        return {
            "source_id": source_id,
            "label": "PDF extraction demo",
            "files": files,
            "owner": owner,
            "visibility": ownership.visibility_of(source_id),
            "mine": bool(owner),
            "reused": False,
        }

    def remove_upload_file(
        self, source_id: str, filename: str, *, owner: str | None = None
    ) -> dict[str, Any]:
        """Remove one file from an upload group and report what remains.

        There was no way to take a file back out of a source (#85). Only the
        owner may edit their upload. A group emptied of its last file is deleted
        along with its ownership record, since a source with no files is not a
        dataset and would otherwise linger as a phantom the picker hides anyway.
        """
        if self.upload_root is None:
            raise ValueError("uploads are not configured")
        match = _UPLOAD_ID.fullmatch(source_id)
        if match is None:
            raise ValueError("source_id is not a valid upload group")
        if self._source_has_execution_history(source_id):
            raise ValueError(
                "a source used by an execution is immutable; create a new project "
                "with different data"
            )
        recorded_owner = self._ownership().owner_of(source_id)
        if recorded_owner is not None and owner is not None and recorded_owner != owner:
            raise PermissionError(f"Only {recorded_owner} can change this source.")
        target_dir = self.upload_root / match.group(1)
        if not target_dir.is_dir():
            raise KeyError(source_id)
        safe_name = Path(filename).name
        if not safe_name or safe_name in {".", ".."}:
            raise ValueError("filename must contain a file name")
        target = (target_dir / safe_name).resolve()
        if target.parent != target_dir.resolve() or not target.is_file():
            raise KeyError(filename)
        target.unlink()
        remaining = sorted(path.name for path in target_dir.iterdir() if path.is_file())
        if not remaining:
            shutil.rmtree(target_dir, ignore_errors=True)
            self._ownership().forget(source_id)
            return {"source_id": source_id, "files": [], "deleted": True}
        return {
            "source_id": source_id,
            "label": self._upload_label(remaining),
            "files": remaining,
            "deleted": False,
        }

    def _source_has_execution_history(self, source_id: str) -> bool:
        """Whether changing this reusable source would invalidate project history."""
        assert self.automation_store is not None
        return any(
            project.source_id == source_id and bool(project.execution_ids)
            for project in self.automation_store.list()
        )

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

    def _profile_cache_path(self, source_id: str) -> Path | None:
        if self.profile_cache_dir is None:
            return None
        # The id is host-controlled (`upload:<token>`) but it reaches here from a
        # URL, so it is reduced to a hash rather than trusted as a filename.
        digest = hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:32]
        return self.profile_cache_dir / f"{digest}.json"

    def _read_cached_profile(self, source_id: str, fingerprint: str) -> dict[str, Any] | None:
        path = self._profile_cache_path(source_id)
        if path is None or not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # A truncated cache file is a performance problem, never a
            # correctness one: fall through and re-profile.
            return None
        if (
            payload.get("schema_version") != _PROFILE_CACHE_SCHEMA_VERSION
            or payload.get("fingerprint") != fingerprint
        ):
            return None
        profile = payload.get("profile")
        return profile if isinstance(profile, dict) else None

    def _write_cached_profile(
        self, source_id: str, fingerprint: str, profile: dict[str, Any]
    ) -> None:
        path = self._profile_cache_path(source_id)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Written via a temporary file and replaced, so a crash mid-write
            # cannot leave a half-file that later reads as a valid cache hit.
            temporary = path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(
                    {
                        "schema_version": _PROFILE_CACHE_SCHEMA_VERSION,
                        "fingerprint": fingerprint,
                        "profile": profile,
                    }
                ),
                encoding="utf-8",
            )
            temporary.replace(path)
        except OSError:
            pass

    def _document_content_fingerprint(self, source_id: str) -> str | None:
        """Hash the bytes of a source's documents so an identical re-upload is a cache hit.

        #64: OCR reran from scratch on a byte-identical PDF because the only
        skip-check keyed on `_source_fingerprint`, which is name/size/mtime — a
        second upload gets a fresh source_id and a new mtime, so it never
        matched. This reads document content instead. The cost is bounded to the
        files the extractor would read anyway, and only the documents, not the
        whole (possibly 121 MB) source. The relative path is folded in because
        the extraction embeds each file's name in `source_file` and candidate
        ids, so identical bytes under a different name are a different output.
        Returns None when there are no extractable documents, so the caller
        skips the cache rather than keying on emptiness.
        """
        root = Path(self.source_path(source_id))
        digest = hashlib.sha256()
        found = False
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix.lower() in PDF_SUFFIXES:
                found = True
                digest.update(str(path.relative_to(root)).encode("utf-8"))
                digest.update(b"\x00")
                with path.open("rb") as stream:
                    for chunk in iter(lambda stream=stream: stream.read(1 << 20), b""):
                        digest.update(chunk)
                digest.update(b"\x00")
        return digest.hexdigest() if found else None

    def _document_cache_path(
        self, content_fingerprint: str, engine: str, settings: dict[str, Any]
    ) -> Path:
        """Where a reusable extraction is recorded, keyed by content + engine + settings.

        Settings are part of the key because they change the output (OCR mode,
        table detection), so a reuse only fires when the inputs the extractor
        actually saw are identical.
        """
        settings_key = json.dumps(
            settings, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
        )
        digest = hashlib.sha256(
            f"{content_fingerprint}\x00{engine}\x00{settings_key}".encode()
        ).hexdigest()
        return self.store.root.parent / "cache" / "documents" / f"{digest}.json"

    def _reuse_cached_extraction(
        self, cache_path: Path, engine: str
    ) -> DocumentExtraction | None:
        """Load a previously extracted, fully successful artifact if one is on record."""
        if not cache_path.is_file():
            return None
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # A truncated cache file is a performance problem, never a
            # correctness one: fall through and re-extract.
            return None
        artifact_id = payload.get("artifact_id") if isinstance(payload, dict) else None
        if not isinstance(artifact_id, str) or not self.store.exists(artifact_id):
            return None
        try:
            return self.store.load(artifact_id, DocumentExtraction)
        except (ArtifactNotFoundError, ValidationError):
            return None

    def _write_document_cache(self, cache_path: Path, artifact_id: str) -> None:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = cache_path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"artifact_id": artifact_id}), encoding="utf-8")
            temporary.replace(cache_path)
        except OSError:
            pass

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

        persisted = self._read_cached_profile(source_id, fingerprint)
        if persisted is not None:
            with self._lock:
                self._profile_cache[source_id] = (fingerprint, persisted)
            return persisted

        source_path = self.source_path(source_id)
        detection_env, detection_error = _file_detection_inventory(Path(source_path).resolve())
        loaded, unreadable = load_directory_with_failures(
            source_path,
            measured_formats=_measured_table_formats(
                Path(source_path).resolve(), detection_env
            ),
        )
        documents = load_pdf_directory(source_path)
        cards = profile_tables(loaded)
        if not cards and not documents:
            raise ValueError("source contains no supported data files")
        # Measured before any run exists. This is what makes the pre-run screen
        # honest: the relationships shown are the same ones schema discovery
        # will reason over, not a picture drawn from column names.
        #
        # Past a table budget the pass is refused rather than run, because it
        # compares every table with every other and a few hundred sharded files
        # take it past the edge proxy's timeout -- the request died at 100s with
        # nothing said (#86). The skip is reported rather than swallowed: an
        # empty list would read as "measured, found none", and somebody would
        # conclude their tables are unrelated.
        relationships_measured = True
        relationships_note: str | None = None
        try:
            relationship_candidates = detect_relationships(
                cards, {table.name: table.frame for table in loaded}
            )
        except TooManyTablesForPairwiseDetection as exc:
            relationships_measured = False
            relationships_note = str(exc)
            relationship_candidates = []
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
            for candidate in relationship_candidates
        ]
        source_root = Path(source_path).resolve()
        tables_by_file: dict[str, list[str]] = {}
        for table in loaded:
            try:
                source_file = Path(table.source_uri).resolve().relative_to(source_root).as_posix()
            except ValueError:
                source_file = Path(table.source_uri).name
            tables_by_file.setdefault(source_file, []).append(table.name)
        structured_suffixes = CSV_SUFFIXES | EXCEL_SUFFIXES | PARQUET_SUFFIXES
        source_files = []
        for path in sorted(source_root.rglob("*")):
            if not path.is_file():
                continue
            name = path.relative_to(source_root).as_posix()
            suffix = path.suffix.lower()
            if suffix in structured_suffixes:
                route = "structured"
                reason_en = "A supported tabular format will be profiled deterministically."
                reason_tr = "Desteklenen bir tablo biçimi belirlenimci şekilde profillenecek."
            elif suffix in PDF_SUFFIXES:
                route = "documents"
                reason_en = "A PDF will be sent to the selected document understanding engine."
                reason_tr = "PDF, seçilen belge anlama motoruna gönderilecek."
            else:
                route = "unsupported"
                unsupported_suffix = suffix or "this file type"
                reason_en = f"No staging adapter is registered for {unsupported_suffix}."
                reason_tr = (
                    f"{unsupported_suffix} için kayıtlı bir hazırlama bağdaştırıcısı yok."
                    if suffix
                    else "Bu dosya türü için kayıtlı bir hazırlama bağdaştırıcısı yok."
                )
            if name in unreadable:
                # It has a supported extension and still could not be read --
                # most often prose in a .txt, which the delimited loader is
                # obliged to try. Shown as needing review rather than dropped,
                # and no longer allowed to fail the whole source.
                route = "needs_review"
                reason_en = f"Could not be read as tabular data: {unreadable[name]}"
                reason_tr = f"Tablo verisi olarak okunamadı: {unreadable[name]}"
            source_files.append(
                {
                    "name": name,
                    "format": suffix.lstrip(".") or "unknown",
                    "route": route,
                    "reason": {"en": reason_en, "tr": reason_tr},
                    "table_names": tables_by_file.get(name, []),
                }
            )
        detection_summary = _measure_file_detection(
            source_root, source_files, detection_env, detection_error
        )
        # Olcum uzantiyla CELISIYORSA artik sessiz kalmiyor. `.csv` adi verilmis
        # bir PDF yapisal veri diye yutuluyordu: 37 satir x 1 kolon, kolon adi
        # `pdf_1_4` -- yani %PDF-1.4 basligi -- ve info seviyesinin ustunde tek
        # bir uyari yok. Karari kesif'in kendi basina VERMESI degil bu; olculen
        # ile iddia edilen ayrildiginda dosyayi insana cikariyor.
        karantina: set[str] = set()
        for satir in source_files:
            if not satir.get("detection_conflicts_with_extension"):
                continue
            karantina.update(tables_by_file.get(satir["name"], []))
            satir["route"] = "needs_review"
            kanit = satir.get("detection_evidence") or ""
            uzanti, olculen = satir["format"], satir.get("detected_flow")
            # Diger `reason` alanlari iki dilli; bu da oyle olmali, yoksa
            # Turkce arayuzde tek Ingilizce satir olarak duruyordu.
            satir["reason"] = {
                "en": (
                    f"Extension claims {uzanti!r} but the content measures as "
                    f"{olculen!r}. {kanit}"
                ).strip(),
                "tr": (
                    f"Uzanti {uzanti!r} diyor ama icerik {olculen!r} olarak "
                    f"olculdu. {kanit}"
                ).strip(),
            }
        # Karantinadaki tablolar profile HIC girmez. Route'u degistirip tabloyu
        # birakmak, agent'a hala `pdf_1_4` adli bir kolon gostermek demekti;
        # asil zarar oradaydi.
        profile_table_payloads = [
            {
                "name": card.table_name,
                "source_file": (
                    Path(card.source_uri).resolve().relative_to(source_root).as_posix()
                    if source_root in Path(card.source_uri).resolve().parents
                    else Path(card.source_uri).name
                ),
                "sheet_name": card.sheet_name,
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
            if card.table_name not in karantina
        ]
        profile_documents = [document.public_summary() for document in documents]
        source_files = _file_profile_insights(
            source_files, profile_table_payloads, profile_documents, relationships
        )
        profile: dict[str, Any] = {
            "source_id": source_id,
            "source_files": source_files,
            "file_detection": detection_summary,
            "relationships": relationships,
            # So a caller can tell "none found" apart from "not measured".
            "relationships_measured": relationships_measured,
            "relationships_note": relationships_note,
            "documents": profile_documents,
            "tables": profile_table_payloads,
            "privacy": (
                "Schema and aggregate statistics only; document metadata may also be shown. "
                "Source rows, values, and PDF text are omitted from this API response."
            ),
        }
        with self._lock:
            self._profile_cache[source_id] = (fingerprint, profile)
        self._write_cached_profile(source_id, fingerprint, profile)
        return profile

    @staticmethod
    def _record_source_discovery(runtime: _RuntimeRun, profile: dict[str, Any]) -> None:
        """Persist the exact routing decision before modality work begins."""
        routes = {
            "structured": [],
            "documents": [],
            "unsupported": [],
        }
        for item in profile.get("source_files", []):
            route = str(item.get("route") or "unsupported")
            routes.setdefault(route, []).append(str(item.get("name") or "unknown"))
        runtime.events.extend(
            [
                {"event": "source_discovery_started", "at": _now()},
                {
                    "event": "source_discovery_ready",
                    "at": _now(),
                    "routes": routes,
                },
            ]
        )
        runtime.updated_at = _now()

    # --------------------------------------------------------------- execution

    #: Staging stops here. Intake measures the data and schema discovery reads
    #: what it means; between them they produce everything a person needs on
    #: screen before deciding anything, and neither depends on a stated problem.
    STAGE_UNTIL = "schema_discovery"

    def stage_run(
        self,
        source_id: str,
        configuration: dict[str, Any] | None = None,
        *,
        reuse_cache: bool = False,
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
        requested_configuration = dict(configuration or {})
        # The automatic Planner greeting is authored once, before any chat
        # exists. Capture the request language before work moves to a background
        # thread; later chat replies follow the user's message and stay verbatim.
        requested_configuration["language"] = i18n.current()
        supplied_run_seed = requested_configuration.get("run_seed")
        run_seed = (
            _coerce_run_seed(supplied_run_seed)
            if supplied_run_seed is not None
            else _new_run_seed()
        )
        requested_configuration["run_seed"] = run_seed
        source_path = self.source_path(source_id)
        profile = self.source_profile(source_id)
        if not profile.get("tables"):
            if not profile.get("documents"):
                raise ValueError("the source contains no supported tables or documents")
            run_id = f"run-{uuid.uuid4().hex[:8]}"
            state = RunState(
                run_id=run_id,
                store=self.store,
                profile=BUILTIN_PROFILES["full_auto"],
                run_seed=run_seed,
            )
            runtime = _RuntimeRun(
                run_id=run_id,
                source_id=source_id,
                state=state,
                configuration={
                    "source_id": source_id,
                    "mode": "agent",
                    "reuse_cache": reuse_cache,
                    "document_only": True,
                    **requested_configuration,
                },
                status="staging",
                current_stage="document_understanding",
            )
            self._record_source_discovery(runtime, profile)
            with self._lock:
                self._runtime_runs[run_id] = runtime
            self._persist_runtime(runtime)
            self._persist_staging_workspace(runtime)

            def analyse_documents() -> None:
                try:
                    self._generate_staging_analysis(runtime)
                    with self._lock:
                        runtime.status = "staged"
                        runtime.current_stage = None
                        runtime.events.append({"event": "staging_analysis_ready", "at": _now()})
                        runtime.updated_at = _now()
                except Exception as exc:  # noqa: BLE001 - durable staging failure
                    with self._lock:
                        runtime.status = "failed"
                        runtime.error = _run_error_text(exc)
                        runtime.current_stage = None
                        runtime.events.append(
                            {
                                "event": "staging_analysis_failed",
                                "at": _now(),
                                "error": runtime.error,
                            }
                        )
                        runtime.updated_at = _now()
                self._persist_runtime(runtime)

            start_worker(analyse_documents, name=f"ads-document-stage-{run_id}")
            return {"run_id": run_id, "status": "staging", "profile": profile}
        fingerprint = self._source_fingerprint(source_id)
        with self._lock:
            cached_staged = self._staged_cache.get(source_id)
        cached_seed = (
            cached_staged[1].get("run_seed")
            if cached_staged is not None and cached_staged[0] == fingerprint
            else None
        )
        can_reuse_cache = (
            reuse_cache
            and cached_staged is not None
            and cached_staged[0] == fingerprint
            and cached_seed is not None
            and (supplied_run_seed is None or run_seed == cached_seed)
        )
        if can_reuse_cache:
            cached_data = cached_staged[1]
            run_seed = _coerce_run_seed(cached_seed)
            requested_configuration["run_seed"] = run_seed
            run_id = f"run-{uuid.uuid4().hex[:8]}"
            state = RunState(
                run_id=run_id,
                store=self.store,
                profile=BUILTIN_PROFILES["full_auto"],
                run_seed=run_seed,
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
                new_att.agent_seeds = list(att_data.get("agent_seeds", []))
                state.active_attempt = None

            runtime = _RuntimeRun(
                run_id=run_id,
                source_id=source_id,
                state=state,
                configuration={
                    "source_id": source_id,
                    "mode": "agent",
                    "reuse_cache": True,
                    **requested_configuration,
                },
                status="staging",
            )
            self._record_source_discovery(runtime, profile)
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

            document_thread: threading.Thread | None = None
            if profile.get("documents"):
                document_thread = start_worker(
                    partial(self._execute_document_understanding, runtime),
                    name=f"ads-document-{run_id}",
                )

            def analyse_cached() -> None:
                try:
                    if document_thread is not None:
                        document_thread.join()
                    self._generate_staging_analysis(runtime)
                    with self._lock:
                        runtime.status = "staged"
                        runtime.current_stage = None
                        runtime.events.append({"event": "staging_analysis_ready", "at": _now()})
                        runtime.updated_at = _now()
                except Exception as exc:  # noqa: BLE001 - durable staging failure
                    with self._lock:
                        runtime.status = "failed"
                        runtime.error = _run_error_text(exc)
                        runtime.current_stage = None
                        runtime.events.append(
                            {
                                "event": "staging_analysis_failed",
                                "at": _now(),
                                "error": runtime.error,
                            }
                        )
                        runtime.updated_at = _now()
                self._persist_runtime(runtime)

            start_worker(analyse_cached, name=f"ads-stage-analysis-{run_id}")
            return {
                "run_id": run_id,
                "status": "staging",
                "profile": self.source_profile(source_id),
            }

        run_id = f"run-{uuid.uuid4().hex[:8]}"
        state = RunState(
            run_id=run_id,
            store=self.store,
            profile=BUILTIN_PROFILES["full_auto"],
            run_seed=run_seed,
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
                "reuse_cache": False,
                **requested_configuration,
            },
            status="staging",
        )
        self._record_source_discovery(runtime, profile)
        with self._lock:
            self._runtime_runs[run_id] = runtime
            runtime.events.append({"event": "run_staged", "at": _now(), "source": source_id})
        self._persist_runtime(runtime)

        document_thread: threading.Thread | None = None
        if profile.get("documents"):
            document_thread = start_worker(
                partial(self._execute_document_understanding, runtime),
                name=f"ads-document-{run_id}",
            )

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
                    runtime.status = (
                        "staging"
                        if runtime.outcome.status in {RunStatus.STAGED, "staged"}
                        else runtime.outcome.status
                    )
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
                                        "agent_seeds": list(att.agent_seeds),
                                    }
                                    for att in state.attempts
                                ],
                                "run_seed": runtime.configuration["run_seed"],
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
                if runtime.outcome.status in {RunStatus.STAGED, "staged"}:
                    if document_thread is not None:
                        document_thread.join()
                    self._generate_staging_analysis(runtime)
                    with self._lock:
                        runtime.status = "staged"
                        runtime.current_stage = None
                        runtime.events.append({"event": "staging_analysis_ready", "at": _now()})
                        runtime.updated_at = _now()
            except Exception as exc:  # noqa: BLE001 - recorded in durable UI state
                with self._lock:
                    runtime.status = "failed"
                    runtime.error = _run_error_text(exc)
                    runtime.current_stage = None
                    runtime.events.append(
                        {
                            "event": "staging_analysis_failed",
                            "at": _now(),
                            "error": runtime.error,
                        }
                    )
                    runtime.updated_at = _now()
            self._persist_runtime(runtime)

        # Held so the second half runs on this state rather than a fresh one.
        runtime.resume = (spec, registry, state, llm)
        start_worker(execute, name=f"ads-stage-{run_id}")
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
            raise _cannot_stage_error(runtime)
        if "run_seed" in configuration and _coerce_run_seed(configuration["run_seed"]) != int(
            runtime.configuration["run_seed"]
        ):
            raise ValueError("run_seed is immutable after a run is created")
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
            raise _cannot_stage_error(runtime)
        if runtime.resume is None:
            raise ValueError(
                "this staged run did not survive a restart and cannot be continued; "
                "choose the dataset again"
            )
        if configuration and "run_seed" in configuration:
            if _coerce_run_seed(configuration["run_seed"]) != int(
                runtime.configuration["run_seed"]
            ):
                raise ValueError("run_seed is immutable after a run is created")
        body = {**runtime.configuration, **(configuration or {})}
        spec, registry, state, llm = runtime.resume
        workspace = self._latest_staging_workspace(run_id)
        if workspace is None or workspace.pipeline_blueprint is None:
            raise ValueError("the staged run has no saved automation graph")
        compiled = self.compile_automation(run_id)["plan"]
        prior_pause = str(runtime.configuration.get("automation_completed_pause") or "") or None
        pause_after_stage = compiled.get("pause_after_stage")
        if prior_pause == pause_after_stage:
            pause_after_stage = None

        graph_checkpoints = {
            str(node["stage_id"])
            for node in compiled["nodes"]
            if node.get("stage_id") and node["control"].get("gate_handler") == "human"
        }
        graph_retries = {
            str(node["stage_id"]): int(node["control"]["max_retries"])
            for node in compiled["nodes"]
            if node.get("stage_id") and node["control"].get("max_retries") is not None
        }

        run_mode = str(body.get("run_mode", "auto"))
        if run_mode not in {"auto", "manual", "fully_auto"}:
            raise ValueError("run_mode must be 'auto', 'manual', or 'fully_auto'")
        if run_mode == "fully_auto":
            if workspace is None or workspace.recommended_plan is None:
                raise ValueError("fully_auto needs a completed planner recommendation")
            plan = workspace.recommended_plan
            if not self._plan_is_accepted(plan):
                raise ValueError("accept the Planner proposal before starting fully-auto")
            if workspace.pipeline_blueprint is not None:
                validate_executable_blueprint(workspace.pipeline_blueprint)
                enabled_document_components = {
                    component.id
                    for component in workspace.pipeline_blueprint.components
                    if component.kind == "document_understanding" and component.enabled
                }
                unfinished_document_outputs = [
                    output
                    for output in workspace.component_outputs
                    if output.component_id in enabled_document_components
                    and output.status != "ready"
                ]
                if unfinished_document_outputs:
                    raise ValueError(
                        "document understanding outputs are not ready; run the component "
                        "or disable it before starting the ML pipeline"
                    )
            # #244/#198: the accepted plan's configuration is the baseline, but a
            # target the person picked at the run control is an explicit choice
            # and must win over whatever the planner proposed. Re-apply the
            # caller's non-empty overrides after the plan so the dropdown aims the
            # run even in fully-auto, where plan.configuration is merged in.
            caller_overrides = {
                key: value
                for key, value in (configuration or {}).items()
                if value not in (None, "")
            }
            body = {
                **body,
                **plan.configuration,
                **caller_overrides,
                "run_mode": "fully_auto",
                "supervision": {
                    "checkpoint_stages": plan.checkpoint_stages,
                    "auto_proceed_stages": plan.auto_proceed_stages,
                    "max_retries_by_stage": plan.max_retries_by_stage,
                },
            }
            directives = dict(state.blackboard.get(STAGE_DIRECTIVES_KEY) or {})
            for stage, lines in plan.stage_directives.items():
                directives.setdefault(stage, []).extend(
                    line for line in lines if line not in directives.get(stage, [])
                )
            state.blackboard[STAGE_DIRECTIVES_KEY] = directives
        supervision = self._normalise_supervision(body.get("supervision") or {})
        checkpoints = sorted(set(supervision["checkpoint_stages"]) | graph_checkpoints)
        supervision["max_retries_by_stage"] = {
            **supervision["max_retries_by_stage"],
            **graph_retries,
        }
        if run_mode == "manual":
            checkpoints = sorted(set(checkpoints) | _PIPELINE_STAGES)
        # The problem has not been chosen yet at this point in the pipeline --
        # the agent chooses it two stages from here -- so the checkpoint that
        # start_run derives from an unstated problem applies here always.
        if run_mode != "fully_auto" and "problem_discovery" not in checkpoints:
            checkpoints.append("problem_discovery")
        supervision["checkpoint_stages"] = sorted(checkpoints)
        state.profile = BUILTIN_PROFILES["full_auto"].model_copy(
            update={"checkpoint_stages": sorted(checkpoints)}
        )
        intent = _as_intent(body.get("instructions"))
        preferences = self._staged_preferences(body)
        state.user_intent = "\n\n".join(part for part in (intent, preferences) if part) or None
        candidate_limit = body.get("candidate_limit")
        if candidate_limit is not None:
            candidate_limit = int(candidate_limit)
            if candidate_limit < 1:
                raise ValueError("candidate_limit must be positive")
        state.blackboard[CANDIDATE_LIMIT_KEY] = candidate_limit
        folds = int(body.get("n_folds", state.blackboard.get(VALIDATION_FOLDS_KEY, 3)))
        if not 2 <= folds <= 20:
            raise ValueError("n_folds must be between 2 and 20")
        state.blackboard[VALIDATION_FOLDS_KEY] = folds
        # #241: a problem stated through the quick-pick selector, skipping the
        # planner conversation. Shape-checked here so a malformed request fails
        # immediately rather than surfacing as a background run failure; the
        # column's actual fitness (does it exist, is its shape viable) is
        # checked deterministically once problem_discovery runs, exactly as it
        # would be for an agent-proposed candidate.
        problem_selection = body.get("problem_selection")
        if problem_selection is not None:
            if not isinstance(problem_selection, dict):
                raise ValueError("problem_selection must be an object")
            kind = problem_selection.get("kind")
            if kind not in {"predict_column", "flag_anomalies"}:
                raise ValueError(
                    "problem_selection.kind must be 'predict_column' or 'flag_anomalies'"
                )
            target_column = problem_selection.get("target_column") or None
            if kind == "predict_column" and not target_column:
                raise ValueError(
                    "problem_selection.target_column is required for 'predict_column'"
                )
            state.blackboard[QUICK_PROBLEM_KEY] = {
                "kind": kind,
                "target_column": str(target_column) if target_column else None,
            }
        # #200: capture the language now, in the request, so the report the
        # background worker renders later is in the language actually chosen
        # rather than the worker's "tr" ContextVar default.
        state.blackboard[RUN_LANGUAGE_KEY] = i18n.current()

        runtime.configuration.update(
            {
                **body,
                "supervision": supervision,
                "run_mode": run_mode,
                "instructions": state.user_intent,
            }
        )
        with self._lock:
            runtime.pause_requested = False
            runtime.status = "running"
            runtime.updated_at = _now()
        self._persist_runtime(runtime)

        event = self._event_recorder(runtime)
        runner = self.workflow_runner or run_workflow
        resume_from = prior_pause or self.STAGE_UNTIL
        resume_at = spec.next_stage(resume_from, EdgeCondition.ON_PROCEED)

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
                    stop_after=pause_after_stage,
                )
                with self._lock:
                    runtime.status = runtime.outcome.status
                    runtime.error = runtime.outcome.error
                    runtime.current_stage = getattr(runtime.outcome, "final_stage", None)
                    if runtime.outcome.status in {RunStatus.STAGED, "staged"}:
                        runtime.configuration["automation_completed_pause"] = pause_after_stage
                    runtime.updated_at = _now()
            except _RunPauseRequested:
                with self._lock:
                    paused_after = runtime.current_stage
                    runtime.status = "staged"
                    runtime.error = None
                    runtime.pause_requested = False
                    if paused_after:
                        runtime.configuration["automation_completed_pause"] = paused_after
                    runtime.events.append(
                        {"event": "run_paused", "at": _now(), "stage": paused_after}
                    )
                    runtime.updated_at = _now()
            except Exception as exc:  # noqa: BLE001 - recorded in durable UI state
                with self._lock:
                    runtime.status = "failed"
                    runtime.error = _run_error_text(exc)
                    runtime.updated_at = _now()
            finally:
                if isinstance(llm, OllamaClient):
                    llm.close()
            self._persist_runtime(runtime)

        start_worker(execute, name=f"ads-run-{run_id}")
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
            should_pause = False
            with self._lock:
                runtime.events.append({"event": name, "at": _now(), **payload})
                runtime.updated_at = _now()
                if name == "stage_started":
                    runtime.current_stage = payload.get("stage")
                if name == "gate_decided" and runtime.pause_requested:
                    verdict = payload.get("verdict")
                    if verdict == "auto_proceed":
                        should_pause = True
                    elif verdict in {"escalate", "abort"}:
                        # The run is stopping here on its own -- for a human
                        # answer or an abort, not because of the pause -- but a
                        # stop all the same. Clear the request so it doesn't
                        # linger and hijack a *later*, unrelated resume (#282):
                        # left set, it would silently re-pause the run the next
                        # time it advances, e.g. right after the human answers
                        # the gate it escalated to.
                        runtime.pause_requested = False
                        runtime.events.append(
                            {
                                "event": "pause_request_resolved",
                                "at": _now(),
                                "stage": payload.get("stage"),
                                "reason": verdict,
                            }
                        )
            self._persist_runtime(runtime)
            if should_pause:
                raise _RunPauseRequested()

        return event

    def pause_after_current_stage(self, run_id: str) -> None:
        """Request a lossless pause at the next completed stage boundary."""
        runtime = self._runtime_runs.get(run_id)
        if runtime is None:
            raise KeyError(run_id)
        if runtime.status not in {"running", "resuming"}:
            raise ValueError(f"run {run_id!r} is not running; it is {runtime.status}")
        with self._lock:
            runtime.pause_requested = True
            runtime.events.append(
                {"event": "pause_requested", "at": _now(), "stage": runtime.current_stage}
            )
            runtime.updated_at = _now()
        self._persist_runtime(runtime)

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
            # Staging now owns durable workspace artifacts (reports, chat and
            # the recommendation), so discarding it must remove their index
            # entries as well or the run remains visible in the library.
            self.store.delete_run(run_id)
            assert self.automation_store is not None
            self.automation_store.detach_execution(run_id)

    def rerun_with_same_seed(self, run_id: str) -> dict[str, Any]:
        """Create a fresh staged run that replays the recorded sampler seed.

        Support needs a new run rather than mutating the old audit record. Cache
        reuse is deliberately disabled: reusing an earlier agent artifact would
        prove neither that the sampler reproduced it nor where a divergence began.
        """
        progress = self.progress(run_id)
        configuration = dict(progress.get("configuration") or {})
        source_id = str(progress.get("source_id") or configuration.get("source_id") or "")
        if not source_id:
            raise ValueError("the original run does not record its source")
        if configuration.get("run_seed") is None:
            raise ValueError("the original run predates recorded run seeds")
        run_seed = _coerce_run_seed(configuration["run_seed"])
        for key in ("automation_id", "parent_run_id", "branch_label", "rerun_of"):
            configuration.pop(key, None)
        configuration.update({"run_seed": run_seed, "rerun_of": run_id})
        return self.stage_run(source_id, configuration, reuse_cache=False)

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
        run_seed: int | None = None,
    ) -> str:
        source_path = self.source_path(source_id)
        if mode not in {"agent", "manual"}:
            raise ValueError("mode must be 'agent' or 'manual'")
        if not 1 <= agent_panel_size <= 3:
            raise ValueError("agent_panel_size must be between 1 and 3")
        if mode == "manual":
            self._validate_configuration(source_id, integration_plan, problem, validation_strategy)
        run_id = f"ui-{uuid.uuid4().hex[:12]}"
        run_seed = _coerce_run_seed(run_seed) if run_seed is not None else _new_run_seed()
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
        if run_mode not in {"auto", "manual", "fully_auto"}:
            raise ValueError("run_mode must be 'auto', 'manual', or 'fully_auto'")
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
        if (
            run_mode != "fully_auto"
            and problem.confirmed_by == "auto"
            and "problem_discovery" not in checkpoints
        ):
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
            run_seed=run_seed,
            user_intent=run_intent,
        )
        # #200: bind the request's language to the run so the report renders in
        # it on the worker rather than defaulting to "tr".
        state.blackboard[RUN_LANGUAGE_KEY] = i18n.current()
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
            "run_seed": run_seed,
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
                    runtime.error = _run_error_text(exc)
                    runtime.updated_at = _now()
            finally:
                if isinstance(llm, OllamaClient):
                    llm.close()
            self._persist_runtime(runtime)

        start_worker(execute, name=f"ads-ui-{run_id}")
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
                    runtime.error = _run_error_text(exc)
                    runtime.updated_at = _now()
            finally:
                if isinstance(llm, OllamaClient):
                    llm.close()
            self._persist_runtime(runtime)

        start_worker(execute, name=f"ads-ui-resume-{run_id}")

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
                "agent_seeds": list(attempt.agent_seeds),
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
                # Older recovered runtimes and focused unit-test doubles do
                # not carry the newly added cooperative control field.
                "pause_requested": bool(getattr(runtime, "pause_requested", False)),
                "attempts": self._attempts(runtime),
                # The question the run stopped to ask. It lived only on the
                # in-memory outcome, so `progress` -- the endpoint the run
                # screen polls -- reported `awaiting_human` and carried nothing
                # to show, and a run that had stopped for a person displayed no
                # way to answer it.
                "pending_question": _pending_question(runtime),
            }

    def _persist_runtime(self, runtime: _RuntimeRun) -> None:
        # Staging can profile structured data and extract documents in parallel.
        # Serialize the whole temp-write/replace sequence so their progress
        # callbacks cannot race over the same .tmp path.
        with self._lock:
            snapshot = self._runtime_snapshot(runtime)
            target = self._run_state_root / f"{runtime.run_id}.json"
            temporary = target.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
            temporary.replace(target)
        # Every failure path sets status="failed" then persists here, so this is
        # the one place that catches them all (#82). Done outside the lock above
        # since it touches a different store; mark_error is a no-op once set.
        if runtime.status == "failed":
            self._mark_automation_error(runtime)

    #: Snapshot states that only a live worker in this process can be making
    #: progress on. `awaiting_human` is deliberately absent: it is durable by
    #: design and is meant to be resumed after a restart.
    _IN_FLIGHT = frozenset({"queued", "running", "staging"})

    def tool_activity(self, run_id: str, after: int = 0) -> dict[str, Any]:
        """Tool calls this run has made since `after`, for a live view (#411).

        Tool use only ever surfaced as a count on a finished stage's artifact,
        which is both after the fact and silent about which tools were called.
        The broker publishes every decision to an in-process feed as it makes
        it; this reads it back with a cursor so a panel can poll for what is
        new instead of re-rendering the whole list.

        `active` is the poller's stop signal: without it a panel watching a run
        that finished half an hour ago keeps asking forever.
        """
        events, dropped = tool_activity_feed.since(run_id, after)
        try:
            status = str(self._progress_snapshot(run_id).get("status") or "")
        except KeyError:
            status = ""
        cursor = events[-1].seq if events else after
        return {
            "run_id": run_id,
            "events": [event.as_dict() for event in events],
            "cursor": cursor,
            # A reader away long enough for the ring to wrap gets told, rather
            # than being handed a contiguous list with a silent hole in it.
            "dropped": dropped,
            "active": status in _ACTIVE_RUN_STATUSES,
        }

    def progress(self, run_id: str) -> dict[str, Any]:
        # #305: every progress source carries artifact ids per stage attempt but
        # not their kinds, so the default view cannot tell a result from an agent
        # audit. Attach the diagnostic id set here, once, rather than in each
        # branch -- a copy so an in-memory runtime snapshot is not mutated.
        return {
            **self._progress_snapshot(run_id),
            "diagnostic_artifact_ids": self._diagnostic_artifact_ids(run_id),
        }

    def _progress_snapshot(self, run_id: str) -> dict[str, Any]:
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
        if snapshot.get("status") == "staged":
            workspace = self._latest_staging_workspace(str(snapshot.get("run_id") or ""))
            if (
                workspace is not None
                and workspace.recommended_plan is None
                and workspace.planner_error
            ):
                error = f"Planner failed before creating a plan: {workspace.planner_error}"
                events = list(snapshot.get("events") or [])
                if not any(item.get("event") == "staging_analysis_failed" for item in events):
                    events.append(
                        {"event": "staging_analysis_failed", "at": _now(), "error": error}
                    )
                return {
                    **snapshot,
                    "status": "failed",
                    "current_stage": None,
                    "error": error,
                    "events": events,
                }
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
                # #305: marks the engineering/provenance artifacts the default
                # view hides, so a client never has to re-derive the closed
                # diagnostic set the backend already owns.
                "diagnostic": is_diagnostic_artifact(ref.artifact_type),
                "presentation": self._artifact_presentation(
                    ref.artifact_type.value, ref.name, ref.summary
                ),
            }
            for ref in self.store.list(run_id)
        ]

    def _diagnostic_artifact_ids(self, run_id: str) -> list[str]:
        """Ids of this run's diagnostic artifacts, for the default-view filter.

        Read from the persisted index rather than the progress snapshot: the
        snapshot carries artifact ids per stage attempt but not their kinds, and
        the kind is the only thing that decides whether an artifact is a person's
        result or an engineering record (#305).
        """
        return [
            ref.artifact_id
            for ref in self.store.list(run_id)
            if is_diagnostic_artifact(ref.artifact_type)
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
        elif artifact_type == "rl_feature_report":
            title = i18n.t("Feature engineering search")
            description = i18n.t(
                "Features an external search added or removed, measured on training rows only."
            )
            preferred = [
                (i18n.t("Features added"), "n_generated_features"),
                (i18n.t("Features removed"), "n_removed_features"),
                (i18n.t("Features kept"), "n_selected_features"),
                (i18n.t("Metric"), "primary_metric"),
                (i18n.t("Improvement"), "api_score_improvement"),
            ]
        elif artifact_type == "rl_enhanced_model":
            title = i18n.t("Model with engineered features")
            description = i18n.t(
                "The same candidates refit on engineered features and scored on the same "
                "untouched holdout."
            )
            preferred = [
                (i18n.t("Winner"), "winner_id"),
                (i18n.t("Metric"), "primary_metric"),
                (i18n.t("Holdout score"), "winner_holdout_score"),
                (i18n.t("Features added"), "n_generated_features"),
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
            description = i18n.t("Validated exploratory output produced by locally executed code.")
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
                + (", grouped by {group_column}" if payload.get("group_column") else "")
                + (", ordered by {time_column}" if payload.get("time_column") else "")
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
                i18n.t("Every feature covered"): set(payload.get("feature_columns", [])).issubset(
                    payload.get("covered_columns", [])
                ),
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
        elif artifact_type == "rl_feature_report":
            story["panels"] = rl_feature_panels(payload)
            status = str(payload.get("status") or "")
            if status == "applicable":
                story["suggestion"] = i18n.t(
                    "Added {added} engineered feature(s) and removed {removed}; "
                    "{metric} moved by {delta} on training rows.",
                    added=len(payload.get("generated_features") or []),
                    removed=len(payload.get("removed_features") or []),
                    metric=payload.get("primary_metric"),
                    delta=payload.get("api_score_improvement"),
                )
            elif status == "unavailable":
                story["suggestion"] = i18n.t(
                    "The feature engineering service was unreachable, so this step was skipped."
                )
                # The detail is a transport error, useful to whoever runs the
                # sidecar and harmless to everyone else. It carries no row data.
                story["warnings"] = [
                    text for text in [str(payload.get("detail") or "")] if text
                ]
            else:
                story["suggestion"] = i18n.t(
                    "This dataset cannot support the external feature search."
                )
            story["generated_features"] = [
                {
                    "name": item.get("name"),
                    "expression": item.get("expression"),
                    "inputs": item.get("inputs", []),
                }
                for item in payload.get("generated_features") or []
            ]
            story["removed_features"] = payload.get("removed_features", [])
        elif artifact_type == "rl_enhanced_model":
            story["panels"] = training_panels(payload)
            story["suggestion"] = i18n.t(
                "Refit on {count} engineered feature(s) and scored on the same holdout.",
                count=len(payload.get("generated_feature_names") or []),
            )
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
                            "Local investigator may read a read-only copy; output remains typed."
                        )
                        if row_access
                        else i18n.t(
                            "Planner context contains schema and aggregate measurements only."
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

    def model_download(self, artifact_id: str) -> tuple[bytes, str]:
        """Return the saved fitted-pipeline bytes for one trained-model artifact.

        #166 ends at "the model can be downloaded", but only reports had a
        download route -- a completed run left a saved model with no way to take
        it off the machine. The trained-model artifact records its fitted
        pipeline as a separate blob (ModelBlobReference.artifact_id ->
        blobs/model.joblib); serve that. A model whose training deferred saving
        the blob has nothing to hand back and is reported as such rather than a
        confusing empty file.
        """
        payload = self.artifact_payload(artifact_id)
        blob = payload.get("model_blob")
        if not isinstance(blob, dict) or not blob.get("artifact_id"):
            raise KeyError("no_saved_model")
        filename = str(blob.get("filename") or "model.joblib")
        blob_path = self.store.blob_dir(str(blob["artifact_id"])) / filename
        if not blob_path.exists():
            raise KeyError("model_blob_missing")
        return blob_path.read_bytes(), filename

    @staticmethod
    def _document_table_sample(
        rows: list[Any], *, max_rows: int = 6, max_columns: int = 10, cell_limit: int = 80
    ) -> list[list[str]]:
        """A small, bounded, stringified corner of a candidate table (#303).

        Caps rows and columns so a mis-detected table with thousands of cells
        cannot bloat the preview, and stringifies every cell so the review UI
        renders a table it can trust rather than guessing at mixed JSON types.
        """
        sample: list[list[str]] = []
        for row in rows[:max_rows]:
            cells = row[:max_columns] if isinstance(row, list) else [row]
            sample.append(["" if cell is None else str(cell)[:cell_limit] for cell in cells])
        return sample

    def artifact_preview(self, artifact_id: str) -> dict[str, Any]:
        """Return a graph-inspector preview without exposing rows or document passages."""
        payload = self.artifact_payload(artifact_id)
        if "producer_component_id" in payload and "summary_text" in payload:
            return {
                "artifact_id": artifact_id,
                "artifact_type": "staging_report",
                "producer_component_id": payload.get("producer_component_id"),
                "title": payload.get("title"),
                "summary": payload.get("summary_text"),
                "findings": payload.get("findings", []),
                "verification_questions": payload.get("verification_questions", []),
            }
        if "engine" in payload and isinstance(payload.get("documents"), list):
            documents = []
            for document in payload["documents"]:
                tables = [
                    {
                        "candidate_id": item.get("candidate_id"),
                        "page_number": item.get("page_number"),
                        "title": item.get("title"),
                        "columns": item.get("columns", []),
                        "row_count": len(item.get("rows", [])),
                        # #303: a person cannot accept or reject a table they
                        # cannot see. The columns give the headers; a bounded
                        # sample of rows gives the shape and enough content to
                        # tell a real table from a mis-detected one, without
                        # streaming an arbitrarily large extraction to the UI.
                        # These rows are text a local engine already read off a
                        # PDF the person uploaded -- not host-measured PII.
                        "sample_rows": self._document_table_sample(item.get("rows", [])),
                        "review_status": item.get("review_status"),
                    }
                    for item in document.get("tables", [])
                ]
                figures = [
                    {
                        "candidate_id": item.get("candidate_id"),
                        "page_number": item.get("page_number"),
                        "caption": item.get("caption"),
                        "kind": item.get("kind"),
                        "review_status": item.get("review_status"),
                    }
                    for item in document.get("figures", [])
                ]
                documents.append(
                    {
                        "source_file": document.get("source_file"),
                        "title": document.get("title"),
                        "page_count": document.get("page_count", 0),
                        "text_characters": len(document.get("markdown", "")),
                        "tables": tables,
                        "figures": figures,
                        "warnings": document.get("warnings", []),
                        "warnings_tr": aligned_turkish(
                            document.get("warnings", []), document.get("warnings_tr", [])
                        ),
                        "duration_seconds": document.get("duration_seconds", 0),
                        "status": "ready",
                    }
                )
            return {
                "artifact_id": artifact_id,
                "artifact_type": "document_extraction",
                "engine": payload.get("engine"),
                "engine_version": payload.get("engine_version"),
                "ocr_mode": (payload.get("settings") or {}).get("ocr", "auto"),
                "duration_seconds": payload.get("duration_seconds"),
                "documents": documents,
                "file_results": payload.get("file_results", []),
                "warnings": payload.get("warnings", []),
            }

        scalar = {
            key: value
            for key, value in payload.items()
            if isinstance(value, str | int | float | bool) and key not in {"markdown", "text"}
        }
        collections = {
            key: len(value) for key, value in payload.items() if isinstance(value, list | dict)
        }
        # Report the real indexed type rather than a generic "artifact", so an
        # unrecognised artifact opens under its actual name instead of a
        # meaningless "ARTIFACT" eyebrow (#74). Falls back only when the index
        # has no entry for it.
        indexed = self.store.type_of(artifact_id)
        return {
            "artifact_id": artifact_id,
            "artifact_type": indexed.value if indexed is not None else "artifact",
            "fields": scalar,
            "collection_sizes": collections,
            # #304: opening the EDA (or another measured) artifact directly, not
            # only its stage, should still show the distributions, missingness,
            # correlation heatmap and relationship charts the backend measured.
            "panels": self._preview_panels(indexed, payload),
        }

    @staticmethod
    def _preview_panels(
        artifact_type: ArtifactType | None, payload: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Analysis charts for the artifacts that measure something (#304).

        The same builders the stage inspector uses, so the artifact dialog and
        the stage view cannot disagree about what EDA looks like.
        """
        builders = {
            ArtifactType.EDA_REPORT: eda_panels,
            ArtifactType.VALIDATION_STRATEGY: validation_panels,
            ArtifactType.LEAKAGE_REPORT: leakage_panels,
            ArtifactType.EVALUATION_REPORT: evaluation_panels,
            ArtifactType.RL_FEATURE_REPORT: rl_feature_panels,
            ArtifactType.RL_ENHANCED_MODEL: training_panels,
        }
        builder = builders.get(artifact_type) if artifact_type is not None else None
        return builder(payload) if builder is not None else []

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
        # #304: EDA and the other measured stages build their analysis charts --
        # distributions, missingness, a correlation heatmap, target
        # relationships -- into each output artifact's story, but the guided
        # stage inspector renders outputs as buttons and only ever showed the
        # multi-source panels above. Hoist the primary output's panels so the
        # inspector has the same visual summary the design has always specified.
        if not stage_panels and latest_story:
            stage_panels = latest_story.get("panels", [])
        stage_status = self._stage_status(
            attempts[-1] if attempts else None,
            progress.get("current_stage"),
            progress.get("status"),
            human_approved=any(
                event.get("event") == "human_decision_recorded"
                and event.get("stage") == stage_id
                and event.get("decision") == "approve"
                for event in progress.get("events", [])
            ),
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

    def _experiment_summary(self, summary: RunSummary) -> dict[str, Any]:
        try:
            progress = self.progress(summary.run_id)
        except KeyError:
            progress = {}
        decisions = self.gate_decisions(summary.run_id)
        attempts = progress.get("attempts", [])
        return {
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

    @staticmethod
    def _winner_metric(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """The winning candidate and its primary-metric evaluation."""
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
        return winner, metric

    def _model_summaries(self, run: RunSummary) -> list[dict[str, Any]]:
        """One row per trained model, with its RL-enhanced counterpart attached.

        Deliberately one row rather than two: a run produces one training
        outcome, and listing the enhanced variant as its own card would read as
        two unrelated models rather than two downloads of the same result. The
        enhanced artifact names the model it belongs to, so the pairing survives
        a run that trained more than once.
        """
        artifacts = self.artifacts(run.run_id)
        enhanced_by_base: dict[str, dict[str, Any]] = {}
        for artifact in artifacts:
            if artifact["type"] != ArtifactType.RL_ENHANCED_MODEL.value:
                continue
            payload = self.artifact_payload(artifact["artifact_id"])
            base_id = str(payload.get("base_model_artifact_id") or "")
            if base_id:
                enhanced_by_base[base_id] = {**payload, "artifact_id": artifact["artifact_id"]}

        models: list[dict[str, Any]] = []
        for artifact in artifacts:
            if artifact["type"] != ArtifactType.TRAINED_MODEL.value:
                continue
            payload = self.artifact_payload(artifact["artifact_id"])
            winner, metric = self._winner_metric(payload)
            models.append(
                {
                    "artifact_id": artifact["artifact_id"],
                    "run_id": run.run_id,
                    "created_at": artifact["created_at"],
                    "winner_id": payload.get("winner_id"),
                    "display_name": winner.get("display_name", payload.get("winner_id")),
                    "estimator": winner.get("estimator_class"),
                    "metric": payload.get("primary_metric"),
                    "holdout_score": metric.get("holdout_score"),
                    "cv_mean": metric.get("cv_mean"),
                    "cv_std": metric.get("cv_std"),
                    "saved": bool(payload.get("model_blob")),
                    "candidate_count": len(payload.get("results", [])),
                    "training_rows": payload.get("training_row_count"),
                    "enhanced": self._enhanced_summary(
                        enhanced_by_base.get(artifact["artifact_id"]),
                        base_score=metric.get("holdout_score"),
                        metric_name=payload.get("primary_metric"),
                    ),
                }
            )
        return models

    def _enhanced_summary(
        self,
        payload: dict[str, Any] | None,
        *,
        base_score: float | None,
        metric_name: Any,
    ) -> dict[str, Any] | None:
        """The RL-enhanced counterpart of one model, as the card's second download."""
        if not payload:
            return None
        winner, metric = self._winner_metric(payload)
        score = metric.get("holdout_score")
        delta: float | None = None
        if isinstance(score, int | float) and isinstance(base_score, int | float):
            # Oriented so a positive number always means "better", whichever way
            # the metric runs. The card colours on this sign.
            lower_is_better = str(metric_name) in {"rmse", "mae", "mape"}
            delta = float(base_score - score) if lower_is_better else float(score - base_score)
        return {
            "artifact_id": payload["artifact_id"],
            "display_name": winner.get("display_name", payload.get("winner_id")),
            "estimator": winner.get("estimator_class"),
            "holdout_score": score,
            "score_delta": delta,
            "generated_feature_count": len(payload.get("generated_feature_names") or []),
            "saved": bool(payload.get("model_blob")),
        }

    def _report_summaries(self, run: RunSummary) -> list[dict[str, Any]]:
        reports: list[dict[str, Any]] = []
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
            assert self.automation_store is not None
            automations = self.automation_store.detach_execution(run_id)
        return {
            "run_id": run_id,
            **deleted,
            "snapshot": int(snapshot_deleted),
            "automations": automations,
        }

    def delete_model(self, artifact_id: str) -> dict[str, Any]:
        """Delete one trained-model artifact. The run it came from is untouched.

        The RL-enhanced counterpart goes with it. The two share one card and one
        delete control, so leaving the enhanced artifact behind would strand a
        model with no affordance left to remove it.
        """
        artifact_type = self.store.type_of(artifact_id)
        if artifact_type is None:
            raise KeyError(artifact_id)
        if artifact_type != ArtifactType.TRAINED_MODEL:
            raise ValueError(f"artifact {artifact_id!r} is not a trained model")
        removed = self.store.delete_artifact(artifact_id)
        for enhanced_id in self._enhanced_model_ids(artifact_id):
            self.store.delete_artifact(enhanced_id)
        return {"artifact_id": artifact_id, **removed}

    def _enhanced_model_ids(self, base_artifact_id: str) -> list[str]:
        """Every RL-enhanced artifact that names ``base_artifact_id`` as its base."""
        found: list[str] = []
        for ref in self.store.list_all(ArtifactType.RL_ENHANCED_MODEL):
            payload = self.artifact_payload(ref.artifact_id)
            if str(payload.get("base_model_artifact_id") or "") == base_artifact_id:
                found.append(ref.artifact_id)
        return found

    def delete_report(self, artifact_id: str) -> dict[str, Any]:
        """Delete one final-report artifact. The run it came from is untouched."""
        artifact_type = self.store.type_of(artifact_id)
        if artifact_type is None:
            raise KeyError(artifact_id)
        if artifact_type != ArtifactType.FINAL_REPORT:
            raise ValueError(f"artifact {artifact_id!r} is not a final report")
        removed = self.store.delete_artifact(artifact_id)
        return {"artifact_id": artifact_id, **removed}


def _run_error_text(exc: BaseException) -> dict[str, str]:
    """Bir kosum hatasini insanin okuyabilecegi hale getir, iki dilde.

    Ekrana `MissingArtifactError: run 'run-446ef0b8' has no 'integration_plan'
    artifact; an upstream stage did not produce it` diye dusuyordu (#263):
    Python sinif adi, run id'si ve tek dil. Kullanicinin yapabilecegi bir sey
    yok, hatta ne oldugunu bile anlamiyor.

    Bilinen turler ceviriliyor; BILINMEYEN her sey eski haliyle geciyor. Bu
    bilerek: tanimadigimiz bir hatayi guzel bir cumleye cevirmek, teshis icin
    gereken tek bilgiyi silmek olurdu. Kirmizi bandin hic Turkcelesmemesi de
    (#265) buradan geliyordu -- alan artik iki dilli.
    """
    ad = type(exc).__name__
    if isinstance(exc, MissingArtifactError):
        eksik = re.search(r"has no '([^']+)' artifact", str(exc))
        tur = eksik.group(1) if eksik else "?"
        return {
            "en": (
                f"A stage before this one did not produce {tur!r}, which the next stage "
                "requires, so the run cannot continue from here. Send the stage that "
                "produces it back for rework, or start a new run."
            ),
            "tr": (
                f"Bundan onceki bir asama {tur!r} uretmedi; sonraki asama onu zorunlu "
                "olarak istiyor, bu yuzden kosum buradan devam edemiyor. Onu ureten "
                "asamayi yeniden calistir ya da yeni bir kosum baslat."
            ),
        }
    ham = f"{ad}: {exc}"
    return {"en": ham, "tr": ham}


def _cannot_stage_error(runtime: _RuntimeRun | None) -> ValueError:
    """Say why a staged-run action can't proceed, in words the reader can act on.

    Both `/staged` (PATCH) and `/start` (POST) used to raise the same bare
    ``ValueError("this run is not staged")`` whatever the actual reason was --
    a run that had finished, failed, aborted, or was waiting on a human answer
    all landed on the same sentence, untranslated, in the reader's face (#282,
    same class of issue as #263/#265). The status is already known here, so
    say what happened instead of repeating the field name back at them.
    """
    if runtime is None:
        return ValueError(i18n.t("This run no longer exists; choose the dataset again."))
    status = runtime.status
    if status == "awaiting_human":
        return ValueError(
            i18n.t("This run is waiting for your answer to a question, not for a restart.")
        )
    if status == "completed":
        return ValueError(i18n.t("This run already finished and cannot be resumed."))
    if status == "aborted":
        return ValueError(i18n.t("This run was aborted and cannot be resumed."))
    if status == "failed":
        return ValueError(i18n.t("This run failed and cannot be resumed; start a new run."))
    if status in {"running", "resuming"}:
        return ValueError(i18n.t("This run is already in progress."))
    return ValueError(i18n.t("This run is not staged."))


def _promotion_message(exc: CandidateNotPromotable) -> str:
    """Say why one extracted table cannot be promoted, in the reader's language.

    Precedent and reasoning are `_cannot_stage_error`'s (#263/#265/#282): the
    promote endpoint answers with the exception text, so a raw
    ``ValueError("accepted candidate '0001_…:table:2' has no rows")`` reached
    the reader verbatim -- English whatever their language, and naming an
    internal identifier they have never seen (#310). The candidate carries its
    own provenance, so name the table the way the review dialog named it.
    """
    name = exc.title or exc.source_file
    table = (
        i18n.t("{name} (page {page})", name=name, page=exc.page_number)
        if exc.page_number
        else name
    )
    if exc.code == "no_rows":
        return i18n.t(
            "No rows were extracted from the table “{table}”, so it cannot become data.",
            table=table,
        )
    if exc.code == "no_columns":
        return i18n.t(
            "No columns were extracted from the table “{table}”, so it cannot become data.",
            table=table,
        )
    if exc.code == "ragged_rows":
        return i18n.t(
            "The rows extracted from the table “{table}” do not all have the same "
            "number of cells, so it cannot become data.",
            table=table,
        )
    return i18n.t(
        "The headers extracted from the table “{table}” do not match its rows, "
        "so it cannot become data.",
        table=table,
    )


def _resolved_pipeline_recommendation(
    requested: Any,
    previous: RuntimeConfigurationPlan | None,
    tables_await_review: bool,
) -> str:
    """What one planner turn is allowed to say about running a pipeline (#316).

    This used to be ``str(requested or "create_pipeline")``, so *any* chat turn
    published a runnable plan and unlocked the ML controls -- a question about
    the raw files, or a reply whose own text told the reader to review the
    extracted PDF tables first. Two rules replace that default:

    A turn that names no recommendation has made no decision, so the workspace
    keeps the one it already carries and a first turn defers. And promoting an
    extracted table is a human decision the product refuses to make silently, so
    a plan that would start ML while candidates sit unreviewed is proposing to
    skip it, and defers instead however confident the planner was.
    """
    valid = {"create_pipeline", "defer_pipeline", "no_pipeline"}
    recommendation = str(requested or "")
    if recommendation not in valid:
        recommendation = previous.pipeline_recommendation if previous else "defer_pipeline"
    if recommendation == "create_pipeline" and tables_await_review:
        return "defer_pipeline"
    return recommendation


def _pending_question(runtime: Any) -> dict[str, Any] | None:
    """The escalation a run is stopped on RIGHT NOW, ready for the wire.

    The status check is the whole point. The outcome keeps its question after
    the run resumes, so a running run kept reporting the gate it had already
    answered: the 2.2s poll put the stale question straight back, the card
    re-rendered from it, and answering it returned 400 -- "this run is not
    awaiting a human decision" -- because the run had moved on (#191).

    One button appearing to work while another failed was a race, not a
    difference between the options: whether a click landed inside a window
    where the run happened to be waiting again.

    Reported here rather than guarded in the card, because a card cannot know
    the question is stale, and every other reader of this payload would have
    had to learn the same rule.
    """
    if getattr(runtime, "status", None) != "awaiting_human":
        return None
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

    # DeepSeek is a paid remote backend billed to one person's key. Every other
    # backend is either local or a subscription the team already shares, so it
    # is the only one that needs an owner. Read from the environment rather than
    # from the branch below, so an injected ControlPlane is gated too. The
    # allowlist is comma-separated so adding a teammate is a config change.
    paid_backend = os.environ.get("ADS_LLM_BACKEND", "").strip().casefold() == "deepseek"
    allowed_users = {
        name.strip()
        for name in os.environ.get("ADS_DEEPSEEK_USERS", "ishak-ads").split(",")
        if name.strip()
    }

    store = ArtifactStore(artifacts_dir)
    if plane is None:
        backend = os.environ.get("ADS_LLM_BACKEND", "ollama").strip().casefold()
        if backend not in {"ollama", "claude_cli", "deepseek"}:
            raise ValueError("ADS_LLM_BACKEND must be 'ollama', 'claude_cli', or 'deepseek'")
        llm_factory: Callable[[], StructuredLLM] | None = None
        if backend == "claude_cli":
            model = os.environ.get("ADS_CLAUDE_MODEL", "haiku")
            effort = os.environ.get("ADS_CLAUDE_EFFORT", "low")
            # Defaults to the client's own budget rather than undercutting it.
            # 90s was the previous default and it expired during real staging
            # synthesis: that call is one bounded request covering every routed
            # source, so a mixed PDF/tabular project needs materially more than
            # a single-table one. Set ADS_CLAUDE_TIMEOUT to tighten it again.
            timeout = float(
                os.environ.get("ADS_CLAUDE_TIMEOUT", str(DEFAULT_CLAUDE_TIMEOUT))
            )

            def create_claude_cli() -> StructuredLLM:
                return ClaudeCliClient(model=model, effort=effort, timeout=timeout)

            llm_factory = create_claude_cli
        if backend == "deepseek":
            ds_model = os.environ.get("ADS_DEEPSEEK_MODEL", DEFAULT_DEEPSEEK_MODEL)
            ds_timeout = float(
                os.environ.get("ADS_DEEPSEEK_TIMEOUT", str(DEFAULT_DEEPSEEK_TIMEOUT))
            )
            ds_rpm = int(
                os.environ.get("ADS_DEEPSEEK_RPM", str(DEFAULT_REQUESTS_PER_MINUTE))
            )
            # One limiter for the process, not one per agent call: the budget
            # protects a single shared key, so every caller must draw from the
            # same bucket or the ceiling means nothing.
            ds_limiter = RateLimiter(requests_per_minute=ds_rpm)

            def create_deepseek() -> StructuredLLM:
                return DeepSeekClient(model=ds_model, timeout=ds_timeout, limiter=ds_limiter)

            llm_factory = create_deepseek

        # Per-user budgets on top of whichever backend was chosen. The DeepSeek
        # limiter above protects one shared API key; this protects people from
        # each other, which is a different question and applies to the local
        # backends too -- one runaway pipeline should not starve four colleagues
        # of the same finite machine. One limiter for the process, because the
        # buckets it holds are already per user and every caller must draw from
        # the same ones or the ceiling means nothing.
        agent_rpm = int(os.environ.get("ADS_AGENT_RPM", str(DEFAULT_REQUESTS_PER_MINUTE)))
        user_limiter = PerUserRateLimiter(
            requests_per_minute=agent_rpm,
            multipliers=multipliers_from_env(),
        )
        if llm_factory is not None:
            backend_factory = llm_factory

            def create_budgeted() -> StructuredLLM:
                return BudgetedLLM(backend_factory(), user_limiter)

            llm_factory = create_budgeted

        plane = ControlPlane(
            store=store,
            source_roots=tuple(Path(root) for root in source_roots or ()),
            llm_factory=llm_factory,
            # Sits beside the artifact store rather than in a temp directory, so
            # it survives a restart -- which is the entire point of persisting
            # it. Deleting the directory is always safe; it only costs one
            # re-profile per source.
            profile_cache_dir=Path(artifacts_dir).parent / "cache" / "profiles",
        )
    app = FastAPI(title="Agentic DS workflow", version="0.2.0")

    # Password gate. Installed only when a credential is configured, so the
    # loopback launcher and the test suite are unaffected; the public launcher
    # refuses to start without one. See ads/api/auth.py.
    auth_config = config_from_env()
    if auth_config is not None and auth_config.enabled:
        install_auth(app, auth_config)

    @app.middleware("http")
    async def attribute_agent_calls(request: Request, call_next):
        """Bind the caller for the duration of one request.

        Agent calls happen several layers down, inside pipeline stages that have
        no business knowing about authentication, so the username travels in a
        context variable rather than through their signatures. Runs spawned from
        here inherit it because `start_worker` copies the context; a thread
        started any other way would execute as anonymous and quietly share the
        anonymous budget with every other unattributed run.
        """
        with bind_user(getattr(request.state, "username", None)):
            return await call_next(request)

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
    def planner_chat(body: dict[str, Any], request: Request) -> dict[str, Any]:
        _guard_paid_backend(request)
        try:
            return plane.planner_chat(
                message=str(body["message"]),
                configuration=body.get("configuration"),
                history=body.get("history"),
                source_id=body.get("source_id"),
                run_id=body.get("run_id"),
                stage_id=body.get("stage_id"),
            )
        except PlannerResponseError as exc:
            # #242: a malformed model reply is an upstream failure, not a bad
            # request. 502 says the gateway got a bad response from the model,
            # and the detail is a readable sentence rather than a parser offset.
            raise HTTPException(status_code=502, detail=str(exc)) from None
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.post("/api/runs/{run_id}/planner-overrides/{proposal_id}/apply")
    def apply_planner_override(run_id: str, proposal_id: str) -> dict[str, Any]:
        try:
            return plane.apply_planner_override(run_id, proposal_id)
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.post("/api/runs/{run_id}/planner-overrides/{proposal_id}/discard")
    def discard_planner_override(run_id: str, proposal_id: str) -> dict[str, Any]:
        try:
            return plane.discard_planner_override(run_id, proposal_id)
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.get("/api/automations")
    def automations() -> list[dict[str, Any]]:
        return plane.list_automations()

    @app.post("/api/automations")
    def create_automation(body: dict[str, Any]) -> dict[str, Any]:
        try:
            return plane.create_automation(str(body["name"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    def _viewer(request: Request) -> str | None:
        """Who is asking. Set by the auth middleware once the session is read."""
        return getattr(request.state, "username", None)

    # Every project route below takes the `Request` so it can name the viewer.
    # A project is its creator's until they say otherwise (#206), and a route
    # that does not ask who is calling cannot enforce that -- which is how every
    # account came to see every project.
    @app.get("/api/projects")
    def projects(request: Request) -> list[dict[str, Any]]:
        return plane.list_projects(viewer=_viewer(request))

    @app.post("/api/projects")
    def create_project(body: dict[str, Any], request: Request) -> dict[str, Any]:
        try:
            return plane.create_project(str(body["name"]), owner=_viewer(request))
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.get("/api/projects/{project_id}")
    def project(project_id: str, request: Request) -> dict[str, Any]:
        try:
            return plane.project(project_id, viewer=_viewer(request))
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown project") from None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.put("/api/projects/{project_id}")
    def update_project(
        project_id: str, body: dict[str, Any], request: Request
    ) -> dict[str, Any]:
        try:
            return plane.update_project(
                project_id,
                expected_revision=int(body["expected_revision"]),
                changes=dict(body.get("changes") or {}),
                viewer=_viewer(request),
            )
        except ProjectRevisionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown project") from None
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.delete("/api/projects/{project_id}")
    def delete_project(project_id: str, request: Request) -> dict[str, Any]:
        try:
            return plane.delete_project(project_id, viewer=_viewer(request))
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown project") from None

    @app.post("/api/projects/{project_id}/visibility")
    def set_project_visibility(
        project_id: str, body: dict[str, Any], request: Request
    ) -> dict[str, Any]:
        """Make a project visible to every signed-in account, or take it back.

        The one thing a non-owner may not do on an otherwise shared project, so
        it is the one project write that answers 403 rather than 404 -- they can
        already see it, and the id is no longer a secret from them.
        """
        try:
            return plane.set_project_visibility(
                project_id,
                visibility=str(body.get("visibility", "")),
                viewer=_viewer(request),
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown project") from None
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.post("/api/projects/{project_id}/sources")
    def add_project_source(
        project_id: str, body: dict[str, Any], request: Request
    ) -> dict[str, Any]:
        try:
            return plane.add_project_source(
                project_id, str(body["source_id"]), viewer=_viewer(request)
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown project or source") from None
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.get("/api/projects/{project_id}/data")
    def project_data(project_id: str, request: Request) -> list[dict[str, Any]]:
        try:
            return plane.project_data(project_id, viewer=_viewer(request))
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown project") from None

    @app.get("/api/projects/{project_id}/automations")
    def project_automations(project_id: str, request: Request) -> list[dict[str, Any]]:
        try:
            return plane.project_automations(project_id, viewer=_viewer(request))
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown project") from None

    @app.post("/api/projects/{project_id}/automations")
    def create_project_automation(
        project_id: str, body: dict[str, Any], request: Request
    ) -> dict[str, Any]:
        try:
            return plane.create_project_automation(
                project_id, str(body["name"]), viewer=_viewer(request)
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown project") from None
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.get("/api/automations/{automation_id}")
    def automation(automation_id: str) -> dict[str, Any]:
        try:
            return plane.automation(automation_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown automation") from None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.put("/api/automations/{automation_id}")
    def update_automation(automation_id: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            changes = dict(body.get("changes") or {})
            return plane.update_automation(
                automation_id,
                expected_revision=int(body["expected_revision"]),
                changes=changes,
            )
        except AutomationRevisionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown automation") from None
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.put("/api/automations/{automation_id}/inputs")
    def select_automation_inputs(automation_id: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            return plane.select_automation_inputs(
                automation_id,
                expected_revision=int(body["expected_revision"]),
                selections=list(body.get("selections") or []),
            )
        except AutomationRevisionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown automation") from None
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.delete("/api/automations/{automation_id}")
    def delete_automation(automation_id: str) -> dict[str, Any]:
        try:
            return plane.delete_automation(automation_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown automation") from None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.get("/api/automations/{automation_id}/executions")
    def automation_executions(automation_id: str) -> list[dict[str, Any]]:
        try:
            automation_record = plane.automation(automation_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown automation") from None
        execution_ids = set(automation_record["execution_ids"])
        return [
            summary.to_dict() for summary in plane.list_runs() if summary.run_id in execution_ids
        ]

    @app.get("/api/automations/{automation_id}/contents")
    def automation_contents(automation_id: str, request: Request) -> dict[str, Any]:
        try:
            return plane.automation_contents(automation_id, viewer=_viewer(request))
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown automation") from None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.get("/api/home")
    def home(request: Request, search: str | None = None) -> dict[str, Any]:
        return plane.home_overview(search=search, viewer=_viewer(request))

    @app.get("/api/projects/{project_id}/contents")
    def project_contents(project_id: str, request: Request) -> dict[str, Any]:
        try:
            return plane.project_contents(project_id, viewer=_viewer(request))
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown project") from None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.get("/api/runs")
    def runs() -> list[dict[str, Any]]:
        return [summary.to_dict() for summary in plane.list_runs()]

    @app.get("/api/data-sources")
    def data_sources(request: Request) -> list[dict[str, Any]]:
        return plane.data_sources(viewer=_viewer(request))

    @app.post("/api/data-sources/{source_id}/visibility")
    def set_source_visibility(
        source_id: str, body: dict[str, Any], request: Request
    ) -> dict[str, Any]:
        """Share a source with the team, or keep it to yourself.

        Only the owner may change this. Anyone else gets 403 rather than 404:
        they can already see the source, so pretending it does not exist would
        be a lie they can disprove.
        """
        viewer = _viewer(request)
        ownership = plane._ownership()
        owner = ownership.owner_of(source_id)
        if owner is None:
            raise HTTPException(
                status_code=400, detail="This source has no recorded owner."
            )
        if viewer != owner:
            raise HTTPException(
                status_code=403, detail=f"Only {owner} can change who sees this source."
            )
        try:
            ownership.set_visibility(source_id, str(body.get("visibility", "")))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return {
            "source_id": source_id,
            "owner": owner,
            "visibility": ownership.visibility_of(source_id),
        }

    @app.get("/api/catalog/datasets")
    def dataset_catalog(
        request: Request,
        search: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, Any]:
        return plane.dataset_catalog(
            search=search,
            page=page,
            page_size=page_size,
            viewer=_viewer(request),
        )

    @app.get("/api/hardening")
    def hardening_status() -> dict[str, Any]:
        return plane.hardening_status()

    @app.get("/api/data-sources/{source_id}/profile")
    def source_profile(source_id: str, request: Request) -> dict[str, Any]:
        try:
            plane.require_source_view(source_id, viewer=_viewer(request))
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
            return plane.upload(
                filename,
                await request.body(),
                source_id=source_id,
                owner=_viewer(request),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.post("/api/demo-data/pdf")
    def install_pdf_demo(request: Request) -> dict[str, Any]:
        try:
            return plane.install_pdf_demo(owner=_viewer(request))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.delete("/api/data-sources/{source_id}/files/{filename}")
    def remove_source_file(
        source_id: str, filename: str, request: Request
    ) -> dict[str, Any]:
        try:
            return plane.remove_upload_file(
                source_id, filename, owner=_viewer(request)
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown source file") from None
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
                run_seed=body.get("run_seed"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return {"run_id": run_id}

    @app.post("/api/runs/{run_id}/rerun")
    def rerun_with_same_seed(run_id: str, request: Request) -> dict[str, Any]:
        _guard_paid_backend(request)
        try:
            return plane.rerun_with_same_seed(run_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown run") from None
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

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

    def _guard_paid_backend(request: Request) -> None:
        """Refuse work that would spend someone else's API key.

        Enforced at the request boundary rather than inside the client: a run
        executes on a worker thread that has no session, so the only place the
        caller is still known is here.
        """
        if not paid_backend:
            return
        username = getattr(request.state, "username", None)
        if username not in allowed_users:
            raise HTTPException(
                status_code=403,
                detail=(
                    "The DeepSeek backend is restricted to "
                    f"{', '.join(sorted(allowed_users))}. Signed in as "
                    f"{username or 'an unidentified session'}."
                ),
            )

    @app.post("/api/runs/staged")
    def stage_run(body: dict[str, Any], request: Request) -> dict[str, Any]:
        _guard_paid_backend(request)
        try:
            configuration = dict(body.get("configuration") or {})
            automation_id = body.get("automation_id")
            if automation_id:
                configuration["automation_id"] = str(automation_id)
                plane.automation(str(automation_id))
            staged = plane.stage_run(
                str(body["source_id"]),
                configuration,
                reuse_cache=bool(body.get("reuse_cache", False)),
            )
            if automation_id:
                plane.attach_automation_execution(
                    str(automation_id),
                    run_id=str(staged["run_id"]),
                    source_id=str(body["source_id"]),
                )
            return staged
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.get("/api/data-sources/{source_id}/pipeline-blueprint")
    def default_staging_pipeline(source_id: str, request: Request) -> dict[str, Any]:
        try:
            plane.require_source_view(source_id, viewer=_viewer(request))
            return plane.default_staging_pipeline(source_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown source") from None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.get("/api/runs/{run_id}/staging")
    def staging_workspace(run_id: str) -> dict[str, Any]:
        try:
            return plane.staging_workspace(run_id)
        except KeyError:
            raise HTTPException(
                status_code=404, detail=f"staging workspace for {run_id!r} is not ready"
            ) from None

    @app.get("/api/staging/components")
    def staging_components() -> dict[str, Any]:
        return {
            "document_engines": document_engine_catalog(),
            "automation_components": [
                item.model_dump(mode="json") for item in automation_component_catalog()
            ],
        }

    @app.get("/api/automation/components")
    def automation_components() -> dict[str, Any]:
        return {
            "components": [item.model_dump(mode="json") for item in automation_component_catalog()],
            "document_engines": document_engine_catalog(),
        }

    @app.put("/api/runs/{run_id}/staging/pipeline")
    def update_staging_pipeline(run_id: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            return plane.update_staging_pipeline(
                run_id,
                dict(body["blueprint"]),
                base_artifact_id=str(body["base_artifact_id"]),
            )
        except StagingWorkspaceConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.put("/api/runs/{run_id}/staging/layout")
    def update_staging_layout(run_id: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            return plane.update_staging_layout(
                run_id,
                dict(body["layout"]),
                base_artifact_id=str(body["base_artifact_id"]),
            )
        except StagingWorkspaceConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.post("/api/runs/{run_id}/staging/plan/accept")
    def accept_staging_plan(run_id: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            return plane.accept_staging_plan(
                run_id,
                base_artifact_id=str(body["base_artifact_id"]),
            )
        except StagingWorkspaceConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.post("/api/runs/{run_id}/automation/compile")
    def compile_automation(run_id: str) -> dict[str, Any]:
        try:
            return plane.compile_automation(run_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="staging workspace not found") from None
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.post("/api/runs/{run_id}/automation/branches/start")
    def start_automation_branches(run_id: str) -> dict[str, Any]:
        try:
            return {"parent_run_id": run_id, "branches": plane.start_automation_branches(run_id)}
        except KeyError:
            raise HTTPException(status_code=404, detail="staging workspace not found") from None
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.post("/api/runs/{run_id}/staging/documents/run")
    def run_document_understanding(run_id: str) -> dict[str, Any]:
        try:
            return plane.run_document_understanding(run_id)
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.post("/api/runs/{run_id}/staging/documents/review")
    def review_document_tables(run_id: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            raw = body.get("decisions") or []
            decisions = {
                str(item["candidate_id"]): str(item["decision"])
                for item in raw
                if isinstance(item, dict)
            }
            return plane.review_document_tables(run_id, decisions)
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.post("/api/runs/{run_id}/staging/documents/promote")
    def promote_document_tables(run_id: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            return plane.promote_document_tables(run_id, str(body.get("review_artifact_id") or ""))
        except CandidateNotPromotable as exc:
            # #310: the reader used to get the exception text verbatim -- English
            # whatever their language, and carrying the internal candidate id.
            # The code is turned into a sentence here, where the request's
            # language is bound, and the table is named the way the review
            # dialog named it.
            raise HTTPException(status_code=400, detail=_promotion_message(exc)) from None
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

    @app.post("/api/runs/{run_id}/pause")
    def pause_run(run_id: str) -> dict[str, str]:
        try:
            plane.pause_after_current_stage(run_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown run") from None
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        return {"run_id": run_id, "status": "pause_requested"}

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

    @app.get("/api/runs/{run_id}/tool-activity")
    def tool_activity(run_id: str, after: int = 0) -> dict[str, Any]:
        # #411: an unknown run is not an error here. The feed is in-memory and
        # a panel may ask about a run this process never executed; an empty,
        # inactive answer stops its polling, where a 404 would make it retry.
        return plane.tool_activity(run_id, after=max(0, after))

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

    @app.get("/api/artifacts/{artifact_id}/preview")
    def artifact_preview(artifact_id: str, response: Response) -> dict[str, Any]:
        try:
            payload = plane.artifact_preview(artifact_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown artifact") from None
        # #368: an artifact is immutable by contract -- a stage produces a new
        # one rather than mutating an existing one, which is what makes fork,
        # time-travel and audit cheap -- so this body cannot change for a given
        # id. Saying so lets the browser answer a repeat itself, including
        # across a full reload, which no in-memory client cache can cover.
        # `private` because a preview belongs to the signed-in reader's run and
        # must not sit in a shared proxy. The prose is composed per language,
        # which `?lang=` puts in the cache key; `Vary` covers a caller that
        # leaves it off and relies on Accept-Language instead.
        response.headers["Cache-Control"] = "private, max-age=31536000, immutable"
        response.headers["Vary"] = "Accept-Language"
        return payload

    @app.get("/api/models/{artifact_id}/download")
    def download_model(artifact_id: str) -> Response:
        try:
            data, filename = plane.model_download(artifact_id)
        except KeyError as exc:
            reason = str(exc)
            if reason in {"no_saved_model", "model_blob_missing"}:
                raise HTTPException(
                    status_code=404, detail="this model has no downloadable blob"
                ) from None
            raise HTTPException(status_code=404, detail="unknown model") from None
        return Response(
            data,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="ads-model-{artifact_id[:12]}-{filename}"'
                )
            },
        )

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

    @app.delete("/api/models/{artifact_id}")
    def delete_model(artifact_id: str) -> dict[str, Any]:
        try:
            return plane.delete_model(artifact_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown model") from None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.delete("/api/reports/{artifact_id}")
    def delete_report(artifact_id: str) -> dict[str, Any]:
        try:
            return plane.delete_report(artifact_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown report") from None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

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
