import { useEffect, useMemo, useState } from "react";

import { api, type ArtifactPreview, type DocumentTableDecisionInput } from "../lib/api";
import { t } from "../lib/i18n";

type Decision = "accepted" | "rejected";

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
 * detected headers, and a bounded sample of its rows, and every candidate needs
 * an explicit Accept or Reject before anything can be promoted. Rejected
 * candidates are never promoted, so they never become pipeline evidence.
 *
 * #359: candidates the extractor produced nothing for never reach the list at
 * all. They used to be shown carrying a warning badge and a disabled Accept,
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
  const [decisions, setDecisions] = useState<Map<string, Decision>>(new Map());
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

  function decide(candidateId: string, decision: Decision) {
    setDecisions((current) => {
      const next = new Map(current);
      // A second click on the same choice clears it, so a mis-click is undoable
      // and lands back on "no decision yet" rather than the opposite verdict.
      if (next.get(candidateId) === decision) next.delete(candidateId);
      else next.set(candidateId, decision);
      return next;
    });
  }

  const allAccepted = candidates.length > 0 && candidates.every(
    (candidate) => decisions.get(candidate.candidateId) === "accepted",
  );

  function toggleAllAccepted() {
    setDecisions((current) => {
      const next = new Map(current);
      for (const candidate of candidates) {
        if (allAccepted) next.delete(candidate.candidateId);
        else next.set(candidate.candidateId, "accepted");
      }
      return next;
    });
  }

  const acceptedCount = useMemo(
    () => [...decisions.values()].filter((decision) => decision === "accepted").length,
    [decisions],
  );
  const undecided = candidates.filter((candidate) => !decisions.has(candidate.candidateId)).length;
  const allDecided = candidates.length > 0 && undecided === 0;

  async function promote() {
    if (busy || !allDecided) return;
    setBusy(true); setError(null);
    try {
      // Every candidate carries a decision, not just the accepted ones. The
      // review artifact is the record of what a person decided, and a record
      // that only lists approvals cannot show that the rest were considered.
      const inputs: DocumentTableDecisionInput[] = candidates.map((candidate) => ({
        candidate_id: candidate.candidateId,
        decision: decisions.get(candidate.candidateId) ?? "rejected",
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
      <div className="max-h-[86vh] w-full max-w-3xl overflow-y-auto rounded-2xl bg-surface p-5 shadow-2xl">
        <div className="flex items-start gap-4">
          <div className="min-w-0 flex-1">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-warn-700">{t("Human decision")}</p>
            <h3 className="mt-1 text-lg font-semibold text-ink">{t("Review extracted tables")}</h3>
            <p className="mt-1 text-xs leading-relaxed text-ink-mute">{t("Accepted tables become training data and are treated exactly like an uploaded file. Anything left unaccepted stays out.")}</p>
          </div>
          <button type="button" className="btn-ghost !px-2 !py-1" aria-label={t("Close")} onClick={onClose}>×</button>
        </div>

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
                <div className="mt-5 flex justify-end">
                  <button type="button" className="btn-ghost text-xs" aria-pressed={allAccepted} onClick={toggleAllAccepted}>
                    {allAccepted ? t("Unselect all") : t("Select all")}
                  </button>
                </div>
                <ul className="mt-3 space-y-4">
                  {candidates.map((candidate) => {
                    const decision = decisions.get(candidate.candidateId);
                    const border = decision === "accepted" ? "border-ok-300" : decision === "rejected" ? "border-stop-200" : "border-line";
                    return (
                      <li key={candidate.candidateId} className={`rounded-xl border ${border} bg-surface`}>
                      <div className="flex items-start gap-3 border-b border-line px-4 py-3">
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-sm font-semibold text-ink">{candidate.title}</span>
                          <span className="mt-0.5 block text-[10px] text-ink-mute">
                            {candidate.sourceFile} · {t("page {page}", { page: candidate.page })} · {t("{rows} rows × {columns} columns", { rows: candidate.rowCount, columns: candidate.columns.length })}
                          </span>
                        </span>
                        <div className="flex shrink-0 items-center gap-1.5" role="group" aria-label={t("Accept or reject this table")}>
                          <button type="button" aria-pressed={decision === "accepted"} onClick={() => decide(candidate.candidateId, "accepted")} className={`rounded-lg px-3 py-1.5 text-[11px] font-semibold transition ${decision === "accepted" ? "bg-ok-600 text-white" : "border border-line text-ink-soft hover:bg-ok-50"}`}>{t("Accept")}</button>
                          <button type="button" aria-pressed={decision === "rejected"} onClick={() => decide(candidate.candidateId, "rejected")} className={`rounded-lg px-3 py-1.5 text-[11px] font-semibold transition ${decision === "rejected" ? "bg-stop-600 text-white" : "border border-line text-ink-soft hover:bg-stop-50"}`}>{t("Reject")}</button>
                        </div>
                      </div>
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
                <button type="button" className="btn-primary text-xs" onClick={() => void promote()} disabled={busy || !allDecided}>
                  {busy ? t("Promoting…") : t("Promote {count} accepted", { count: acceptedCount })}
                </button>
                {allDecided
                  ? <span className="text-[10px] text-ink-faint">{t("Recorded as a decision either way.")}</span>
                  : <span className="text-[10px] font-medium text-warn-700">{t("Decide on every table first ({count} left).", { count: undecided })}</span>}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
