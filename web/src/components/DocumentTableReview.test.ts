/**
 * The extracted-table review, and the blind click it used to require (#303).
 *
 * The old dialog listed a title and a page number and defaulted every candidate
 * to rejected. A person could neither see what a candidate table held nor was
 * asked to decide on it -- closing the dialog silently rejected everything. The
 * review now shows the detected headers over a sample of the rows, and requires
 * an explicit Accept or Reject on every candidate before anything is promoted.
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

  it("requires an explicit accept or reject on every candidate", () => {
    // Three states, not a checkbox: undecided is its own state, and promote is
    // blocked until none are left, so nothing is promoted by omission.
    expect(SOURCE).toContain('decide(candidate.candidateId, "accepted")');
    expect(SOURCE).toContain('decide(candidate.candidateId, "rejected")');
    expect(SOURCE).toContain("const allDecided = candidates.length > 0 && undecided === 0");
    expect(SOURCE).toContain("disabled={busy || !allDecided}");
    // No implicit-reject checkbox survives from the old dialog.
    expect(SOURCE).not.toContain('type="checkbox"');
  });

  it("never promotes a rejected candidate", () => {
    // The promote request sends each candidate's own decision; only the backend
    // turns accepted ones into tables, so a reject cannot become evidence.
    expect(SOURCE).toContain('decisions.get(candidate.candidateId) ?? "rejected"');
  });

  it("translates the new review copy", () => {
    expect(CATALOGUE).toContain('"Accept": "Kabul et"');
    expect(CATALOGUE).toContain('"Reject": "Reddet"');
    expect(CATALOGUE).toContain('"{rows} rows × {columns} columns": "{rows} satır × {columns} sütun"');
    expect(CATALOGUE).toContain('"Decide on every table first ({count} left).":');
  });
});

describe("bulk candidate selection (#317)", () => {
  it("accepts every candidate at once and clears the bulk choice when repeated", () => {
    expect(SOURCE).toContain("const allAccepted = candidates.length > 0 && candidates.every");
    expect(SOURCE).toContain("function toggleAllAccepted()");
    expect(SOURCE).toContain('next.set(candidate.candidateId, "accepted")');
    expect(SOURCE).toContain("next.delete(candidate.candidateId)");
    expect(SOURCE).toContain('allAccepted ? t("Unselect all") : t("Select all")');
  });

  it("keeps both bulk-action labels translated", () => {
    expect(CATALOGUE).toContain('"Select all": "Tümünü seç"');
    expect(CATALOGUE).toContain('"Unselect all": "Tümünün seçimini kaldır"');
  });
});
