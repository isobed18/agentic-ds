import { useEffect, useRef, useState } from "react";

import { api, type ArtifactPreview } from "../lib/api";
import { activeLanguage, t } from "../lib/i18n";
import { keepRevealed, nextToReveal, revealDelay } from "./artifactReveal";
import { artifactTitle } from "./artifactTitle";

/** Reveal ids one at a time, in the order they first appeared.
 *
 * The runner writes several artifacts inside a single 1.5s polling tick, so
 * without this three of them appear in the same frame and the graph reads as a
 * page refresh rather than as work happening. Watching progress is the entire
 * reason somebody is on this screen while a run is live; artifacts arriving one
 * after another is what makes it legible as progress.
 *
 * Already-revealed ids keep their position, so a later poll cannot reorder what
 * the viewer has already seen -- the sequence is a record of arrival, and
 * re-sorting it would make earlier work look like it happened later.
 */
export function useSequentialReveal(ids: string[], intervalMs = 420): string[] {
  const key = ids.join("|");
  const [shown, setShown] = useState<string[]>([]);

  useEffect(() => {
    setShown((current) => keepRevealed(current, ids));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  useEffect(() => {
    const next = nextToReveal(shown, ids);
    if (next === undefined) return;
    const timer = window.setTimeout(() => {
      setShown((current) => (current.includes(next) ? current : [...current, next]));
    }, revealDelay(shown.length, intervalMs));
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, shown, intervalMs]);

  return shown;
}

/** Fetch each artifact's display title once, when the list is first expanded.
 *
 * #66: the only per-artifact title lookup is `artifactPreview(id)`, one at a
 * time when a modal opens. Surfacing the title next to the node means fetching
 * them up front — but only on expand, and only once each, so a collapsed node
 * costs nothing and a poll that re-reports the same ids does not re-fetch.
 */
function useArtifactTitles(ids: string[], enabled: boolean): Record<string, string> {
  const [previews, setPreviews] = useState<Record<string, ArtifactPreview>>({});
  const requested = useRef<Set<string>>(new Set());

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    for (const id of ids) {
      if (requested.current.has(id)) continue;
      requested.current.add(id);
      void api
        .artifactPreview(id)
        .then((preview) => {
          if (!cancelled) setPreviews((current) => ({ ...current, [id]: preview }));
        })
        .catch(() => {
          // A failed title is a missing label, never a broken node: allow a
          // later expand to try again rather than leaving the row blank forever.
          requested.current.delete(id);
        });
    }
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, ids.join("|")]);

  const language = activeLanguage();
  const titles: Record<string, string> = {};
  for (const id of ids) {
    const preview = previews[id];
    titles[id] = preview ? artifactTitle(preview, language, t) : t("Loading…");
  }
  return titles;
}

/** The artifacts a stage produced, behind a single "Artifacts (N)" opener.
 *
 * The badge under a node used to be inconsistent (#46: inline here, absolute
 * elsewhere, at three different offsets) and led nowhere useful (#45). This is
 * one shared control every node type gets identically: a pill that straddles
 * the node's bottom edge, and toggles a dashed list of numbered artifacts —
 * each captioned with its own title so you can see what an artifact is without
 * opening it (#66). The count still ticks up one per poll (`useSequentialReveal`),
 * which is the live-progress signal the old round nodes existed to give.
 */
export function ArtifactNodes({ ids, onOpen }: { ids: string[]; onOpen: (id: string) => void }) {
  const shown = useSequentialReveal(ids);
  const [open, setOpen] = useState(false);
  const titles = useArtifactTitles(shown, open);
  if (!ids.length) return null;
  return (
    // The column's top is pinned to the node's bottom edge and carries no
    // height-proportional transform, so the list below can only grow downward.
    // A translate percentage resolves against the element's *own* height, so
    // while the column wrapped both the pill and the list, every height change
    // was split between its two ends: the list grew down by half and the pill
    // rose by half. Opening the list moved the control out from under the
    // cursor that clicked it, and each artifact `useSequentialReveal` appends
    // during a run lifted it again until it overlapped its node (#162).
    <div className="absolute left-1/2 top-full z-10 flex w-full -translate-x-1/2 flex-col items-center">
      {/* The straddle from #66 moves onto the pill, whose own height never
          changes, so it is a fixed offset rather than a share of the column. */}
      <div className="-translate-y-1/2">
        <button
          type="button"
          onClick={() => setOpen((current) => !current)}
          aria-expanded={open}
          className="pointer-events-auto rounded-full border border-brand-300 bg-surface px-3 py-1 text-[10px] font-semibold tabular-nums text-brand-700 shadow-card transition hover:-translate-y-0.5 hover:border-brand-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500"
        >
          {t("Artifacts ({count})", { count: shown.length })}
        </button>
      </div>
      {open && (
        <ol className="pointer-events-auto mt-2 flex w-full min-w-0 flex-col gap-2" aria-label={t("Artifacts")}>
          {shown.map((id, index) => (
            <li key={id} className="flex min-w-0 items-center gap-2">
              {/* The numbered circle straddles the dashed line down the list,
                  the way the opener straddles the node edge above it. */}
              <span className="relative grid h-7 w-7 shrink-0 place-items-center rounded-full border border-brand-300 bg-brand-50 text-[10px] font-semibold tabular-nums text-brand-700">
                {index + 1}
                {index < shown.length - 1 && (
                  <span
                    className="absolute left-1/2 top-full h-2 w-px -translate-x-1/2 border-l border-dashed border-line"
                    aria-hidden="true"
                  />
                )}
              </span>
              <button
                type="button"
                onClick={() => onOpen(id)}
                title={titles[id]}
                className="artifact-node min-w-0 max-w-[calc(100%-2.25rem)] truncate rounded-lg border border-line bg-surface px-2.5 py-1.5 text-left text-[10px] text-ink-soft shadow-card transition hover:border-brand-400 hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500"
              >
                {titles[id]}
              </button>
            </li>
          ))}
          {shown.length < ids.length && (
            <li aria-hidden="true" className="flex items-center gap-2">
              <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full border border-dashed border-line">
                <span className="h-2 w-2 animate-pulse rounded-full bg-brand-300" />
              </span>
            </li>
          )}
        </ol>
      )}
    </div>
  );
}

