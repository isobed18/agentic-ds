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
  // `eda` and `leakage_audit` are the ids the established workflow declares.
  // This group previously named `exploratory_analysis` (an artifact type) and
  // `lineage_audit` (an id nothing produces), so both stages ran but were
  // silently dropped from this group's status, inspection, and artifact count.
  // tests/test_guided_pipeline_groups.py holds every id here to the spec.
  {
    id: "analysis",
    title: "Analyze and validate",
    description: "Measure patterns, choose validation, and check for leakage before training.",
    stages: ["validation_strategy", "eda", "leakage_audit"],
  },
  {
    id: "features",
    title: "Build and split",
    description: "Create model-ready features and divide the data without contaminating evaluation.",
    stages: ["feature_pipeline", "splitting"],
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
