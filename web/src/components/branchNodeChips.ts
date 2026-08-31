/**
 * How many file names a branch node shows at the width it currently has (#186).
 *
 * The node is resizable, but its chip row was a hard `slice(0, 3)`: widening it
 * gave the row plenty of space and still showed three names and a "+N" that
 * revealed nothing. The chip is not clickable either, so an expanded node knew
 * no more than a collapsed one.
 *
 * The count is estimated from the text rather than measured. Measuring would
 * mean laying the chips out, reading them back and re-rendering, which is a
 * lot of machinery for a summary chip row -- and the row wraps, so an estimate
 * that is slightly generous costs a second line rather than clipped text.
 */

/** Matches `max-w-[120px]` on the chip. */
const CHIP_MAX = 120;
/** `px-2` either side. */
const CHIP_PADDING = 16;
/** `gap-1` between chips. */
const CHIP_GAP = 4;
/** `p-4` either side of the card. */
const CARD_PADDING = 32;
/** Average glyph advance at the chip's 9px medium text. */
const CHAR = 4.6;

/** Today's cap, kept as a floor so a default-width node never shows less. */
export const MIN_CHIPS = 3;

function chipWidth(text: string): number {
  return Math.min(CHIP_MAX, CHIP_PADDING + text.length * CHAR);
}

/**
 * The number of file chips to render before the "+N" chip.
 *
 * Never more than there are files, and never fewer than {@link MIN_CHIPS},
 * so a node at its default width keeps the layout it has today and only ever
 * gains names as it grows.
 */
export function visibleFileChips(names: string[], nodeWidth: number): number {
  if (names.length <= MIN_CHIPS) return names.length;

  const room = nodeWidth - CARD_PADDING;
  let used = 0;
  let fit = 0;
  for (const name of names) {
    const next = used + (fit ? CHIP_GAP : 0) + chipWidth(name);
    if (next > room) break;
    used = next;
    fit += 1;
  }

  // The "+N" chip needs a place too, or it is what wraps to the second line.
  while (fit > MIN_CHIPS && fit < names.length) {
    const more = chipWidth(`+${names.length - fit}`);
    if (used + CHIP_GAP + more <= room) break;
    fit -= 1;
    used -= chipWidth(names[fit]) + (fit ? CHIP_GAP : 0);
  }

  return Math.min(names.length, Math.max(MIN_CHIPS, fit));
}
