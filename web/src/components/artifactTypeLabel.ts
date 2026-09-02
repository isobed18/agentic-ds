/** The English label for every kind in the `ArtifactType` vocabulary.
 *
 * The label used to be derived from the type code at runtime --
 * `integration_plan` -> `"Integration plan"` -- and handed straight to `t()`.
 * That is invisible to the catalogue test, which scans the source for calls
 * made with a string literal, so 29 of the kinds reached a Turkish screen with
 * no Turkish entry and nothing failed (#366). The derivation was also wrong for initialisms in
 * English: `eda_report` read "Eda report" and nothing mechanical can know that
 * "EDA" is not a word.
 *
 * Writing the labels out fixes both. `ArtifactType` is a closed vocabulary --
 * `ads.contracts.base` says so, because the agent context policy depends on it
 * -- so the table is enumerable, and `tests/test_artifact_type_labels.py` holds
 * it to the enum: a new kind fails there instead of shipping untranslated.
 */
const LABELS: Record<string, string> = {
  data_card: "Data card",
  integration_plan: "Integration plan",
  integration_trial: "Integration trial",
  problem_candidates: "Problem candidates",
  problem_definition: "Problem definition",
  validation_strategy: "Validation strategy",
  validation_trial: "Validation trial",
  eda_report: "EDA report",
  exploratory_analysis: "Exploratory analysis",
  feature_spec: "Feature spec",
  feature_experiment: "Feature experiment",
  leakage_report: "Leakage report",
  candidate_set: "Candidate set",
  trained_model: "Trained model",
  model_experiment: "Model experiment",
  evaluation_report: "Evaluation report",
  final_report: "Final report",
  agent_audit: "Agent audit",
  measurement_bundle: "Measurement bundle",
  comprehension_brief: "Comprehension brief",
  staging_workspace: "Staging workspace",
  staging_report: "Staging report",
  document_extraction: "Document extraction",
  document_table_review: "Document table review",
  automation_execution_plan: "Automation execution plan",
  graph_patch: "Graph patch",
  table_asset: "Table asset",
  split_manifest: "Split manifest",
  node_attempt: "Node attempt",
  critique: "Critique",
  gate_decision: "Gate decision",
};

/** Every label an artifact type can render, for the catalogue test.
 *
 * These reach `t()` through a variable, so the literal scanner that guards the
 * Turkish catalogue cannot see any of them.
 */
export const ARTIFACT_TYPE_LABELS: string[] = Object.values(LABELS);

/** The i18n key naming one artifact kind.
 *
 * A code outside the vocabulary still gets a readable name rather than the raw
 * snake_case: the server is free to add a kind before the web app is rebuilt,
 * and an unstyled `integration_plan` on a card is worse than an untranslated
 * "Integration plan".
 */
export function artifactTypeLabel(artifactType: string): string {
  const known = LABELS[artifactType];
  if (known) return known;
  const words = artifactType.replaceAll("_", " ");
  return words.charAt(0).toLocaleUpperCase() + words.slice(1);
}
