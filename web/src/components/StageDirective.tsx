/**
 * Talk to the agent working this stage.
 *
 * Not a gate answer and not a retry correction. A correction says the last
 * attempt was wrong; this says what you want, and it applies every time the
 * stage runs including the first — so it can be left for a stage that has not
 * started yet, which is the point.
 *
 * The planner can write the same instructions from its own panel. This is the
 * direct route for when you already know which stage you mean.
 */
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { t } from "../lib/i18n";
import { Badge, Spinner, cx } from "./ui";

export function StageDirective({ runId, stageId }: { runId: string; stageId: string }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [sent, setSent] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.directives(runId)
      .then((d) => { if (!cancelled) setSent(d.directives[stageId] ?? []); })
      .catch(() => { /* absent directives are the normal case */ });
    return () => { cancelled = true; };
  }, [runId, stageId]);

  async function send() {
    const text = draft.trim();
    if (!text) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.directStage(runId, stageId, text);
      setSent(result.directives);
      setDraft("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card mb-4 px-4 py-3">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 text-left"
      >
        <Badge tone="brand">{t("Instruct")}</Badge>
        <span className="text-sm font-medium text-ink">{t("Tell this stage's agent what you want")}</span>
        {sent.length > 0 && <Badge>{sent.length}</Badge>}
        <span className="ml-auto text-xs text-ink-faint">{open ? "−" : "+"}</span>
      </button>

      {open && (
        <div className="mt-3">
          {sent.length > 0 && (
            <ul className="mb-3 space-y-1">
              {sent.map((line, i) => (
                <li key={i} className="rounded-lg bg-surface-sunken px-3 py-2 text-xs text-ink-soft">
                  {line}
                </li>
              ))}
            </ul>
          )}

          <textarea
            rows={2}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={t("e.g. prefer a linear model and report calibration")}
            className="field w-full"
          />

          {error && (
            <p className="mt-2 rounded-lg bg-stop-50 px-3 py-2 text-xs text-stop-700">{error}</p>
          )}

          <div className="mt-2 flex items-center gap-2">
            {busy && <Spinner />}
            <button
              onClick={() => void send()}
              disabled={!draft.trim() || busy}
              className={cx("btn-primary text-xs")}
            >
              {t("Send to the agent")}
            </button>
            <span className="text-[11px] text-ink-faint">
              {t("Applies the next time this stage runs.")}
            </span>
          </div>
        </div>
      )}
    </section>
  );
}
