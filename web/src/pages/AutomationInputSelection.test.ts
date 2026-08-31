/**
 * The select-all toggle on the automation input picker (#184).
 *
 * `AutomationInputSelector` loads its files in an effect, so a static render
 * only ever produces the spinner. The rule worth pinning is the derived one --
 * whether the button offers "Select all" or "Unselect all" -- so it is tested
 * directly, and the rendering around it is pinned from source.
 */
import { describe, expect, it } from "vitest";

import { type AutomationInputFile } from "../lib/api";
import { t } from "../lib/i18n";
import SOURCE from "./ProjectWorkspace.tsx?raw";
import { everyFileSelected } from "./ProjectWorkspace";

const files: AutomationInputFile[] = [
  { source_id: "source-a", path: "orders.csv" },
  { source_id: "source-a", path: "returns.csv" },
  { source_id: "source-b", path: "orders.csv" },
];

function keys(...picked: AutomationInputFile[]): Set<string> {
  return new Set(picked.map((file) => `${file.source_id}\u0000${file.path}`));
}

describe("whether every listed file is selected (#184)", () => {
  it("is false with nothing selected", () => {
    expect(everyFileSelected(files, new Set())).toBe(false);
  });

  it("is false while one file is still unchecked", () => {
    expect(everyFileSelected(files, keys(files[0], files[1]))).toBe(false);
  });

  it("is true once every file is checked", () => {
    expect(everyFileSelected(files, keys(...files))).toBe(true);
  });

  it("separates two sources that share a file name", () => {
    // The same path under a different source is a different row, so selecting
    // one must not read as selecting both.
    expect(everyFileSelected(files, keys(files[0], files[1]))).toBe(false);
    expect(everyFileSelected([files[0]], keys(files[2]))).toBe(false);
  });

  it("ignores a stale key for a file the project no longer has", () => {
    // The set is seeded from the automation's saved selection, which can name a
    // file that has since left the project. It must not hold the toggle back.
    const stale = keys(...files, { source_id: "source-c", path: "gone.csv" });
    expect(everyFileSelected(files, stale)).toBe(true);
  });

  it("is false for an empty list, where every() would say true", () => {
    expect(everyFileSelected([], new Set())).toBe(false);
  });
});

describe("the toggle the picker renders", () => {
  it("offers both labels through the catalogue", () => {
    expect(SOURCE).toContain('{allSelected ? t("Unselect all") : t("Select all")}');
    expect(t("Select all")).toBe("Tümünü seç");
    expect(t("Unselect all")).toBe("Tümünün seçimini kaldır");
  });

  it("checks every listed file and clears the whole selection", () => {
    expect(SOURCE).toContain("setSelected(allSelected ? new Set() : new Set(files.map(keyOf)))");
  });

  it("sits above the file list", () => {
    expect(SOURCE.indexOf('t("Unselect all")')).toBeLessThan(SOURCE.indexOf("{files.map((file) =>"));
  });

  it("leaves the primary action disabled after unselecting everything", () => {
    expect(SOURCE).toContain('disabled={!selected.size || busy} onClick={() => void save()}');
  });
});
