/**
 * The extracted-table review, and the blind click it used to require (#303).
 *
 * The old dialog listed a title and a page number and defaulted every candidate
 * to rejected. A person could neither see what a candidate table held nor was
 * asked to decide on it -- closing the dialog silently rejected everything. The
 * review now shows the detected headers over a sample of the rows, so a person
 * decides against evidence. #360 then replaced the Accept/Reject pair with one
 * checkbox per candidate, because the pair's three states and the two that
 * "Select all" assumes could not compose.
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
    // The promote request still sends every candidate's own decision, so the
    // review artifact records what was considered rather than only approvals.
    expect(SOURCE).toContain('decision: accepted.has(candidate.candidateId) ? "accepted" : "rejected"');
  });

  it("still flags a candidate with no rows and refuses to accept it (#310)", () => {
    // A candidate carrying headers and no rows cannot be promoted, so its box
    // is not checkable and the warning stays on the card.
    expect(SOURCE).toContain("const empty = candidate.rowCount === 0");
    expect(SOURCE).toContain('t("No data extracted — cannot be accepted")');
    expect(SOURCE).toContain("disabled={empty}");
  });

  it("translates the review copy", () => {
    expect(CATALOGUE).toContain('"{rows} rows × {columns} columns": "{rows} satır × {columns} sütun"');
    expect(CATALOGUE).toContain('"No data extracted — cannot be accepted": "Veri çıkarılamadı — kabul edilemez"');
  });
});

/**
 * A failed extraction is not a decision waiting to be made (#359).
 *
 * A candidate the extractor produced neither rows nor a preview sample for is
 * dropped from the list entirely, rather than shown disabled (#310 still
 * covers the narrower case of a candidate with a preview sample but no
 * reported row count).
 */
describe("candidates that failed extraction (#359)", () => {
  it("drops candidates the extractor produced no rows for", () => {
    // `row_count` is the length of the extracted rows, so zero is a failure
    // rather than a small table.
    expect(SOURCE).toContain(
      ".filter((candidate) => candidate.rowCount > 0 || candidate.sampleRows.length > 0)",
    );
  });

  it("still shows a candidate whose preview carried rows but no count", () => {
    // The filter accepts either signal, so a preview that sampled rows without
    // reporting a count is not thrown away with the genuine failures.
    expect(SOURCE).toContain("candidate.sampleRows.length > 0)");
  });
});

/**
 * One checkbox per candidate, replacing the Accept/Reject pair (#360, over #317).
 *
 * The pair carried three states -- accepted, rejected, undecided -- while
 * "Select all" assumed two. The reviewer's ordinary case (accept most, reject
 * a couple) could be expressed once and then not corrected: pressing "Select
 * all" again cleared every decision rather than the accepted ones, and Promote
 * stayed gated behind ruling on every row.
 */
describe("checkbox selection (#360)", () => {
  it("gives each candidate one checkbox instead of a verdict pair", () => {
    expect(SOURCE).toContain("const [accepted, setAccepted] = useState<Set<string>>(new Set())");
    expect(SOURCE).toContain("function setAcceptance(candidateId: string, checked: boolean)");
    expect(SOURCE).toContain("setAcceptance(candidate.candidateId, event.target.checked)");
    // The verdict buttons are gone, and with them the toggle that cleared a
    // decision back to "undecided".
    expect(SOURCE).not.toContain("function decide(");
  });

  it("drives the header checkbox through the standard tri-state", () => {
    expect(SOURCE).toContain("const allAccepted = selectable.length > 0 && acceptedCount === selectable.length");
    expect(SOURCE).toContain("const someAccepted = acceptedCount > 0 && !allAccepted");
    // `indeterminate` has no HTML attribute; it is set on the node itself.
    expect(SOURCE).toContain("node.indeterminate = someAccepted");
  });

  it("counts only promotable candidates towards 'all'", () => {
    // Otherwise one failed extraction (#310) puts "all checked" out of reach
    // and the header can never leave indeterminate.
    expect(SOURCE).toContain("const selectable = useMemo(() => candidates.filter((candidate) => candidate.rowCount > 0)");
    expect(SOURCE).toContain("new Set(selectable.map((candidate) => candidate.candidateId))");
  });

  it("gates promote on one checked table rather than on deciding every row", () => {
    expect(SOURCE).toContain("if (busy || acceptedCount === 0) return");
    expect(SOURCE).toContain("disabled={busy || acceptedCount === 0}");
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
