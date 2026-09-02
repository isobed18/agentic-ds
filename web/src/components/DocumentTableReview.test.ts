/**
 * The extracted-table review, and the blind click it used to require (#303).
 *
 * The old dialog listed a title and a page number and defaulted every candidate
 * to rejected. A person could neither see what a candidate table held nor was
 * asked to decide on it -- closing the dialog silently rejected everything. The
 * review now shows the detected headers over a sample of the rows, so a person
 * decides against evidence. #360 then replaced the Accept/Reject pair with one
 * checkbox per candidate, and #359 stopped listing candidates the extractor
 * failed on at all.
 *
 * Pinned as source shape rather than a rendered dialog; the component reaches
 * the API and a real extraction, which a static test cannot stand up.
 */
import { describe, expect, it } from "vitest";

import SOURCE from "./DocumentTableReview.tsx?raw";
import CATALOGUE from "../lib/i18n.ts?raw";

describe("the extracted-table review (#303)", () => {
  it("shows the detected headers and a bounded sample of rows", () => {
    // The evidence the decision is made against: columns as a header row and
    // the sampled rows the preview carries, in a real table element.
    expect(SOURCE).toContain("sampleRows: Array.isArray(table.sample_rows)");
    expect(SOURCE).toContain("candidate.columns.map((column");
    expect(SOURCE).toContain("candidate.sampleRows.map((row");
    expect(SOURCE).toContain("<table");
  });

  it("names the page and the table's shape so a mis-detection is visible", () => {
    expect(SOURCE).toContain('t("page {page}", { page: candidate.page })');
    expect(SOURCE).toContain('t("{rows} rows × {columns} columns"');
  });

  it("never promotes an unchecked candidate", () => {
    // The promote request sends each candidate's own decision; only the backend
    // turns accepted ones into tables, so a reject cannot become evidence.
    expect(SOURCE).toContain('decision: accepted.has(candidate.candidateId) ? "accepted" : "rejected"');
  });

  it("translates the review copy", () => {
    expect(CATALOGUE).toContain('"{rows} rows × {columns} columns": "{rows} satır × {columns} sütun"');
    expect(CATALOGUE).toContain('"Promote {count} accepted":');
  });
});

describe("failed extractions stay out of the list (#359)", () => {
  it("drops candidates the extractor produced no rows for", () => {
    // `row_count` is the length of the extracted rows, so zero is a failed
    // extraction. Such a candidate could never be promoted (#310) -- listing it
    // asked for a decision that had already been made.
    expect(SOURCE).toContain(
      ".filter((candidate) => candidate.rowCount > 0 || candidate.sampleRows.length > 0)",
    );
  });

  it("no longer needs the un-acceptable-candidate warning", () => {
    // Nothing reaches the list that the badge could describe.
    expect(SOURCE).not.toContain("const empty = candidate.rowCount === 0");
    expect(SOURCE).not.toContain('t("No data extracted — cannot be accepted")');
  });
});

describe("checkbox selection (#360, replacing #317)", () => {
  it("gives each candidate one checkbox instead of an accept/reject pair", () => {
    // Checked is accepted and unchecked is rejected, so there is no third
    // state for "Select all" to fight with.
    expect(SOURCE).toContain("const [accepted, setAccepted] = useState<Set<string>>(new Set())");
    expect(SOURCE).toContain("function setAcceptance(candidateId: string, checked: boolean)");
    expect(SOURCE).toContain("setAcceptance(candidate.candidateId, event.target.checked)");
    // The old three-state verdict buttons are gone.
    expect(SOURCE).not.toContain('t("Accept")}</button>');
    expect(SOURCE).not.toContain('t("Reject")}</button>');
  });

  it("drives the header checkbox through the standard tri-state", () => {
    expect(SOURCE).toContain("const allAccepted = candidates.length > 0 && acceptedCount === candidates.length");
    expect(SOURCE).toContain("const someAccepted = acceptedCount > 0 && !allAccepted");
    // `indeterminate` has no HTML attribute; it is set on the node itself.
    expect(SOURCE).toContain("node.indeterminate = someAccepted");
    expect(SOURCE).toContain("function toggleAllAccepted()");
  });

  it("gates promote on one checked table rather than on deciding every row", () => {
    expect(SOURCE).toContain("if (busy || acceptedCount === 0) return");
    expect(SOURCE).toContain("disabled={busy || acceptedCount === 0}");
    // The "decide on every table first" gate goes with the third state.
    expect(SOURCE).not.toContain("allDecided");
    expect(CATALOGUE).not.toContain('"Decide on every table first');
  });

  it("keeps the selection copy translated", () => {
    expect(CATALOGUE).toContain('"Select all": "Tümünü seç"');
    expect(CATALOGUE).toContain('"Unselect all": "Tümünün seçimini kaldır"');
    expect(CATALOGUE).toContain('"Accept all {count} tables":');
    expect(CATALOGUE).toContain('"Unchecked tables are recorded as rejected.":');
    expect(CATALOGUE).toContain('"Check at least one table to promote.":');
  });
});
