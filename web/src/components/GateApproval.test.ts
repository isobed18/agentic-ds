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
    // "approve" is an option_id the card knows, so it now renders the Turkish
    // label rather than whatever the server called it (#192). The other two
    // ids here are not ones the server actually sends -- it sends "retry" and
    // "abort" -- so they exercise the fallback and still show their own text.
    expect(markup).toContain("Onayla ve devam et");
    expect(markup).toContain("SEND_BACK_FOR_REWORK");
    expect(markup).toContain("STOP_THE_RUN");
    // Every option is a real, enabled button wired to answer the gate.
    expect((markup.match(/<button/g) ?? []).length).toBe(3);
  });
});


describe("the gate decision card in Turkish (#192)", () => {
  function render(prompt: Partial<GateDecision["human_prompt"]> & object, attempt = 1) {
    const decision = {
      stage_id: "evaluation",
      attempt,
      verdict: "escalate",
      reason_code: "stage_risk_requires_review",
      triggered_rules: [],
      human_prompt: { stage_id: "evaluation", question: "RAW", context_summary: "", options: [], ...prompt },
    } as unknown as GateDecision;
    return renderToStaticMarkup(
      createElement(ApprovalCard, { runId: "run-x", decision, onAnswered: () => undefined }),
    );
  }

  it("translates the options the server actually sends", () => {
    // These four ids are the ones ads/gates/evaluator.py emits. Their English
    // wording is built inside the run worker, where no request language
    // exists, so the card has to translate from the id.
    const markup = render({
      options: [
        { option_id: "approve", label: "EN_APPROVE", consequence: "EN_C1" },
        { option_id: "retry", label: "EN_RETRY", consequence: "EN_C2", downstream_effect: "EN_D" },
        { option_id: "abort", label: "EN_ABORT", consequence: "EN_C3" },
        { option_id: "review_first", label: "EN_REVIEW", consequence: "EN_C4" },
      ],
    });
    expect(markup).toContain("Onayla ve devam et");
    expect(markup).toContain("Yeniden çalışması için geri gönder");
    expect(markup).toContain("Koşumu durdur");
    expect(markup).toContain("Karar vermeden önce artifact"); // kesme isareti markup'ta kaciriliyor
    for (const english of ["EN_APPROVE", "EN_RETRY", "EN_ABORT", "EN_REVIEW", "EN_C1"]) {
      expect(markup).not.toContain(english);
    }
  });

  it("puts the stage name into the reworded downstream warning", () => {
    const markup = render({
      stage_id: "schema_discovery",
      options: [{ option_id: "retry", label: "x", consequence: "y", downstream_effect: "EN_DOWNSTREAM" }],
    });
    expect(markup).toContain("schema_discovery aşamasını ve sonrasındaki her şeyi");
    expect(markup).not.toContain("EN_DOWNSTREAM");
  });

  it("keeps the server's text for an option it does not know", () => {
    // An option added on the server must still render. Without the fallback
    // this card would go blank the moment the two sides drift.
    const markup = render({ options: [{ option_id: "brand_new", label: "SERVER_SAYS", consequence: "SERVER_WHY" }] });
    expect(markup).toContain("SERVER_SAYS");
    expect(markup).toContain("SERVER_WHY");
  });

  it("marks a re-escalation as a new question rather than a card that came back (#191)", () => {
    // A rework can legitimately escalate the same stage again, and the card
    // reappearing looked exactly like the stale one that used to return every
    // 2.2 seconds. The attempt is what tells a real loop from a stale card.
    expect(render({}, 3)).toContain("3. deneme");
    // The first escalation has nothing to disambiguate, so it stays quiet.
    expect(render({}, 1)).not.toContain("deneme");
  });

  it("renders the question from question_kind, and falls back to the raw text", () => {
    expect(render({ question_kind: "checkpoint" })).toContain("zorunlu bir kontrol noktası");
    expect(render({ question_kind: "problem" })).toContain("bir sorun saptandığı için durdu");
    // An older server that does not send the field at all still shows its question.
    expect(render({ question: "ESKI_SUNUCU_SORUSU" })).toContain("ESKI_SUNUCU_SORUSU");
  });
});

describe("the gate card's per-rule detail (#259)", () => {
  it("translates the boilerplate and each reason when the server sends reason_codes", () => {
    const decision = {
      stage_id: "schema_discovery",
      attempt: 3,
      verdict: "escalate",
      reason_code: "retry_budget_exhausted",
      triggered_rules: ["retry_budget_exhausted", "repeated_identical_failure"],
      human_prompt: {
        stage_id: "schema_discovery",
        question: "RAW",
        question_kind: "problem",
        context_summary: "SHOULD_NOT_RENDER_RAW_ENGLISH_BLOB",
        context_note: "Load and deterministically profile every source table.",
        reason_codes: ["retry_budget_exhausted", "repeated_identical_failure"],
        options: [],
      },
    } as unknown as GateDecision;

    const markup = renderToStaticMarkup(
      createElement(ApprovalCard, { runId: "run-x", decision, onAnswered: () => undefined }),
    );

    expect(markup).toContain("Neden durdu:");
    expect(markup).toContain("Deneme hakkı kalmadı");
    expect(markup).toContain("Aynı hata tekrarlandı");
    expect(markup).not.toContain("SHOULD_NOT_RENDER_RAW_ENGLISH_BLOB");
  });

  it("falls back to the raw context_summary when reason_codes is absent (an older run)", () => {
    const decision = {
      stage_id: "schema_discovery",
      attempt: 1,
      verdict: "escalate",
      reason_code: "retry_budget_exhausted",
      triggered_rules: [],
      human_prompt: {
        stage_id: "schema_discovery",
        question: "RAW",
        context_summary: "OLD_RUN_RAW_ENGLISH_CONTEXT",
        options: [],
      },
    } as unknown as GateDecision;

    const markup = renderToStaticMarkup(
      createElement(ApprovalCard, { runId: "run-x", decision, onAnswered: () => undefined }),
    );

    expect(markup).toContain("OLD_RUN_RAW_ENGLISH_CONTEXT");
    expect(markup).not.toContain("Neden durdu:");
  });
});

describe("the leakage gate offers columns to drop instead of free text (#262)", () => {
  it("renders a default-checked checkbox per suspect column, labelled by kind", () => {
    const decision = {
      stage_id: "leakage_audit",
      attempt: 3,
      verdict: "escalate",
      reason_code: "leakage_unresolved",
      triggered_rules: ["leakage_unresolved"],
      human_prompt: {
        stage_id: "leakage_audit",
        question: "RAW",
        context_summary: "",
        leakage_suspect_columns: ["total_comp_ytd", "followup_missing"],
        leakage_target_columns: ["total_comp_ytd"],
        options: [{ option_id: "retry", label: "Send back", consequence: "c" }],
      },
    } as unknown as GateDecision;

    const markup = renderToStaticMarkup(
      createElement(ApprovalCard, { runId: "run-x", decision, onAnswered: () => undefined }),
    );

    expect(markup).toContain("Çıkarılacak sütunlar");
    expect(markup).toContain("total_comp_ytd");
    expect(markup).toContain("followup_missing");
    expect(markup).toContain("hedef sızıntısı");
    expect(markup).toContain("engelleyici sızıntı");
    // Both boxes start checked, matching what the auto-retry would have dropped.
    expect((markup.match(/type="checkbox"/g) ?? []).length).toBe(2);
    expect((markup.match(/checked=""/g) ?? []).length).toBe(2);
  });

  it("shows no drop-columns section for an escalation without suspect columns", () => {
    const decision = {
      stage_id: "evaluation",
      attempt: 1,
      verdict: "escalate",
      reason_code: "model_below_baseline",
      triggered_rules: [],
      human_prompt: {
        stage_id: "evaluation",
        question: "RAW",
        context_summary: "",
        options: [{ option_id: "retry", label: "Send back", consequence: "c" }],
      },
    } as unknown as GateDecision;

    const markup = renderToStaticMarkup(
      createElement(ApprovalCard, { runId: "run-x", decision, onAnswered: () => undefined }),
    );

    expect(markup).not.toContain("Çıkarılacak sütunlar");
    expect(markup).not.toContain('type="checkbox"');
  });
});

describe("a gate on a stage that produced nothing (#198)", () => {
  it("explains why approve is missing instead of just dropping it", () => {
    const decision = {
      stage_id: "problem_discovery",
      attempt: 1,
      verdict: "escalate",
      reason_code: "stage_risk_requires_review",
      triggered_rules: [],
      human_prompt: {
        stage_id: "problem_discovery",
        question: "RAW",
        question_kind: "no_output",
        context_summary: "",
        options: [
          { option_id: "retry", label: "x", consequence: "y" },
          { option_id: "abort", label: "z", consequence: "w" },
        ],
      },
    } as unknown as GateDecision;

    const markup = renderToStaticMarkup(
      createElement(ApprovalCard, { runId: "run-x", decision, onAnswered: () => undefined }),
    );

    expect(markup).toContain("problem_discovery aşaması hiçbir şey üretmedi");
    expect(markup).toContain("Yeniden çalışması için geri gönder");
    expect(markup).not.toContain("Onayla ve devam et");
  });
});
