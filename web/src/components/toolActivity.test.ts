import { describe, expect, it } from "vitest";

import CATALOGUE from "../lib/i18n.ts?raw";
import PANEL_SOURCE from "./PlannerPanel.tsx?raw";
import type { ToolActivityEvent } from "../lib/api";
// The catalogue defaults to Turkish, so ask for English here and assert the
// English wording; the Turkish half is asserted against the catalogue itself
// below. The import is dynamic because `t` binds the language at module load.
Object.defineProperty(globalThis, "localStorage", {
  value: { getItem: () => "en" },
  configurable: true,
});

const { agentLabel, mergeToolActivity, toolActivityLine, toolLabel } = await import("./toolActivity");

function event(overrides: Partial<ToolActivityEvent> = {}): ToolActivityEvent {
  return {
    seq: 1,
    agent: "eda_investigator",
    tool: "profile_table",
    decision: "allowed",
    at: "2026-09-03T05:00:00Z",
    ...overrides,
  };
}

describe("the live tool line (#411)", () => {
  it("names the agent and the tool", () => {
    expect(toolActivityLine(event())).toBe("Analysis agent used the profile table tool");
  });

  it("says when a call was refused or failed", () => {
    // Hiding these would be the wrong kind of quiet: "the agent tried X and
    // was not allowed to" is what somebody watching a stalled run needs.
    expect(toolActivityLine(event({ decision: "denied" }))).toContain("was not allowed to use");
    expect(toolActivityLine(event({ decision: "failed" }))).toContain("failed");
  });

  it("falls back to a readable id rather than dropping an unmapped agent", () => {
    // A tool or agent added later should show up wrong-but-readable, not
    // vanish from the feed until someone remembers to translate it.
    expect(agentLabel("brand_new_investigator")).toBe("brand new investigator");
    expect(toolLabel("sample_rows")).toBe("sample rows");
  });
});

describe("merging polled tool events (#411)", () => {
  it("returns only what has not been shown yet", () => {
    const first = mergeToolActivity([], [event({ seq: 1 }), event({ seq: 2 })]);
    expect(first.added.map((item) => item.seq)).toEqual([1, 2]);

    // The cursor only advances after a successful append, so a failed request
    // repeats a range rather than skipping it. Repeating must be harmless.
    const again = mergeToolActivity(first.events, [event({ seq: 2 }), event({ seq: 3 })]);
    expect(again.added.map((item) => item.seq)).toEqual([3]);
    expect(again.events.map((item) => item.seq)).toEqual([1, 2, 3]);
  });

  it("bounds the transcript on a long run", () => {
    const many = Array.from({ length: 10 }, (_, index) => event({ seq: index + 1 }));
    const merged = mergeToolActivity([], many, 4);
    expect(merged.events.map((item) => item.seq)).toEqual([7, 8, 9, 10]);
    // What was actually appended this round is unaffected by the cap; the cap
    // is bookkeeping, not a decision about what a person just watched happen.
    expect(merged.added).toHaveLength(10);
  });
});

describe("the planner panel shows tool use as it happens (#411)", () => {
  it("carries a third kind of line that is not a chat turn", () => {
    expect(PANEL_SOURCE).toContain('interface Message { role: "assistant" | "user" | "tool"');
    // Light italic status text, not a speaker and a bubble.
    expect(PANEL_SOURCE).toContain('className="px-1 text-3xs italic leading-relaxed text-ink-faint"');
  });

  it("polls the feed with a cursor and stops when the run is done", () => {
    expect(PANEL_SOURCE).toContain("await api.toolActivity(runId, cursor)");
    expect(PANEL_SOURCE).toContain("keepGoing = feed.active");
    expect(PANEL_SOURCE).toContain("if (!cancelled && keepGoing) timer = setTimeout");
    // A blip is not a reason to stop narrating a run that is still going.
    expect(PANEL_SOURCE).toContain("keepGoing = true;");
  });

  it("keeps the live lines when the saved transcript is loaded", () => {
    // `chat_history` is the durable half and does not contain these, so a
    // reload of it used to be able to wipe what had just been shown.
    expect(PANEL_SOURCE).toContain(
      'setMessages((current) => [...saved, ...current.filter((item) => item.role === "tool")]);',
    );
  });

  it("translates every line and every agent name", () => {
    expect(CATALOGUE).toContain('"{agent} used the {tool} tool": "{agent} {tool} aracını kullandı"');
    expect(CATALOGUE).toContain('"{agent} was not allowed to use the {tool} tool":');
    expect(CATALOGUE).toContain('"{agent}\'s {tool} tool call failed":');
    expect(CATALOGUE).toContain('"Analysis agent": "Analiz agent\'ı"');
    expect(CATALOGUE).toContain('"Validation agent": "Doğrulama agent\'ı"');
  });
});
