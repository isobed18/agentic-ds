/** The fixed ML spine, shared by every canvas that draws it.
 *
 * It lived in `GuidedPipeline.tsx` while that file was the only one rendering
 * it. Since #214 the understanding canvas and the accepted pipeline are one
 * graph, so both halves read this table -- and importing it from a component
 * module would have made `UnderstandingWorkspace` and `GuidedPipeline` import
 * each other. The spine is data, not a view, so it lives on its own.
 *
 * Exported rather than copied: tests/test_guided_pipeline_groups.py holds every
 * id here to the workflow spec, and a second hand-maintained list would not
 * inherit that.
 */
export const GROUPS: Array<{ id: string; title: string; description: string; stages: string[] }> = [
  {
    id: "prepare",
    title: "Prepare ML data",
    description: "Combine approved tables into one verified modeling dataset.",
    stages: ["integration"],
  },
  {
    id: "objective",
    title: "Define the objective",
    description: "Choose the prediction goal and confirm that the available data can support it.",
    stages: ["problem_discovery"],
  },
  // `validation_strategy`, `eda` and `leakage_audit` used to share one "Analyze
  // and validate" node. They are three genuinely different things -- choosing
  // how to validate, looking at the data, and checking for leakage -- and
  // folding them into one node meant none of them, EDA least of all, had a name
  // anyone could find on the canvas (#212). One node each now; splitting does
  // not change what runs, only what the canvas shows for it.
  // tests/test_guided_pipeline_groups.py holds every id here to the spec.
  {
    id: "validation_strategy",
    title: "Choose validation",
    description: "Decide how training and holdout data are separated to keep evaluation honest.",
    stages: ["validation_strategy"],
  },
  {
    id: "eda",
    title: "Exploratory analysis",
    description: "Measure distributions, missingness, and relationships before training.",
    stages: ["eda"],
  },
  {
    id: "leakage_audit",
    title: "Leakage audit",
    description: "Check candidate features for information that would make results unrealistically good.",
    stages: ["leakage_audit"],
  },
  {
    id: "features",
    title: "Build and split",
    description: "Create model-ready features and divide the data without contaminating evaluation.",
    // `rl_feature_engineering` runs after the split on purpose -- it sends
    // training rows only, so the external search cannot see the holdout -- but
    // it belongs to this group because what it produces is features.
    stages: ["feature_pipeline", "splitting", "rl_feature_engineering"],
  },
  {
    id: "model",
    title: "Train and evaluate",
    description: "Train candidate models and compare them on held-out data.",
    stages: ["training", "evaluation"],
  },
  {
    id: "report",
    title: "Review results",
    description: "Summarize the selected model, evidence, limitations, and next steps.",
    stages: ["report"],
  },
];

/** Every panel selection that belongs to the ML half of the graph. */
export const ML_SELECTIONS = ["summary", ...GROUPS.map((group) => group.id)];
