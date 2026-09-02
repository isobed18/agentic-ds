import { useEffect, useMemo, useState } from "react";

import { api, type ArtifactPreview, type DocumentTableDecisionInput } from "../lib/api";
import { t } from "../lib/i18n";

type Candidate = {
  candidateId: string;
  sourceFile: string;
  title: string;
  page: string;
  columns: string[];
  rowCount: number;
  sampleRows: string[][];
};

/** Review extracted PDF table candidates and promote the accepted ones.
 *
 * The backend has had both halves of this for a while -- one endpoint records
 * the decisions, another turns accepted candidates into real tables -- and
 * nothing in the interface called either. What existed was copy saying "review
 * and promote each extracted table before it can enter training data", with no
 * way to do it (#76).
 *
 * #303: a checkbox that defaulted to rejected still asked for trust without
 * evidence -- a person could not see what a candidate table actually held, so
 * accepting one was a blind click. Each candidate now shows its page, its
 * detected headers, and a bounded sample of its rows, so the decision is made
 * against evidence. Rejected candidates are never promoted, so they never
 * become pipeline evidence.
 *
 * #360: one checkbox per candidate, not an Accept/Reject button pair. The pair
 * carried three states (accepted / rejected / undecided) while "Select all"
 * assumed two, so the two controls could not compose: accepting most tables
 * and rejecting a few could be expressed, but pressing "Select all" again
 * cleared every decision rather than the accepted ones, and Promote stayed
 * gated behind ruling on every row. Checked means accepted, unchecked means
 * rejected, and the header is the ordinary tri-state select-all.
 *
 * #359: candidates the extractor produced nothing for never reach the list at
 * all. They used to be shown carrying a warning badge and a disabled checkbox,
 * which made a failed extraction look like a decision waiting to be made --
 * and still cost a click each, because the promote gate wanted a verdict on
 * every row.
 */
export function DocumentTableReview({
  runId,
  extractionArtifactId,
  onClose,
  onPromoted,
}: {
  runId: string;
  extractionArtifactId: string;
  onClose: () => void;
  onPromoted: () => void;
}) {
  const [preview, setPreview] = useState<ArtifactPreview | null>(null);
  // Checked means accepted; a candidate not in here is rejected on promote (#360).
  const [accepted, setAccepted] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [promoted, setPromoted] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.artifactPreview(extractionArtifactId)
      .then((result) => { if (!cancelled) setPreview(result); })
      .catch((caught) => { if (!cancelled) setError(caught instanceof Error ? caught.message : String(caught)); });
    return () => { cancelled = true; };
  }, [extractionArtifactId]);

  const candidates = useMemo<Candidate[]>(() => (preview?.documents ?? []).flatMap((document) =>
    (document.tables ?? []).map((table) => ({
      candidateId: String(table.candidate_id),
      sourceFile: document.source_file,
      title: String(table.title ?? table.candidate_id),
      page: String(table.page_number ?? "—"),
      columns: Array.isArray(table.columns) ? table.columns.map(String) : [],
      rowCount: typeof table.row_count === "number" ? table.row_count : 0,
      sampleRows: Array.isArray(table.sample_rows)
        ? (table.sample_rows as unknown[]).map((row) => (Array.isArray(row) ? row.map(String) : []))
        : [],
    }))
    // #359: `row_count` is the length of the rows the extractor actually
    // produced, so a zero here is a failed extraction rather than a small
    // table. Such a candidate can never be promoted (#310), so listing it asks
    // a person to make a decision that has already been made for them.
    .filter((candidate) => candidate.rowCount > 0 || candidate.sampleRows.length > 0),
  ), [preview]);

  function setAcceptance(candidateId: string, checked: boolean) {
    setAccepted((current) => {
      const next = new Set(current);
      if (checked) next.add(candidateId);
      else next.delete(candidateId);
      return next;
    });
  }

  // #310 still applies: a candidate the extractor got no rows out of cannot be
  // promoted, so its box is not checkable and it is excluded from "all". Were
  // it counted, a single failed extraction would put "all checked" out of
  // reach and the header would never leave indeterminate.
  const selectable = useMemo(() => candidates.filter((candidate) => candidate.rowCount > 0), [candidates]);
  const acceptedCount = useMemo(
    () => selectable.filter((candidate) => accepted.has(candidate.candidateId)).length,
    [selectable, accepted],
  );
  const allAccepted = selectable.length > 0 && acceptedCount === selectable.length;
  // The third state every multi-select header has: some checked, but not all.
  const someAccepted = acceptedCount > 0 && !allAccepted;

  function toggleAllAccepted() {
    // Ordinary tri-state behaviour: indeterminate resolves upward to "all
    // checked", and only an already-full selection is cleared.
    setAccepted(allAccepted ? new Set() : new Set(selectable.map((candidate) => candidate.candidateId)));
  }

  async function promote() {
    // #360: unchecked is a verdict, not a gap, so the only thing left to gate
    // on is whether anything at all was accepted.
    if (busy || acceptedCount === 0) return;
    setBusy(true); setError(null);
    try {
      // Every candidate carries a decision, not just the accepted ones. The
      // review artifact is the record of what a person decided, and a record
      // that only lists approvals cannot show that the rest were considered.
      const inputs: DocumentTableDecisionInput[] = candidates.map((candidate) => ({
        candidate_id: candidate.candidateId,
        decision: accepted.has(candidate.candidateId) ? "accepted" : "rejected",
      }));
      const review = await api.reviewDocumentTables(runId, inputs);
      const result = await api.promoteDocumentTables(runId, review.artifact_id);
      setPromoted(result.table_assets.length);
      onPromoted();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-ink/30 p-4" role="dialog" aria-modal="true">
      {/* #388: the header used to sit inside the scrolling container, so
          with more than a few candidates the × scrolled out of view and the
          only way out of the dialog was to scroll back up to find it. The
          panel is a column now: the header keeps its place and the list
          below it is what moves. */}
      <div className="flex max-h-[86vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl bg-surface shadow-2xl">
        <div className="flex shrink-0 items-start gap-4 border-b border-line p-5">
          <div className="min-w-0 flex-1">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-warn-700">{t("Human decision")}</p>
            <h3 className="mt-1 text-lg font-semibold text-ink">{t("Review extracted tables")}</h3>
            <p className="mt-1 text-xs leading-relaxed text-ink-mute">{t("Accepted tables become training data and are treated exactly like an uploaded file. Anything left unaccepted stays out.")}</p>
          </div>
          <button type="button" className="btn-ghost !px-2 !py-1" aria-label={t("Close")} onClick={onClose}>×</button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-5">
        {error && <p role="alert" className="mt-4 rounded-lg bg-stop-50 px-3 py-2 text-xs text-stop-700">{t("Something went wrong: {detail}", { detail: error })}</p>}

        {promoted !== null ? (
          <div className="mt-5 rounded-xl border border-ok-200 bg-ok-50 px-4 py-4">
            <p className="text-sm font-semibold text-ok-700">{t("{count} tables promoted", { count: promoted })}</p>
            <p className="mt-1 text-[11px] text-ink-mute">{promoted === 0 ? t("Nothing was accepted, so nothing entered the pipeline.") : t("They now behave like any other uploaded table.")}</p>
            <button type="button" className="btn-primary mt-4 w-full justify-center text-xs" onClick={onClose}>{t("Close")}</button>
          </div>
        ) : (
          <>
            {!preview && !error && <p className="mt-5 text-xs text-ink-mute">{t("Loading…")}</p>}
            {preview && !candidates.length && <p className="mt-5 rounded-lg bg-surface-sunken px-3 py-3 text-xs text-ink-mute">{t("This extraction produced no table candidates.")}</p>}
            {candidates.length > 0 && (
              <>
                {/* #360: the header box is the same control as the rows below
                    it, in the same column, so "check all then uncheck two"
                    reads the way it does in every other list. */}
                <div className="mt-5 flex items-center gap-3 rounded-lg bg-surface-sunken px-4 py-2">
                  <input
                    type="checkbox"
                    className="h-4 w-4 shrink-0 accent-ok-600"
                    checked={allAccepted}
                    disabled={!selectable.length}
                    // `indeterminate` is a DOM property with no HTML
                    // attribute, so React can only set it on the node itself.
                    ref={(node) => { if (node) node.indeterminate = someAccepted; }}
                    onChange={toggleAllAccepted}
                    aria-label={allAccepted ? t("Unselect all") : t("Select all")}
                  />
                  <span className="text-xs font-medium text-ink-soft">{t("Accept all {count} tables", { count: selectable.length })}</span>
                </div>
                <ul className="mt-3 space-y-4">
                  {candidates.map((candidate) => {
                    const empty = candidate.rowCount === 0;
                    const checked = accepted.has(candidate.candidateId);
                    return (
                      <li key={candidate.candidateId} className={`rounded-xl border ${checked ? "border-ok-300" : "border-line"} bg-surface`}>
                      <label className={`flex items-start gap-3 border-b border-line px-4 py-3 ${empty ? "" : "cursor-pointer"}`}>
                        <input
                          type="checkbox"
                          className="mt-0.5 h-4 w-4 shrink-0 accent-ok-600 disabled:cursor-not-allowed disabled:opacity-40"
                          checked={checked}
                          disabled={empty}
                          title={empty ? t("No data extracted — cannot be accepted") : undefined}
                          onChange={(event) => setAcceptance(candidate.candidateId, event.target.checked)}
                        />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-sm font-semibold text-ink">{candidate.title}</span>
                          <span className="mt-0.5 block text-[10px] text-ink-mute">
                            {candidate.sourceFile} · {t("page {page}", { page: candidate.page })} · {t("{rows} rows × {columns} columns", { rows: candidate.rowCount, columns: candidate.columns.length })}
                          </span>
                        </span>
                        <span className={`shrink-0 rounded-md px-2 py-1 text-[10px] font-semibold ${checked ? "bg-ok-50 text-ok-700" : "bg-surface-sunken text-ink-faint"}`}>
                          {checked ? t("Enters ML") : t("Stays out")}
                        </span>
                      </label>
                      {/* The faithful visual preview of what was detected: the
                          detected headers over a bounded sample of the rows, so
                          the decision above is made against evidence (#303). */}
                      <div className="overflow-x-auto px-4 py-3">
                        {candidate.columns.length > 0 || candidate.sampleRows.length > 0 ? (
                          <table className="w-full border-collapse text-[10px]">
                            {candidate.columns.length > 0 && (
                              <thead>
                                <tr>{candidate.columns.map((column, index) => <th key={index} className="border border-line bg-surface-sunken px-2 py-1 text-left font-semibold text-ink">{column}</th>)}</tr>
                              </thead>
                            )}
                            <tbody>
                              {candidate.sampleRows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex} className="border border-line px-2 py-1 text-ink-mute">{cell}</td>)}</tr>)}
                            </tbody>
                          </table>
                        ) : (
                          <p className="text-[10px] text-ink-faint">{t("No preview was extracted for this candidate.")}</p>
                        )}
                        {candidate.rowCount > candidate.sampleRows.length && (
                          <p className="mt-2 text-[10px] text-ink-faint">{t("Showing the first {shown} of {total} rows.", { shown: candidate.sampleRows.length, total: candidate.rowCount })}</p>
                        )}
                      </div>
                      </li>
                    );
                  })}
                </ul>
              </>
            )}
            {candidates.length > 0 && (
              <div className="mt-5 flex flex-wrap items-center gap-3 border-t border-line pt-4">
                <button type="button" className="btn-primary text-xs" onClick={() => void promote()} disabled={busy || acceptedCount === 0}>
                  {busy ? t("Promoting…") : t("Promote {count} accepted", { count: acceptedCount })}
                </button>
                {acceptedCount > 0
                  ? <span className="text-[10px] text-ink-faint">{t("Unchecked tables are recorded as rejected.")}</span>
                  : <span className="text-[10px] font-medium text-warn-700">{t("Check at least one table to promote.")}</span>}
              </div>
            )}
          </>
        )}
        </div>
      </div>
    </div>
  );
}
