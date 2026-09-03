/**
 * Whether the engineering/provenance artifacts are on screen, for every
 * surface that lists artifacts.
 *
 * #305 introduced the idea -- agent audits, measurement bundles, internal
 * trials and attempt diagnostics are records of how the run worked, not the
 * data, model or report a person came for -- and the backend already owns the
 * closed set (`is_diagnostic_artifact`, surfaced as `diagnostic` on the
 * artifact index and as `diagnostic_artifact_ids` on the progress snapshot).
 *
 * What #305 did not give it was a home. `showDiagnostics` was `useState`
 * inside `GuidedPipeline`, so exactly one of the app's artifact lists could
 * see it: the six ML group cards. The stage inspector, the staging graph, the
 * advanced editor and the attempt counts went on listing diagnostics whatever
 * the toggle said -- and the stage inspector is the panel that opens when you
 * click the very node whose chips were just filtered, so the same run showed
 * two different artifact lists a click apart. Pressing the control appeared to
 * do nothing, which is what made it read as broken (#424).
 *
 * It is a property of the view, not of one component, so it lives in one
 * module every surface can read. A store rather than a context: the surfaces
 * that need it are on three different pages with no common ancestor short of
 * the app root, and threading a provider through them to carry one boolean
 * would be the same coupling with more moving parts.
 */
import { useEffect, useState, useSyncExternalStore } from "react";

import { api } from "./api";

const STORAGE_KEY = "ads.diagnostics.visible";

/** `sessionStorage`, like the panel width: revealing diagnostics is something
 *  a person does while looking into one thing, and defaulting a later visit
 *  back to the clean view is the behaviour #305 asked for. Every access is
 *  guarded -- a private window can throw on the getter itself. */
function readStored(): boolean {
  try {
    return globalThis.sessionStorage?.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

let visible = readStored();
const listeners = new Set<() => void>();

function snapshot(): boolean {
  return visible;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Show or hide the diagnostics everywhere at once. */
export function setShowDiagnostics(next: boolean): void {
  if (next === visible) return;
  visible = next;
  try {
    globalThis.sessionStorage?.setItem(STORAGE_KEY, next ? "1" : "0");
  } catch {
    // A viewer who blocks site data still gets the toggle; they just start
    // from the default again next time.
  }
  for (const listener of [...listeners]) listener();
}

/** The current preference, for the surfaces that render artifact lists. */
export function useShowDiagnostics(): boolean {
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}

const EMPTY: ReadonlySet<string> = new Set();

/** The diagnostic ids in a progress snapshot.
 *
 * The backend decides what counts; a client that re-derived it from artifact
 * kinds would be a second copy of that rule, free to disagree with the first.
 * Callers already polling progress use this; `useDiagnosticIds` is for the
 * ones that are not.
 */
export function diagnosticIdsOf(
  progress: { diagnostic_artifact_ids?: string[] } | null | undefined,
): ReadonlySet<string> {
  const ids = progress?.diagnostic_artifact_ids;
  return ids?.length ? new Set(ids) : EMPTY;
}

/** The run's diagnostic ids for a surface that does not already poll progress.
 *
 * One request, on mount, per run. Unlike the live canvases this serves screens
 * whose artifact lists are read rather than watched, so a snapshot is enough
 * and a second poller would be noise.
 */
export function useDiagnosticIds(runId: string | null | undefined): ReadonlySet<string> {
  const [ids, setIds] = useState<ReadonlySet<string>>(EMPTY);
  useEffect(() => {
    if (!runId) {
      setIds(EMPTY);
      return;
    }
    let cancelled = false;
    void api
      .runProgress(runId)
      .then((progress) => {
        if (!cancelled) setIds(diagnosticIdsOf(progress));
      })
      .catch(() => {
        // A run with no progress snapshot has no diagnostics to hide, and a
        // failed lookup must not take an artifact list down with it.
      });
    return () => {
      cancelled = true;
    };
  }, [runId]);
  return ids;
}

/** `items` with the diagnostics dropped, unless the viewer asked to see them.
 *
 * `isDiagnostic` rather than a set of ids so a list whose entries already
 * carry the backend's `diagnostic` flag -- `StageDetail.outputs` does -- can
 * use the same filter as one that only has ids.
 */
export function withoutDiagnostics<T>(
  items: readonly T[],
  isDiagnostic: (item: T) => boolean,
  show: boolean,
): readonly T[] {
  if (show) return items;
  return items.filter((item) => !isDiagnostic(item));
}
