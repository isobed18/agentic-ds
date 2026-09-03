import { useEffect, useMemo, useRef, useState } from "react";

import { api, type ArtifactPreview } from "../lib/api";
import { useShowDiagnostics } from "../lib/diagnostics";
import { activeLanguage, t } from "../lib/i18n";
import { keepRevealed, nextToReveal, revealDelay } from "./artifactReveal";
import { artifactTitle } from "./artifactTitle";
import { cx } from "./ui";

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
 *
 * #368: "once each" was only true for as long as this component stayed mounted.
 * `ProjectWorkspace` renders each section as `{view === "data" && <…/>}`, so
 * switching tabs unmounts the subtree and takes both `requested` and `previews`
 * with it. Returning re-fetched every expanded artifact. The dedupe now lives
 * in the module-level cache in `api.ts`, which outlives the unmount; the ref
 * here still stops this instance from stacking duplicate in-flight calls within
 * a single render pass. Reading the cache synchronously at render is what
 * removes the second symptom — a title already fetched this session renders on
 * the first frame back rather than passing through "Loading…" again while the
 * cached promise resolves.
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
    const preview = previews[id] ?? api.cachedArtifactPreview(id);
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
export function ArtifactNodes({ ids, activeId = null, onOpen, diagnosticIds }: { ids: string[]; activeId?: string | null; onOpen: (id: string) => void;
  /** Which of `ids` are diagnostics.
   *
   * #424: this used to mark them only, and each caller was left to filter its
   * own list -- which exactly one of them did. Every artifact list on a canvas
   * comes through here, so the filter belongs here too: hand this component
   * the run's diagnostic ids and the node respects the toggle by construction.
   * Omit it on a list with no run behind it and nothing is hidden or marked,
   * which is the old behaviour. */
  diagnosticIds?: ReadonlySet<string>;
}) {
  const showDiagnostics = useShowDiagnostics();
  // #408: "Show diagnostics" changed only which ids this list *would* contain
  // if somebody expanded it -- on a node that is collapsed by default and may
  // be off screen -- so pressing it produced no visible change anywhere and
  // the control read as broken. A list that gains rows opens itself, and
  // closes again when they go away, unless the viewer has since taken the pill
  // over by clicking it; their own choice outranks the toolbar's.
  const visibleIds = useMemo(
    () => (showDiagnostics || !diagnosticIds ? ids : ids.filter((id) => !diagnosticIds.has(id))),
    [ids, diagnosticIds, showDiagnostics],
  );
  const revealed = showDiagnostics && ids.some((id) => diagnosticIds?.has(id) ?? false);
  const shown = useSequentialReveal(visibleIds);
  const [open, setOpen] = useState(false);
  // Whether `open` is the caller's doing or the viewer's. Set while `revealed`
  // drives it, cleared the moment the pill itself is pressed.
  const auto = useRef(false);
  useEffect(() => {
    if (revealed) { auto.current = true; setOpen(true); }
    else if (auto.current) { auto.current = false; setOpen(false); }
  }, [revealed]);
  const titles = useArtifactTitles(shown, open);
  if (!visibleIds.length) return null;
  return (
    // This column stays in normal flow. An absolute `top-full` list contributed
    // no height to the node, so a vertically stacked branch remained under it
    // and was covered as the collection expanded (#327).
    <div className="relative z-10 flex w-full flex-col items-center">
      {/* The straddle from #66 moves onto the pill, whose own height never
          changes, so it is a fixed offset rather than a share of the column. */}
      <div className="-translate-y-1/2">
        <button
          type="button"
          onClick={() => { auto.current = false; setOpen((current) => !current); }}
          aria-expanded={open}
          className="pointer-events-auto rounded-full border border-brand-300 bg-surface px-3 py-1 text-3xs font-semibold tabular-nums text-brand-700 shadow-card transition hover:-translate-y-0.5 hover:border-brand-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500"
        >
          {t("Artifacts ({count})", { count: shown.length })}
        </button>
      </div>
      {open && (
        <ol className="pointer-events-auto mt-2 flex w-full min-w-0 flex-col gap-2" aria-label={t("Artifacts")}>
          {shown.map((id, index) => {
            const active = id === activeId;
            // #408: the rows the toggle just added say so. Without this the
            // list simply got longer, which is not a visible answer to "what
            // did that button do".
            const diagnostic = diagnosticIds?.has(id) ?? false;
            return (
            <li key={id} className="flex min-w-0 items-center gap-2">
              {/* The numbered circle straddles the dashed line down the list,
                  the way the opener straddles the node edge above it. */}
              <span className="relative grid h-7 w-7 shrink-0 place-items-center rounded-full border border-brand-300 bg-brand-50 text-3xs font-semibold tabular-nums text-brand-700">
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
                aria-current={active ? "true" : undefined}
                className={cx("artifact-node min-w-0 max-w-[calc(100%-2.25rem)] truncate rounded-lg border bg-surface px-2.5 py-1.5 text-left text-3xs text-ink-soft shadow-card transition hover:border-brand-400 hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500", diagnostic ? "border-dashed border-slate-300" : "border-line", active && "ring-2 ring-brand-400")}
              >
                {diagnostic && <span className="mr-1.5 rounded bg-surface-sunken px-1 py-0.5 text-4xs font-semibold uppercase tracking-wide text-ink-faint">{t("Diagnostic")}</span>}
                {titles[id]}
              </button>
            </li>
            );
          })}
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

