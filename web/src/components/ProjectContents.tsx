/**
 * A project's own data, models and reports, shown as tabs inside the project
 * (#80, #111). These read the project-scoped read model (`/api/projects/{id}/
 * contents`), which is assembled from the project's execution history -- never a
 * global catalogue filtered by source -- so nothing another project produced
 * from the same reusable input can appear here. Source reuse is instead made
 * legible: the Data view names the other projects bound to the same input.
 */
import type { ProjectContents } from "../lib/api";
import type { WorkspaceView } from "./automationWorkspaceState";
import { Badge, DataTable, Empty, Metric, Spinner } from "./ui";
import { t } from "../lib/i18n";

const fmt = (n: number) => n.toLocaleString();
const num = (n: number, d = 2) => (Number.isFinite(n) ? n.toFixed(d) : "—");

export function ProjectContentsPanel({
  view,
  contents,
  loading,
}: {
  view: WorkspaceView;
  contents: ProjectContents | null;
  loading: boolean;
}) {
  if (loading && !contents) return <div className="grid h-full place-items-center"><Spinner label={t("Loading…")} /></div>;
  return (
    <div className="h-full overflow-y-auto bg-surface-sunken px-6 py-6">
      <div className="mx-auto max-w-5xl">
        {view === "data" && <DataView contents={contents} />}
        {view === "models" && <ModelsView contents={contents} />}
        {view === "reports" && <ReportsView contents={contents} />}
      </div>
    </div>
  );
}

function DataView({ contents }: { contents: ProjectContents | null }) {
  const data = contents?.data;
  if (!data) {
    return <Empty title={t("No data attached")} hint={t("Add files in the Editor to give this project data.")} />;
  }
  // Source reuse is deliberately legible (#78): a reusable input can feed
  // several independent projects, so name the others bound to the same source.
  const others = (contents?.source_references ?? []).filter(
    (reference) => reference.project_id !== contents?.project.automation_id,
  );
  return (
    <div className="space-y-4">
      <div className="card px-5 py-4">
        <h3 className="text-sm font-semibold text-ink">{data.label}</h3>
        {others.length > 0 && (
          <p className="mt-1 text-xs text-ink-mute">
            {t("Also used by {names}", { names: others.map((reference) => reference.name).join(", ") })}
          </p>
        )}
        {data.profile_error ? (
          <p className="mt-3 rounded-lg bg-warn-50 px-3 py-2 text-xs text-warn-700">{data.profile_error}</p>
        ) : (
          <>
            {(data.tables ?? 0) > 0 && (
              <>
                <div className="mb-3 mt-4 flex flex-wrap gap-2">
                  <Metric label={t("Rows")} value={fmt(data.rows ?? 0)} />
                  <Metric label={t("Columns")} value={String(data.columns ?? 0)} />
                  <Metric label={t("Candidate keys")} value={String(data.candidate_keys ?? 0)} />
                  <Metric label={t("Sensitive")} value={String(data.sensitive_columns ?? 0)} />
                </div>
                <DataTable
                  columns={[t("Table"), t("Format"), t("Rows"), t("Columns"), t("Keys"), t("Issues")]}
                  rows={(data.table_summaries ?? []).map((table) => [table.name, table.format, fmt(table.rows), table.columns, table.candidate_keys, table.issues.length])}
                />
              </>
            )}
            {(data.documents ?? 0) > 0 && (
              <div className={(data.tables ?? 0) > 0 ? "mt-4" : "mt-4"}>
                <div className="mb-3 flex flex-wrap gap-2">
                  <Metric label={t("Documents")} value={String(data.documents ?? 0)} />
                  <Metric label={t("Pages")} value={String(data.document_pages ?? 0)} />
                </div>
                <DataTable
                  columns={[t("Document"), t("Format"), t("Pages")]}
                  rows={(data.document_summaries ?? []).map((summary) => [summary.name, summary.format, summary.pages])}
                />
              </div>
            )}
            {data.privacy && <p className="mt-3 text-xs text-ink-faint">{data.privacy}</p>}
          </>
        )}
      </div>
    </div>
  );
}

function ModelsView({ contents }: { contents: ProjectContents | null }) {
  const models = contents?.models ?? [];
  if (!models.length) return <Empty title={t("No models yet")} hint={t("Complete a run through the training stage to save a model.")} />;
  return (
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
      {models.map((model) => (
        <article key={model.artifact_id} className="card px-4 py-3.5">
          <div className="mb-2 flex items-start justify-between gap-2">
            <div>
              <h3 className="text-sm font-semibold">{model.display_name}</h3>
              <p className="text-xs text-ink-mute">{model.estimator}</p>
            </div>
            {model.saved && <Badge tone="ok">{t("saved")}</Badge>}
          </div>
          <dl className="grid grid-cols-2 gap-2 border-t border-line-soft pt-2.5">
            <Pair label={`Holdout ${model.metric}`} value={num(model.holdout_score)} />
            <Pair label={t("CV mean")} value={num(model.cv_mean)} />
            <Pair label={t("CV std")} value={num(model.cv_std)} />
            <Pair label={t("Training rows")} value={fmt(model.training_rows)} />
          </dl>
          <div className="mt-2.5 flex items-center justify-between gap-2 border-t border-line-soft pt-2">
            <p className="min-w-0 flex-1 truncate font-mono text-[10.5px] text-ink-faint">
              run {model.run_id} · {model.candidate_count} candidates
            </p>
            {/* #166: a completed run leaves a saved model that must be
                downloadable. Only a saved model has a joblib blob behind it. */}
            {model.saved && (
              <a
                href={`/api/models/${model.artifact_id}/download`}
                className="btn-ghost !py-1 text-xs"
                download
              >
                {t("Download model")}
              </a>
            )}
          </div>
        </article>
      ))}
    </div>
  );
}

function ReportsView({ contents }: { contents: ProjectContents | null }) {
  const reports = contents?.reports ?? [];
  if (!reports.length) return <Empty title={t("No reports yet")} hint={t("Reports appear once a run reaches the report stage.")} />;
  return (
    <div className="space-y-2">
      {reports.map((report) => (
        <article key={report.artifact_id} className="card flex items-center gap-3 px-4 py-3">
          <div className="min-w-0 flex-1">
            <h3 className="truncate text-sm font-medium">{String(report.title ?? report.preview ?? "Evaluation report")}</h3>
            <p className="font-mono text-[11px] text-ink-faint">run {report.run_id}</p>
          </div>
          <a href={`/api/reports/${report.artifact_id}/download`} className="btn-ghost !py-1.5 text-xs" download>
            {t("Download")}
          </a>
        </article>
      ))}
    </div>
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
