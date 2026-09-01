import { describe, expect, it } from "vitest";

import SOURCE from "./ProjectWorkspace.tsx?raw";

describe("project dataset cards (#312)", () => {
  it("keeps long dataset names inside the card without hiding the file count", () => {
    const heading = SOURCE.match(/<h2[^>]*>\{source\.label\}<\/h2>/)?.[0] ?? "";

    expect(heading).toContain("min-w-0");
    expect(heading).toContain("flex-1");
    expect(heading).toContain("truncate");
    expect(heading).toContain("title={source.label}");
    expect(SOURCE).toMatch(/\{source\.label\}<\/h2><Metric label=\{t\("Files"\)\}/);
  });
});
