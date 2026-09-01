/**
 * Choose the data. That is the whole dialog.
 *
 * It used to carry the review and the configuration as two further steps,
 * which put the most consequential screen in the product inside a modal, ahead
 * of the run, showing a profile the run had not yet performed. Choosing a
 * dataset now starts a real run that stops after intake and schema discovery,
 * and everything that used to live here happens on that run's intake stage,
 * with the pipeline visible around it.
 */
import { useEffect, useState } from "react";
import { api, type DatasetSummary } from "../lib/api";
import { t } from "../lib/i18n";
import { Badge, Spinner, cx } from "./ui";

export function LaunchDialog({
  onClose, onLaunched, initialSourceId = null,
}: {
  onClose: () => void;
  /** Receives the staged run's id; it is a real run from this moment. */
  onLaunched: (runId: string) => void;
  /** Preselected from the data screen. */
  initialSourceId?: string | null;
}) {
  const [sources, setSources] = useState<{ source_id: string; label: string }[]>([]);
  /**
   * Richer than `sources`: table and column counts, quality issues, sensitive
   * columns. A bare list of names gave no basis for choosing between datasets,
   * which is the only decision this dialog asks for.
   */
  const [catalog, setCatalog] = useState<DatasetSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [staging, setStaging] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [reuseCache, setReuseCache] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api.dataSources()
      .then((list) => { if (!cancelled) setSources(list); })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    // Enrichment only (row/table counts merged onto the source picker), so a
    // single full page is enough; the picker still works without it.
    api.datasets({ pageSize: 100 })
      .then((page) => { if (!cancelled) setCatalog(page.items); })
      .catch(() => { /* the counts are enrichment; the list still works */ });
    return () => { cancelled = true; };
  }, []);

  // Preselected from the data screen: stage it and get out of the way.
  useEffect(() => {
    if (initialSourceId) void choose(initialSourceId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialSourceId]);

  async function choose(sourceId: string) {
    setStaging(sourceId);
    setError(null);
    try {
      const staged = await api.stageRun(sourceId, reuseCache);
      onLaunched(staged.run_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setStaging(null);
    }
  }

  async function upload(files: FileList | null) {
    if (!files?.length) return;
    setUploading(true);
    setError(null);
    try {
      let sourceId: string | undefined;
      for (const file of Array.from(files)) {
        const result = await api.upload(file, sourceId);
        sourceId = result.source_id;
      }
      if (sourceId) await choose(sourceId);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 p-4">
      <div className="flex max-h-full w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-line bg-surface shadow-pop">
        <header className="flex shrink-0 items-center gap-3 border-b border-line px-5 py-3.5">
          <div className="min-w-0 flex-1">
            <h2 className="text-base font-semibold">{t("New run")}</h2>
            <p className="mt-0.5 text-[11px] text-ink-mute">
              {t("Choose the data. Intake and schema discovery run straight away; you configure the rest on the pipeline.")}
            </p>
          </div>
          <button onClick={onClose} className="rounded p-1 text-ink-faint hover:bg-surface-sunken hover:text-ink" title={t("Close")}>
            <svg viewBox="0 0 20 20" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8">
              <path d="m5 5 10 10M15 5 5 15" strokeLinecap="round" />
            </svg>
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          <label className="mb-3 flex items-center gap-2 text-[11px] text-ink-mute">
            <input
              type="checkbox"
              checked={reuseCache}
              onChange={(event) => setReuseCache(event.target.checked)}
            />
            {t("Reuse a previous analysis of unchanged files")}
          </label>
          {loading && <Spinner label={t("Loading datasets…")} />}
          {error && <p className="mb-3 rounded-lg bg-stop-50 px-3 py-2 text-xs text-stop-700">{t("Something went wrong: {detail}", { detail: error })}</p>}

          <div className="grid gap-2.5">
            {sources.map((s) => {
              const d = catalog.find((c) => c.source_id === s.source_id);
              const busy = staging === s.source_id;
              return (
                <button
                  key={s.source_id}
                  onClick={() => void choose(s.source_id)}
                  disabled={staging !== null}
                  className={cx(
                    "rounded-xl border px-4 py-3 text-left transition-colors disabled:opacity-60",
                    busy ? "border-brand-500 bg-brand-50" : "border-line hover:bg-surface-sunken",
                  )}
                >
                  <div className="flex items-baseline gap-2">
                    <span className="text-sm font-semibold text-ink">{s.label}</span>
                    {(d?.sensitive_columns ?? 0) > 0 && (
                      <Badge tone="warn">{d?.sensitive_columns} {t("personal")}</Badge>
                    )}
                    {(d?.quality_issues ?? 0) > 0 && (
                      <Badge tone="warn">{d?.quality_issues} {t("issues")}</Badge>
                    )}
                    {busy && <Badge tone="brand">{t("Starting…")}</Badge>}
                  </div>

                  {/* A folder under data/ that holds no supported files comes
                      back with only a profile_error, so every count here is
                      treated as optional rather than assumed present. */}
                  {d?.tables !== undefined ? (
                    <>
                      <p className="mt-1 text-xs text-ink-soft">
                        {d.tables} {t("tables")} · {(d.rows ?? 0).toLocaleString()} {t("rows")} ·{" "}
                        {d.columns} {t("columns")} · {d.candidate_keys} {t("candidate keys")}
                      </p>
                      {/* The table names are what tell you whether this is the
                          dataset you meant; the totals alone do not. */}
                      <p className="mt-1.5 truncate font-mono text-[11px] text-ink-faint">
                        {(d.table_summaries ?? []).map((table) => table.name).join(" · ")}
                      </p>
                    </>
                  ) : d?.profile_error ? (
                    <p className="mt-1 text-xs text-warn-700">{d.profile_error}</p>
                  ) : (
                    <p className="mt-1 text-xs text-ink-faint">{t("Profiling…")}</p>
                  )}
                </button>
              );
            })}
          </div>

          {!loading && sources.length === 0 && (
            <p className="rounded-lg border border-line bg-surface-sunken px-4 py-6 text-center text-xs text-ink-mute">
              {t("No datasets found. Place source files under")} <code>data/</code>{" "}
              {t("on the server, or upload them below.")}
            </p>
          )}

          <label className="mt-4 flex cursor-pointer items-center justify-center gap-2 rounded-xl border border-dashed border-line px-4 py-4 text-xs text-ink-mute hover:border-ink-faint">
            <input
              type="file"
              multiple
              className="hidden"
              onChange={(e) => void upload(e.target.files)}
              disabled={uploading || staging !== null}
            />
            {uploading ? t("Uploading…") : t("Or upload files — CSV, Parquet, Excel. Several files become one dataset.")}
          </label>

          <p className="mt-4 text-[11px] leading-relaxed text-ink-faint">
            {t("Profiled locally. No rows leave this machine.")}
          </p>
        </div>
      </div>
    </div>
  );
}
