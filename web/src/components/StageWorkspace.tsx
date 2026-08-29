import { t } from "../lib/i18n";
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
  type ProfiledTable,
  type RunOptions,
  type StageDetail,
  type StageOutput,
  type Story,
  type WorkflowNode,
} from "../lib/api";
import { elapsedLabel, reasonLabel, statusLabel, verdictLabel } from "../lib/status";
import { AnalysisStrip, type AnalysisPanel } from "./AnalysisStrip";
import { BranchPicker } from "./BranchPicker";
import { SensitivityOverride } from "./SensitivityOverride";
import { StageDirective } from "./StageDirective";
import { ApprovalCard } from "./GateApproval";
import { NeedsAttention, type AttentionItem } from "./NeedsAttention";
import { SchemaMap } from "./SchemaMap";
import { IntakeStage } from "./IntakeStage";
import { stageName } from "./PipelineRail";
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
  runId, node, sourceId = null, runStatus = null, options = null,
  onAnswered, onBranched, onStarted, onDiscarded,
}: {
  runId: string | null;
  node: WorkflowNode | null;
  sourceId?: string | null;
  /** The run's own status, which is what makes intake a decision screen. */
  runStatus?: string | null;
  options?: RunOptions | null;
  onAnswered: () => void;
  onBranched?: (runIds: string[]) => void;
  onStarted?: () => void;
  onDiscarded?: () => void;
}) {
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
    return <div className="p-6"><Empty title={t("Select a stage")} hint="Pick a node in the pipeline above to see its workspace." /></div>;
  }

  const outputs = detail?.outputs ?? [];
  const candidates = outputs.flatMap((o) => o.story?.choices ?? []);
  // Intake emits one data card per source table; the override needs their
  // columns, and this is the same payload the panel above already renders.
  const profileTables = outputs
    .filter((o) => o.type === "data_card")
    .map((o) => (o.summary ?? {}) as unknown as ProfiledTable)
    .filter((t) => Array.isArray((t as ProfiledTable).columns));
  const attention = collectAttention(detail);
  // The gate that is actually waiting on a person, if any.
  const openGate = detail?.gate_decisions.find((g) => g.human_prompt) ?? null;
  const needsHuman = detail?.human_view?.needs_human;

  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
      <header className="mb-4">
        <div className="flex flex-wrap items-center gap-2.5">
          <h2 className="text-xl font-semibold tracking-tight">{stageName(node.id)}</h2>
          <Badge tone={toneFor(node.status)}>{statusLabel(node.status)}</Badge>
          <Badge tone={node.kind === "deterministic" ? "neutral" : "brand"}>
            {node.kind === "deterministic" ? t("Deterministic") : t("Agent")}
          </Badge>
          {node.attempt_count > 1 && (
            <Badge tone="warn">{node.attempt_count} {t("attempts")}</Badge>
          )}
          {elapsedLabel(node.elapsed_seconds) && (
            <span
              className="text-xs tabular-nums text-ink-mute"
              title={node.ended_at ?? node.started_at ?? undefined}
            >
              {elapsedLabel(node.elapsed_seconds)}
            </span>
          )}
        </div>
        <p className="mt-1 max-w-3xl text-sm text-ink-mute">
          {t(detail?.stage?.description ?? node.description ?? "")}
        </p>
      </header>

      {loading && !detail && <Spinner label={t("Loading stage…")} />}
      {error && <p className="card border-stop-500/30 bg-stop-50 px-4 py-3 text-sm text-stop-700">{error}</p>}

      {needsHuman && detail?.human_view?.state_label && !openGate && (
        <p className="card mb-4 border-l-4 border-l-warn-500 px-4 py-3 text-sm text-ink-soft">
          {detail.human_view.state_label}
        </p>
      )}

      {openGate && runId && (
        <ApprovalCard runId={runId} decision={openGate} onAnswered={onAnswered} />
      )}

      {/* Intake is the screen where the run is decided, so it is the whole
          workspace rather than one panel among several: the data as measured,
          the PII call while it is still free to change, and the configuration
          the run continues with. */}
      {runId && node.id === "intake" && sourceId && (
        <IntakeStage
          runId={runId}
          sourceId={sourceId}
          staged={runStatus === "staged"}
          staging={runStatus === "staging"}
          tables={profileTables}
          options={options}
          onStarted={() => onStarted?.()}
          onDiscarded={() => onDiscarded?.()}
        />
      )}

      {/* Offered while any other stage is stopped for a person: the
          classification is on screen and nothing has been built on it yet. */}
      {openGate && runId && node.id !== "intake" && profileTables.length > 0 && (
        <SensitivityOverride runId={runId} tables={profileTables} />
      )}

      {runId && node.kind !== "deterministic" && (
        <StageDirective runId={runId} stageId={node.id} />
      )}

      {/* Only offered where the choice actually forks the project. Branching a
          later stage would produce runs that differ in nothing a person picked. */}
      {runId && node.id === "problem_discovery" && candidates.length > 1 && (
        <BranchPicker
          choices={candidates}
          sourceId={sourceId}
          parentRunId={runId}
          onBranched={(ids) => onBranched?.(ids)}
        />
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
        <Attempts attempts={detail.attempts} />
      )}

      {/* Gate internals -- reason codes, rule ids, artifact hashes -- are
          debugging output, not something a person reading their own run needs.
          They remain in the API and the audit record; they are simply no longer
          the first thing on screen. */}
      {detail && detail.gate_decisions.length > 0 && (
        <Disclosure title={t("Decision history")} count={detail.gate_decisions.length}>
          <ul className="space-y-1.5">
            {detail.gate_decisions.map((g, i) => (
              <li key={i} className="flex flex-wrap items-center gap-2 rounded-lg border border-line bg-surface-sunken px-3 py-2 text-sm">
                <Badge tone={toneFor(g.verdict)}>{verdictLabel(g.verdict)}</Badge>
                <span className="text-ink-soft">{reasonLabel(g.reason_code)}</span>
                {g.attempt > 1 && (
                  <span className="text-xs text-ink-mute">
                    {t("attempt {n}", { n: g.attempt })}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </Disclosure>
      )}

      {outputs.length > 0 && (
        <Disclosure title={t("Artifacts")} count={outputs.length}>
          <ul className="space-y-1.5">
            {outputs.map((a) => (
              <li key={a.artifact_id} className="flex items-center gap-2 text-sm">
                <Badge>{a.type}</Badge>
                <span className="truncate text-ink-soft">{a.name}</span>

              </li>
            ))}
          </ul>
        </Disclosure>
      )}

      {!loading && !error && outputs.length === 0 && (
        <Empty
          title={node.status === "pending" ? t("This stage has not run yet") : t("No output recorded")}
          hint={node.status === "pending" ? t("Start a run to populate this workspace.") : undefined}
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
            <span className="font-semibold text-brand-700">{t("Recommendation:")} </span>{s.suggestion}
          </p>
        </div>
      )}


      {/* The agent's own explanations, each carrying what it was measured from
          and the question a person should ask before believing it. Marked as a
          proposal rather than a finding, because that is what it is. */}
      {has("insights") && (
        <Disclosure title={t("What the agent noticed")} count={s.insights!.length} defaultOpen>
          <div className="space-y-2">
            {s.insights!.map((item, i) => (
              <article key={i} className="rounded-lg border border-line bg-surface-sunken px-4 py-3">
                <div className="mb-1 flex flex-wrap items-center gap-2">
                  <Badge tone="brand">{t("Agent proposal")}</Badge>
                  {item.subjects?.slice(0, 3).map((subject) => (
                    <code key={subject} className="rounded bg-surface px-1.5 py-0.5 text-[11px] text-ink-soft">
                      {subject}
                    </code>
                  ))}
                </div>
                <p className="text-sm leading-relaxed text-ink">{item.interpretation}</p>
                {item.why_it_matters && (
                  <p className="mt-1 text-xs leading-relaxed text-ink-mute">{item.why_it_matters}</p>
                )}
                {item.verification_question && (
                  <p className="mt-2 border-l-2 border-warn-500 pl-2 text-xs text-warn-700">
                    {item.verification_question}
                  </p>
                )}
              </article>
            ))}
          </div>
        </Disclosure>
      )}

      {has("choices") && (
        <Disclosure title={t("Candidate problems")} count={s.choices!.length} defaultOpen>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {s.choices!.map((c, i) => (
              <article key={i} className={cx("card px-4 py-3", i === 0 && "border-brand-500 ring-2 ring-brand-100")}>
                <div className="mb-1 flex items-start justify-between gap-2">
                  <h4 className="text-sm font-semibold leading-tight">{c.title}</h4>
                  {c.viable === false && <Badge tone="stop">{t("not viable")}</Badge>}
                  {i === 0 && c.viable !== false && <Badge tone="brand">{t("selected")}</Badge>}
                </div>
                <dl className="mt-2 grid grid-cols-3 gap-2 border-t border-line-soft pt-2">
                  <Pair label={t("Target")} value={c.target ?? "—"} />
                  <Pair label={t("Task")} value={(c.task ?? "—").replace(/_/g, " ")} />
                  <Pair label={t("Metric")} value={c.metric ?? "—"} />
                </dl>
                {c.rationale && <p className="mt-2 text-xs leading-relaxed text-ink-mute">{c.rationale}</p>}
              </article>
            ))}
          </div>
        </Disclosure>
      )}

      {has("model_comparison") && (
        <Disclosure title={t("Model comparison")} count={s.model_comparison!.length} defaultOpen>
          <DataTable
            columns={[t("Candidate"), t("Role"), t("CV mean"), t("CV std"), t("Holdout")]}
            rows={s.model_comparison!.map((m) => [
              m.selected ? `${m.candidate}  ★` : m.candidate,
              m.baseline ? t("baseline") : m.selected ? t("selected") : t("candidate"),
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
        <Disclosure title={t("Relationship map")} count={s.schema_graph.edges.length} defaultOpen>
          <SchemaMap graph={s.schema_graph} />
        </Disclosure>
      )}

      {has("criteria") && (
        <Disclosure title={t("Coverage checks")} count={Object.keys(s.criteria!).length}>
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
        <Disclosure title={t("Decision history")} count={s.history_alerts!.length}>
          <ul className="space-y-1.5">
            {s.history_alerts!.map((a, i) => (
              <li key={i} className="flex items-start gap-2 rounded-lg border border-line bg-surface-sunken px-3 py-2 text-sm text-ink-soft">
                <Badge tone={a.resolved ? "ok" : "warn"}>{a.resolved ? t("resolved") : t("open")}</Badge>
                <span>{a.detail}</span>
              </li>
            ))}
          </ul>
        </Disclosure>
      )}

      {has("findings") && (
        <Disclosure title={t("Findings")} count={s.findings!.length}>
          <pre className="overflow-x-auto rounded-lg bg-surface-sunken p-3 text-[11px] leading-relaxed text-ink-soft">
            {JSON.stringify(s.findings, null, 2)}
          </pre>
        </Disclosure>
      )}

      {has("excluded_columns") && (
        <Disclosure title={t("Excluded columns")} count={s.excluded_columns!.length}>
          <div className="flex flex-wrap gap-1.5">
            {s.excluded_columns!.map((c) => <Badge key={c} tone="warn">{c}</Badge>)}
          </div>
        </Disclosure>
      )}

      {s.rationale && (
        <Disclosure title={t("Rationale")}>
          <p className="max-w-3xl text-sm leading-relaxed text-ink-soft">{s.rationale}</p>
        </Disclosure>
      )}

      {has("quality_checks") && (
        <Disclosure title={t("Quality checks")} count={s.quality_checks!.length} defaultOpen>
          <ul className="space-y-1.5">
            {s.quality_checks!.map((qc, i) => (
              <li key={i} className="flex items-center gap-2 rounded-lg border border-line bg-surface-sunken px-3 py-2 text-sm text-ink-soft">
                <span className={cx(
                  "grid h-4 w-4 place-items-center rounded-full text-[10px] font-bold text-white",
                  qc.passed ? "bg-ok-500" : "bg-stop-500"
                )}>
                  {qc.passed ? "✓" : "!"}
                </span>
                <span className="font-medium text-ink">{qc.label}</span>
                {qc.detail && <span className="text-xs text-ink-mute">· {qc.detail}</span>}
              </li>
            ))}
          </ul>
        </Disclosure>
      )}

      {has("panel") && (
        <Disclosure title={t("Agent panel execution & activity")} count={s.panel!.length} defaultOpen>
          <div className="space-y-2">
            <DataTable
              columns={[
                t("Member"),
                t("Model"),
                t("Turns"),
                t("Result"),
                t("Validation checks"),
                t("Repairs"),
                t("Duration"),
              ]}
              rows={s.panel!.map((m) => [
                `#${m.member}`,
                m.model,
                m.attempts,
                m.accepted ? t("Accepted") : t("Rejected"),
                m.validation_failures?.length ? m.validation_failures.join(", ") : t("All passed"),
                m.repairs?.length ? m.repairs.join(", ") : t("None needed"),
                m.latency_s !== undefined ? `${m.latency_s}s` : "—",
              ])}
            />
          </div>
        </Disclosure>
      )}

      {s.report_markdown && (
        <Disclosure title={t("Full report")}>
          <pre className="max-h-[520px] overflow-auto whitespace-pre-wrap rounded-lg bg-surface-sunken p-3 text-xs leading-relaxed text-ink-soft">
            {s.report_markdown}
          </pre>
        </Disclosure>
      )}
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


/**
 * What each attempt did, and why the gate was not satisfied.
 *
 * A rejected attempt produces no artifacts, so the workspace showed nothing at
 * all for it: seven minutes of a 27B model would pass, the rail would say
 * "×2", and the screen would be empty. The reason was in the attempt's
 * critique the whole time and was never rendered -- the person watching had no
 * way to tell a slow stage from a broken one.
 */
function Attempts({ attempts }: { attempts: StageDetail["attempts"] }) {
  const unfinished = attempts.some((a) => !a.ended_at);
  return (
    <Disclosure
      title={t("Attempts")}
      count={attempts.length}
      defaultOpen={attempts.length > 1 || unfinished}
    >
      <ol className="space-y-2">
        {attempts.map((attempt) => {
          const seconds =
            attempt.ended_at && attempt.started_at
              ? (Date.parse(attempt.ended_at) - Date.parse(attempt.started_at)) / 1000
              : null;
          const unmet = attempt.critique?.unmet_criteria ?? [];
          const findings = attempt.critique?.findings ?? [];
          return (
            <li
              key={attempt.attempt}
              className="rounded-lg border border-line bg-surface-sunken px-3 py-2"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs font-semibold tabular-nums text-ink">
                  #{attempt.attempt}
                </span>
                <Badge tone={attempt.verdict ? toneFor(attempt.verdict) : "neutral"}>
                  {attempt.verdict ? verdictLabel(attempt.verdict) : t("running")}
                </Badge>
                {elapsedLabel(seconds) && (
                  <span className="text-[11px] tabular-nums text-ink-mute">
                    {elapsedLabel(seconds)}
                  </span>
                )}
                {attempt.artifact_ids.length > 0 && (
                  <span className="text-[11px] text-ink-faint">
                    {attempt.artifact_ids.length} {t("artifacts")}
                  </span>
                )}
              </div>

              {attempt.error && (
                <p className="mt-1.5 text-[11px] text-stop-700">{attempt.error}</p>
              )}

              {/* The mechanical checks the plan failed. These are what turned a
                  seven-minute stage into a retry, and they are the only thing
                  that explains an empty screen. */}
              {unmet.length > 0 && (
                <ul className="mt-1.5 space-y-1">
                  {unmet.map((id) => {
                    const finding = findings.find((f) => f.check_id === id);
                    return (
                      <li key={id} className="text-[11px] leading-snug text-warn-700">
                        · {t(checkText(id, finding?.evidence))}
                      </li>
                    );
                  })}
                </ul>
              )}
            </li>
          );
        })}
      </ol>
    </Disclosure>
  );
}

/**
 * A check id is a code, so it is translated as one. The recorded evidence is
 * the fallback for a check this build has never seen -- better an English
 * sentence than a bare identifier.
 */
function checkText(checkId: string, evidence?: string | null): string {
  return CHECK_TEXT[checkId] ?? evidence ?? checkId;
}

const CHECK_TEXT: Record<string, string> = {
  "schema.base_grain_declared": "The plan declared no base grain.",
  "schema.fan_out_aggregated":
    "The plan did not pass the relationship and fan-out validators.",
  "schema.plan_trial_passed":
    "The plan failed a trial execution against the real tables.",
};
