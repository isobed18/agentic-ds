import { useEffect, useState } from "react";

import { t } from "../lib/i18n";
import { keepRevealed, nextToReveal, revealDelay } from "./artifactReveal";

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

/** The artifacts a stage produced, as round nodes hanging off it.
 *
 * These were a single chip reading "3 artifacts" -- true, and unreadable at a
 * glance: the count told you something existed without telling you it had
 * arrived just now, and there was nothing to aim at. As nodes they are objects
 * in the graph, which is what they are.
 */
export function ArtifactNodes({ ids, onOpen }: { ids: string[]; onOpen: (id: string) => void }) {
  const shown = useSequentialReveal(ids);
  if (!ids.length) return null;
  return (
    <div className="pointer-events-none absolute -bottom-14 left-0 right-0 flex flex-col items-center">
      {/* Stem from the stage node down to its artifacts, so the connection is
          drawn rather than implied by proximity. */}
      <span className="h-3 w-px bg-line" aria-hidden="true" />
      <ul className="pointer-events-auto flex flex-wrap items-center justify-center gap-1.5" aria-label={t("Artifacts")}>
        {shown.map((id, index) => (
          <li key={id}>
            <button
              type="button"
              onClick={() => onOpen(id)}
              title={t("Artifact {number}", { number: index + 1 })}
              className="artifact-node grid h-8 w-8 place-items-center rounded-full border border-brand-300 bg-brand-50 text-[10px] font-semibold tabular-nums text-brand-700 shadow-card transition hover:-translate-y-0.5 hover:border-brand-500 hover:bg-brand-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500"
            >
              {index + 1}
            </button>
          </li>
        ))}
        {shown.length < ids.length && (
          <li aria-hidden="true">
            <span className="grid h-8 w-8 place-items-center rounded-full border border-dashed border-line text-[10px] text-ink-faint">
              <span className="h-2 w-2 animate-pulse rounded-full bg-brand-300" />
            </span>
          </li>
        )}
      </ul>
    </div>
  );
}

