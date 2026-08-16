/**
 * Centre workspace for one stage.
 *
 * The backend already attaches a presentation layer to every artifact — a
 * `story` with a headline, an explanation, facts, and whichever measured
 * structures that stage produced. This component renders whatever is present
 * rather than switching on stage id, so a stage that starts emitting a model
 * comparison gets a comparison table without a change here.
 *
 * Everything below the facts row is collapsed by default: the brief asks for
 * progressive disclosure, and only the summary and an open approval request
 * earn screen space unasked.
 */
import { useEffect, useState } from "react";
import {
  api,
  type FactValue,
  type GateDecision,
  type StageDetail,
  type StageOutput,
  type Story,
  type WorkflowNode,
} from "../lib/api";
import { statusLabel } from "../lib/status";
import { AnalysisStrip, type AnalysisPanel } from "./AnalysisStrip";
import { NeedsAttention, type AttentionItem } from "./NeedsAttention";
import { SchemaMap } from "./SchemaMap";
import { titleize } from "./PipelineRail";
import { Badge, DataTable, Disclosure, Empty, Metric, Spinner, cx, toneFor } from "./ui";

/** Long floats are measurements, not identifiers — show them at human precision. */
function fmt(v: FactValue | null | undefined): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "number") {
    if (Number.isInteger(v)) return v.toLocaleString();
    return Math.abs(v) >= 1000
      ? v.toLocaleString(undefined, { maximumFractionDigits: 1 })
      : v.toFixed(4);
  }
  return v;
}

export function StageWorkspace({
  runId, node, onAnswered,
}: { runId: string | null; node: WorkflowNode | null; onAnswered: () => void }) {
  const [detail, setDetail] = useState<StageDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!runId || !node) { setDetail(null); return; }
    let cancelled = false;
    setLoading(true);
    setError(null);
    api.stage(runId, node.id)
      .then((d) => { if (!cancelled) setDetail(d); })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [runId, node?.id, node?.status, node?.attempt_count]);

  if (!node) {
    return <div className="p-6"><Empty title="Select a stage" hint="Pick a node in the pipeline above to see its workspace." /></div>;
  }

  const outputs = detail?.outputs ?? [];
  const attention = collectAttention(detail);
  // The gate that is actually waiting on a person, if any.
  const openGate = detail?.gate_decisions.find((g) => g.human_prompt) ?? null;
  const needsHuman = detail?.human_view?.needs_human;

  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
      <header className="mb-4">
        <div className="flex flex-wrap items-center gap-2.5">
          <h2 className="text-xl font-semibold tracking-tight">{titleize(node.id)}</h2>
          <Badge tone={toneFor(node.status)}>{statusLabel(node.status)}</Badge>
          <Badge tone={node.kind === "deterministic" ? "neutral" : "brand"}>
            {node.kind === "deterministic" ? "Deterministic" : "Agent"}
          </Badge>
          {node.attempt_count > 1 && <Badge tone="warn">{node.attempt_count} attempts</Badge>}
        </div>
        <p className="mt-1 max-w-3xl text-sm text-ink-mute">
          {detail?.stage?.description ?? node.description}
        </p>
      </header>

      {loading && !detail && <Spinner label="Loading stage…" />}
      {error && <p className="card border-stop-500/30 bg-stop-50 px-4 py-3 text-sm text-stop-700">{error}</p>}

      {needsHuman && detail?.human_view?.state_label && !openGate && (
        <p className="card mb-4 border-l-4 border-l-warn-500 px-4 py-3 text-sm text-ink-soft">
          {detail.human_view.state_label}
        </p>
      )}

      {openGate && runId && (
        <ApprovalCard runId={runId} decision={openGate} onAnswered={onAnswered} />
      )}

      {attention.length > 0 && (
        <div className="mb-4">
          <NeedsAttention items={attention} />
        </div>
      )}

      {detail?.panels && detail.panels.length > 0 && (
        <AnalysisStrip panels={detail.panels as AnalysisPanel[]} />
      )}

      {outputs.map((o) => <OutputBlock key={o.artifact_id} output={o} />)}

      {detail && detail.attempts.length > 0 && (
        <Disclosure title="Attempts" count={detail.attempts.length}>
          <DataTable
            columns={["#", "Verdict", "Started", "Ended", "Artifacts"]}
            rows={detail.attempts.map((a) => [
              a.attempt,
              a.verdict ?? (a.error ? "error" : "—"),
              (a.started_at ?? "").replace("T", " ").slice(0, 19),
              (a.ended_at ?? "—").replace("T", " ").slice(0, 19),
              a.artifact_ids.length,
            ])}
          />
        </Disclosure>
      )}

      {detail && detail.gate_decisions.length > 0 && (
        <Disclosure title="Gate decisions" count={detail.gate_decisions.length}>
          <ul className="space-y-1.5">
            {detail.gate_decisions.map((g, i) => (
              <li key={i} className="flex flex-wrap items-center gap-2 rounded-lg border border-line bg-surface-sunken px-3 py-2 text-sm">
                <Badge tone={toneFor(g.verdict)}>{g.verdict}</Badge>
                <code className="font-mono text-[11px] text-ink-faint">{g.reason_code}</code>
                <span className="text-xs text-ink-mute">attempt {g.attempt}</span>
                {g.triggered_rules.length > 0 && (
                  <span className="w-full font-mono text-[11px] text-ink-faint">
                    {g.triggered_rules.join(", ")}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </Disclosure>
      )}

      {outputs.length > 0 && (
        <Disclosure title="Artifacts" count={outputs.length}>
          <ul className="space-y-1.5">
            {outputs.map((a) => (
              <li key={a.artifact_id} className="flex items-center gap-2 text-sm">
                <Badge>{a.type}</Badge>
                <span className="truncate text-ink-soft">{a.name}</span>
                <code className="ml-auto shrink-0 font-mono text-[11px] text-ink-faint">
                  {a.artifact_id.slice(0, 16)}
                </code>
              </li>
            ))}
          </ul>
        </Disclosure>
      )}

      {!loading && !error && outputs.length === 0 && (
        <Empty
          title={node.status === "pending" ? "This stage has not run yet" : "No output recorded"}
          hint={node.status === "pending" ? "Start a run to populate this workspace." : undefined}
        />
      )}
    </div>
  );
}

/** One artifact, rendered from its story. */
function OutputBlock({ output }: { output: StageOutput }) {
  const s: Story = output.story ?? {};
  const has = (k: keyof Story) => {
    const v = s[k];
    if (Array.isArray(v)) return v.length > 0;
    if (v && typeof v === "object") return Object.keys(v).length > 0;
    return v !== undefined && v !== null && v !== "";
  };

  return (
    <section className="mb-5">
      {(s.headline || s.explanation) && (
        <div className="mb-3">
          <h3 className="text-base font-semibold text-ink">{s.headline ?? output.name}</h3>
          {s.explanation && <p className="mt-0.5 max-w-3xl text-sm text-ink-mute">{s.explanation}</p>}
        </div>
      )}

      {has("facts") && (
        <div className="mb-3 flex flex-wrap gap-2">
          {s.facts!.map((f) => <Metric key={f.label} label={f.label} value={fmt(f.value)} />)}
        </div>
      )}

      {s.suggestion && (
        <div className="card mb-3 flex items-start gap-3 border-brand-200 bg-brand-50 px-4 py-3">
          <svg viewBox="0 0 20 20" className="mt-0.5 h-4 w-4 shrink-0 text-brand-600" fill="none" stroke="currentColor" strokeWidth="1.7">
            <path d="M10 2.5 11.6 7 16 8.5 11.6 10 10 14.5 8.4 10 4 8.5 8.4 7z" strokeLinejoin="round" />
          </svg>
          <p className="text-sm leading-relaxed text-ink-soft">
            <span className="font-semibold text-brand-700">Recommendation: </span>{s.suggestion}
          </p>
        </div>
      )}


      {has("choices") && (
        <Disclosure title="Candidate problems" count={s.choices!.length} defaultOpen>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {s.choices!.map((c, i) => (
              <article key={i} className={cx("card px-4 py-3", i === 0 && "border-brand-500 ring-2 ring-brand-100")}>
                <div className="mb-1 flex items-start justify-between gap-2">
                  <h4 className="text-sm font-semibold leading-tight">{c.title}</h4>
                  {c.viable === false && <Badge tone="stop">not viable</Badge>}
                  {i === 0 && c.viable !== false && <Badge tone="brand">selected</Badge>}
                </div>
                <dl className="mt-2 grid grid-cols-3 gap-2 border-t border-line-soft pt-2">
                  <Pair label="Target" value={c.target ?? "—"} />
                  <Pair label="Task" value={(c.task ?? "—").replace(/_/g, " ")} />
                  <Pair label="Metric" value={c.metric ?? "—"} />
                </dl>
                {c.rationale && <p className="mt-2 text-xs leading-relaxed text-ink-mute">{c.rationale}</p>}
              </article>
            ))}
          </div>
        </Disclosure>
      )}

      {has("model_comparison") && (
        <Disclosure title="Model comparison" count={s.model_comparison!.length} defaultOpen>
          <DataTable
            columns={["Candidate", "Role", "CV mean", "CV std", "Holdout"]}
            rows={s.model_comparison!.map((m) => [
              m.selected ? `${m.candidate}  ★` : m.candidate,
              m.baseline ? "baseline" : m.selected ? "selected" : "candidate",
              fmt(m.cv_mean),
              fmt(m.cv_std),
              fmt(m.holdout),
            ])}
          />
        </Disclosure>
      )}

      {has("holdout_metrics") && (
        <div className="mb-3 flex flex-wrap gap-2">
          {s.holdout_metrics!.map((m) => (
            <Metric key={m.metric} label={`Holdout ${m.metric}`} value={fmt(m.score)} />
          ))}
        </div>
      )}

      {has("panels") && <AnalysisStrip panels={s.panels as AnalysisPanel[]} />}

      {s.schema_graph && s.schema_graph.edges.length > 0 && (
        <Disclosure title="Relationship map" count={s.schema_graph.edges.length} defaultOpen>
          <SchemaMap graph={s.schema_graph} />
        </Disclosure>
      )}

      {has("criteria") && (
        <Disclosure title="Coverage checks" count={Object.keys(s.criteria!).length}>
          <ul className="space-y-1.5">
            {Object.entries(s.criteria!).map(([label, ok]) => (
              <li key={label} className="flex items-center gap-2 text-sm text-ink-soft">
                <span className={cx("grid h-4 w-4 place-items-center rounded-full text-[10px] font-bold text-white", ok ? "bg-ok-500" : "bg-stop-500")}>
                  {ok ? "✓" : "!"}
                </span>
                {label}
              </li>
            ))}
          </ul>
        </Disclosure>
      )}

      {has("history_alerts") && (
        <Disclosure title="Decision history" count={s.history_alerts!.length}>
          <ul className="space-y-1.5">
            {s.history_alerts!.map((a, i) => (
              <li key={i} className="flex items-start gap-2 rounded-lg border border-line bg-surface-sunken px-3 py-2 text-sm text-ink-soft">
                <Badge tone={a.resolved ? "ok" : "warn"}>{a.resolved ? "resolved" : "open"}</Badge>
                <span>{a.detail}</span>
              </li>
            ))}
          </ul>
        </Disclosure>
      )}

      {has("findings") && (
        <Disclosure title="Findings" count={s.findings!.length}>
          <pre className="overflow-x-auto rounded-lg bg-surface-sunken p-3 text-[11px] leading-relaxed text-ink-soft">
            {JSON.stringify(s.findings, null, 2)}
          </pre>
        </Disclosure>
      )}

      {has("excluded_columns") && (
        <Disclosure title="Excluded columns" count={s.excluded_columns!.length}>
          <div className="flex flex-wrap gap-1.5">
            {s.excluded_columns!.map((c) => <Badge key={c} tone="warn">{c}</Badge>)}
          </div>
        </Disclosure>
      )}

      {s.rationale && (
        <Disclosure title="Rationale">
          <p className="max-w-3xl text-sm leading-relaxed text-ink-soft">{s.rationale}</p>
        </Disclosure>
      )}

      {s.report_markdown && (
        <Disclosure title="Full report">
          <pre className="max-h-[520px] overflow-auto whitespace-pre-wrap rounded-lg bg-surface-sunken p-3 text-xs leading-relaxed text-ink-soft">
            {s.report_markdown}
          </pre>
        </Disclosure>
      )}
    </section>
  );
}


function ApprovalCard({
  runId, decision, onAnswered,
}: { runId: string; decision: GateDecision; onAnswered: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const prompt = decision.human_prompt!;

  async function answer(optionId: string) {
    setBusy(optionId);
    setError(null);
    try {
      await api.answer(runId, {
        stage_id: decision.stage_id,
        decision: optionId,
        instructions: note ? [note] : [],
      });
      setNote("");
      onAnswered();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="card mb-4 border-l-4 border-l-stop-500 px-4 py-4">
      <div className="mb-1 flex items-center gap-2">
        <Badge tone="stop">Approval required</Badge>
        <code className="font-mono text-[11px] text-ink-faint">{decision.reason_code}</code>
      </div>
      <h3 className="text-sm font-semibold text-ink">{prompt.question}</h3>
      <p className="mt-1 whitespace-pre-wrap text-xs leading-relaxed text-ink-mute">{prompt.context_summary}</p>

      {decision.triggered_rules.length > 0 && (
        <p className="mt-2 font-mono text-[11px] text-ink-faint">rules: {decision.triggered_rules.join(", ")}</p>
      )}

      <div className="mt-3 grid gap-2 sm:grid-cols-3">
        {prompt.options.map((o) => (
          <button
            key={o.option_id}
            onClick={() => void answer(o.option_id)}
            disabled={busy !== null}
            className={cx(
              "rounded-lg border px-3 py-2.5 text-left transition-colors disabled:opacity-50",
              o.recommended ? "border-brand-500 bg-brand-50 hover:bg-brand-100" : "border-line bg-surface hover:bg-surface-sunken",
            )}
          >
            <span className="block text-sm font-medium text-ink">{o.label}{o.recommended && " ★"}</span>
            <span className="mt-0.5 block text-[11px] leading-snug text-ink-mute">{o.consequence}</span>
            {o.downstream_effect && <span className="mt-1 block text-[11px] text-warn-700">{o.downstream_effect}</span>}
          </button>
        ))}
      </div>

      {prompt.allows_free_text !== false && (
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Optional instructions to attach to a rework…"
          className="field mt-2.5 text-xs"
        />
      )}
      {error && <p className="mt-2 text-xs text-stop-700">{error}</p>}
    </section>
  );
}

function Pair({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[10px] uppercase tracking-wide text-ink-faint">{label}</dt>
      <dd className="truncate text-xs font-semibold text-ink">{value}</dd>
    </div>
  );
}

/**
 * Collect this stage's concerns from wherever they were measured.
 *
 * Three independent sources: warnings an artifact recorded about itself, the
 * severity an analysis panel was assigned, and the rules a gate escalation
 * triggered. Severity is never recomputed here — only read.
 */
function collectAttention(detail: StageDetail | null): AttentionItem[] {
  if (!detail) return [];
  const items: AttentionItem[] = [];

  for (const output of detail.outputs) {
    const story = output.story ?? {};
    const panels = story.panels ?? [];

    // An artifact's own warnings and its panel severities are two views of the
    // same measurements — "2 columns over 10% missing" arrives both as a
    // warning string and as the missing-values panel. When panels exist they
    // are the richer view, so the strings would only repeat them.
    if (!panels.length) {
      for (const warning of story.warnings ?? []) {
        items.push({ severity: "warning", title: warning, source: output.name });
      }
    }

    for (const panel of panels) {
      if (panel.severity === "issue" || panel.severity === "warning") {
        items.push({
          severity: panel.severity,
          title: `${panel.title}: ${panel.caption ?? ""}`.trim(),
          detail: panel.insights?.[0],
          source: output.name,
        });
      }
    }
  }

  for (const panel of detail.panels ?? []) {
    if (panel.severity === "issue" || panel.severity === "warning") {
      items.push({
        severity: panel.severity,
        title: `${panel.title}: ${panel.caption ?? ""}`.trim(),
        detail: panel.insights?.[0],
        source: "source table",
      });
    }
  }

  for (const decision of detail.gate_decisions) {
    if (decision.verdict === "escalate" || decision.verdict === "abort") {
      items.push({
        severity: "issue",
        title: decision.reason_code.replace(/_/g, " "),
        detail: decision.triggered_rules.join(", ") || undefined,
        source: `gate · attempt ${decision.attempt}`,
      });
    }
  }
  return items;
}
