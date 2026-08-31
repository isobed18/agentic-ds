import { describe, expect, it } from "vitest";

import { artifactTitle } from "./artifactTitle";

const t = (text: string) => `t:${text}`;

describe("resolving an artifact's one-line title", () => {
  it("prefers the preview's own bilingual title, in the active language", () => {
    const preview = { artifact_type: "staging_report", title: { en: "Inventory Report", tr: "Envanter Raporu" } };
    expect(artifactTitle(preview, "tr", t)).toBe("Envanter Raporu");
    expect(artifactTitle(preview, "en", t)).toBe("Inventory Report");
  });

  it("names a document extraction by its first titled document", () => {
    const preview = {
      artifact_type: "document_extraction",
      documents: [
        { source_file: "a.pdf", title: null, page_count: 1, text_characters: 0, tables: [], figures: [], warnings: [] },
        { source_file: "b.pdf", title: "Q3 Brief", page_count: 2, text_characters: 0, tables: [], figures: [], warnings: [] },
      ],
    };
    expect(artifactTitle(preview, "en", t)).toBe("Q3 Brief");
  });

  it("falls back to the document label when no document is titled", () => {
    const preview = {
      artifact_type: "document_extraction",
      documents: [
        { source_file: "a.pdf", title: null, page_count: 1, text_characters: 0, tables: [], figures: [], warnings: [] },
      ],
    };
    expect(artifactTitle(preview, "en", t)).toBe("t:Extracted document artifacts");
  });

  it("uses the real type for any other untitled artifact", () => {
    expect(artifactTitle({ artifact_type: "data_card" }, "en", t)).toBe("t:Data card");
  });
});
