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
