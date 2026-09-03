import type { ArtifactPreview } from "../lib/api";
import { artifactTypeLabel } from "./artifactTypeLabel";

/** The one-line label for an artifact, chosen the same way the full modal chooses it.
 *
 * #66: the collapsed "Artifacts (N)" list needs to say what each artifact *is*
 * without opening the modal. That title is not new content — `ArtifactDialog`
 * and `ArtifactModal` already resolve exactly this: `preview.title` when present,
 * a named document, or a type-derived fallback. Sharing the rule here keeps the
 * preview card and the opened modal from ever disagreeing about an artifact's
 * name.
 */
export function artifactTitle(
  preview: Pick<ArtifactPreview, "artifact_type" | "title" | "documents">,
  language: "en" | "tr",
  translate: (text: string) => string,
): string {
  if (preview.title) return language === "tr" ? preview.title.tr : preview.title.en;
  if (preview.artifact_type === "document_extraction") {
    const named = preview.documents?.find((document) => document.title)?.title;
    return named ?? translate("Extracted document artifacts");
  }
  // Generic previews have no authored title, but the indexed type is still a
  // truthful, useful name. Falling back to "Artifact details" made every such
  // row and modal indistinguishable even after the backend exposed its type.
  return translate(artifactTypeLabel(preview.artifact_type));
}
