import { describe, expect, it } from "vitest";

import CATALOGUE from "../lib/i18n.ts?raw";
import AUTOMATION_SOURCE from "./AutomationWorkspace.tsx?raw";
import PROJECT_SOURCE from "./ProjectWorkspace.tsx?raw";
import SHELL_SOURCE from "../components/Shell.tsx?raw";

describe("the project-first journey (#157, #165)", () => {
  it("routes project, then automation, as two distinct levels", () => {
    expect(AUTOMATION_SOURCE).toContain('params.get("project")');
    expect(AUTOMATION_SOURCE).toContain("<ProjectWorkspace");
    expect(AUTOMATION_SOURCE).toContain("<AutomationEditor projectId={projectId}");
  });

  it("opens a project on its overview instead of a graph", () => {
    expect(PROJECT_SOURCE).toContain('type ProjectView = "overview"');
    expect(PROJECT_SOURCE).toContain('view === "overview" && <ProjectOverview');
    expect(PROJECT_SOURCE).toContain('t("Project overview")');
  });

  it("uploads at project scope and shows project files", () => {
    expect(PROJECT_SOURCE).toContain("api.addProjectSource(projectId, group)");
    expect(PROJECT_SOURCE).toContain("source.files.map");
    expect(PROJECT_SOURCE).toContain('t("Choose previously uploaded data")');
    expect(PROJECT_SOURCE).toContain("disabled={uploading}");
    // #172: progress is rendered before the add-file action panel.
    expect(PROJECT_SOURCE.indexOf("{progress &&")).toBeLessThan(PROJECT_SOURCE.indexOf('t("Upload new files")'));
  });

  it("offers the bundled PDF benchmark as ready project data (#302)", () => {
    expect(PROJECT_SOURCE).toContain("api.installPdfDemo()");
    expect(PROJECT_SOURCE).toContain('t("Use PDF demo")');
    expect(PROJECT_SOURCE).toContain("api.addProjectSource(projectId, demo.source_id)");
  });

  it("lists many automations and creates them through their parent", () => {
    expect(PROJECT_SOURCE).toContain("api.createProjectAutomation(projectId");
    expect(PROJECT_SOURCE).toContain("automations.map");
    expect(PROJECT_SOURCE).toContain("onOpen(created.automation_id)");
  });

  it("keeps project Models and Reports pages labelled by automation", () => {
    expect(PROJECT_SOURCE).toContain('t("Project models")');
    expect(PROJECT_SOURCE).toContain('t("Project reports")');
    expect(PROJECT_SOURCE).toContain('t("From {automation}", { automation: model.automation_name })');
    expect(PROJECT_SOURCE).toContain('t("From {automation}", { automation: report.automation_name })');
  });

  it("removes the automation toolbar's uploaded-data picker and uploader", () => {
    // #380 turned the header's pinned 58px into the rem it was worth, so it
    // grows with the taller rows inside it instead of clipping them.
    const header = AUTOMATION_SOURCE.match(/<header className="relative flex h-\[3\.625rem\][\s\S]*?<\/header>/)?.[0] ?? "";
    expect(header).toContain("<LanguagePicker");
    expect(header).not.toContain("<select");
    expect(header).not.toContain('title={t("Add files")}');
    expect(AUTOMATION_SOURCE).toContain("<AutomationInputSelector");
  });

  it("returns to the project tab the automation was opened from (#199)", () => {
    // The back button must never rebuild the query from scratch again: doing so
    // dropped the tab and always landed on Overview.
    expect(AUTOMATION_SOURCE).toContain("onClick={() => setParams(projectReturnParams(projectId, origin))}");
    expect(AUTOMATION_SOURCE).toContain('const origin = automationOrigin(params.get("from"));');
    expect(AUTOMATION_SOURCE).toContain('onOpenAutomation={(id) => setParams(automationParams(projectId, id, automationOrigin(params.get("view"))))}');
    // Every rewrite of the automation's own URL keeps the origin, so a tab
    // switch or a started run cannot strip it before the person heads back.
    expect(AUTOMATION_SOURCE.match(/setParams\(\{[^}]*automation: automationId/g)).toBeNull();
  });

  it("uses automation-scoped output data and keeps the human gate reachable", () => {
    expect(AUTOMATION_SOURCE).toContain("api.automationContents(automationId)");
    expect(AUTOMATION_SOURCE).toContain("<ApprovalCard");
    expect(AUTOMATION_SOURCE).toContain("pendingQuestion?.human_prompt");
  });

  it("passes the selected source into the advanced planner (#190)", () => {
    expect(AUTOMATION_SOURCE).toContain("<PipelineBuilder runId={runId} sourceId={sourceId}");
  });

  it("carries the chosen ML target into the guided run (#244/#198)", () => {
    // The guided run used to pass only the run mode, so a chosen target never
    // reached the pipeline. GuidedPipeline's onRun now hands back the column,
    // and runAcceptedWorkflow forwards it to startStaged as target_column.
    expect(AUTOMATION_SOURCE).toContain("onRun={(runMode, target, problemKind, checkpointStages) => void runAcceptedWorkflow(runMode, target, problemKind, checkpointStages)}");
    expect(AUTOMATION_SOURCE).toContain("targetColumn ? { target_column: targetColumn } : {}");
  });

  it("forwards a quick-picked problem kind as problem_selection, skipping the planner (#241)", () => {
    expect(AUTOMATION_SOURCE).toContain('problemKind: "predict_column" | "flag_anomalies" | null = null');
    expect(AUTOMATION_SOURCE).toContain("problemKind ? { problem_selection: { kind: problemKind, target_column: targetColumn } } : {}");
  });
});

describe("reaching notifications from inside a project (#286)", () => {
  it("keeps the bell on every page inside a project", () => {
    // `Shell` suppresses its whole top bar -- bell included -- for any URL
    // carrying `?project=`, which is every page inside a project. A run that
    // finished while someone was in the editor could not be seen or dismissed
    // without navigating out first.
    for (const source of [AUTOMATION_SOURCE, PROJECT_SOURCE]) {
      expect(source).toContain('import { Notifications } from "../components/Notifications"');
      expect(source).toContain("<LanguagePicker /><Notifications />");
    }
  });

  it("composes the shell's own component rather than a second bell", () => {
    // Two implementations would mean two seen-state readers of the same
    // localStorage key, drifting the moment one of them changed.
    expect(SHELL_SOURCE).toContain('import { Notifications } from "./Notifications"');
    expect(SHELL_SOURCE).toContain("<Notifications />");
    for (const source of [AUTOMATION_SOURCE, PROJECT_SOURCE]) {
      expect(source).not.toContain("localStorage.getItem(\"ads.notifications");
    }
  });

  it("still suppresses the shared top bar inside a project", () => {
    // The contextual header replaces it; this is why the bell had to be
    // composed in rather than un-guarded in the shell.
    expect(SHELL_SOURCE).toContain("const isAutomationEditor = location.pathname.startsWith(\"/projects\")");
    expect(SHELL_SOURCE).toContain("{!isAutomationEditor && <header");
  });
});

describe("clearing the automation name is a cancelled edit (#425)", () => {
  it("sends nothing for an empty field and puts the saved name back", () => {
    // `"" !== automation.name`, so the old guard fell through and PUT
    // `{ name: "" }`. That fails `min_length=1` on the contract, so the reader
    // got a stringified pydantic error and the box was left blank -- hiding
    // the automation's real name until a reload, while every further click
    // away fired the same failing request again.
    expect(AUTOMATION_SOURCE).toContain("const trimmed = name.trim();");
    expect(AUTOMATION_SOURCE).toContain(
      "if (!automation || !trimmed || trimmed === automation.name) { setName(automation?.name ?? name); return; }",
    );
  });

  it("restores the saved name when the server rejects a rename", () => {
    // The catch used to leave `name` as typed, so the header kept showing a
    // name the automation does not have.
    expect(AUTOMATION_SOURCE).toContain("catch (caught) { setError(messageOf(caught)); setName(automation.name); }");
  });

  it("matches the sibling screen, which already had both guards", () => {
    // `ProjectWorkspace.persistName` is the same function; this bug was the
    // two pieces it has and this one did not.
    expect(PROJECT_SOURCE).toContain("if (!project || !trimmed || trimmed === project.name)");
    expect(PROJECT_SOURCE).toContain("setError(messageOf(caught)); setName(project.name);");
  });
});


describe("opening a past execution in the workspace (#443)", () => {
  /** The body of `openExecution`, so a match cannot come from elsewhere. */
  function openBody(): string {
    const start = AUTOMATION_SOURCE.indexOf("async function openExecution(");
    expect(start).toBeGreaterThan(-1);
    return AUTOMATION_SOURCE.slice(start, AUTOMATION_SOURCE.indexOf("async function deleteExecution("));
  }

  it("gives a completed run an action at all", () => {
    // The detail panel's row was `{active && Pause}{retryable && Retry}`, and a
    // completed run is neither -- so the most common case in the list rendered
    // an empty action row. Every run has this one.
    expect(AUTOMATION_SOURCE).toContain('onClick={() => onOpen(selected.run_id)}>{t("Open in the Editor")}');
    const panel = AUTOMATION_SOURCE.slice(AUTOMATION_SOURCE.indexOf('t("Selected execution")'));
    expect(panel.indexOf('t("Open in the Editor")')).toBeLessThan(panel.indexOf('t("Pause after current stage")'));
  });

  it("actually switches the workspace instead of only rewriting the URL", () => {
    // The effect that resolves `params.get("run")` into a loaded run is keyed
    // on `[automationId, sourceId, automation?.automation_id]`, so it does not
    // re-run when the run param changes. Writing the param alone would change
    // the link and nothing on screen.
    const body = openBody();
    expect(body).toContain("api.runProgress(targetRunId)");
    expect(body).toContain("api.stagingWorkspace(targetRunId)");
    expect(body).toContain("setRunId(targetRunId);");
    expect(body).toContain("setWorkspace(saved);");
    expect(body).toContain('setActiveView("editor");');
  });

  it("keeps #68's deep-link contract, writing the run param the same way", () => {
    // An external `?view=runs&run=<id>` link and this button have to agree on
    // which run the workspace is showing.
    expect(openBody()).toContain(
      'setParams({ ...automationParams(projectId, automationId, origin), run: targetRunId }, { replace: true });',
    );
  });

  it("does not leave the previous run's state on the opened one", () => {
    // The banner, the gate card and the advanced graph all belong to the run
    // being left; carried over, they describe a run that is no longer shown.
    const body = openBody();
    expect(body).toContain("setError(runErrorText(progress?.error) || null);");
    expect(body).toContain("setPendingQuestion(");
    expect(body).toContain("setAdvancedGraph(false);");
  });

  it("resolves both fetches before touching state, so the graph never flashes", () => {
    // Clearing first and filling in after would show an empty canvas labelled
    // with the new run's id for as long as the round trip takes.
    const body = openBody();
    expect(body).toContain("const [progress, saved] = await Promise.all([");
    expect(body.indexOf("Promise.all([")).toBeLessThan(body.indexOf("setRunId(targetRunId);"));
  });

  it("falls back to the row's own status when the run cannot be re-read", () => {
    // A deleted or unreachable run should still open to whatever the history
    // list already knows, rather than to a blank status.
    expect(openBody()).toContain('setRunStatus(String(progress?.status ?? chosen?.status ?? ""));');
  });

  it("leaves the row click meaning what it meant", () => {
    // Reading a run's facts and switching the whole workspace to it are
    // different intentions; one click cannot mean both. The list gets its own
    // affordance beside Delete instead.
    expect(AUTOMATION_SOURCE).toContain("onClick={() => setSelected(item)}");
    expect(AUTOMATION_SOURCE).toContain('aria-label={t("Open this execution in the Editor")}');
    expect(AUTOMATION_SOURCE).toContain("onClick={() => onOpen(item.run_id)}");
  });

  it("stops telling the reader to do something the panel cannot do", () => {
    // The footer said "select a completed node in the Editor" while giving no
    // way to point the Editor at the run being described.
    expect(AUTOMATION_SOURCE).not.toContain("Select a completed node in the Editor");
    expect(CATALOGUE).toContain(`"Open in the Editor": "Düzenleyici'de aç"`);
    expect(CATALOGUE).toContain('"Open this execution in the Editor"');
  });
});
