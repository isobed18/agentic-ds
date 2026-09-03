import { describe, expect, it } from "vitest";

import SOURCE from "./GuidedPipeline.tsx?raw";
import GROUPS_SOURCE from "./mlPipelineGroups.ts?raw";
import BUILDER_SOURCE from "./PipelineBuilder.tsx?raw";
import PANEL_SOURCE from "./PlannerPanel.tsx?raw";
import CATALOGUE from "../lib/i18n.ts?raw";
import PAGE_SOURCE from "../pages/AutomationWorkspace.tsx?raw";
import UNDERSTANDING_SOURCE from "./UnderstandingWorkspace.tsx?raw";
import ARTIFACT_NODES_SOURCE from "./ArtifactNodes.tsx?raw";
import STAGE_WORKSPACE_SOURCE from "./StageWorkspace.tsx?raw";
import API_SOURCE from "../lib/api.ts?raw";
import DIAGNOSTICS_SOURCE from "../lib/diagnostics.ts?raw";
import SENSITIVITY_SOURCE from "./SensitivityOverride.tsx?raw";

describe("staged sensitivity review (#330)", () => {
  it("puts the existing PII override in the active guided workspace before plan acceptance", () => {
    // The control already existed in the retired /workflows screen, which made
    // its backend endpoint look complete while the product's real
    // AutomationWorkspace path never mounted it. Keep the assertion on the
    // active guided component so moving it back to a dead route fails here.
    expect(SOURCE).toContain('import { SensitivityOverride } from "./SensitivityOverride"');
    expect(SOURCE).toContain('t("Review personal data")');
    expect(SOURCE).toContain("{sensitivitySelected && <Inspector");
    expect(SOURCE).toContain("<SensitivityOverride runId={runId} tables={profile.tables}");
    expect(SOURCE).toContain("!accepted && profile.tables.length > 0");
  });

  it("keeps an applied override visibly saved instead of snapping back to the machine value", () => {
    // Before #330, clearing `changes` after a successful request would make the
    // selected button fall back to the unchanged source profile. The saved
    // baseline makes the immediate success feedback truthful while the backend
    // classification is already updated for the pipeline.
    expect(SENSITIVITY_SOURCE).toContain("const [applied, setApplied]");
    expect(SENSITIVITY_SOURCE).toContain("setApplied((previous) => ({ ...previous, ...result.applied }))");
    expect(SENSITIVITY_SOURCE).toContain("setChanges({})");
  });
});

describe("planner access across the automation (#97, #378)", () => {
  it("mounts the planner chat, not just a read-only rationale block", () => {
    // The "Chat with Planner" entry point lived only in UnderstandingAndProposal
    // and was unmounted once a plan was accepted -- so once the pipeline was
    // running, and at the exact moment a gate escalated for human input, there
    // was no way to consult the planner. #378 moved the mount up again, from
    // the guided canvas to the page, for the same reason one step earlier: the
    // canvas itself only exists for two of five lifecycle states.
    expect(PAGE_SOURCE).toContain('import { PlannerPanel } from "../components/PlannerPanel"');
    expect(PAGE_SOURCE).toContain("<PlannerPanel");
    expect(PAGE_SOURCE).toContain("runId={runId}");
    // And it is not left behind on the canvas.
    expect(SOURCE).not.toContain("<PlannerPanel");
  });

  it("exposes a toggle that opens the panel", () => {
    // Gated on its own open state so it does not permanently occupy the page.
    // #188: this test always called the control a toggle, but the handler it
    // pinned only ever opened the panel. Now it flips, so the assertion says so.
    expect(PAGE_SOURCE).toContain('onClick={() => setPlannerOpen((open) => !open)}');
    expect(PAGE_SOURCE).toContain('t("Chat with Planner")');
    expect(PAGE_SOURCE).toContain("plannerOpen &&");
  });

  it("anchors the opener to the bottom centre, away from the run controls (#284)", () => {
    // It is a persistent entry point rather than one of the run controls, so it
    // gets the opposite edge to itself instead of a slot in the top toolbar.
    expect(PAGE_SOURCE).toContain('className="fixed bottom-6 left-1/2 z-30 -translate-x-1/2"');
    // Exactly one opener in the whole automation workspace.
    expect(PAGE_SOURCE.match(/t\("Chat with Planner"\)/g) ?? []).toHaveLength(1);
    expect(SOURCE).not.toContain("Chat with Planner");
  });

  it("survives every lifecycle state, which is why it left the canvas (#378)", () => {
    // The canvas is mounted for `proposal` and `guided_pipeline` only. The
    // opener is rendered beside the whole lifecycle switch instead, so `empty`,
    // `source` and `understanding` reach the Planner too -- and PlannerPanel
    // already copes with a null runId.
    const openerAt = PAGE_SOURCE.indexOf('t("Chat with Planner")');
    const switchAt = PAGE_SOURCE.indexOf('lifecycle === "empty"');
    expect(switchAt).toBeGreaterThan(-1);
    expect(openerAt).toBeGreaterThan(switchAt);
    expect(PANEL_SOURCE).toContain("runId = null");
  });

  it("keeps the opener outside any canvas transform so it cannot drift (#60)", () => {
    // A `position: fixed` element inside a transformed ancestor is positioned
    // against that ancestor, which is how this exact button once slid across
    // the screen as the graph zoomed. On the page it has no transformed
    // ancestor at all, and CanvasSurface still renders `overlay` outside its
    // scaled content for everything that stayed behind.
    expect(SOURCE).not.toContain("fixed bottom-6 left-1/2");
    expect(UNDERSTANDING_SOURCE).toContain("{overlay}");
  });

  it("gives every ML planner mount the source and data-first prompts (#190)", () => {
    expect(PAGE_SOURCE).toContain("sourceId={sourceId || profile?.source_id || null}");
    expect(PAGE_SOURCE).toContain('t("What columns are in this data?")');
    expect(PAGE_SOURCE).toContain('t("Rank the best target columns and ML problems.")');
    expect(PAGE_SOURCE).not.toContain('t("What is this gate asking?")');
    expect(BUILDER_SOURCE).toContain("sourceId?: string | null");
    expect(BUILDER_SOURCE).toContain("<PlannerPanel runId={runId} sourceId={sourceId}");
    expect(PANEL_SOURCE).toContain("problem_recommendations");
    expect(PANEL_SOURCE).toContain('t("Ranked ML opportunities")');
  });

  it("keeps the staging prompts until the plan is accepted", () => {
    expect(PAGE_SOURCE).toContain('lifecycle === "guided_pipeline"');
    expect(PAGE_SOURCE).toContain('t("Stop after EDA so I can inspect it.")');
  });
});

describe("acting on extracted tables from the plan overview (#311)", () => {
  it("offers the review dialog beside the note that says they are unpromoted", () => {
    // The note told the reader the candidates "remain review-only until
    // explicitly promoted" and gave them no way to promote one: the only
    // Review button lived on the staging document panel, a canvas away.
    expect(SOURCE).toContain('import { DocumentTableReview } from "./DocumentTableReview"');
    expect(SOURCE).toContain('t("Review {count} extracted tables", { count: candidateTables })');
    expect(SOURCE).toContain("candidateTables > 0 && extractionId");
    expect(SOURCE).toContain("<DocumentTableReview runId={runId}");
  });

  it("re-reads the workspace after a promotion so the count is not stale", () => {
    // Promoting turns candidates into real tables, so the candidate count and
    // the ML inputs beside it both describe the state before the click.
    expect(SOURCE).toContain("api.stagingWorkspace(runId).then(onWorkspaceUpdated)");
  });

  it("passes the plan overview what it needs to open the dialog", () => {
    expect(SOURCE).toContain("<PlanSummary runId={runId}");
    expect(SOURCE).toContain("workspace.document_extractions?.at(-1)?.artifact_id");
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
    expect(SOURCE).toContain("fixed top-[4.375rem] left-1/2");
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
    expect(SOURCE).toContain('import { Badge, Empty, Pause, Play, cx } from "./ui"');
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

describe("re-run control (#247, #378)", () => {
  it("exposes a vector reload icon that re-runs, not a text glyph or a route through Execution history", () => {
    expect(PAGE_SOURCE).toContain("><Reload /></button>");
    expect(PAGE_SOURCE).toContain("onClick={() => void rerunAutomation()}");
    expect(PAGE_SOURCE).toContain("async function rerunAutomation()");
    expect(PAGE_SOURCE).toContain("const staged = await api.rerun(runId);");
    // #378: it is an automation-level action, so it left the canvas entirely
    // rather than being handed down as a prop.
    expect(SOURCE).not.toContain("onRerun");
    expect(SOURCE).not.toContain("<Reload />");
  });

  it("sits beside the automation name and is disabled, never hidden (#378)", () => {
    // Gated on `accepted && !active` it was absent for the whole first half of
    // an automation's life and again during every run. One predictable spot,
    // with its own state explaining itself.
    const nameField = PAGE_SOURCE.indexOf('aria-label={t("Automation name")}');
    const rerun = PAGE_SOURCE.indexOf('aria-label={t("Re-run this automation")}');
    const tabs = PAGE_SOURCE.indexOf('absolute left-1/2 flex -translate-x-1/2 rounded-lg');
    expect(nameField).toBeGreaterThan(-1);
    expect(rerun).toBeGreaterThan(nameField);
    expect(rerun).toBeLessThan(tabs);
    expect(PAGE_SOURCE).toContain("const canRerun = Boolean(runId) && !busy && !isRunActive(runStatus)");
    expect(PAGE_SOURCE).toContain("disabled={!canRerun}");
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
    // say which are engineering records. The filter reads that set.
    expect(SOURCE).toContain("const diagnosticIds = useMemo(() => diagnosticIdsOf(progress)");
    expect(DIAGNOSTICS_SOURCE).toContain("progress?.diagnostic_artifact_ids");
  });

  it("offers the toggle only when there is a diagnostic to reveal", () => {
    expect(SOURCE).toContain("diagnosticCount > 0 &&");
    expect(SOURCE).toContain('t("Diagnostics ({count})", { count: diagnosticCount })');
  });

  // #423: a button that renamed itself left the state readable only by pressing
  // it, resized the toolbar under the pointer, and dropped the count in the
  // "hide" state. The state belongs in a checkbox, and the label -- count and
  // all -- stays put across both states.
  it("carries the diagnostics state in a checkbox, not in the label", () => {
    expect(SOURCE).not.toContain('t("Hide diagnostics")');
    expect(SOURCE).not.toContain('t("Show diagnostics ({count})"');
    expect(SOURCE).toContain('<input type="checkbox" checked={showDiagnostics} onChange={(event) => setShowDiagnostics(event.target.checked)}');
  });

  it("translates the diagnostics toggle", () => {
    expect(CATALOGUE).toContain('"Diagnostics ({count})": "Tanılama ({count})"');
    expect(CATALOGUE).not.toContain('"Hide diagnostics"');
    expect(CATALOGUE).not.toContain('"Show diagnostics ({count})"');
  });

  it("says which rows are the diagnostics", () => {
    expect(SOURCE).toContain("diagnosticIds={diagnosticIds}");
    expect(CATALOGUE).toContain('"Diagnostic": "Tanılama"');
  });
});

describe("the diagnostics toggle reaches every artifact surface (#424)", () => {
  // #305 put `showDiagnostics` in `useState` inside GuidedPipeline, so the six
  // ML group cards were the only artifact list in the app that could see it.
  // Every other surface listed diagnostics unconditionally -- including the
  // stage inspector, which opens from the very node whose chips had just been
  // filtered, so the same run showed two different artifact lists one click
  // apart and the control read as doing nothing.
  it("keeps the preference in one module every surface can read", () => {
    expect(DIAGNOSTICS_SOURCE).toContain("export function useShowDiagnostics(): boolean");
    expect(DIAGNOSTICS_SOURCE).toContain("export function setShowDiagnostics(next: boolean): void");
    expect(DIAGNOSTICS_SOURCE).toContain("useSyncExternalStore(subscribe, snapshot, snapshot)");
    // And not back in one component's local state, which is the bug.
    expect(SOURCE).not.toContain("const [showDiagnostics, setShowDiagnostics] = useState");
    expect(SOURCE).toContain("const showDiagnostics = useShowDiagnostics();");
  });

  it("filters in the node every canvas artifact list already goes through", () => {
    // Rather than in each caller, which is how one of them ended up being the
    // only one that filtered. GuidedPipeline hands over the unfiltered ids and
    // the closed set; ArtifactNodes does the hiding.
    expect(ARTIFACT_NODES_SOURCE).toContain("const showDiagnostics = useShowDiagnostics();");
    expect(ARTIFACT_NODES_SOURCE).toContain("ids.filter((id) => !diagnosticIds.has(id))");
    expect(SOURCE).not.toContain("visibleArtifactIdsByStage");
    expect(SOURCE).toContain("artifactIdsByStage.get(stage) ?? []");
  });

  it("filters the stage inspector, which opens from the node it disagreed with", () => {
    // `StageEvidence` rendered `detail.outputs` straight from
    // `api.stage(runId, stageId)` with no filter at all.
    expect(SOURCE).toContain("withoutDiagnostics(detail.outputs ?? [], (output) => output.diagnostic === true, showDiagnostics)");
    // Reading the backend's per-artifact flag, not re-deriving the kinds here.
    expect(API_SOURCE).toContain("diagnostic?: boolean;");
  });

  it("filters the per-stage chips in the docked group panel", () => {
    expect(SOURCE).toContain("withoutDiagnostics(artifactIds, (id) => diagnosticIds.has(id), showDiagnostics)");
  });

  it("gives the staging graph the set it never had", () => {
    // `UnderstandingWorkspace` mounted ArtifactNodes with no diagnosticIds at
    // all, so those pills listed engineering records unmarked with no way to
    // hide them -- and that screen has no diagnostics control of its own.
    expect(UNDERSTANDING_SOURCE).toContain("const diagnosticIds = useMemo(() => diagnosticIdsOf(progress)");
    expect(UNDERSTANDING_SOURCE).toContain("diagnosticIds?: ReadonlySet<string>; trailing?: React.ReactNode }) {");
    expect(UNDERSTANDING_SOURCE).toContain("<ArtifactNodes ids={artifactIds} activeId={activeArtifactId} diagnosticIds={diagnosticIds} onOpen={onOpenArtifact} />");
    // And the guided canvas draws the same staging half, so it passes its own.
    expect(SOURCE).toContain("activeArtifactId={preview?.artifact_id ?? null} diagnosticIds={diagnosticIds} trailing={mlPipeline}");
  });

  it("filters the advanced editor's output list", () => {
    // `PipelineBuilder` listed every `artifact_ids` entry per output port. It
    // has a run id but no progress poller, so it asks for the ids once.
    expect(BUILDER_SOURCE).toContain("const diagnosticIds = useDiagnosticIds(runId);");
    expect(BUILDER_SOURCE).toContain("visibleIds(output.artifact_ids).map((artifactId)");
    expect(BUILDER_SOURCE).toContain("!outputs.some((output) => visibleIds(output.artifact_ids).length)");
    expect(DIAGNOSTICS_SOURCE).toContain("export function useDiagnosticIds(");
  });

  it("counts what the default view shows, in the attempt list too", () => {
    // "{n} artifacts" per attempt counted the hidden diagnostics in.
    expect(STAGE_WORKSPACE_SOURCE).toContain("const visibleCount = (ids: string[]) => (showDiagnostics ? ids.length : ids.filter((id) => !diagnosticIds.has(id)).length);");
    expect(STAGE_WORKSPACE_SOURCE).toContain("{visibleCount(attempt.artifact_ids)} {t(\"artifacts\")}");
    expect(STAGE_WORKSPACE_SOURCE).toContain("const visibleOutputs = withoutDiagnostics(outputs, (o) => o.diagnostic === true, showDiagnostics);");
  });

  it("counts the run's diagnostics, not the ML stages' share of them", () => {
    // The number on the control was taken over ML stage attempts only, while
    // the staging half of the same canvas drew diagnostics of its own -- so it
    // described neither what was hidden nor what pressing it would reveal.
    expect(SOURCE).toContain("const diagnosticCount = diagnosticIds.size;");
    expect(SOURCE).not.toContain("for (const ids of artifactIdsByStage.values()) seen +=");
  });

  it("does not let a private window take an artifact list down with it", () => {
    // Every storage access is guarded: the getter itself can throw.
    expect(DIAGNOSTICS_SOURCE).toContain("globalThis.sessionStorage?.getItem(STORAGE_KEY)");
    expect(DIAGNOSTICS_SOURCE).toContain('globalThis.sessionStorage?.setItem(STORAGE_KEY, next ? "1" : "0");');
    // Both reads and both writes sit inside one, and a run whose progress
    // lookup fails simply has no diagnostics to hide.
    expect(DIAGNOSTICS_SOURCE.match(/} catch {/g) ?? []).toHaveLength(2);
    expect(DIAGNOSTICS_SOURCE).toContain(".catch(() => {");
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

  it("animates the arrow into the running stage, and only that one (#313)", () => {
    // The arrow into the first ML group was hardcoded inert. #214 deleted the
    // "Data understood" tile it used to leave; it leaves "Proposed plan" now.
    //
    // #313: this test used to pin the inter-group rule
    // `status === "running" || … || groupStatuses[index + 1] === "running"`,
    // which lit the arrow behind a running group as well as the one in front
    // of it -- the defect itself, asserted. One rule now decides every arrow,
    // and `pipelineArrows.test.ts` holds the behaviour.
    expect(SOURCE).not.toContain("<Arrow active={false} complete />");
    expect(SOURCE).toContain('import { activeArrows } from "./pipelineArrows"');
    expect(SOURCE).toContain("const arrowActive = activeArrows(groupStatuses)");
    expect(SOURCE).toContain("<Arrow active={arrowActive[0]} complete={accepted}");
    expect(SOURCE).toContain("<Arrow active={arrowActive[index + 1]}");
    expect(SOURCE).not.toContain('groupStatuses[index + 1] === "running"');
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
      SOURCE.indexOf('<div data-no-pan className="fixed top-[4.375rem]'),
      SOURCE.indexOf("\n    </div>", SOURCE.indexOf('<div data-no-pan className="fixed top-[4.375rem]')),
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
    expect(SOURCE).toContain("const active = accepted && isRunActive(activeStatus)");
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
