# The hardening track: what actually makes this worth building

This is the brief for the current phase of work. It states the problem, what already
exists, what has been decided and why, and what nobody has answered yet. It
deliberately does not state a solution.

---

## 1. The product

A fully local, self-hosted agentic data-science platform. A user points it at their
own tables and it takes a project from raw sources to a trained, evaluated,
reproducible model, stopping for a human wherever a decision cannot be made safely
without one.

Hard constraints, all non-negotiable:

- Everything runs on-premise. No cloud LLM APIs. The whole system must be air-gappable.
- No copyleft or source-available-with-restrictions dependencies (no AGPL, SUL, FSL).
- Hardware: a single RTX 3090 for development (so ~27B local models via Ollama), and
  8×H100 in the target production environment.
- One agent runs at a time. Parallel pipeline execution is explicitly not needed.

The long-term shape is an n8n-style composable workflow builder — branching, loops,
checkpoints, parallel branches, human intervention at any point, dynamic routing, and
per-agent tool and context boundaries.

## 2. The problem this track exists to solve

**A strong general-purpose coding agent, handed a dataset and a notebook, can already
do a large fraction of an ML project.** Codex can. Claude can. That is the baseline
this product has to beat, and it is a high one.

So the differentiation cannot be any of these, because none of them clear that bar:

- Having several agents instead of one.
- Different system prompts per pipeline stage.
- Tool-calling.
- Orchestration, retries, or a workflow graph.
- A nice n8n-like interface.

Every one of those is infrastructure. A generic agent with a good prompt gets most of
the same result without them. If that is all this is, it is a worse notebook.

**The question this track has to answer is therefore:**

> What mechanism, behind the agents, makes them genuinely reliable data scientists
> rather than generic coding agents assigned to different stages of a pipeline?

Put differently: where does *domain intelligence* live in this system, as opposed to
plumbing? What does this system know, enforce, or measure that a capable generalist
with a Jupyter kernel would get wrong or would not think to check?

This is a real open question, not a rhetorical one with an answer hidden at the bottom
of the page. It should be investigated, argued about, and then built.

## 3. What already exists

Read the code, not this summary — but this is the shape of it.

**A two-plane separation.** Agents never see raw rows. They see DataCards: schema,
aggregate statistics, measured relationships, roughly 600 tokens. Every measurement an
agent reasons over was computed by Python first.

**A proposal/artifact split.** Agents author judgment only. The system attaches
measurements, ids and timestamps. An agent physically cannot assert a number into the
record — the contracts exclude the fields. `ProblemCandidateProposal` has no
`support`; feasibility is measured by `ads.discovery.support` and attached afterwards.

**A content-addressed immutable artifact store.** SHA-256 over the semantic payload,
`created_at` excluded from the hash.

**A deterministic gate evaluator.** 18 rules across 4 precedence tiers
(HARD → RISK → SIGNAL → PROFILE). User autonomy preference is the *weakest* input, so
a user asking for full automation cannot switch off a leakage block.

**Deterministic measurement modules** that the agents cannot override:
`discovery/leakage.py` (four independent leakage families — target correlation,
perfect separator via directional ROC AUC, unwindowed aggregate via provenance,
missingness separator), `discovery/support.py`, `discovery/validation_signals.py`
(protection-dominance as a partial order, not a ranking), `intake/keys.py`
(candidate keys, join overlap).

**Grammar-constrained structured output.** Ollama `format` = JSON schema → GBNF, then
Pydantic validation, then semantic validators, then deterministic auto-repair, then a
corrective retry with the failures fed back.

**A permission broker and tool registry** with tiers (READ_META < READ_DATA < EXECUTE
< WRITE_ARTIFACT < MUTATE_SOURCE, the last denied to every agent unconditionally), and
an isolated Docker sandbox for code execution (no network, read-only root, all
capabilities dropped).

**Agent panels.** The same stage can be sampled N times; disagreement on the
decision-bearing fields escalates to a human. Recently tightened to unanimity.

**A React control plane** that renders measured output as charts and analyses.

## 4. Decisions already taken, and why

Do not silently reverse these. Argue with them in `docs/AGENT_DIALOGUE.md` if you
think they are wrong — several have already been revised that way.

- **Agents never see raw rows.** Both a privacy property and a quality one: an agent
  that reads rows starts pattern-matching on samples instead of reasoning about
  measured structure.
- **Judgment and measurement are separated at the contract level**, not by asking the
  model nicely. Asking an LLM for a positive-class count invites a plausible guess,
  and that guess would then gate a CRITICAL decision.
- **The gate is deterministic.** An LLM does not decide whether to stop.
- **Retry identity comes from control flow, not from data.** Three separate defects
  were caused by keying a control decision off an incidental property of the payload.
- **Panel agreement is measured over decision fields, not prose.** Comparing whole
  contracts made rewording look like disagreement.
- **Adjusted mutual information, not plain NMI**, because chance correction matters
  for near-unique columns.

## 5. Known uncertainties

Genuinely open. Some may be the answer to §2; some may be dead ends.

- The panel mechanism measures *stability*, not *correctness*. Three samples can agree
  and all be wrong. Is stability the right signal, and what would measure correctness?
- The gate rules are hand-written thresholds. They encode real expertise, but nothing
  validates them against outcomes.
- `execute_python` gives an agent a kernel over `/data`. Today no agent is granted
  EXECUTE, and the two-plane separation depends on that staying true. If agents ever
  need to write real analysis code, the whole boundary needs rethinking.
- There is no notion of a *reference method* — nothing that says "for this kind of
  data and this target, this is the standard approach and here is why deviating is or
  is not justified."
- Nothing accumulates across runs. Every run starts from zero knowledge.
- The system checks whether a decision is *defensible*. It does not check whether it
  is *good*.

## 6. What this track is not

- Not a report-writing exercise. Research and notes are intermediate coordination
  tools; the deliverable is a substantially stronger system.
- Not model training. RLHF, DPO, SFT and distillation are out of scope by explicit
  instruction.
- Not a UI project. The control plane is in reasonable shape.

## 7. How to work this

1. **Read the repository first.** Distinguish what is infrastructure from what is
   actual domain intelligence. Much of what looks impressive is plumbing.
2. **Research what exists elsewhere.** Agentic data-science systems, AutoML, data
   quality and validation frameworks, causal and leakage tooling, statistical
   test-selection systems. Find out what has already been solved, what is genuinely
   hard, and where the real failure modes of automated data science are documented.
   Check licences against the actual repositories.
3. **Form a view and bring it back.** Write it into `docs/AGENT_DIALOGUE.md` as a
   `PROPOSAL` with the reasoning and the evidence. Expect it to be argued with.
4. **Agree, then build.** Implementation follows alignment, not the other way round.

The bar for a proposed mechanism: *a capable generalist agent with a notebook would
get this wrong, or would not know to check it, and here is the measurement that shows
that.*
