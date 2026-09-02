/** What an artifact's collection count is actually counting.
 *
 * The generic preview fallback in `artifact_preview()` calls `len()` on every
 * list and dict in the payload, and the dialog formatted all of them with one
 * string -- `"{count} items"` / `"{count} öğe"`. So a feature-column count read
 * "42 öğe", which is true and useless: "öğe" is a generic word for "element"
 * and says nothing about what 42 counts (#297).
 *
 * The rule is the key's trailing noun, not the whole key. Payload field names
 * are consistent about it -- `feature_columns`, `dropped_columns`,
 * `leakage_suspect_columns`, `model_eligible_columns` and roughly twenty more
 * all end in `columns` -- so a suffix match covers the whole family, and a new
 * contract field gets the right unit with no change here. Anything that is not
 * on this list keeps "items": a rule list or a check list really is a list of
 * opaque items, and inventing a unit for one would be worse than the generic
 * word.
 *
 * The English side of each entry is deliberately not pluralised beyond the
 * bare noun, matching the existing `"{count} items"`.
 */

/** Ordered because the first match wins; every pattern anchors to the key's end. */
const UNITS: [RegExp, string][] = [
  [/(?:^|_)columns$/, "{count} columns"],
  [/(?:^|_)rows$/, "{count} rows"],
  [/(?:^|_)tables$/, "{count} tables"],
  [/(?:^|_)files$/, "{count} files"],
  [/(?:^|_)documents$/, "{count} documents"],
  [/(?:^|_)pages$/, "{count} pages"],
  [/(?:^|_)warnings$/, "{count} warnings"],
  [/(?:^|_)findings$/, "{count} findings"],
  [/(?:^|_)candidates$/, "{count} candidates"],
  [/(?:^|_)metrics$/, "{count} metrics"],
];

/** Every phrase {@link countUnit} can return, for the catalogue test.
 *
 * These reach `t()` through a variable, so the literal scanner that guards the
 * Turkish catalogue cannot see them.
 */
export const COUNT_UNITS: string[] = [...UNITS.map(([, phrase]) => phrase), "{count} items"];

/** The i18n key for one collection's count. */
export function countUnit(key: string): string {
  return UNITS.find(([pattern]) => pattern.test(key))?.[1] ?? "{count} items";
}
