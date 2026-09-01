import { describe, expect, it } from "vitest";

import SOURCE from "./GuidedPipeline.tsx?raw";
import GROUPS_SOURCE from "./mlPipelineGroups.ts?raw";
import BUILDER_SOURCE from "./PipelineBuilder.tsx?raw";
import PANEL_SOURCE from "./PlannerPanel.tsx?raw";
import CATALOGUE from "../lib/i18n.ts?raw";
import PAGE_SOURCE from "../pages/AutomationWorkspace.tsx?raw";
import UNDERSTANDING_SOURCE from "./UnderstandingWorkspace.tsx?raw";

describe("planner access in the guided pipeline (#97)", () => {
  it("mounts the planner chat, not just a read-only rationale block", () => {
    // The "Chat with Planner" entry point lived only in UnderstandingAndProposal
    // and was unmounted once a plan was accepted -- so once the pipeline was
    // running, and at the exact moment a gate escalated for human input, there
    // was no way to consult the planner. The running view now imports and
    // renders the same PlannerPanel the staging view uses.
    expect(SOURCE).toContain('import { PlannerPanel } from "./PlannerPanel"');
    expect(SOURCE).toContain("<PlannerPanel");
    expect(SOURCE).toContain("runId={runId}");
  });

  it("exposes a toggle in the run toolbar that opens the panel", () => {
    // Reachable from the fixed run-control toolbar, gated on its own open state
    // so it does not permanently occupy the canvas.
    // #188: this test always called the control a toggle, but the handler it
    // pinned only ever opened the panel. Now it flips, so the assertion says so.
    expect(SOURCE).toContain('onClick={() => setPlannerOpen((open) => !open)}');
    expect(SOURCE).toContain('t("Chat with Planner")');
    expect(SOURCE).toContain("plannerOpen &&");
  });

  it("gives every ML planner mount the source and data-first prompts (#190)", () => {
    expect(SOURCE).toContain("sourceId={profile.source_id}");
    expect(SOURCE).toContain('t("What columns are in this data?")');
    expect(SOURCE).toContain('t("Rank the best target columns and ML problems.")');
    expect(SOURCE).not.toContain('t("What is this gate asking?")');
    expect(BUILDER_SOURCE).toContain("sourceId?: string | null");
    expect(BUILDER_SOURCE).toContain("<PlannerPanel runId={runId} sourceId={sourceId}");
    expect(PANEL_SOURCE).toContain("problem_recommendations");
    expect(PANEL_SOURCE).toContain('t("Ranked ML opportunities")');
  });
});

describe("what a finished run offers (#285)", () => {
  it("does not push the reader to the Runs tab when the pipeline completes", () => {
    // The completion state already shows what the run produced. A button
    // offering to navigate somewhere else added a step and no information.
    expect(SOURCE).not.toContain("onOpenExecutions");
    expect(SOURCE).not.toContain('onClick={onOpenExecutions}');
    expect(PAGE_SOURCE).not.toContain("onOpenExecutions");
  });

  it("keeps the report group's node title, which is not the removed button", () => {
    // The issue guessed the "Review results" string and pipeline group might
    // become dead with the button. Neither does: the string is the title of
    // the report group's node on the canvas, rendered through t(group.title),
    // and the catalogue entry is what translates it.
    expect(GROUPS_SOURCE).toContain('title: "Review results"');
    expect(CATALOGUE).toContain('"Review results": "Sonuçları incele"');
  });

  it("still reports a finished run's status where the control used to be", () => {
    // `complete` is not dead either -- it is what suppresses the status badge
    // while a run is live and lets it show once the run has ended.
    expect(SOURCE).toContain("const complete = accepted && activeStatus === \"completed\"");
    expect(SOURCE).toContain("!complete && <StatusBadge");
  });
});

describe("the run control (#197)", () => {
  it("docks the control top-centre instead of floating at the bottom edge", () => {
    // Bottom-middle placement was easy to miss, so the whole workspace read as
    // stuck (#194). Top-centre is where the eye lands on the canvas.
    expect(SOURCE).toContain("fixed top-[70px] left-1/2");
    expect(SOURCE).not.toContain("fixed bottom-4 left-1/2");
  });

  it("carries state in one Run/Pause primary button, not a button that becomes loose text", () => {
    // Run and Continue while a run can start; Pause while it is live -- both are
    // the same primary control, and the separate ghost pause button is gone. No
    // lowercase status label stands in where a button used to be.
    expect(SOURCE).toContain('t(currentStage ? "Continue" : "Run")');
    expect(SOURCE).toContain("{active && <button");
    expect(SOURCE).not.toContain('t("Pause after current stage")');
  });

  it("uses stroked vector play/pause icons, not OS text glyphs, and a distinct primary button (#248)", () => {
    // Literal ▶/⏸ render in whatever emoji font the OS supplies; the run button
    // uses the same stroked 20x20 chrome as everything else, and the primary
    // control is its own row above a separate, quieter secondary bar rather than
    // one chip among five.
    expect(SOURCE).toContain('import { Badge, Empty, Pause, Play, Reload, cx } from "./ui"');
    expect(SOURCE).toContain("><Play />");
    expect(SOURCE).toContain("><Pause />");
    expect(SOURCE).not.toContain('<span aria-hidden="true">▶</span>');
    expect(SOURCE).not.toContain('<span aria-hidden="true">⏸</span>');
    // The Run button and the Review-plan/Planner/Advanced bar are separate rows.
    expect(SOURCE).toContain('flex -translate-x-1/2 flex-col items-center gap-2');
  });

  it("reacts to its own click without a reload", () => {
    // The poll had parked itself and stale progress outranked the fresh prop, so
    // the button only changed after a remount. Re-arm on runStatus, and trust a
    // non-staged polled status over the pre-click "staged".
    expect(SOURCE).toContain("}, [runId, runStatus, accepted]);");
    expect(SOURCE).toContain('polledStatus && polledStatus !== "staged" ? polledStatus : String(runStatus');
  });
});

describe("re-run control (#247)", () => {
  it("exposes a vector reload icon that fires onRerun, not a text glyph or a route through Execution history", () => {
    expect(SOURCE).toContain("onRerun: () => void;");
    expect(SOURCE).toContain("onClick={onRerun}");
    expect(SOURCE).toContain("><Reload /></button>");
    expect(PAGE_SOURCE).toContain("async function rerunAutomation()");
    expect(PAGE_SOURCE).toContain("const staged = await api.rerun(runId);");
    expect(PAGE_SOURCE).toContain("onRerun={() => void rerunAutomation()}");
  });
});

describe("target-column picker (#244/#198)", () => {
  it("offers a dropdown of the base table's profiled columns before the run starts", () => {
    // The guided flow had no way to choose the ML target, and the free-text gate
    // box was never read. The picker is a real <select> of the base table's
    // columns, shown only before the run starts (canStart, no current stage).
    expect(SOURCE).toContain("const targetColumns = baseTable?.columns ?? [];");
    expect(SOURCE).toContain("canStart && !currentStage && targetColumns.length > 0");
    expect(SOURCE).toContain("<select value={targetColumn}");
    expect(SOURCE).toContain("targetColumns.map((column) =>");
    expect(SOURCE).toContain('t("Let the agent decide")');
  });

  it("defaults to the plan's target and passes the chosen column to the run", () => {
    // Defaulting to the plan's target means an unchanged picker is a no-op; a
    // changed one aims the run. The value travels with the run mode into onRun.
    expect(SOURCE).toContain('useState<string>(String(planConfig.target_column ?? ""))');
    expect(SOURCE).toContain('onRun(approveEachStage ? "manual" : "fully_auto", targetColumn || null, problemKind === "ask_planner" ? null : problemKind)');
  });

  it("translates the picker's tooltip", () => {
    expect(CATALOGUE).toContain("Choose which column the model should predict, or let problem discovery propose one.");
    expect(CATALOGUE).toContain("Modelin tahmin edeceği sütunu seçin");
  });
});

describe("quick problem selector (#241)", () => {
  it("offers a problem-kind picker that defaults to asking the planner", () => {
    // Before this, an ordinary problem ("predict this column", "flag the odd
    // ones") had no path but the planner conversation. "ask_planner" preserves
    // that exact existing behaviour as the default.
    expect(SOURCE).toContain('useState<"ask_planner" | "predict_column" | "flag_anomalies">("ask_planner")');
    expect(SOURCE).toContain('<option value="ask_planner">{t("Ask the planner")}</option>');
    expect(SOURCE).toContain('<option value="predict_column">{t("Predict a column")}</option>');
    expect(SOURCE).toContain('<option value="flag_anomalies">{t("Flag unusual rows")}</option>');
  });

  it("passes the picked kind to onRun instead of always going through the planner", () => {
    expect(SOURCE).toContain('onRun(approveEachStage ? "manual" : "fully_auto", targetColumn || null, problemKind === "ask_planner" ? null : problemKind)');
  });

  it("requires a target column before Run is enabled for a predict-column pick", () => {
    expect(SOURCE).toContain('disabled={busy || !profile.tables.length || (problemKind === "predict_column" && !targetColumn)}');
  });

  it("translates the picker's options", () => {
    expect(CATALOGUE).toContain('"Ask the planner": "Planlayıcıya sor"');
    expect(CATALOGUE).toContain('"Predict a column": "Bir sütunu tahmin et"');
    expect(CATALOGUE).toContain('"Flag unusual rows": "Alışılmadık satırları işaretle"');
  });

  it("forwards the selection to startStaged as problem_selection", () => {
    expect(PAGE_SOURCE).toContain('problemKind: "predict_column" | "flag_anomalies" | null = null');
    expect(PAGE_SOURCE).toContain("problem_selection: { kind: problemKind, target_column: targetColumn }");
  });
});

describe("diagnostics are hidden from the default artifact chips (#305)", () => {
  it("reads the backend's diagnostic id set rather than re-deriving the kinds", () => {
    // The stage attempts carry artifact ids, not kinds, so only the backend can
    // say which are engineering records. The chip filter reads that set.
    expect(SOURCE).toContain("progress?.diagnostic_artifact_ids");
    expect(SOURCE).toContain("const diagnosticIds = useMemo");
  });

  it("filters diagnostics out of the chips unless the toggle is on", () => {
    expect(SOURCE).toContain("const [showDiagnostics, setShowDiagnostics] = useState(false)");
    expect(SOURCE).toContain("if (showDiagnostics) return artifactIdsByStage;");
    expect(SOURCE).toContain("ids.filter((id) => !diagnosticIds.has(id))");
    // Both chip render sites read the filtered map, so neither the canvas nodes
    // nor the stage inspector can leak a diagnostic back into the default view.
    expect(SOURCE).not.toContain("artifactIds={artifactIdsByStage.get(node.id)");
    expect(SOURCE).toContain("visibleArtifactIdsByStage.get(node.id)");
    expect(SOURCE).toContain("visibleArtifactIdsByStage.get(stage)");
  });

  it("offers the toggle only when there is a diagnostic to reveal", () => {
    expect(SOURCE).toContain("diagnosticCount > 0 &&");
    expect(SOURCE).toContain('t("Hide diagnostics")');
    expect(SOURCE).toContain('t("Show diagnostics ({count})", { count: diagnosticCount })');
  });

  it("translates the diagnostics toggle", () => {
    expect(CATALOGUE).toContain('"Hide diagnostics": "Tanılamayı gizle"');
    expect(CATALOGUE).toContain('"Show diagnostics ({count})": "Tanılamayı göster ({count})"');
  });
});

describe("EDA analysis charts reach the guided stage inspector (#304)", () => {
  it("renders the stage's analysis panels, not only artifact buttons", () => {
    // The measured charts arrive on detail.panels; the inspector showed a list
    // of "Open artifact" buttons and never drew them.
    expect(SOURCE).toContain('import { AnalysisStrip, type AnalysisPanel } from "./AnalysisStrip"');
    expect(SOURCE).toContain("const panels = (detail.panels ?? []) as AnalysisPanel[]");
    expect(SOURCE).toContain("panels.length > 0 && <div");
    expect(SOURCE).toContain("<AnalysisStrip panels={panels} />");
  });
});

describe("live progress on the canvas (#194)", () => {
  it("drives the current stage from the run's own signal, not only reported nodes", () => {
    // A live run whose current stage had not yet reported a workflow node used
    // to fall through to "pending" and read as idle. groupStatus now takes the
    // run's current_stage/active and marks that group running.
    expect(SOURCE).toContain("stages.includes(currentStage) || (!currentStage && groupIndex === 0)");
    expect(SOURCE).toContain("const groupStatuses = groups.map(");
  });

  it("distinguishes a pipeline waiting to be started from one that is running", () => {
    // The accepted-but-unstarted first group reads "waiting to start" and points
    // at the Run control instead of sharing the neutral "pending" look.
    expect(SOURCE).toContain("const waiting = canStart &&");
    expect(SOURCE).toContain('t("Waiting to start")');
    expect(SOURCE).toContain('t("Press Run above to start")');
  });

  it("animates the arrows into and out of the running stage", () => {
    // The arrow into the first ML group was hardcoded inert. #214 deleted the
    // "Data understood" tile it used to leave; it leaves "Proposed plan" now.
    expect(SOURCE).not.toContain("<Arrow active={false} complete />");
    expect(SOURCE).toContain('<Arrow active={groupStatuses[0] === "running"} complete={accepted}');
    expect(SOURCE).toContain('groupStatuses[index + 1] === "running"');
  });

  it("translates the new waiting strings", () => {
    for (const key of ["Waiting to start", "Press Run above to start"]) {
      expect(CATALOGUE.includes(`"${key}":`), `no Turkish entry for ${key}`).toBe(true);
    }
  });
});

describe("guided pipeline node descriptions (#168)", () => {
  it("uses one explanatory sentence per group instead of joining bare stage names", () => {
    // Joining `node.label` values produced fragments such as "integration" and
    // "problem discovery" that repeated the title without explaining the work.
    const groupsSource = GROUPS_SOURCE.slice(
      GROUPS_SOURCE.indexOf("export const GROUPS"),
      GROUPS_SOURCE.indexOf("/** Every panel selection"),
    );
    const descriptions = [...groupsSource.matchAll(/description: "([^"]+)"/g)].map(
      (match) => match[1],
    );

    // #212 split the "Analyze and validate" group into one node each for
    // validation_strategy, eda and leakage_audit, taking the count from 6 to 8.
    expect(descriptions).toHaveLength(8);
    expect(descriptions.every((description) => description.endsWith("."))).toBe(true);
    expect(SOURCE).toContain("subtitle={t(group.description)}");
    expect(SOURCE).not.toContain("group.nodes.map((node) => t(node.label");
  });
});

describe("guided pipeline stage names (#294)", () => {
  it("uses the standalone translated stage name instead of a lowercase stage id", () => {
    // Stage ids are lowercase codes. Passing them directly to t() selects the
    // catalogue's mid-sentence Turkish labels (for example "birleştirme").
    // The shared formatter selects the capitalized standalone labels and also
    // expands `eda` to its real display name.
    expect(SOURCE).toContain('import { stageName } from "./PipelineRail"');
    expect(SOURCE).toContain("{stageName(node.id)}");
    expect(SOURCE).not.toContain('t(node.label ?? node.id.replaceAll("_", " "))');
  });
});

/**
 * One continuous graph, for the whole life of the run (#214).
 *
 * Accepting the plan used to replace the canvas. `automationView` flipped from
 * "proposal" to "guided_pipeline", and those two values rendered mutually
 * exclusive components in the same slot -- different canvas, different graph,
 * different selection model, different toolbar. Everything built up during
 * understanding (uploaded files, intake, the routing branches, synthesis, the
 * proposal) vanished the moment the ML pipeline appeared, and the pipeline
 * opened on a "Data understood" tile that was nothing but a stand-in for the
 * seven nodes it had just discarded.
 */
describe("the understanding graph and the ML pipeline are one canvas (#214)", () => {
  it("hangs the ML nodes off the real graph instead of a stand-in tile", () => {
    // The tile is the whole bug in one node: seven nodes and every branch
    // collapsed into "Intake, routing, and synthesis complete".
    expect(SOURCE).not.toContain('t("Data understood")');
    expect(SOURCE).not.toContain('t("Intake, routing, and synthesis complete")');
    // The staging graph is drawn by the component that owns it, and the ML
    // nodes are its trailing row -- the same flex row, so the edge out of
    // "Proposed plan" is a real edge and not a boundary between screens.
    expect(SOURCE).toContain("<RoutingGraph");
    expect(SOURCE).toContain("trailing={mlPipeline}");
    expect(UNDERSTANDING_SOURCE).toContain("trailing?: React.ReactNode");
  });

  it("keeps one canvas, and it is the one that zooms", () => {
    // `PanCanvas` had no zoom, and the merged row is roughly twice as wide as
    // either half. `CanvasSurface` has zoom and sizes its box to the graph
    // (#203), which is the dependency this issue named.
    expect(SOURCE).toContain("<CanvasSurface");
    expect(SOURCE).not.toContain("function PanCanvas");
  });

  it("keeps one selection, reaching one docked panel from either half", () => {
    // A click on Intake and a click on "Train and evaluate" go through the
    // same piece of state; staging ids and ML ids are disjoint, so which panel
    // opens is decided by which set the selection belongs to.
    expect(SOURCE).toContain("const mlSelected = selected && ML_SELECTIONS.includes(selected)");
    expect(SOURCE).toContain("const stagingSelected = selected && !mlSelected");
    expect(SOURCE).toContain("<RoutingInspector selection={stagingSelected}");
    expect(SOURCE).toContain("{mlSelected && <Inspector");
    // Both dock into the column the canvas surrenders, rather than one of them
    // being fixed over it (#40).
    expect(SOURCE).toContain("docked={selected !== null}");
  });

  it("keeps one toolbar: Accept before acceptance, Run after", () => {
    const toolbar = SOURCE.slice(
      SOURCE.indexOf('<div data-no-pan className="fixed top-[70px]'),
      SOURCE.indexOf("\n    </div>", SOURCE.indexOf('<div data-no-pan className="fixed top-[70px]')),
    );
    expect(toolbar).toContain('{!accepted && <button');
    expect(toolbar).toContain('t("Accept and add base pipeline")');
    expect(toolbar).toContain("{canStart && <button");
    // And the proposal panel no longer offers a second, competing accept.
    expect(UNDERSTANDING_SOURCE).toContain("{onAccept && <button");
  });

  it("does not offer to run a pipeline whose plan is still a proposal", () => {
    // The staging run is already "staged" while the plan is unaccepted, so
    // every run control has to be gated on the decision, not on the status.
    expect(SOURCE).toContain("const canStart = accepted && activeStatus ===");
    expect(SOURCE).toContain('const active = accepted && ["running", "resuming"]');
    expect(SOURCE).toContain('const failed = accepted && ["failed", "interrupted", "aborted"]');
    expect(SOURCE).toContain('const complete = accepted && activeStatus === "completed"');
  });

  it("stops the plan node calling itself a proposal once it is accepted", () => {
    // The node used to leave the screen at that moment, so it never had to
    // describe a plan it had already handed on. It stays now, so it does.
    expect(SOURCE).toContain('accepted ? "accepted" : "ready"');
    expect(SOURCE).toContain('selection === "proposal" && accepted ? "summary" : selection');
    expect(UNDERSTANDING_SOURCE).toContain('export type ProposalStatus');
    expect(UNDERSTANDING_SOURCE).toContain('t("The pipeline below runs this plan")');
    expect(CATALOGUE).toContain('"The pipeline below runs this plan":');
  });

  it("fades the ML nodes rather than hiding them until the plan is accepted", () => {
    expect(SOURCE).toContain("dimmed={!accepted}");
    expect(SOURCE).toContain('dimmed && "opacity-60"');
  });

  it("renders one component for both lifecycle states", () => {
    // `automationView` keeps both states -- they still decide what the toolbar
    // offers and whether the ML nodes are live -- but they stop deciding which
    // component renders, which is what threw the graph away.
    expect(PAGE_SOURCE).toContain('{(lifecycle === "proposal" || lifecycle === "guided_pipeline")');
    expect(PAGE_SOURCE).toContain('accepted={lifecycle === "guided_pipeline"}');
    expect(PAGE_SOURCE).not.toContain("<UnderstandingAndProposal");
  });

  it("translates the strings the merged panel adds", () => {
    for (const key of ["Not started yet", "This stage runs once the plan is accepted."]) {
      expect(CATALOGUE.includes(`"${key}":`), `no Turkish entry for ${key}`).toBe(true);
    }
    // These four reached t() through a ternary, so the literal scanner never
    // saw them and they shipped in English until this panel was merged (#38).
    for (const key of ["Accepted ML plan", "Base ML pipeline", "What will run", "Stage details"]) {
      expect(CATALOGUE.includes(`"${key}":`), `no Turkish entry for ${key}`).toBe(true);
    }
  });
});
