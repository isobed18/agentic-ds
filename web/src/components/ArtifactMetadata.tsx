import type { ArtifactPreview } from "../lib/api";
import { activeLanguage, t, type Language } from "../lib/i18n";

type FieldValue = string | number | boolean;

function fieldLabel(key: string): string {
  const words = key.replaceAll("_", " ");
  return words.charAt(0).toLocaleUpperCase() + words.slice(1);
}

function isBlank(value: FieldValue | undefined): boolean {
  return value === undefined || (typeof value === "string" && value.trim() === "");
}

/**
 * Collapse `foo` / `foo_tr` pairs down to the one the reader can actually read.
 *
 * The generic preview fallback in `artifact_preview()` copies every scalar in
 * the payload into `fields`, and several contracts store both languages as
 * sibling keys -- `EvaluationReport` carries `problem_title` next to
 * `problem_title_tr`. Rendering the dict as it arrives therefore showed a
 * Turkish reader the same problem title and description twice, once in each
 * language, as two separate cards (#290).
 *
 * The Turkish half is never a label of its own: it fills its English sibling's
 * slot, or -- when there is no sibling -- appears under the base name. A blank
 * `_tr` counts as absent, because the contract defaults it to `""` when the
 * upstream author produced no Turkish variant, and an empty card is worse than
 * English prose.
 */
export function localizedFields(
  fields: Record<string, FieldValue>,
  language: Language,
): [string, FieldValue][] {
  const entries: [string, FieldValue][] = [];
  for (const [key, value] of Object.entries(fields)) {
    const base = key.endsWith("_tr") ? key.slice(0, -"_tr".length) : "";
    if (base) {
      if (base in fields || isBlank(value)) continue;
      entries.push([base, value]);
      continue;
    }
    const turkish = fields[`${key}_tr`];
    entries.push([key, language === "tr" && !isBlank(turkish) ? (turkish as FieldValue) : value]);
  }
  return entries;
}

export function hasArtifactMetadata(preview: ArtifactPreview): boolean {
  return Boolean(
    Object.keys(preview.fields ?? {}).length || Object.keys(preview.collection_sizes ?? {}).length,
  );
}

/** Render the privacy-safe scalar values and collection counts from the generic
 * preview contract. The API has always returned these for unrecognised artifact
 * types, but both dialogs ignored them and therefore opened an empty card (#164).
 */
export function ArtifactMetadata({ preview }: { preview: ArtifactPreview }) {
  const fields = localizedFields(preview.fields ?? {}, activeLanguage());
  const collections = Object.entries(preview.collection_sizes ?? {});
  if (!fields.length && !collections.length) return null;

  return (
    <div className="mt-4 space-y-4" data-artifact-metadata>
      {fields.length > 0 && (
        <section>
          <p className="text-[10px] font-semibold uppercase tracking-wide text-ink-faint">{t("Fields")}</p>
          <dl className="mt-2 grid gap-2 sm:grid-cols-2">
            {fields.map(([key, value]) => (
              <div key={key} className="min-w-0 rounded-lg bg-surface-sunken px-3 py-2">
                <dt className="text-[9px] uppercase tracking-wide text-ink-faint">{fieldLabel(key)}</dt>
                <dd className="mt-1 break-words text-xs font-semibold text-ink">{String(value)}</dd>
              </div>
            ))}
          </dl>
        </section>
      )}
      {collections.length > 0 && (
        <section>
          <p className="text-[10px] font-semibold uppercase tracking-wide text-ink-faint">{t("Collection counts")}</p>
          <dl className="mt-2 grid gap-2 sm:grid-cols-2">
            {collections.map(([key, count]) => (
              <div key={key} className="flex min-w-0 items-center justify-between gap-3 rounded-lg border border-line px-3 py-2">
                <dt className="truncate text-xs font-medium text-ink-soft">{fieldLabel(key)}</dt>
                <dd className="shrink-0 text-[10px] font-semibold tabular-nums text-brand-700">{t("{count} items", { count })}</dd>
              </div>
            ))}
          </dl>
        </section>
      )}
    </div>
  );
}
