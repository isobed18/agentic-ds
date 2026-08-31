import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { type AutomationDefinition } from "../lib/api";
import { t } from "../lib/i18n";
import { AutomationDeleteDialog } from "./ProjectWorkspace";

const automation: AutomationDefinition = {
  schema_version: "1",
  automation_id: "automation-fed654cba321",
  name: "Churn baseline",
  status: "saved",
  revision: 4,
  pipeline_layout: { version: "1", nodes: [], collapsed_branches: [] },
  execution_ids: ["run-1", "run-2"],
  created_at: "2026-01-01T00:00:00+00:00",
  updated_at: "2026-01-02T00:00:00+00:00",
};

describe("automation deletion (#185)", () => {
  it("names the automation and says what the deletion keeps", () => {
    const markup = renderToStaticMarkup(
      <AutomationDeleteDialog automation={automation} busy={false} onCancel={() => undefined} onConfirm={() => undefined} />,
    );

    expect(markup).toContain('role="dialog"');
    expect(markup).toContain("Churn baseline");
    expect(markup).toContain(t("This removes the automation, its graph, and its data selection."));
    expect(markup).toContain(t("Its execution history and the project data are kept."));
    expect(markup).toContain(t("Delete automation"));
    expect(markup).toContain(t("Cancel"));
  });

  it("locks both answers while the deletion is in flight", () => {
    const markup = renderToStaticMarkup(
      <AutomationDeleteDialog automation={automation} busy onCancel={() => undefined} onConfirm={() => undefined} />,
    );

    expect(markup).toContain(t("Deleting…"));
    expect(markup.match(/disabled=""/g)?.length).toBe(2);
  });
});
