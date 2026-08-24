/**
 * What the data says, before anything is decided about it.
 *
 * This sits between choosing a dataset and configuring a run, because that is
 * the moment it is useful: intake has already measured everything and no model
 * has yet committed to a problem. Reading it here is what stops the pipeline
 * being a black box you point at a folder.
 *
 * Everything shown is measured. The relationships in particular are overlaps
 * between real column values, not guesses from column names — which is how a
 * join between `provider_ref` and `physician_id` is found at all, and how the
 * unmatched rows that would silently vanish from the joined table get named
 * before rather than after they vanish.
 */
import { useEffect, useState } from "react";
import { api, type MeasuredRelationship, type SourceProfile } from "../lib/api";
import { t } from "../lib/i18n";
import { PlannerPanel } from "./PlannerPanel";
import { SchemaDiagram } from "./SchemaDiagram";
import { Badge, Spinner, cx } from "./ui";

export function DataReview({ sourceId }: { sourceId: string }) {
  const [profile, setProfile] = useState<SourceProfile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openTable, setOpenTable] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);

  useEffect(() => {
    if (!sourceId) return;
    let cancelled = false;
    setProfile(null);
    setError(null);
    api.sourceProfile(sourceId)
      .then((p) => {
        if (cancelled) return;
        setProfile(p);
        setOpenTable(p.tables[0]?.name ?? null);
      })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); });
    return () => { cancelled = true; };
  }, [sourceId]);

  if (error) {
    return <p className="rounded-lg bg-stop-50 px-3 py-2 text-sm text-stop-700">{error}</p>;
  }
  if (!profile) return <Spinner label={t("Reading the data…")} />;

  const totals = profile.tables.reduce(
    (acc, table) => ({
      rows: acc.rows + table.rows,
      columns: acc.columns + table.columns_count,
      sensitive:
        acc.sensitive + table.columns.filter((c) => c.sensitivity === "pii").length,
      issues: acc.issues + table.issues.length,
    }),
    { rows: 0, columns: 0, sensitive: 0, issues: 0 },
  );

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Figure label={t("Tables")} value={profile.tables.length} />
        <Figure label={t("Rows")} value={totals.rows.toLocaleString()} />
        <Figure label={t("Columns")} value={totals.columns} />
        <Figure label={t("Personal")} value={totals.sensitive} tone={totals.sensitive ? "warn" : undefined} />
      </div>

      {/* Above the per-table detail, because "how do these fit together" is the
          question a person has before "what is in this column". */}
      {profile.tables.length > 1 && (
        <section className="rounded-xl border border-line bg-surface px-4 py-3">
          <h3 className="mb-2 text-sm font-semibold text-ink">{t("Schema")}</h3>
          <SchemaDiagram
            tables={profile.tables}
            relationships={profile.relationships ?? []}
          />
        </section>
      )}

      {profile.tables.map((table) => {
        const open = openTable === table.name;
        const sensitive = table.columns.filter((c) => c.sensitivity === "pii").length;
        return (
          <section key={table.name} className="rounded-xl border border-line bg-surface">
            <button
              onClick={() => setOpenTable(open ? null : table.name)}
              className="flex w-full flex-wrap items-center gap-2 px-4 py-3 text-left"
            >
              <span className="font-medium text-ink">{table.name}</span>
              <Badge>{table.format}</Badge>
              <span className="text-xs text-ink-mute">
                {table.rows.toLocaleString()} {t("rows")} · {table.columns_count} {t("columns")}
              </span>
              {table.candidate_keys.length > 0 && (
                <Badge tone="ok">{t("key")}: {table.candidate_keys[0].join(" + ")}</Badge>
              )}
              {sensitive > 0 && <Badge tone="warn">{sensitive} {t("personal")}</Badge>}
              <span className="ml-auto text-xs text-ink-faint">{open ? "−" : "+"}</span>
            </button>

            {open && (
              <div className="overflow-x-auto border-t border-line-soft px-4 py-3">
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
                    {table.columns.map((column) => (
                      <tr key={column.name} className="border-t border-line-soft">
                        <td className="py-1.5 pr-4 font-medium text-ink">{column.name}</td>
                        <td className="py-1.5 pr-4 text-ink-mute">
                          {t(column.semantic_type.replace(/_/g, " "))}
                        </td>
                        <td className="py-1.5 pr-4 text-right tabular-nums text-ink-soft">
                          {(column.null_rate * 100).toFixed(1)}%
                        </td>
                        <td className="py-1.5 pr-4 text-right tabular-nums text-ink-soft">
                          {(column.unique_rate * 100).toFixed(1)}%
                        </td>
                        <td className="py-1.5">
                          {column.sensitivity === "pii" && (
                            <Badge tone="warn">{t("personal")}</Badge>
                          )}
                          {column.is_unique && <Badge tone="ok">{t("unique")}</Badge>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {table.issues.length > 0 && (
                  <ul className="mt-3 space-y-1">
                    {table.issues.map((code) => (
                      <li key={code} className="text-xs text-ink-mute">· {t(code.replace(/_/g, " "))}</li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </section>
        );
      })}

      <Relationships relationships={profile.relationships ?? []} />

      <section className="rounded-xl border border-line bg-surface">
        <button
          onClick={() => setAsking((a) => !a)}
          className="flex w-full items-center gap-2 px-4 py-3 text-left"
        >
          <Badge tone="brand">{t("Ask")}</Badge>
          <span className="text-sm font-medium text-ink">{t("Ask about this data")}</span>
          <span className="ml-auto text-xs text-ink-faint">{asking ? "−" : "+"}</span>
        </button>
        {asking && (
          <div className="border-t border-line-soft p-3">
            <PlannerPanel sourceId={sourceId} />
          </div>
        )}
      </section>
    </div>
  );
}

function Figure({
  label, value, tone,
}: { label: string; value: string | number; tone?: "warn" }) {
  return (
    <div className={cx(
      "rounded-lg border px-3 py-2",
      tone === "warn" ? "border-warn-500/40 bg-warn-50" : "border-line bg-surface",
    )}>
      <p className="text-[10px] uppercase tracking-wide text-ink-faint">{label}</p>
      <p className="mt-0.5 text-lg font-semibold tabular-nums text-ink">{value}</p>
    </div>
  );
}

/**
 * The relationship map. Optional in the sense that a dataset may have none —
 * not in the sense that it is decoration. Each edge is measured, and an edge
 * with unmatched rows is the single most useful thing on this screen, because
 * those rows disappear from the joined table without anyone being told.
 */
function Relationships({ relationships }: { relationships: MeasuredRelationship[] }) {
  const [open, setOpen] = useState(true);
  if (!relationships.length) {
    return (
      <section className="rounded-xl border border-line bg-surface px-4 py-3">
        <h3 className="text-sm font-semibold text-ink">{t("Relationships")}</h3>
        <p className="mt-1 text-xs text-ink-mute">
          {t("No column overlaps strong enough to suggest a join between these tables.")}
        </p>
      </section>
    );
  }
  return (
    <section className="rounded-xl border border-line bg-surface">
      <button onClick={() => setOpen((o) => !o)} className="flex w-full items-center gap-2 px-4 py-3 text-left">
        <h3 className="text-sm font-semibold text-ink">{t("Relationships")}</h3>
        <Badge>{relationships.length} {t("measured")}</Badge>
        <span className="ml-auto text-xs text-ink-faint">{open ? "−" : "+"}</span>
      </button>
      {open && (
        <div className="space-y-2 border-t border-line-soft px-4 py-3">
          {relationships.map((r, i) => (
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
                    className={cx("h-full rounded-full", r.orphan_rate > 0.1 ? "bg-warn-500" : "bg-ok-500")}
                    style={{ width: `${Math.round(r.overlap_rate * 100)}%` }}
                  />
                </div>
                <span className="text-xs text-ink-mute">
                  {(r.overlap_rate * 100).toFixed(1)}% {t("of rows match")}
                  {r.orphan_rate > 0 && (
                    <> · <span className={r.orphan_rate > 0.1 ? "text-warn-700" : ""}>
                      {(r.orphan_rate * 100).toFixed(1)}% {t("unmatched")}
                    </span></>
                  )}
                </span>
              </div>
              {r.orphan_rate > 0.02 && (
                <p className="mt-1.5 text-[11px] leading-relaxed text-warn-700">
                  {t("Unmatched rows would be dropped from the joined table. Worth knowing what they are before the pipeline decides for you.")}
                </p>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
