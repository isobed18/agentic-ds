/**
 * A project's own data, models and reports, shown as tabs inside the project
 * (#80, #111). These read the project-scoped read model (`/api/projects/{id}/
 * contents`), which is assembled from the project's execution history -- never a
 * global catalogue filtered by source -- so nothing another project produced
 * from the same reusable input can appear here. Source reuse is instead made
 * legible: the Data view names the other projects bound to the same input.
 */
import { useState } from "react";
import {
  api,
  type AutomationContents,
  type ModelSummary,
  type ReportSummary,
} from "../lib/api";
import type { WorkspaceView } from "./automationWorkspaceState";
import { Badge, Empty, Spinner, cx } from "./ui";
import { useOverlayDismiss } from "./overlayDismiss";
import { t } from "../lib/i18n";

const fmt = (n: number) => n.toLocaleString();
// Widened to accept null: an enhanced model's holdout score is absent until it
// has one, and `Number.isFinite(null)` is false but `null.toFixed` is a crash.
const num = (n: number | null | undefined, d = 2) =>
  typeof n === "number" && Number.isFinite(n) ? n.toFixed(d) : "—";

export function ProjectContentsPanel({
  view,
  contents,
  loading,
  onChanged,
}: {
  view: WorkspaceView;
  contents: AutomationContents | null;
  loading: boolean;
  onChanged: () => void;
}) {
  if (loading && !contents) return <div className="grid h-full place-items-center"><Spinner label={t("Loading…")} /></div>;
  return (
    <div className="h-full overflow-y-auto bg-surface-sunken px-6 py-6">
      <div className="mx-auto max-w-5xl">
        {view === "data" && <DataView contents={contents} />}
        {view === "models" && <ModelsView contents={contents} onChanged={onChanged} />}
        {view === "reports" && <ReportsView contents={contents} onChanged={onChanged} />}
      </div>
    </div>
  );
}

function DataView({ contents }: { contents: AutomationContents | null }) {
  const data = contents?.data ?? [];
  if (!data.length) return <Empty title={t("No data selected")} hint={t("Choose project files from the automation graph to create its private snapshot.")} />;
  return <section><h2 className="text-lg font-semibold text-ink">{t("Selected automation data")}</h2><p className="mt-1 text-sm text-ink-mute">{t("Only these project files belong to this automation.")}</p><div className="mt-4 space-y-2">{data.map((file) => <article key={`${file.source_id}:${file.path}`} className="card flex items-center gap-3 px-4 py-3"><span aria-hidden="true">▤</span><div className="min-w-0"><p className="truncate text-sm font-medium text-ink">{file.path}</p><p className="truncate font-mono text-[10px] text-ink-faint">{file.source_id}</p></div></article>)}</div></section>;
}

function ModelsView({ contents, onChanged }: { contents: AutomationContents | null; onChanged: () => void }) {
  const models = contents?.models ?? [];
  const [deleting, setDeleting] = useState<ModelSummary | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function confirmDelete() {
    if (!deleting || deleteBusy) return;
    setDeleteBusy(true); setError(null);
    try { await api.deleteModel(deleting.artifact_id); setDeleting(null); onChanged(); }
    catch (caught) { setError(messageOf(caught)); }
    finally { setDeleteBusy(false); }
  }
  if (!models.length) return <Empty title={t("No models yet")} hint={t("Complete a run through the training stage to save a model.")} />;
  return (
    <>
      {error && <p className="mb-3 text-xs text-stop-700">{t("Something went wrong: {detail}", { detail: error })}</p>}
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {models.map((model) => (
          <article key={model.artifact_id} className="card px-4 py-3.5">
            <div className="mb-2 flex items-start justify-between gap-2">
              <div>
                <h3 className="text-sm font-semibold">{model.display_name}</h3>
                <p className="text-xs text-ink-mute">{model.estimator}</p>
              </div>
              <div className="flex items-center gap-1">
                {model.saved && <Badge tone="ok">{t("saved")}</Badge>}
                <button type="button" aria-label={t("Delete model")} title={t("Delete model")} onClick={() => setDeleting(model)} className="grid h-7 w-7 shrink-0 place-items-center rounded-lg text-ink-faint transition hover:bg-stop-50 hover:text-stop-700">
                  <TrashIcon />
                </button>
              </div>
            </div>
            {model.enhanced ? <ModelComparison model={model} /> : (
              <dl className="grid grid-cols-2 gap-2 border-t border-line-soft pt-2.5">
                <Pair label={`Holdout ${model.metric}`} value={num(model.holdout_score)} />
                <Pair label={t("CV mean")} value={num(model.cv_mean)} />
                <Pair label={t("CV std")} value={num(model.cv_std)} />
                <Pair label={t("Training rows")} value={fmt(model.training_rows)} />
              </dl>
            )}
            {model.enhanced && (
              <dl className="mt-2 grid grid-cols-3 gap-2">
                <Pair label={t("CV mean")} value={num(model.cv_mean)} />
                <Pair label={t("CV std")} value={num(model.cv_std)} />
                <Pair label={t("Training rows")} value={fmt(model.training_rows)} />
              </dl>
            )}
            <div className="mt-2.5 flex items-center justify-between gap-2 border-t border-line-soft pt-2">
              <p className="min-w-0 flex-1 truncate font-mono text-[10.5px] text-ink-faint">
                run {model.run_id} · {model.candidate_count} candidates
              </p>
              {/* #166: a single-model run keeps its download in the footer.
                  Paired runs put each download in its labelled comparison
                  column, so the file cannot be mistaken for its counterpart. */}
              <div className="flex shrink-0 items-center gap-1.5">
                {!model.enhanced && model.saved && (
                  <a
                    href={`/api/models/${model.artifact_id}/download`}
                    className="btn-ghost !py-1 text-xs"
                    download
                  >
                    {t("Download original")}
                  </a>
                )}
              </div>
            </div>
          </article>
        ))}
      </div>
      {deleting && <ArtifactDeleteDialog title={t("Delete {name}?", { name: deleting.display_name })} confirmLabel={t("Delete model")} busy={deleteBusy} onCancel={() => setDeleting(null)} onConfirm={() => void confirmDelete()} />}
    </>
  );
}

/** The original and RL-enhanced results, compared inside one run card.
 *
 * The delta arrives already oriented so positive means better, whichever way the
 * metric runs. It is toned on that sign rather than always reading as a win: an
 * enhanced model that scored worse should look like it did, because the run
 * still offers it for download and a person choosing between the two needs to
 * see which one actually won.
 */
export function ModelComparison({ model }: { model: ModelSummary }) {
  const enhanced = model.enhanced;
  if (!enhanced) return null;
  const delta = enhanced.score_delta;
  const better = typeof delta === "number" && delta > 0;
  return (
    <section
      aria-label={t("Original and RL-enhanced model comparison")}
      className="mt-2.5 grid grid-cols-2 overflow-hidden rounded-xl border border-line"
    >
      <div data-model-variant="original" className="min-w-0 bg-surface-sunken p-3">
        <p className="text-[10px] font-semibold uppercase tracking-wide text-ink-mute">{t("Original model")}</p>
        <p className="mt-2 text-[10px] text-ink-faint">{t("Holdout {metric}", { metric: model.metric })}</p>
        <p className="text-lg font-semibold tabular-nums text-ink">{num(model.holdout_score)}</p>
        <p className="mt-1 truncate text-[10px] text-ink-faint" title={model.estimator}>{model.estimator}</p>
        {model.saved && <a href={`/api/models/${model.artifact_id}/download`} className="btn-ghost mt-2 inline-flex !py-1 text-[10px]" download>{t("Download original")}</a>}
      </div>
      <div data-model-variant="rl-enhanced" className={cx("min-w-0 border-l border-line p-3", better ? "bg-ok-50/70" : "bg-warn-50/60")}>
        <div className="flex items-start justify-between gap-1.5">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-ink-mute">{t("RL-enhanced model")}</p>
          {typeof delta === "number" && <Badge tone={better ? "ok" : "warn"}>{`${delta > 0 ? "+" : ""}${delta.toFixed(3)}`}</Badge>}
        </div>
        <p className="mt-2 text-[10px] text-ink-faint">{t("Holdout {metric}", { metric: model.metric })}</p>
        <p className="text-lg font-semibold tabular-nums text-ink">{num(enhanced.holdout_score)}</p>
        <p className="mt-1 truncate text-[10px] text-ink-faint" title={enhanced.estimator}>{enhanced.estimator}</p>
        <p className="mt-1 text-[10px] text-ink-soft">{t("With {count} engineered feature(s)", { count: enhanced.generated_feature_count })}</p>
        {enhanced.saved && <a href={`/api/models/${enhanced.artifact_id}/download`} className="btn-ghost mt-2 inline-flex !py-1 text-[10px]" download>{t("Download RL-enhanced")}</a>}
      </div>
    </section>
  );
}

function ReportsView({ contents, onChanged }: { contents: AutomationContents | null; onChanged: () => void }) {
  const reports = contents?.reports ?? [];
  const [deleting, setDeleting] = useState<ReportSummary | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function confirmDelete() {
    if (!deleting || deleteBusy) return;
    setDeleteBusy(true); setError(null);
    try { await api.deleteReport(deleting.artifact_id); setDeleting(null); onChanged(); }
    catch (caught) { setError(messageOf(caught)); }
    finally { setDeleteBusy(false); }
  }
  if (!reports.length) return <Empty title={t("No reports yet")} hint={t("Reports appear once a run reaches the report stage.")} />;
  return (
    <>
      {error && <p className="mb-3 text-xs text-stop-700">{t("Something went wrong: {detail}", { detail: error })}</p>}
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
            <button type="button" aria-label={t("Delete report")} title={t("Delete report")} onClick={() => setDeleting(report)} className="grid h-7 w-7 shrink-0 place-items-center rounded-lg text-ink-faint transition hover:bg-stop-50 hover:text-stop-700">
              <TrashIcon />
            </button>
          </article>
        ))}
      </div>
      {deleting && <ArtifactDeleteDialog title={t("Delete this report?")} confirmLabel={t("Delete report")} busy={deleteBusy} onCancel={() => setDeleting(null)} onConfirm={() => void confirmDelete()} />}
    </>
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

/**
 * A model or report is a byproduct of a run, not a top-level record with its
 * own name -- there's nothing more specific to warn about than "this output",
 * unlike an automation/project delete which also names what else is affected.
 */
function ArtifactDeleteDialog({ title, confirmLabel, busy, onCancel, onConfirm }: { title: string; confirmLabel: string; busy: boolean; onCancel: () => void; onConfirm: () => void }) {
  // Dismissing a confirmation is a cancel, never a confirm (#387).
  const panel = useOverlayDismiss<HTMLDivElement>(onCancel);
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-ink/35 p-4" role="dialog" aria-modal="true" aria-labelledby="delete-artifact-title">
      <div ref={panel} className="w-full max-w-md rounded-2xl bg-surface p-6 shadow-2xl">
        <div className="grid h-10 w-10 place-items-center rounded-full bg-stop-50 text-stop-700" aria-hidden="true"><TrashIcon /></div>
        <h2 id="delete-artifact-title" className="mt-4 text-lg font-semibold text-ink">{title}</h2>
        <p className="mt-2 text-sm leading-relaxed text-ink-mute">{t("The run that produced it keeps its history; only this saved output is removed.")}</p>
        <div className="mt-6 flex justify-end gap-2">
          <button type="button" className="btn-ghost" disabled={busy} onClick={onCancel}>{t("Cancel")}</button>
          <button type="button" className="rounded-lg bg-stop-600 px-4 py-2 text-sm font-semibold text-white hover:bg-stop-700 disabled:opacity-50" disabled={busy} onClick={onConfirm}>{busy ? t("Deleting…") : confirmLabel}</button>
        </div>
      </div>
    </div>
  );
}

function TrashIcon() {
  return (
    <svg viewBox="0 0 20 20" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
      <path d="M3.75 5.5h12.5M8 5.5V4.25c0-.41.34-.75.75-.75h2.5c.41 0 .75.34.75.75V5.5" strokeLinecap="round" />
      <path d="M5.75 5.5l.62 9.9c.04.61.55 1.1 1.16 1.1h4.94c.61 0 1.12-.49 1.16-1.1l.62-9.9" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M8.5 8.5v5M11.5 8.5v5" strokeLinecap="round" />
    </svg>
  );
}

function messageOf(caught: unknown): string {
  return caught instanceof Error ? caught.message : String(caught);
}
