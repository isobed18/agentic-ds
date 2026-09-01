import { describe, expect, it } from "vitest";

import SOURCE from "./Catalog.tsx?raw";
import CATALOGUE from "../lib/i18n.ts?raw";

const HOME = SOURCE.slice(SOURCE.indexOf("export function Home"));

describe("the minimal home page (#301)", () => {
  it("welcomes the reader and sends detailed work to Projects", () => {
    expect(HOME).toContain('t("Welcome to Agentic DS")');
    expect(HOME).toContain('t("Your data-science work starts inside a project.")');
    expect(HOME).toContain('<Link to="/projects" className="btn-primary');
    expect(HOME).toContain('t("Open Projects")');
    expect(HOME.match(/btn-primary/g)).toHaveLength(1);
  });

  it("is an orientation page, not another project catalogue", () => {
    expect(HOME).not.toContain("api.home(");
    expect(HOME).not.toContain("Search projects and outputs");
    expect(HOME).not.toContain("Recently produced");
    expect(HOME).toContain("bg-gradient-to-br");
  });

  it("ships Turkish copy for every new visible string", () => {
    for (const key of [
      "Welcome to Agentic DS",
      "Your data-science work starts inside a project.",
      "Keep data, automations, models, and reports together. Open Projects to create or continue your work.",
      "Open Projects",
      "Local by design",
      "Measured before modeled",
      "Decisions stay reviewable",
    ]) {
      expect(CATALOGUE).toContain(`"${key}":`);
    }
  });
});
