import type { ToolActivityEvent } from "../lib/api";
import { t } from "../lib/i18n";

/**
 * How a tool call reads as one line of chat (#411).
 *
 * Kept out of `PlannerPanel` because it is the part worth testing on its own:
 * the panel is a polling loop and a list, and this is the vocabulary.
 *
 * Agent and tool ids are internal names. `eda_investigator` and `profile_table`
 * are precise and are not prose, so each gets a label; anything unmapped falls
 * back to the id with its underscores opened out, which is wrong-but-readable
 * rather than blank -- a tool added later shows up as "sample rows" instead of
 * disappearing from the feed until someone remembers to translate it.
 */
const AGENT_LABELS: Record<string, string> = {
  eda_investigator: "Analysis agent",
  feature_investigator: "Feature agent",
  leakage_investigator: "Leakage agent",
  model_investigator: "Model agent",
  problem_investigator: "Problem agent",
  schema_discovery: "Schema agent",
  sensitivity_investigator: "Personal-data agent",
  validation_investigator: "Validation agent",
  interpretation: "Interpretation agent",
  problem_discovery: "Problem agent",
  validation_strategy: "Validation agent",
  planner: "Planner",
};

export function agentLabel(agentId: string): string {
  const known = AGENT_LABELS[agentId];
  return known ? t(known) : humanize(agentId);
}

/** The tool's own id, opened out. Tool ids are the registry's public
 *  vocabulary and are named for what they do, so they read well already; what
 *  they do not need is a second, drifting list of translations. */
export function toolLabel(toolId: string): string {
  return humanize(toolId);
}

function humanize(id: string): string {
  return id.replaceAll("_", " ").trim() || id;
}

/** The chat line for one tool call.
 *
 * A refused or failed call says so. Hiding them would be the wrong kind of
 * quiet: "the agent tried X and was not allowed to" is exactly the sort of
 * thing somebody watching a stalled run needs to see.
 */
export function toolActivityLine(event: ToolActivityEvent): string {
  const agent = agentLabel(event.agent);
  const tool = toolLabel(event.tool);
  if (event.decision === "denied") return t("{agent} was not allowed to use the {tool} tool", { agent, tool });
  if (event.decision === "failed") return t("{agent}'s {tool} tool call failed", { agent, tool });
  return t("{agent} used the {tool} tool", { agent, tool });
}

/** Merge new events into the ones already shown, keeping arrival order.
 *
 * `added` is what the caller should actually append to the transcript. The poll
 * returns only what is past its cursor, but the cursor only advances on a
 * successful append, so a retried request hands back a range already shown --
 * and a duplicated line reads as the agent having called the tool twice.
 *
 * `events` is capped: the transcript is a conversation, not a log, and a long
 * investigation makes hundreds of calls. Dropping from the front bounds the
 * bookkeeping without changing what a person just watched happen.
 */
export function mergeToolActivity(
  current: ToolActivityEvent[],
  incoming: ToolActivityEvent[],
  limit = 200,
): { events: ToolActivityEvent[]; added: ToolActivityEvent[] } {
  const seen = new Set(current.map((event) => event.seq));
  const added: ToolActivityEvent[] = [];
  for (const event of incoming) {
    if (seen.has(event.seq)) continue;
    seen.add(event.seq);
    added.push(event);
  }
  const merged = [...current, ...added];
  return { events: merged.length > limit ? merged.slice(merged.length - limit) : merged, added };
}
