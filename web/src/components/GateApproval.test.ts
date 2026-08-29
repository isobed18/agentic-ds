import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ApprovalCard } from "./GateApproval";
import type { GateDecision } from "../lib/api";

Object.defineProperty(globalThis, "localStorage", {
  value: { getItem: () => null },
  configurable: true,
});

describe("the human gate escalation card (#81)", () => {
  it("renders the question, reasoning and decision options a stuck run needs", () => {
    // Before this, the only component that rendered a gate escalation lived in
    // the unrouted Workflows page, so an `awaiting_human` run had no reachable
    // way to be answered. The card itself must at least render its content.
    const decision: GateDecision = {
      stage_id: "schema_discovery",
      attempt: 3,
      verdict: "escalate",
      reason_code: "retry_budget_exhausted",
      triggered_rules: ["repeated_identical_failure"],
      human_prompt: {
        stage_id: "schema_discovery",
        question: "SCHEMA_KEEPS_FAILING_QUESTION",
        context_summary: "THREE_IDENTICAL_FAILURES_CONTEXT",
        options: [
          { option_id: "approve", label: "APPROVE_ANYWAY", consequence: "continue", recommended: true },
          { option_id: "rework", label: "SEND_BACK_FOR_REWORK", consequence: "retry" },
          { option_id: "stop", label: "STOP_THE_RUN", consequence: "halt" },
        ],
      },
    };

    const markup = renderToStaticMarkup(
      createElement(ApprovalCard, { runId: "run-x", decision, onAnswered: () => undefined }),
    );

    expect(markup).toContain("SCHEMA_KEEPS_FAILING_QUESTION");
    expect(markup).toContain("THREE_IDENTICAL_FAILURES_CONTEXT");
    expect(markup).toContain("APPROVE_ANYWAY");
    expect(markup).toContain("SEND_BACK_FOR_REWORK");
    expect(markup).toContain("STOP_THE_RUN");
    // Every option is a real, enabled button wired to answer the gate.
    expect((markup.match(/<button/g) ?? []).length).toBe(3);
  });
});
