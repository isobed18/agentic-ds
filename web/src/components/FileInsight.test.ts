import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { FileInsight } from "./FileInsight";
import { activeLanguage, t } from "../lib/i18n";
import INTAKE_SOURCE from "./UnderstandingWorkspace.tsx?raw";
import DATA_SOURCE from "../pages/ProjectWorkspace.tsx?raw";

describe("per-file profile insights (#329)", () => {
  it("renders the concise row-free summary", () => {
    const markup = renderToStaticMarkup(createElement(FileInsight, {
      insight: {
        en: "1 table · 42 rows · keyed table · key candidate: customer_id · 2 quality notes",
        tr: "1 tablo · 42 satır · anahtarlı tablo · anahtar adayı: customer_id · 2 kalite notu",
      },
    }));

    expect(markup).toContain(t("File insight"));
    expect(markup).toContain(activeLanguage() === "tr" ? "42 satır" : "42 rows");
    expect(markup).toContain("customer_id");
  });

  it("mounts the same evidence line in both Data and Intake file cards", () => {
    expect(DATA_SOURCE).toContain("<FileInsight insight={summary?.insight}");
    expect(INTAKE_SOURCE).toContain("<FileInsight insight={file.insight}");
  });
});
