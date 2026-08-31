import type { ArtifactPreview } from "../lib/api";
import { t } from "../lib/i18n";

function fieldLabel(key: string): string {
  const words = key.replaceAll("_", " ");
  return words.charAt(0).toLocaleUpperCase() + words.slice(1);
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
  const fields = Object.entries(preview.fields ?? {});
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
