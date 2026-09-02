import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { ModelSummary } from "../lib/api";
import { ModelComparison } from "./ProjectContents";
import SOURCE from "./GuidedPipeline.tsx?raw";
import GROUPS_SOURCE from "./mlPipelineGroups.ts?raw";
import CONTENTS_SOURCE from "./ProjectContents.tsx?raw";
import RAIL_SOURCE from "./PipelineRail.tsx?raw";
import CATALOGUE from "../lib/i18n.ts?raw";
import API_SOURCE from "../lib/api.ts?raw";
import PROJECT_SOURCE from "../pages/ProjectWorkspace.tsx?raw";

describe("the RL feature-engineering stage on the canvas", () => {
  it("belongs to the 'Build and split' group the person already looks at", () => {
    // tests/test_guided_pipeline_groups.py holds these ids to the workflow spec,
    // so a stage added here that the runner does not execute fails on the Python
    // side, and one the runner executes that no group claims fails there too.
    expect(GROUPS_SOURCE).toContain(
      'stages: ["feature_pipeline", "splitting", "rl_feature_engineering"]',
    );
  });

  it("shows the stage's note on the card, with its tone", () => {
    // The card had one footer line and no warning affordance at all, so an
    // unreachable service was invisible until someone opened the panel.
    expect(SOURCE).toContain("const note = group.nodes.find((node) => node.note)?.note ?? undefined");
    expect(SOURCE).toContain("note={note}");
    expect(SOURCE).toContain('note.tone === "warn" ? "text-warn-700" : "text-ink-soft"');
  });

  it("does not decide for itself what counts as a warning", () => {
    // Severity and tone are assigned on the server, like panel severity, so the
    // two sides cannot disagree about whether something is a problem. The note
    // arrives translated; the browser only picks a colour from the tone.
    expect(API_SOURCE).toContain('note?: { text: string; tone: "neutral" | "warn" } | null;');
    expect(SOURCE).not.toContain('rl_feature_engineering"');
  });

  it("names the stage rather than prettifying its id into 'Rl'", () => {
    expect(RAIL_SOURCE).toContain('if (id === "rl_feature_engineering") return "RL feature engineering"');
    expect(CATALOGUE).toContain('"RL feature engineering": "RL öznitelik mühendisliği"');
  });
});

describe("one model card, two results", () => {
  const pairedModel: ModelSummary = {
    artifact_id: "base-model",
    run_id: "run-1",
    created_at: "2026-09-02T00:00:00Z",
    winner_id: "rf",
    display_name: "Random forest",
    estimator: "RandomForestRegressor",
    metric: "rmse",
    holdout_score: 4,
    cv_mean: 3.8,
    cv_std: 0.2,
    saved: true,
    candidate_count: 3,
    training_rows: 100,
    enhanced: {
      artifact_id: "enhanced-model",
      display_name: "RL-enhanced random forest",
      estimator: "RandomForestRegressor",
      holdout_score: 3,
      score_delta: 1,
      generated_feature_count: 2,
      saved: true,
    },
  };

  it("renders the original and RL result together with the same metric", () => {
    const markup = renderToStaticMarkup(createElement(ModelComparison, { model: pairedModel }));

    expect(markup).toContain("Özgün model");
    expect(markup).toContain("RL ile geliştirilmiş model");
    expect(markup.match(/rmse/g)).toHaveLength(2);
    expect(markup).toContain("4.00");
    expect(markup).toContain("3.00");
    expect(markup).toContain('data-model-variant="original"');
    expect(markup).toContain('data-model-variant="rl-enhanced"');
  });

  it("offers both models from the same card in the automation view", () => {
    // One run produces one training outcome. Two cards would read as two
    // unrelated models rather than two files from the same result.
    expect(CONTENTS_SOURCE).toContain('{t("Download original")}');
    expect(CONTENTS_SOURCE).toContain('{t("Download RL-enhanced")}');
    expect(CONTENTS_SOURCE).toContain("href={`/api/models/${enhanced.artifact_id}/download`}");
  });

  it("offers both models from the same card in the project view", () => {
    expect(PROJECT_SOURCE).toContain("model.enhanced ? <ModelComparison model={model} />");
    expect(CONTENTS_SOURCE).toContain('data-model-variant="original"');
    expect(CONTENTS_SOURCE).toContain('data-model-variant="rl-enhanced"');
  });

  it("hides the second download when there is no enhanced model", () => {
    const markup = renderToStaticMarkup(
      createElement(ModelComparison, { model: { ...pairedModel, enhanced: null } }),
    );
    expect(markup).toBe("");
    expect(CONTENTS_SOURCE).toContain("enhanced.saved && <a");
  });

  it("shares one row renderer between the two views", () => {
    // Two hand-maintained copies would drift, and the project view is the one
    // people actually open.
    expect(PROJECT_SOURCE).toContain('import { ModelComparison } from "../components/ProjectContents"');
    expect(CONTENTS_SOURCE).toContain("export function ModelComparison(");
    expect(CONTENTS_SOURCE).toContain("model.enhanced ? <ModelComparison model={model} />");
    expect(PROJECT_SOURCE).toContain("model.enhanced ? <ModelComparison model={model} />");
  });

  it("shows a worse enhanced model as worse rather than as a win", () => {
    // The delta arrives already oriented so positive means better whichever way
    // the metric runs. Tone it on that sign: the run still offers the model for
    // download, and whoever picks between the two needs to see which one won.
    expect(CONTENTS_SOURCE).toContain("const better = typeof delta === \"number\" && delta > 0;");
    expect(CONTENTS_SOURCE).toContain('<Badge tone={better ? "ok" : "warn"}>');
  });

  it("translates every new string", () => {
    expect(CATALOGUE).toContain('"Download original": "Özgün modeli indir"');
    expect(CATALOGUE).toContain('"Download RL-enhanced": "RL ile geliştirilmiş modeli indir"');
    expect(CATALOGUE).toContain('"Original model": "Özgün model"');
    expect(CATALOGUE).toContain('"With {count} engineered feature(s)": "{count} üretilmiş öznitelik ile"');
  });
});
