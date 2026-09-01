import { useEffect, useMemo, useState } from "react";

import { api, type ArtifactPreview, type DocumentTableDecisionInput } from "../lib/api";
import { t } from "../lib/i18n";

type Candidate = {
  candidateId: string;
  sourceFile: string;
  title: string;
  page: string;
};

/** Review extracted PDF table candidates and promote the accepted ones.
 *
 * The backend has had both halves of this for a while -- one endpoint records
 * the decisions, another turns accepted candidates into real tables -- and
 * nothing in the interface called either. What existed was copy saying "review
 * and promote each extracted table before it can enter training data", with no
 * way to do it (#76).
 *
 * Everything defaults to rejected. Accepting a candidate is what puts a number
 * a machine read off a PDF page into the data a model learns from, and that has
 * to be an act rather than an omission: a default of "accepted" would mean a
 * person who closed this dialog without reading it had approved everything.
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
    })),
  ), [preview]);

  function toggle(candidateId: string) {
    setAccepted((current) => {
      const next = new Set(current);
      if (next.has(candidateId)) next.delete(candidateId); else next.add(candidateId);
      return next;
    });
  }

  async function promote() {
    if (busy || !candidates.length) return;
    setBusy(true); setError(null);
    try {
      // Every candidate carries a decision, not just the accepted ones. The
      // review artifact is the record of what a person decided, and a record
      // that only lists approvals cannot show that the rest were considered.
      const decisions: DocumentTableDecisionInput[] = candidates.map((candidate) => ({
        candidate_id: candidate.candidateId,
        decision: accepted.has(candidate.candidateId) ? "accepted" : "rejected",
      }));
      const review = await api.reviewDocumentTables(runId, decisions);
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
      <div className="max-h-[86vh] w-full max-w-2xl overflow-y-auto rounded-2xl bg-surface p-5 shadow-2xl">
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
              <ul className="mt-5 space-y-2">
                {candidates.map((candidate) => {
                  const isAccepted = accepted.has(candidate.candidateId);
                  return (
                    <li key={candidate.candidateId}>
                      <label className={`flex cursor-pointer items-start gap-3 rounded-xl border px-3 py-3 transition ${isAccepted ? "border-ok-300 bg-ok-50/60" : "border-line bg-surface"}`}>
                        <input type="checkbox" className="mt-0.5 h-4 w-4" checked={isAccepted} onChange={() => toggle(candidate.candidateId)} />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-xs font-semibold text-ink">{candidate.title}</span>
                          <span className="mt-0.5 block text-[10px] text-ink-mute">{candidate.sourceFile} · {t("page {page}", { page: candidate.page })}</span>
                        </span>
                        <span className={`shrink-0 text-[10px] font-semibold ${isAccepted ? "text-ok-700" : "text-ink-faint"}`}>{isAccepted ? t("Enters ML") : t("Stays out")}</span>
                      </label>
                    </li>
                  );
                })}
              </ul>
            )}
            {candidates.length > 0 && (
              <div className="mt-5 flex items-center gap-3 border-t border-line pt-4">
                <button type="button" className="btn-primary text-xs" onClick={() => void promote()} disabled={busy}>
                  {busy ? t("Promoting…") : t("Promote {count} accepted", { count: accepted.size })}
                </button>
                <span className="text-[10px] text-ink-faint">{t("Recorded as a decision either way.")}</span>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
