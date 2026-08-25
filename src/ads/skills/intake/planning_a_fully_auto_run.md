---
skill_id: intake.planning_a_fully_auto_run
trigger: always
applies_to: [source_comprehension]
source: original
---

# Plan a fully automatic run from measured evidence

Produce a ready-to-review runtime plan only after intake and schema discovery
have completed. The plan is a proposal until a person accepts it.

## Evidence and authority

- Treat DataCards, measured overlaps, executable integration trials, and gate
  results as facts. Never replace them with a more convenient interpretation.
- Explain every relationship through its exact table and column endpoints.
  Value overlap is evidence of a possible join, not proof of business meaning.
- Keep agent interpretation visibly separate from measurements. Include a
  verification question when domain meaning is uncertain.
- Hard safety gates remain authoritative in fully-auto mode. Do not recommend
  disabling leakage, privacy, destructive-action, or retry-exhaustion controls.

## Runtime plan

Recommend only settings supported by the available evidence: base table and
grain, target/task/metric preferences, validation protection, candidate budget,
stage-specific directives, checkpoints, and retry budgets. Give one concise
rationale per consequential override. If the data does not support a setting,
omit it instead of guessing.

The planner replaces routine human configuration only after acceptance. Fully
auto means policy-only checkpoints may proceed without a person; it never means
an unsafe or mechanically invalid artifact becomes valid.

## Reports

Create a short source briefing, a relationship-and-join-risk report, and a
pipeline-readiness report. Lead with findings that could change whether the run
should proceed. Avoid restating every profile statistic already visible in the
workspace.
