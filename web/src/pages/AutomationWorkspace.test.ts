import { describe, expect, it } from "vitest";

import SOURCE from "./AutomationWorkspace.tsx?raw";

describe("the automation data controls", () => {
  it("keeps Add files beside the uploaded-data picker", () => {
    // The regression this exists for (#50). Language and save-state controls
    // used to separate the plus button from the select it adds files to.
    const toolbar = SOURCE.match(/<div className="ml-auto flex items-center gap-2">([\s\S]*?)<\/div>/)?.[1] ?? "";
    const picker = toolbar.indexOf("</select>");
    const addFiles = toolbar.indexOf('title={t("Add files")}');
    const language = toolbar.indexOf("<LanguagePicker");

    expect(picker).toBeGreaterThan(-1);
    expect(addFiles).toBeGreaterThan(picker);
    expect(language).toBeGreaterThan(addFiles);
  });

  it("surfaces the human gate escalation so a stuck run can be answered (#81)", () => {
    // The gate-answering UI (ApprovalCard) was only ever rendered from the
    // unrouted Workflows page, so an `awaiting_human` run sat stuck with no
    // reachable screen. AutomationWorkspace now mounts it, gated on the run's
    // pending gate question.
    expect(SOURCE).toContain("<ApprovalCard");
    expect(SOURCE).toContain("pendingQuestion");
    expect(SOURCE).toContain("pending_question");
    // It is gated on the run actually having a human prompt to answer.
    expect(SOURCE).toContain("pendingQuestion?.human_prompt");
  });

  it("no longer shows a header save indicator that only tracked two fields (#79)", () => {
    // The "Saved/Saving…/Unsaved changes" indicator read as a project-wide
    // save status but only tracked the name field and the advanced pipeline
    // graph -- and the advanced graph already has its own "Save workflow"
    // button (PipelineBuilder). It was removed; nothing here should reference
    // save state any more.
    expect(SOURCE).not.toContain("saveState");
    expect(SOURCE).not.toContain('t("Unsaved changes")');
    const toolbar = SOURCE.match(/<div className="ml-auto flex items-center gap-2">([\s\S]*?)<\/div>/)?.[1] ?? "";
    // LanguagePicker is now the last control in the toolbar.
    expect(toolbar).toContain("<LanguagePicker");
    expect(toolbar.indexOf("<span")).toBe(-1);
  });
});
