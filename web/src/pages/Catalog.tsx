import { t } from "../lib/i18n";
/**
 * Sidebar destinations: Datasets, Experiments, Models, Reports, Settings, Home.
 *
 * The brief requires these to be functional rather than decorative — a saved
 * model must actually appear under Models, a generated report under Reports.
 * Each reads the catalog endpoints the backend already exposes.
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Badge, DataTable, Disclosure, Empty, Metric, Spinner, toneFor } from "../components/ui";
import { api, type DatasetSummary, type ExperimentSummary, type Hardening, type ModelSummary, type ReportSummary } from "../lib/api";

function useAsync<T>(load: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    load()
      .then((d) => { if (!cancelled) setData(d); })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { data, error, loading };
}

function Page({ title, subtitle, children }: { title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <div className="h-full overflow-y-auto px-6 py-5">
      <header className="mb-4">
        <h2 className="text-xl font-semibold tracking-tight">{title}</h2>
        {subtitle && <p className="mt-0.5 text-sm text-ink-mute">{subtitle}</p>}
      </header>
      {children}
    </div>
  );
}

/**
 * Hardening values are heterogeneous — booleans, numbers, lists of strings, and
 * lists of mount objects. String() on the last of those yields "[object
 * Object]", so structured values are rendered as their key=value pairs.
 */
function renderValue(v: unknown): React.ReactNode {
  if (typeof v === "boolean") return v ? "enabled" : "disabled";
  if (Array.isArray(v)) {
    return (
      <ul className="space-y-0.5">
        {v.map((item, i) => <li key={i}>{renderValue(item)}</li>)}
      </ul>
    );
  }
  if (v && typeof v === "object") {
    return Object.entries(v as Record<string, unknown>)
      .map(([k, val]) => `${k}=${String(val)}`)
      .join("  ");
  }
  return String(v);
}

const fmt = (n: number) => n.toLocaleString();
const num = (n: number, d = 4) => (Number.isFinite(n) ? n.toFixed(d) : "—");

export function Datasets() {
  const { data, error, loading } = useAsync<DatasetSummary[]>(() => api.datasets());
  return (
    <Page title={t("Datasets")} subtitle={t("Profiled sources. Schema and aggregate statistics only — no raw rows are stored or shown.")}>
      {loading && <Spinner label={t("Loading datasets…")} />}
      {error && <p className="text-sm text-stop-700">{error}</p>}
      {data?.length === 0 && <Empty title={t("No datasets yet")} hint={t("Drop files into data/ and run the pipeline to profile them.")} />}
      <div className="space-y-3">
        {data?.map((d) => (
          <Disclosure
            key={d.source_id}
            title={d.label}
            right={
              <div className="flex items-center gap-1.5">
                {(d.sensitive_columns ?? 0) > 0 && <Badge tone="warn">{d.sensitive_columns} {t("sensitive")}</Badge>}
                {(d.quality_issues ?? 0) > 0 && <Badge tone="stop">{d.quality_issues} {t("issues")}</Badge>}
                {(d.tables ?? 0) > 0 && <Badge>{d.tables} {t("tables")}</Badge>}
                {(d.documents ?? 0) > 0 && <Badge>{d.documents} {t("documents")}</Badge>}
              </div>
            }
          >
            {/* An unreadable folder arrives with a profile_error and none of the
                measured fields, so say what went wrong rather than rendering a
                row of zeroes that looks like a real empty dataset. */}
            {d.profile_error ? (
              <p className="rounded-lg bg-warn-50 px-3 py-2 text-xs text-warn-700">{d.profile_error}</p>
            ) : (
              <>
                {(d.tables ?? 0) > 0 && (
                  <>
                    <div className="mb-3 flex flex-wrap gap-2">
                      <Metric label={t("Rows")} value={fmt(d.rows ?? 0)} />
                      <Metric label={t("Columns")} value={String(d.columns ?? 0)} />
                      <Metric label={t("Candidate keys")} value={String(d.candidate_keys ?? 0)} />
                      <Metric label={t("Sensitive")} value={String(d.sensitive_columns ?? 0)} />
                    </div>
                    <DataTable
                      columns={[t("Table"), t("Format"), t("Rows"), t("Columns"), t("Keys"), t("Issues")]}
                      rows={(d.table_summaries ?? []).map((t) => [t.name, t.format, fmt(t.rows), t.columns, t.candidate_keys, t.issues.length])}
                    />
                  </>
                )}
                {/* A PDF-only source has no tables; the table metrics above would
                    all be zero and the DataTable empty, which read as a broken
                    dataset (#69). Render what it actually holds instead. */}
                {(d.documents ?? 0) > 0 && (
                  <div className={(d.tables ?? 0) > 0 ? "mt-4" : ""}>
                    <div className="mb-3 flex flex-wrap gap-2">
                      <Metric label={t("Documents")} value={String(d.documents ?? 0)} />
                      <Metric label={t("Pages")} value={String(d.document_pages ?? 0)} />
                    </div>
                    <DataTable
                      columns={[t("Document"), t("Format"), t("Pages")]}
                      rows={(d.document_summaries ?? []).map((s) => [s.name, s.format, s.pages])}
                    />
                  </div>
                )}
                <p className="mt-3 text-xs text-ink-faint">{d.privacy}</p>
              </>
            )}
          </Disclosure>
        ))}
      </div>
    </Page>
  );
}

export function Models() {
  const { data, error, loading } = useAsync<ModelSummary[]>(() => api.models());
  return (
    <Page title={t("Models")} subtitle={t("Trained artifacts with their measured holdout performance and provenance.")}>
      {loading && <Spinner label={t("Loading models…")} />}
      {error && <p className="text-sm text-stop-700">{error}</p>}
      {data?.length === 0 && <Empty title={t("No models yet")} hint={t("Complete a run through the training stage to save a model.")} />}
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {data?.map((m) => (
          <article key={m.artifact_id} className="card px-4 py-3.5">
            <div className="mb-2 flex items-start justify-between gap-2">
              <div>
                <h3 className="text-sm font-semibold">{m.display_name}</h3>
                <p className="text-xs text-ink-mute">{m.estimator}</p>
              </div>
              {m.saved && <Badge tone="ok">{t("saved")}</Badge>}
            </div>
            <dl className="grid grid-cols-2 gap-2 border-t border-line-soft pt-2.5">
              <Pair label={`Holdout ${m.metric}`} value={num(m.holdout_score, 2)} />
              <Pair label={t("CV mean")} value={num(m.cv_mean, 2)} />
              <Pair label={t("CV std")} value={num(m.cv_std, 2)} />
              <Pair label={t("Training rows")} value={fmt(m.training_rows)} />
            </dl>
            <p className="mt-2.5 border-t border-line-soft pt-2 font-mono text-[10.5px] text-ink-faint">
              run {m.run_id} · {m.candidate_count} candidates
            </p>
          </article>
        ))}
      </div>
    </Page>
  );
}

export function Reports() {
  const { data, error, loading } = useAsync<ReportSummary[]>(() => api.reports());
  return (
    <Page title={t("Reports")} subtitle={t("Generated evaluation reports, downloadable as markdown.")}>
      {loading && <Spinner label={t("Loading reports…")} />}
      {error && <p className="text-sm text-stop-700">{error}</p>}
      {data?.length === 0 && <Empty title={t("No reports yet")} hint={t("Reports appear once a run reaches the report stage.")} />}
      <div className="space-y-2">
        {data?.map((r) => (
          <article key={r.artifact_id} className="card flex items-center gap-3 px-4 py-3">
            <div className="min-w-0 flex-1">
              <h3 className="truncate text-sm font-medium">{String(r.title ?? "Evaluation report")}</h3>
              <p className="font-mono text-[11px] text-ink-faint">run {r.run_id}</p>
            </div>
            <a href={`/api/reports/${r.artifact_id}/download`} className="btn-ghost !py-1.5 text-xs" download>
              {t("Download")}
            </a>
          </article>
        ))}
      </div>
    </Page>
  );
}

export function Experiments() {
  const { data, error, loading } = useAsync<ExperimentSummary[]>(() => api.experiments());
  return (
    <Page title={t("Experiments")} subtitle={t("Every run, with the decisions and gate outcomes it produced.")}>
      {loading && <Spinner label={t("Loading experiments…")} />}
      {error && <p className="text-sm text-stop-700">{error}</p>}
      {data?.length === 0 && <Empty title={t("No experiments yet")} hint={t("Start a run from Workflows.")} />}
      <div className="space-y-2">
        {data?.map((e) => (
          // Carry the automation id so the link opens that run's workspace. Without
          // it, /automation has no `automation` param and falls back to the project
          // library, dropping the run entirely -- the run was never reachable (#68).
          <Link key={e.run_id} to={`/automation?${typeof e.automation_id === "string" && e.automation_id ? `automation=${encodeURIComponent(e.automation_id)}&` : ""}view=runs&run=${encodeURIComponent(e.run_id)}`} className="card block px-4 py-3 hover:border-ink-faint">
            <div className="flex items-center gap-2">
              <span className="font-mono text-xs text-ink">{e.run_id}</span>
              {typeof e.status === "string" && <Badge tone={toneFor(e.status)}>{e.status.replace(/_/g, " ")}</Badge>}
            </div>
          </Link>
        ))}
      </div>
    </Page>
  );
}

export function Settings() {
  const { data, error, loading } = useAsync<Hardening>(() => api.hardening());
  return (
    <Page title={t("Settings")} subtitle={t("Enforced guarantees. These are properties of the system, not preferences.")}>
      {loading && <Spinner label={t("Loading…")} />}
      {error && <p className="text-sm text-stop-700">{error}</p>}
      <div className="space-y-3">
        {data && Object.entries(data).map(([group, values]) => (
          <Disclosure key={group} title={group.replace(/_/g, " ")} defaultOpen>
            <dl className="grid gap-2 sm:grid-cols-2">
              {Object.entries(values).map(([k, v]) => (
                <div key={k} className="rounded-lg border border-line bg-surface-sunken px-3 py-2">
                  <dt className="text-[10px] uppercase tracking-wide text-ink-faint">{k.replace(/_/g, " ")}</dt>
                  <dd className="text-sm text-ink">{renderValue(v)}</dd>
                </div>
              ))}
            </dl>
          </Disclosure>
        ))}
      </div>
    </Page>
  );
}

export function Home() {
  const models = useAsync<ModelSummary[]>(() => api.models());
  const datasets = useAsync<DatasetSummary[]>(() => api.datasets());
  const reports = useAsync<ReportSummary[]>(() => api.reports());

  return (
    <Page title={t("Understand unfamiliar data")} subtitle={t("From mixed files to an agreed ML plan, with every source and decision visible.")}>
      <section className="mb-5 rounded-2xl border border-brand-200 bg-gradient-to-br from-brand-50 to-surface px-6 py-6 shadow-card">
        <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-brand-700">{t("Recommended next action")}</p>
        <div className="mt-2 flex flex-wrap items-end justify-between gap-4"><div><h3 className="text-xl font-semibold text-ink">{t("Give Agentic DS your files")}</h3><p className="mt-2 max-w-2xl text-sm leading-relaxed text-ink-mute">{t("Intake routes every PDF, table, and ambiguous source. You review what becomes ML data before the base pipeline can run.")}</p></div><Link to="/automation" className="btn-primary">{t("Start a data project")} →</Link></div>
        <ol className="mt-6 grid gap-2 sm:grid-cols-5">{["Upload", "Intake", "Understand", "Agree on plan", "Run and review"].map((step, index) => <li key={step} className="rounded-lg border border-brand-100 bg-surface/80 px-3 py-3"><span className="text-[10px] font-semibold text-brand-700">{index + 1}</span><p className="mt-1 text-xs font-medium text-ink">{t(step)}</p></li>)}</ol>
      </section>
      <div className="mb-5 flex flex-wrap gap-2">
        <Metric label={t("Datasets")} value={String(datasets.data?.length ?? "—")} />
        <Metric label={t("Models")} value={String(models.data?.length ?? "—")} />
        <Metric label={t("Reports")} value={String(reports.data?.length ?? "—")} />
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <Link to="/automation" className="card px-5 py-4 hover:border-brand-500">
          <h3 className="text-sm font-semibold">{t("Open data projects")}</h3>
          <p className="mt-1 text-xs leading-relaxed text-ink-mute">
            {t("Continue an intake, review a proposed ML plan, or inspect a transparent run.")}
          </p>
        </Link>
        <Link to="/datasets" className="card px-5 py-4 hover:border-brand-500">
          <h3 className="text-sm font-semibold">{t("Review your data")}</h3>
          <p className="mt-1 text-xs leading-relaxed text-ink-mute">
            {t("Profiles, candidate keys and sensitive-column detection — schema and statistics only, never raw rows.")}
          </p>
        </Link>
      </div>
    </Page>
  );
}

function Pair({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[10px] uppercase tracking-wide text-ink-faint">{label}</dt>
      <dd className="text-sm font-semibold text-ink">{value}</dd>
    </div>
  );
}
