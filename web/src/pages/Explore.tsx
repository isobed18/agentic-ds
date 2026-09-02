import { activeLanguage, t } from "../lib/i18n";
/**
 * The front door: get data in, understand it, then decide how the run should go.
 *
 * This exists because the product's first step was missing. The API has always
 * accepted uploads and profiled sources; nothing in the UI called either, so a
 * dataset could only arrive by being placed on the server's filesystem by hand,
 * and runs were started from a page that never showed the data.
 *
 * Nothing here is a model's opinion. Intake runs the moment a file lands, and
 * the relationships drawn below are measured overlaps between real column
 * values — including the ones the data never declares. The planner can be asked
 * about any of it, but it answers from the same profile shown on screen.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  api,
  type DataSource,
  type ProfiledTable,
  type PipelineBlueprint,
  type SourceProfile,
  type StagingWorkspace,
} from "../lib/api";
import { Badge, Empty, Spinner, cx } from "../components/ui";
import { PlannerPanel } from "../components/PlannerPanel";
import { PipelineBuilder } from "../components/PipelineBuilder";
import { SchemaDiagram } from "../components/SchemaDiagram";

export function Explore({ onStart }: { onStart: (runId: string) => void }) {
  const [searchParams] = useSearchParams();
  const [sources, setSources] = useState<DataSource[]>([]);
  const [sourceId, setSourceId] = useState<string>(() => searchParams.get("source") ?? "");
  const [profile, setProfile] = useState<SourceProfile | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [openTable, setOpenTable] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [runStatus, setRunStatus] = useState<string | null>(null);
  const [workspace, setWorkspace] = useState<StagingWorkspace | null>(null);
  const [pipelineBlueprint, setPipelineBlueprint] = useState<PipelineBlueprint | null>(null);
  const [reuseCache, setReuseCache] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const refreshSources = useCallback(async () => {
    try {
      setSources(await api.dataSources());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => { void refreshSources(); }, [refreshSources]);

  // Intake runs here, not on a button. The profile *is* the intake result, so
  // selecting a dataset is the same action as reading it.
  useEffect(() => {
    setRunId(null);
    setRunStatus(null);
    setWorkspace(null);
    setPipelineBlueprint(null);
    if (!sourceId) { setProfile(null); return; }
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([api.sourceProfile(sourceId), api.runs(), api.defaultStagingPipeline(sourceId)])
      .then(async ([p, runs, defaultPipeline]) => {
        if (cancelled) return;
        setProfile(p);
        setPipelineBlueprint(defaultPipeline);
        setOpenTable(p.tables[0]?.name ?? null);
        const previous = runs.find(
          (run) => run.source_id === sourceId && (run.stages ?? []).includes("staging"),
        );
        if (!previous) return;
        const saved = await api.stagingWorkspace(previous.run_id).catch(() => null);
        if (cancelled || !saved) return;
        setRunId(previous.run_id);
        setRunStatus(previous.status);
        setWorkspace(saved);
        if (saved.pipeline_blueprint) setPipelineBlueprint(saved.pipeline_blueprint);
      })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [sourceId]);

  useEffect(() => {
    if (!runId || runStatus !== "staging") return;
    let cancelled = false;
    const poll = async () => {
      try {
        const progress = await api.runProgress(runId);
        if (cancelled) return;
        const status = String(progress.status ?? "staging");
        setRunStatus(status);
        if (status === "staged") {
          const saved = await api.stagingWorkspace(runId);
          setWorkspace(saved);
          if (saved.pipeline_blueprint) setPipelineBlueprint(saved.pipeline_blueprint);
        }
        if (status === "failed") setError(String(progress.error ?? t("Staging failed.")));
      } catch (caught) {
        if (!cancelled) setError(caught instanceof Error ? caught.message : String(caught));
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 2500);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [runId, runStatus]);

  async function analyze() {
    if (!sourceId || runStatus === "staging") return;
    setError(null);
    setWorkspace(null);
    setRunStatus("staging");
    try {
      const staged = await api.stageRun(sourceId, reuseCache, pipelineBlueprint);
      setRunId(staged.run_id);
      setRunStatus(staged.status);
      if (staged.status === "staged") {
        const saved = await api.stagingWorkspace(staged.run_id);
        setWorkspace(saved);
        if (saved.pipeline_blueprint) setPipelineBlueprint(saved.pipeline_blueprint);
      }
    } catch (caught) {
      setRunStatus(null);
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }

  async function acceptPlan() {
    if (!runId || !workspace?.recommended_plan) return;
    setError(null);
    try {
      await api.startStaged(runId, { run_mode: "fully_auto" });
      onStart(runId);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }

  function openRun() {
    if (runId) onStart(runId);
  }

  async function acceptFiles(files: FileList | File[]) {
    const list = Array.from(files);
    if (!list.length) return;
    setUploading(true);
    setError(null);
    try {
      // Every file after the first joins the same upload group, so a
      // multi-table dataset arrives as one source instead of several.
      let group: string | undefined;
      for (const file of list) {
        const result = await api.upload(file, group);
        group = result.source_id;
      }
      await refreshSources();
      if (group) setSourceId(group);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-6 py-6">
      <header className="mb-5">
        <h1 className="text-xl font-semibold tracking-tight">{t("Automation designer")}</h1>
        <p className="mt-1 max-w-2xl text-sm text-ink-mute">
          {t("Build one typed graph that understands unfamiliar data, creates reports, branches into ML problems, and keeps every artifact attached to its producer.")}
        </p>
      </header>

      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); void acceptFiles(e.dataTransfer.files); }}
        onClick={() => fileInput.current?.click()}
        className={cx(
          "mb-4 cursor-pointer rounded-xl border-2 border-dashed px-6 py-7 text-center transition-colors",
          dragging ? "border-brand-500 bg-brand-50" : "border-line hover:border-ink-faint hover:bg-surface-sunken",
        )}
      >
        <input
          ref={fileInput}
          type="file"
          multiple
          accept=".csv,.tsv,.xlsx,.xls,.parquet,.pdf"
          className="hidden"
          onChange={(e) => { void acceptFiles(e.target.files ?? []); e.target.value = ""; }}
        />
        {uploading ? (
          <Spinner label={t("Uploading and profiling…")} />
        ) : (
          <>
            <p className="text-sm font-medium text-ink">{t("Drop CSV, Excel, Parquet or PDF files here")}</p>
            <p className="mt-1 text-xs text-ink-mute">
              {t("Or click to choose. Drop several at once to keep them as one dataset.")}
            </p>
          </>
        )}
      </div>

      <p className="mb-4 text-2xs text-ink-faint">
        {t("Databases are not supported yet — export the tables you need as files for now.")}
      </p>

      {error && (
        <p className="mb-4 rounded-lg bg-stop-50 px-3 py-2 text-sm text-stop-700">{t("Something went wrong: {detail}", { detail: error })}</p>
      )}

      {sources.length > 0 && (
        <div className="mb-6 flex flex-wrap gap-2">
          {sources.map((s) => (
            <button
              key={s.source_id}
              onClick={() => setSourceId(s.source_id)}
              className={cx(
                "rounded-lg border px-3 py-1.5 text-sm transition-colors",
                sourceId === s.source_id
                  ? "border-brand-500 bg-brand-50 text-ink"
                  : "border-line bg-surface text-ink-soft hover:bg-surface-sunken",
              )}
            >
              {s.label}
            </button>
          ))}
        </div>
      )}

      {loading && <Spinner label={t("Reading the data…")} />}

      {!loading && !profile && sources.length === 0 && (
        <Empty title={t("No data yet")} hint={t("Drop a file above to begin.")} />
      )}

      {profile && (
        <div className="space-y-5">
          <UnderstandingMonitor
            profile={profile}
            runStatus={runStatus}
            workspace={workspace}
            reuseCache={reuseCache}
            onReuseCache={setReuseCache}
            onAnalyze={() => void analyze()}
            onAccept={() => void acceptPlan()}
            onOpen={openRun}
          />

          {pipelineBlueprint && (
            <PipelineBuilder
              runId={runId}
              baseArtifactId={workspace?.artifact_id ?? null}
              blueprint={pipelineBlueprint}
              componentOutputs={workspace?.component_outputs ?? []}
              onChange={setPipelineBlueprint}
              onSaved={(saved) => {
                setWorkspace(saved);
                if (saved.pipeline_blueprint) setPipelineBlueprint(saved.pipeline_blueprint);
              }}
            />
          )}

          <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
            <main className="min-w-0 space-y-5">
              <UnderstandingHighlights profile={profile} />

              {(profile.documents?.length ?? 0) > 0 && <DocumentUnderstanding profile={profile} />}

              {profile.tables.length > 0 && <section className="rounded-xl border border-line bg-surface p-4">
                <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <div className="flex items-center gap-2">
                      <h2 className="text-sm font-semibold text-ink">{t("Schema discovery")}</h2>
                      <Badge tone="ok">{t("measured evidence")}</Badge>
                    </div>
                    <p className="mt-1 max-w-3xl text-xs leading-relaxed text-ink-mute">
                      {t("The clear view shows the strongest non-redundant backbone. Open all measured connections when you need the complete evidence graph.")}
                    </p>
                  </div>
                </div>
                <SchemaDiagram tables={profile.tables} relationships={profile.relationships ?? []} />
              </section>}

              {workspace && <StagingResults workspace={workspace} />}
              {!workspace && <AgentBriefing key={sourceId} sourceId={sourceId} />}
              <FormatReadiness profile={profile} />

              {profile.tables.length > 0 && <section>
                <div className="mb-2">
                  <h2 className="text-sm font-semibold text-ink">{t("Intake details")}</h2>
                  <p className="mt-0.5 text-xs text-ink-mute">{t("Open a table to inspect columns, keys, sensitivity, and quality notes.")}</p>
                </div>
                <TableList
                  tables={profile.tables}
                  open={openTable}
                  onToggle={(name) => setOpenTable((cur) => (cur === name ? null : name))}
                />
              </section>}

              <div className="rounded-xl border border-brand-200 bg-brand-50 p-4">
                <h2 className="text-sm font-semibold text-ink">{t("Ready to prepare the pipeline")}</h2>
                <p className="mt-1 max-w-3xl text-xs leading-relaxed text-ink-mute">
                  {t("Preparing creates a staged run that completes intake and schema discovery first. You can review or correct it before integration and modeling continue.")}
                </p>
                {profile.tables.length > 0 ? (
                  <button onClick={() => void analyze()} disabled={runStatus === "staging"} className="btn-primary mt-3 text-xs">
                    {runStatus === "staging" ? t("Analyzing intake and schema…") : t("Run intake + schema discovery")}
                  </button>
                ) : (
                  <p className="mt-3 text-xs font-medium text-warn-700">
                    {t("Document understanding is ready for chat. A structured pipeline needs tables; PDF chart-to-row extraction is not available yet.")}
                  </p>
                )}
              </div>
            </main>

            <aside className="min-w-0 xl:sticky xl:top-4">
              <PlannerPanel
                runId={runId}
                sourceId={sourceId}
                starterPrompts={[
                  "What does this dataset appear to describe?",
                  "Explain the simplest relationship path.",
                  "What should I verify before joining these tables?",
                  "What useful questions could this data answer?",
                  "What do the uploaded PDFs say, and which pages support it?",
                  "How might the documents connect to the structured tables?",
                  "Recommend pipeline overrides and explain the evidence for each one.",
                ]}
              />
            </aside>
          </div>
        </div>
      )}
    </div>
  );
}
function UnderstandingMonitor({
  profile,
  runStatus,
  workspace,
  reuseCache,
  onReuseCache,
  onAnalyze,
  onAccept,
  onOpen,
}: {
  profile: SourceProfile;
  runStatus: string | null;
  workspace: StagingWorkspace | null;
  reuseCache: boolean;
  onReuseCache: (value: boolean) => void;
  onAnalyze: () => void;
  onAccept: () => void;
  onOpen: () => void;
}) {
  const relationshipCount = profile.relationships?.length ?? 0;
  const documentCount = profile.documents?.length ?? 0;
  return (
    <section className="rounded-xl border border-line bg-surface p-4 shadow-card">
      <div className="mb-3 flex flex-wrap items-start gap-3">
        <div className="mr-auto">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold text-ink">{t("Staging monitor")}</h2>
            <Badge tone="ok">{t("ready for conversation")}</Badge>
          </div>
          <p className="mt-1 text-xs text-ink-mute">
            {t("Understand the source before deciding what the pipeline should do.")}
          </p>
        </div>
        {profile.tables.length > 0 && (
          <div className="flex flex-wrap items-center justify-end gap-2">
            <label className="flex items-center gap-2 text-2xs text-ink-mute">
              <input
                type="checkbox"
                checked={reuseCache}
                onChange={(event) => onReuseCache(event.target.checked)}
              />
              {t("Reuse a previous analysis of unchanged files")}
            </label>
            {workspace?.recommended_plan?.accepted ? (
              <button onClick={onOpen} className="btn-primary !py-1.5 text-xs">
                {t("Open run")}
              </button>
            ) : workspace?.recommended_plan ? (
              <>
                <button onClick={onAnalyze} className="btn-secondary !py-1.5 text-xs">
                  {t("Analyze again")}
                </button>
                <button onClick={onAccept} className="btn-primary !py-1.5 text-xs">
                  {t("Accept plan and run fully auto")}
                </button>
              </>
            ) : (
              <button onClick={onAnalyze} disabled={runStatus === "staging"} className="btn-primary !py-1.5 text-xs">
                {runStatus === "staging" ? t("Analyzing…") : t("Run intake + schema discovery")}
              </button>
            )}
          </div>
        )}
      </div>

      <div className="grid gap-2 md:grid-cols-[1fr_auto_1fr_auto_1fr] md:items-stretch">
        <MonitorStep
          number="01"
          title={t("Intake")}
          status={t(workspace ? "complete" : runStatus === "staging" ? "running" : "ready")}
          detail={`${profile.tables.length} ${t("tables")} · ${documentCount} ${t("documents")} · ${profile.tables.reduce((sum, table) => sum + table.columns_count, 0)} ${t("columns")}`}
        />
        <div className="hidden items-center text-ink-faint md:flex">→</div>
        <MonitorStep
          number="02"
          title={t("Schema discovery")}
          status={t(workspace ? "complete" : runStatus === "staging" ? "running" : "ready")}
          detail={`${relationshipCount} ${t("relationships")} · ${t("value-overlap evidence")}`}
        />
        <div className="hidden items-center text-ink-faint md:flex">→</div>
        <MonitorStep
          number="03"
          title={t("Local planner")}
          status={t(workspace?.recommended_plan ? "plan ready" : runStatus === "staging" ? "analyzing" : "available")}
          detail={workspace?.recommended_plan
            ? t("Reports, relationship explanations, and a fully-auto runtime plan are saved as one artifact.")
            : t("Ask questions or generate an interpretation before starting.")}
          agent
        />
      </div>
    </section>
  );
}

function MonitorStep({
  number,
  title,
  status,
  detail,
  agent = false,
}: {
  number: string;
  title: string;
  status: string;
  detail: string;
  agent?: boolean;
}) {
  return (
    <div className="rounded-lg border border-line bg-surface-sunken px-3 py-2.5">
      <div className="flex items-center gap-2">
        <span className="font-mono text-3xs text-ink-faint">{number}</span>
        <span className="text-xs font-semibold text-ink">{title}</span>
        <Badge tone={agent ? "brand" : "ok"}>{status}</Badge>
      </div>
      <p className="mt-1.5 text-2xs leading-relaxed text-ink-mute">{detail}</p>
    </div>
  );
}

function UnderstandingHighlights({ profile }: { profile: SourceProfile }) {
  const relationships = profile.relationships ?? [];
  const documents = profile.documents ?? [];
  const personal = profile.tables.reduce(
    (sum, table) => sum + table.columns.filter((column) => column.sensitivity === "pii").length,
    0,
  );
  const issues = profile.tables.reduce((sum, table) => sum + table.issues.length, 0);
  const lossy = relationships.filter((relationship) => relationship.orphan_rate > 0.02);
  const possibleTargets = profile.tables.reduce(
    (sum, table) => sum + table.columns.filter((column) => column.candidate_target).length,
    0,
  );
  const highestLoss = lossy.reduce((max, relationship) => Math.max(max, relationship.orphan_rate), 0);

  return (
    <section>
      <div className="mb-2 flex items-center gap-2">
        <h2 className="text-sm font-semibold text-ink">{t("Understanding snapshot")}</h2>
        <Badge>{t("deterministic")}</Badge>
      </div>
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        <InsightCard
          label={t("What arrived")}
          value={`${profile.tables.length} ${t("tables")} · ${documents.length} ${t("documents")}`}
          detail={`${issues} ${t("quality notes")} · ${personal} ${t("sensitive columns")}`}
        />
        <InsightCard
          label={t("How it connects")}
          value={`${relationships.length} ${t("measured links")}`}
          detail={relationships.length ? t("Based on real value overlap, not names alone.") : t("No strong cross-table overlap was found.")}
        />
        <InsightCard
          label={t("Join risk")}
          value={lossy.length ? `${lossy.length} ${t("lossy links")}` : t("No measured loss")}
          detail={lossy.length ? `${(highestLoss * 100).toFixed(1)}% ${t("maximum unmatched rows")}` : t("Measured links retain their dependent rows.")}
          tone={lossy.length ? "warn" : "ok"}
        />
        <InsightCard
          label={t("Analysis openings")}
          value={`${possibleTargets} ${t("candidate targets")}`}
          detail={t("Candidates only; the planner still has to establish usefulness and validity.")}
        />
      </div>
    </section>
  );
}

function InsightCard({
  label,
  value,
  detail,
  tone,
}: {
  label: string;
  value: string;
  detail: string;
  tone?: "ok" | "warn";
}) {
  return (
    <div className={cx(
      "rounded-xl border px-3.5 py-3",
      tone === "warn" ? "border-warn-500/40 bg-warn-50" : tone === "ok" ? "border-ok-500/30 bg-ok-50" : "border-line bg-surface",
    )}>
      <p className="text-3xs font-semibold uppercase tracking-wide text-ink-faint">{label}</p>
      <p className="mt-1 text-base font-semibold text-ink">{value}</p>
      <p className="mt-1 text-2xs leading-relaxed text-ink-mute">{detail}</p>
    </div>
  );
}

function localized(value: { en: string; tr: string }): string {
  return activeLanguage() === "tr" ? value.tr : value.en;
}

function StagingResults({ workspace }: { workspace: StagingWorkspace }) {
  const plan = workspace.recommended_plan;
  return (
    <section className="space-y-4 rounded-xl border border-brand-200 bg-surface p-4 shadow-card">
      <header className="flex flex-wrap items-start gap-2">
        <div className="mr-auto">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold text-ink">{t("Data understanding results")}</h2>
            <Badge tone="ok">{t("ready")}</Badge>
            {workspace.cache_reused && <Badge tone="brand">{t("cache reused")}</Badge>}
          </div>
          <p className="mt-1 text-2xs text-ink-mute">
            {t("Intake, schema evidence, planner reports, runtime plan, and chat history share this run lineage.")}
          </p>
        </div>
      </header>

      <div>
        <h3 className="mb-2 text-xs font-semibold text-ink">{t("Relationships explained")}</h3>
        <div className="grid gap-2 lg:grid-cols-2">
          {workspace.relationship_explanations.map((relationship) => (
            <details
              key={`${relationship.from_table}:${relationship.from_columns.join("+")}→${relationship.to_table}:${relationship.to_columns.join("+")}`}
              className="group rounded-lg border border-line bg-surface-sunken"
            >
              <summary className="flex cursor-pointer list-none flex-wrap items-center gap-1.5 px-3 py-2.5 text-3xs">
                <Badge tone="ok">{t("measured + interpreted")}</Badge>
                <span className="min-w-0 flex-1 truncate font-mono text-ink-soft">
                  {relationship.from_table}.{relationship.from_columns.join("+")} → {relationship.to_table}.{relationship.to_columns.join("+")}
                </span>
                <span className="text-sm text-ink-faint group-open:rotate-45">+</span>
              </summary>
              <div className="border-t border-line-soft px-3 pb-3 pt-2">
                <p className="text-xs leading-relaxed text-ink">{localized(relationship.explanation)}</p>
                <p className="mt-1 text-2xs leading-relaxed text-ink-mute">{localized(relationship.why_it_matters)}</p>
                <p className="mt-2 text-3xs text-brand-700">{localized(relationship.verification_question)}</p>
              </div>
            </details>
          ))}
        </div>
      </div>

      <div>
        <h3 className="mb-2 text-xs font-semibold text-ink">{t("Planner reports")}</h3>
        <div className="grid gap-2 lg:grid-cols-3">
          {workspace.reports.map((report) => (
            <details key={report.title.en} className="group rounded-lg border border-line bg-surface-sunken">
              <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2.5">
                <h4 className="min-w-0 flex-1 truncate text-xs font-semibold text-ink">{localized(report.title)}</h4>
                <span className="text-sm text-ink-faint group-open:rotate-45">+</span>
              </summary>
              <div className="border-t border-line-soft px-3 pb-3 pt-2">
                <p className="text-2xs leading-relaxed text-ink-mute">{localized(report.summary)}</p>
                {report.findings.length > 0 && (
                  <ul className="mt-2 space-y-1">
                    {report.findings.map((item) => <li key={item.en} className="text-3xs text-ink-soft">· {localized(item)}</li>)}
                  </ul>
                )}
              </div>
            </details>
          ))}
          {workspace.reports.length === 0 && (
            <p className="text-xs text-ink-mute">{t("The measured workspace is ready; planner prose was not available.")}</p>
          )}
        </div>
      </div>

      {plan && (
        <details open className="rounded-lg border border-brand-200 bg-brand-50">
          <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2.5">
            <h3 className="text-xs font-semibold text-ink">{t("Recommended runtime plan")}</h3>
            <Badge tone="brand">
              {t(plan.accepted ? "fully auto · accepted" : "fully auto · ready to accept")}
            </Badge>
            <span className="ml-auto text-ink-faint">⌄</span>
          </summary>
          <div className="grid gap-2 border-t border-brand-100 px-3 pb-3 pt-2 md:grid-cols-2">
            <div>
              <p className="text-3xs font-semibold uppercase tracking-wide text-ink-faint">{t("Configuration")}</p>
              <dl className="mt-1 space-y-1">
                {Object.entries(plan.configuration).map(([key, value]) => (
                  <div key={key} className="flex gap-2 text-2xs"><dt className="font-mono text-ink-mute">{key}</dt><dd className="ml-auto text-right text-ink">{JSON.stringify(value)}</dd></div>
                ))}
              </dl>
            </div>
            <div>
              <p className="text-3xs font-semibold uppercase tracking-wide text-ink-faint">{t("Why these overrides")}</p>
              <ul className="mt-1 space-y-1">
                {plan.rationale.map((item) => <li key={item.en} className="text-2xs text-ink-soft">· {localized(item)}</li>)}
              </ul>
            </div>
          </div>
        </details>
      )}

      {workspace.planner_error && (
        <p className="rounded-lg bg-warn-50 px-3 py-2 text-2xs text-warn-700">
          {t("Planner analysis is unavailable right now. The measured intake and relationship view is still ready.")}
        </p>
      )}
    </section>
  );
}

function AgentBriefing({ sourceId }: { sourceId: string }) {
  const [briefing, setBriefing] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function generate() {
    setBusy(true);
    setError(null);
    try {
      const response = await api.plannerChat({
        source_id: sourceId,
        message: (
          "Create a concise data-understanding briefing for someone who has never seen these files. "
          + "Explain the likely role and grain of each table and document, the simplest measured relationship backbone, "
          + "the most relevant PDF findings with file-and-page citations, how documents may connect to tables, "
          + "join-loss and privacy risks, and three useful questions the source may help answer. "
          + "Separate measured facts from inferences, recommend only evidence-supported pipeline overrides, "
          + "and end with what the human should verify."
        ),
      });
      setBriefing(String(response.reply ?? response.message ?? t("No briefing returned.")));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded-xl border border-line bg-surface p-4">
      <div className="flex flex-wrap items-start gap-3">
        <div className="mr-auto">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold text-ink">{t("Local agent briefing")}</h2>
            <Badge tone="brand">{t("interpretation, not gate evidence")}</Badge>
          </div>
          <p className="mt-1 max-w-3xl text-xs leading-relaxed text-ink-mute">
            {t("The local planner uses the same row-free intake and schema summaries shown here plus Agentic DS runtime skills.")}
          </p>
        </div>
        {!briefing && (
          <button onClick={() => void generate()} disabled={busy} className="btn-ghost !py-1.5 text-xs">
            {busy ? t("Generating…") : t("Generate briefing")}
          </button>
        )}
      </div>
      {busy && <div className="mt-3"><Spinner label={t("Local planner is reading the measured summaries…")} /></div>}
      {error && <p className="mt-3 rounded-lg bg-stop-50 px-3 py-2 text-xs text-stop-700">{t("Something went wrong: {detail}", { detail: error })}</p>}
      {briefing && (
        <div className="mt-3 rounded-lg border border-brand-100 bg-brand-50 px-3.5 py-3">
          <p className="whitespace-pre-wrap text-xs leading-6 text-ink-soft">{briefing}</p>
          <button onClick={() => void generate()} disabled={busy} className="mt-3 text-2xs font-medium text-brand-700 hover:underline">
            {t("Regenerate from current evidence")}
          </button>
        </div>
      )}
    </section>
  );
}

function DocumentUnderstanding({ profile }: { profile: SourceProfile }) {
  return (
    <section className="rounded-xl border border-line bg-surface p-4">
      <div className="mb-3 flex flex-wrap items-start gap-2">
        <div className="mr-auto">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold text-ink">{t("Document understanding")}</h2>
            <Badge tone="brand">{t("local PDF text")}</Badge>
          </div>
          <p className="mt-1 max-w-3xl text-xs text-ink-mute">
            {t("The planner retrieves bounded page excerpts locally. Embedded raster images are counted; charts and scanned pages still need OCR or vision and are not training data yet.")}
          </p>
        </div>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        {(profile.documents ?? []).map((document) => {
          const coverage = document.pages ? document.text_pages / document.pages : 0;
          return (
            <article key={document.name} className="rounded-lg border border-line bg-surface-sunken p-3">
              <div className="flex items-start gap-2">
                <div className="min-w-0 flex-1">
                  <h3 className="truncate text-xs font-semibold text-ink" title={document.name}>{document.title || document.name}</h3>
                  <p className="mt-0.5 truncate text-3xs text-ink-faint">{document.name}</p>
                </div>
                <Badge tone={document.understanding_status === "text_ready" ? "ok" : "warn"}>
                  {t(document.understanding_status === "text_ready" ? "text ready" : "needs OCR / vision")}
                </Badge>
              </div>
              <div className="mt-3 grid grid-cols-3 gap-2 text-center">
                <DocumentMetric value={document.pages} label={t("pages")} />
                <DocumentMetric value={document.text_pages} label={t("text pages")} />
                <DocumentMetric value={document.image_count} label={t("embedded images")} />
              </div>
              <div className="mt-3">
                <div className="mb-1 flex justify-between text-3xs text-ink-faint">
                  <span>{t("Text-layer coverage")}</span><span>{(coverage * 100).toFixed(0)}%</span>
                </div>
                <div className="h-1.5 overflow-hidden rounded-full bg-line-soft">
                  <div className="h-full rounded-full bg-brand-500" style={{ width: `${Math.max(2, coverage * 100)}%` }} />
                </div>
              </div>
              <p className="mt-3 text-3xs text-warn-700">{t("Training rows: not extracted")}</p>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function DocumentMetric({ value, label }: { value: number; label: string }) {
  return <div className="rounded-md bg-surface px-2 py-2"><p className="text-sm font-semibold text-ink">{value}</p><p className="text-4xs text-ink-faint">{label}</p></div>;
}

function FormatReadiness({ profile }: { profile: SourceProfile }) {
  const present = new Set(profile.tables.map((table) => table.format.toLowerCase()));
  const documents = profile.documents ?? [];
  const formats = [
    { label: "CSV / TSV", active: present.has("csv") || present.has("tsv") || present.has("txt") },
    { label: "Excel", active: present.has("excel") || present.has("xlsx") || present.has("xls") },
    { label: "Parquet", active: present.has("parquet") },
  ];
  return (
    <section className="rounded-xl border border-line bg-surface px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="mr-auto">
          <h2 className="text-sm font-semibold text-ink">{t("Input format readiness")}</h2>
          <p className="mt-0.5 text-2xs text-ink-mute">{t("Structured files can enter the ML pipeline. PDFs can enter local understanding and chat; chart-to-row extraction remains planned.")}</p>
        </div>
        {formats.map((format) => <Badge key={format.label} tone={format.active ? "ok" : "neutral"}>{format.label}</Badge>)}
        <Badge tone={documents.length ? "ok" : "neutral"}>PDF · {documents.length ? t("understanding ready") : t("available")}</Badge>
        <Badge tone="neutral">{t("PDF chart training extraction")} · {t("planned")}</Badge>
      </div>
    </section>
  );
}

function TableList({
  tables, open, onToggle,
}: { tables: ProfiledTable[]; open: string | null; onToggle: (name: string) => void }) {
  return (
    <div className="space-y-2">
      {tables.map((table) => {
        const expanded = open === table.name;
        const sensitive = table.columns.filter((c) => c.sensitivity === "pii").length;
        return (
          <section key={table.name} className="rounded-xl border border-line bg-surface">
            <button
              onClick={() => onToggle(table.name)}
              className="flex w-full flex-wrap items-center gap-2 px-4 py-3 text-left"
            >
              <span className="font-medium text-ink">{table.name}</span>
              <Badge>{table.format}</Badge>
              <span className="text-xs text-ink-mute">
                {table.rows.toLocaleString()} rows · {table.columns_count} columns
              </span>
              {table.candidate_keys.length > 0 && (
                <Badge tone="ok">key: {table.candidate_keys[0].join(" + ")}</Badge>
              )}
              {sensitive > 0 && <Badge tone="warn">{sensitive} {t("personal")}</Badge>}
              {table.issues.length > 0 && <Badge tone="warn">{table.issues.length} notes</Badge>}
              <span className="ml-auto text-xs text-ink-faint">{expanded ? "−" : "+"}</span>
            </button>

            {expanded && (
              <div className="border-t border-line-soft px-4 py-3">
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-3xs uppercase tracking-wide text-ink-faint">
                        <th className="pb-2 pr-4 text-left font-medium">{t("Column")}</th>
                        <th className="pb-2 pr-4 text-left font-medium">{t("Kind")}</th>
                        <th className="pb-2 pr-4 text-right font-medium">{t("Missing")}</th>
                        <th className="pb-2 pr-4 text-right font-medium">{t("Distinct")}</th>
                        <th className="pb-2 text-left font-medium" />
                      </tr>
                    </thead>
                    <tbody>
                      {table.columns.map((c) => (
                        <tr key={c.name} className="border-t border-line-soft">
                          <td className="py-1.5 pr-4 font-medium text-ink">{c.name}</td>
                          <td className="py-1.5 pr-4 text-ink-mute">{c.semantic_type.replace(/_/g, " ")}</td>
                          <td className="py-1.5 pr-4 text-right tabular-nums text-ink-soft">
                            {(c.null_rate * 100).toFixed(1)}%
                          </td>
                          <td className="py-1.5 pr-4 text-right tabular-nums text-ink-soft">
                            {(c.unique_rate * 100).toFixed(1)}%
                          </td>
                          <td className="py-1.5">
                            {c.sensitivity !== "public" && <Badge tone="warn">{t(c.sensitivity)}</Badge>}
                            {c.is_unique && <Badge tone="ok">{t("unique")}</Badge>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {table.issues.length > 0 && (
                  <ul className="mt-3 space-y-1">
                    {table.issues.map((code) => (
                      <li key={code} className="text-xs text-ink-mute">· {code.replace(/_/g, " ")}</li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}
