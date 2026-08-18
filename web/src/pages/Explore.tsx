import { t } from "../lib/i18n";
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
import {
  api,
  type DataSource,
  type MeasuredRelationship,
  type ProfiledTable,
  type SourceProfile,
} from "../lib/api";
import { Badge, Empty, Spinner, cx } from "../components/ui";
import { PlannerPanel } from "../components/PlannerPanel";

export function Explore({ onStart }: { onStart: (sourceId: string) => void }) {
  const [sources, setSources] = useState<DataSource[]>([]);
  const [sourceId, setSourceId] = useState<string>("");
  const [profile, setProfile] = useState<SourceProfile | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [openTable, setOpenTable] = useState<string | null>(null);
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
    if (!sourceId) { setProfile(null); return; }
    let cancelled = false;
    setLoading(true);
    setError(null);
    api.sourceProfile(sourceId)
      .then((p) => { if (!cancelled) { setProfile(p); setOpenTable(p.tables[0]?.name ?? null); } })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [sourceId]);

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
        <h1 className="text-xl font-semibold tracking-tight">{t("Your data")}</h1>
        <p className="mt-1 max-w-2xl text-sm text-ink-mute">
          {t("Drop files in or pick a dataset. Everything below is measured the moment the data lands — no model has seen it yet.")}
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
          accept=".csv,.tsv,.xlsx,.xls,.parquet"
          className="hidden"
          onChange={(e) => { void acceptFiles(e.target.files ?? []); e.target.value = ""; }}
        />
        {uploading ? (
          <Spinner label={t("Uploading and profiling…")} />
        ) : (
          <>
            <p className="text-sm font-medium text-ink">{t("Drop CSV, Excel or Parquet files here")}</p>
            <p className="mt-1 text-xs text-ink-mute">
              {t("Or click to choose. Drop several at once to keep them as one dataset.")}
            </p>
          </>
        )}
      </div>

      <p className="mb-4 text-[11px] text-ink-faint">
        {t("Databases are not supported yet — export the tables you need as files for now.")}
      </p>

      {error && (
        <p className="mb-4 rounded-lg bg-stop-50 px-3 py-2 text-sm text-stop-700">{error}</p>
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
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
          <div className="min-w-0">
            <TableList
              tables={profile.tables}
              open={openTable}
              onToggle={(name) => setOpenTable((cur) => (cur === name ? null : name))}
            />

            <RelationshipMap relationships={profile.relationships ?? []} />

            <div className="mt-5 rounded-xl border border-line bg-surface p-4">
              <h2 className="text-sm font-semibold text-ink">{t("Ready to run")}</h2>
              <p className="mt-1 text-xs leading-relaxed text-ink-mute">
                {t("From here the agents read this same profile, propose what is worth predicting and how to validate it, and stop for you before committing.")}
              </p>
              <button onClick={() => onStart(sourceId)} className="btn-primary mt-3 text-xs">
                {t("Continue to the pipeline")}
              </button>
            </div>
          </div>

          <aside className="min-w-0">
            <PlannerPanel sourceId={sourceId} />
          </aside>
        </div>
      )}
    </div>
  );
}

function TableList({
  tables, open, onToggle,
}: { tables: ProfiledTable[]; open: string | null; onToggle: (name: string) => void }) {
  return (
    <div className="space-y-2">
      {tables.map((table) => {
        const expanded = open === table.name;
        const sensitive = table.columns.filter((c) => c.sensitivity !== "public").length;
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
              {sensitive > 0 && <Badge tone="warn">{sensitive} sensitive</Badge>}
              {table.issues.length > 0 && <Badge tone="warn">{table.issues.length} notes</Badge>}
              <span className="ml-auto text-xs text-ink-faint">{expanded ? "−" : "+"}</span>
            </button>

            {expanded && (
              <div className="border-t border-line-soft px-4 py-3">
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-[10px] uppercase tracking-wide text-ink-faint">
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
                            {c.sensitivity !== "public" && <Badge tone="warn">{c.sensitivity}</Badge>}
                            {c.is_unique && <Badge tone="ok">unique</Badge>}
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

/**
 * The measured relationship map.
 *
 * These are not declared foreign keys — the sources are flat files and declare
 * nothing. Each edge is an overlap measured between the actual values of two
 * columns, which is how a join survives a column being named `provider_ref` on
 * one side and `physician_id` on the other.
 */
function RelationshipMap({ relationships }: { relationships: MeasuredRelationship[] }) {
  const [open, setOpen] = useState(true);
  if (!relationships.length) {
    return (
      <div className="mt-5 rounded-xl border border-line bg-surface px-4 py-3">
        <h2 className="text-sm font-semibold text-ink">{t("Relationships")}</h2>
        <p className="mt-1 text-xs text-ink-mute">
          {t("No column overlaps strong enough to suggest a join between these tables.")}
        </p>
      </div>
    );
  }
  return (
    <div className="mt-5 rounded-xl border border-line bg-surface">
      <button onClick={() => setOpen((o) => !o)} className="flex w-full items-center gap-2 px-4 py-3 text-left">
        <h2 className="text-sm font-semibold text-ink">{t("Relationships")}</h2>
        <Badge>{relationships.length} measured</Badge>
        <span className="ml-auto text-xs text-ink-faint">{open ? "−" : "+"}</span>
      </button>
      {open && (
        <div className="space-y-2 border-t border-line-soft px-4 py-3">
          {relationships.map((r, i) => {
            const orphans = r.orphan_rate;
            return (
              <div key={i} className="rounded-lg border border-line bg-surface-sunken px-3 py-2">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
                  <span className="font-medium text-ink">{r.from_table}</span>
                  <code className="rounded bg-surface px-1.5 py-0.5 text-[11px] text-ink-soft">
                    {r.from_columns.join(" + ")}
                  </code>
                  <span className="text-ink-faint">→</span>
                  <span className="font-medium text-ink">{r.to_table}</span>
                  <code className="rounded bg-surface px-1.5 py-0.5 text-[11px] text-ink-soft">
                    {r.to_columns.join(" + ")}
                  </code>
                  <Badge tone="neutral">{r.cardinality}</Badge>
                </div>
                <div className="mt-1.5 flex items-center gap-3">
                  <div className="h-1.5 w-32 overflow-hidden rounded-full bg-line">
                    <div
                      className={cx("h-full rounded-full", orphans > 0.1 ? "bg-warn-500" : "bg-ok-500")}
                      style={{ width: `${Math.round(r.overlap_rate * 100)}%` }}
                    />
                  </div>
                  <span className="text-xs text-ink-mute">
                    {(r.overlap_rate * 100).toFixed(1)}% of rows match
                    {orphans > 0 && (
                      <>
                        {" · "}
                        <span className={orphans > 0.1 ? "text-warn-700" : "text-ink-mute"}>
                          {(orphans * 100).toFixed(1)}% unmatched
                        </span>
                      </>
                    )}
                  </span>
                </div>
                {orphans > 0.02 && (
                  <p className="mt-1.5 text-[11px] leading-relaxed text-warn-700">
                    Unmatched rows would be dropped from the joined table. Worth knowing what
                    they are before the pipeline decides for you.
                  </p>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
