/** Ordering rules for artifacts appearing one at a time on a live run.
 *
 * Kept out of the component so the behaviour can be tested directly: the whole
 * point is *ordering over time*, which is exactly what is invisible in a
 * rendered snapshot and easy to break by "simplifying" the hook later.
 */

/** Drop ids the run no longer reports, without reordering what is on screen.
 *
 * A retry discards artifacts, so the reported set can shrink. What must never
 * happen is a reorder: the sequence the viewer watched arrive is a record of
 * when work happened, and re-sorting it would make earlier work appear later.
 */
export function keepRevealed(current: string[], reported: string[]): string[] {
  const live = new Set(reported);
  const kept = current.filter((id) => live.has(id));
  // Same contents means the caller can keep its existing array and skip a
  // render; a fresh array every poll would re-run the reveal timer forever.
  return kept.length === current.length ? current : kept;
}

/** The next id to reveal, or undefined when everything reported is shown. */
export function nextToReveal(shown: string[], reported: string[]): string | undefined {
  const seen = new Set(shown);
  return reported.find((id) => !seen.has(id));
}

/** How long to wait before revealing `next`.
 *
 * The first artifact of a fresh view appears immediately -- somebody opening a
 * finished run should not watch a staggered replay of work that ended hours
 * ago. Every subsequent one is spaced, which is what makes a live run legible
 * as progress rather than as a page refresh.
 */
export function revealDelay(shownCount: number, intervalMs: number): number {
  return shownCount === 0 ? 0 : intervalMs;
}

/** The reported ids with repeats removed, first occurrence winning.
 *
 * #446: artifact ids are content-addressed, so a stage that is retried and
 * produces identical content produces the *same id* -- and several id lists
 * flatten across attempts or stages without a `set()`. A duplicate could never
 * be revealed (`nextToReveal` skips anything already shown, and the hook's own
 * `includes` guard would refuse it), so `shown.length` could never reach
 * `ids.length`, and the pending placeholder rendered on every frame with no
 * timer left to schedule. Not slow -- stopped.
 *
 * Deduping at every caller was the other half of the fix and is also done, but
 * the reveal has to be unable to stall on whatever it is handed: a rendering
 * artifact that outlives its data is worse than a duplicate row.
 */
export function uniqueIds(ids: string[]): string[] {
  return ids.length === new Set(ids).size ? ids : [...new Set(ids)];
}

/** Whether more artifacts are still waiting to be revealed.
 *
 * The component compared `shown.length` against the raw `ids` it was given.
 * Besides the duplicate stall, that was wrong for a second reason after #431:
 * `ids` is the unfiltered list, and diagnostics are filtered out of what is
 * actually revealed -- so with diagnostics hidden the placeholder pulsed
 * forever on any node holding one.
 */
export function revealPending(shown: string[], reported: string[]): boolean {
  return nextToReveal(shown, reported) !== undefined;
}
