import { describe, expect, it } from "vitest";

import CATALOGUE from "../lib/i18n.ts?raw";
import WORKSPACE_SOURCE from "../pages/AutomationWorkspace.tsx?raw";
import SOURCE from "./GuidedPipeline.tsx?raw";

describe("review checkpoints reach the run (#447)", () => {
  it("sends the checkpoints the canvas is showing", () => {
    // `onRun` took three arguments and none of them was the checkpoint set, so
    // the set drawn on the canvas and the set the engine ran with were the same
    // only by coincidence -- and nothing on screen could change either.
    expect(SOURCE).toContain("checkpointStages: string[],");
    expect(SOURCE).toContain(
      'onRun(approveEachStage ? "manual" : "fully_auto", targetColumn || null, problemKind === "ask_planner" ? null : problemKind, [...checkpointSet])',
    );
  });

  it("carries them through the page into the start request", () => {
    // The middle of the chain: `runAcceptedWorkflow` dropped them on the floor,
    // so `supervision.checkpoint_stages` was never in the request body.
    expect(WORKSPACE_SOURCE).toContain("checkpointStages: string[] | null = null");
    expect(WORKSPACE_SOURCE).toContain(
      "...(checkpointStages ? { supervision: { checkpoint_stages: checkpointStages } } : {})",
    );
    expect(WORKSPACE_SOURCE).toContain(
      "onRun={(runMode, target, problemKind, checkpointStages) => void runAcceptedWorkflow(runMode, target, problemKind, checkpointStages)}",
    );
  });

  it("follows the plan until somebody decides otherwise", () => {
    // `null` is not "no checkpoints" -- it means the plan's, so a later planner
    // turn that adds one is still honoured. Only an actual toggle pins a set.
    expect(SOURCE).toContain("useState<string[] | null>(null)");
    expect(SOURCE).toContain("new Set(chosenCheckpoints ?? planCheckpoints)");
  });

  it("lets a stage be toggled, both ways", () => {
    // Asking the planner was the only way to add one, and there was no way at
    // all to remove one.
    expect(SOURCE).toContain("function toggleCheckpoint(stage: string)");
    expect(SOURCE).toContain("if (next.has(stage)) next.delete(stage);");
    expect(SOURCE).toContain("else next.add(stage);");
  });

  it("offers the control on a stage that has not run, which is when it matters", () => {
    // "Not started yet" was the entire panel for an unstarted group, so the one
    // moment when setting a checkpoint is still useful was the moment the panel
    // offered nothing. The stage list comes from the group, not from the run,
    // so it exists before any node does.
    const empty = SOURCE.slice(SOURCE.indexOf('<Empty title={t("Not started yet")}'));
    expect(empty).toContain("{canStart && selectedGroup && <div");
    expect(empty).toContain("selectedGroup.stages.map((stage) => <CheckpointToggle");
  });

  it("says which stage, not just that the group has one", () => {
    // The group node's "Human approval" badge says a group has a checkpoint
    // somewhere in it; a group can hold three stages.
    expect(SOURCE).toContain("function CheckpointToggle(");
    expect(SOURCE).toContain("{stageName(stage)}");
    expect(SOURCE).toContain('{checked ? t("Pauses for review") : t("Runs through")}');
    expect(CATALOGUE).toContain('"Pauses for review": "İnceleme için duraklar"');
    expect(CATALOGUE).toContain('"Runs through": "Durmadan geçer"');
  });

  it("does not pretend the gate can change once the run has it", () => {
    // Mid-run the engine has already been handed its policy, so a control that
    // appeared to change it would be lying.
    expect(SOURCE).toContain("canSetCheckpoint={canStart}");
    expect(SOURCE).toContain("{canSetCheckpoint && <div");
  });
});
