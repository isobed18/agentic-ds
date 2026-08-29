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
});
