import { useEffect, useState } from "react";
import { api, type DataSource } from "../lib/api";
import { t } from "../lib/i18n";
import { Badge, Empty, Spinner } from "../components/ui";

export function YourData({ onOpen }: { onOpen: (sourceId: string) => void }) {
  const [sources, setSources] = useState<DataSource[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.dataSources().then(setSources).catch((caught) => {
      setError(caught instanceof Error ? caught.message : String(caught));
    });
  }, []);

  return (
    <div className="h-full overflow-y-auto px-6 py-6">
      <header className="mb-5">
        <h1 className="text-xl font-semibold tracking-tight">{t("Your data")}</h1>
        <p className="mt-1 max-w-2xl text-sm text-ink-mute">
          {t("Sources stay separate from staging. Open one when you want intake, document understanding, schema discovery, reports, and planner chat.")}
        </p>
      </header>
      {error && <p className="rounded-lg bg-stop-50 px-3 py-2 text-sm text-stop-700">{error}</p>}
      {sources === null && <Spinner label={t("Loading datasets…")} />}
      {sources?.length === 0 && <Empty title={t("No data yet")} hint={t("Open Staging to upload structured files or PDFs.")} />}
      {sources && sources.length > 0 && (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {sources.map((source) => (
            <section key={source.source_id} className="rounded-xl border border-line bg-surface p-4 shadow-card">
              <div className="flex items-start gap-3">
                <div className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-brand-50 text-brand-700">▦</div>
                <div className="min-w-0 flex-1">
                  <h2 className="truncate text-sm font-semibold text-ink">{source.label}</h2>
                  <p className="mt-1 text-[11px] text-ink-mute">{source.source_id}</p>
                </div>
                {source.files && <Badge>{source.files.length} {t("files")}</Badge>}
              </div>
              {source.files && (
                <p className="mt-3 line-clamp-2 text-xs text-ink-mute">{source.files.join(" · ")}</p>
              )}
              <button onClick={() => onOpen(source.source_id)} className="btn-primary mt-4 w-full text-xs">
                {t("Open in staging")}
              </button>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
