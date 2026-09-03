/** Dismissal bookkeeping for the notifications panel.
 *
 * Notification items are derived from the live run list on every poll, so there
 * is nothing on the server to delete: an item the user cleared comes straight
 * back on the next fetch unless the browser remembers it was cleared. That
 * memory is what this file is.
 *
 * Kept out of the component because the interesting behaviour is *across
 * polls*, which a rendered snapshot cannot show.
 */

export interface Dismissable {
  id: string;
}

/** The items still worth showing. */
export function visibleItems<T extends Dismissable>(items: T[], dismissed: Set<string>): T[] {
  return items.filter((item) => !dismissed.has(item.id));
}

/** The dismissed set after clearing everything currently on screen.
 *
 * Only the visible ids are added. Clearing the panel must not pre-dismiss an
 * item that has not been shown yet -- a question raised between the click and
 * the next poll is exactly the notification the user needs.
 */
export function dismissAll(dismissed: Set<string>, items: Dismissable[]): Set<string> {
  return new Set([...dismissed, ...items.map((item) => item.id)]);
}

/** The dismissed set after clearing one item. */
export function dismissOne(dismissed: Set<string>, id: string): Set<string> {
  return new Set([...dismissed, id]);
}

/** Drop remembered ids the run list no longer produces.
 *
 * Without this the stored set grows for the life of the browser profile, one
 * entry per run ever finished. An id that is gone from the feed cannot come
 * back -- ids are derived from run id, stage and attempt -- so forgetting it is
 * safe, and it keeps the key bounded by the number of live runs.
 */
export function pruneDismissed(dismissed: Set<string>, items: Dismissable[]): Set<string> {
  const live = new Set(items.map((item) => item.id));
  const kept = [...dismissed].filter((id) => live.has(id));
  // Same contents means the caller can keep its existing set and skip both a
  // render and a localStorage write on every poll.
  return kept.length === dismissed.size ? dismissed : new Set(kept);
}
