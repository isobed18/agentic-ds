/**
 * A project's own data, models and reports, shown as tabs inside the project
 * (#80, #111). These read the project-scoped read model (`/api/projects/{id}/
 * contents`), which is assembled from the project's execution history -- never a
 * global catalogue filtered by source -- so nothing another project produced
 * from the same reusable input can appear here. Source reuse is instead made
 * legible: the Data view names the other projects bound to the same input.
 */
import type { AutomationContents } from "../lib/api";
import type { WorkspaceView } from "./automationWorkspaceState";
import { Badge, Empty, Spinner } from "./ui";
import { t } from "../lib/i18n";

const fmt = (n: number) => n.toLocaleString();
const num = (n: number, d = 2) => (Number.isFinite(n) ? n.toFixed(d) : "—");

export function ProjectContentsPanel({
  view,
  contents,
  loading,
}: {
  view: WorkspaceView;
  contents: AutomationContents | null;
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

function DataView({ contents }: { contents: AutomationContents | null }) {
  const data = contents?.data ?? [];
  if (!data.length) return <Empty title={t("No data selected")} hint={t("Choose project files from the automation graph to create its private snapshot.")} />;
  return <section><h2 className="text-lg font-semibold text-ink">{t("Selected automation data")}</h2><p className="mt-1 text-sm text-ink-mute">{t("Only these project files belong to this automation.")}</p><div className="mt-4 space-y-2">{data.map((file) => <article key={`${file.source_id}:${file.path}`} className="card flex items-center gap-3 px-4 py-3"><span aria-hidden="true">▤</span><div className="min-w-0"><p className="truncate text-sm font-medium text-ink">{file.path}</p><p className="truncate font-mono text-[10px] text-ink-faint">{file.source_id}</p></div></article>)}</div></section>;
}

function ModelsView({ contents }: { contents: AutomationContents | null }) {
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

function ReportsView({ contents }: { contents: AutomationContents | null }) {
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
