# Working agreement: Claude session ↔ Codex session

Two agent sessions are developing this repository at the same time. This document is
the contract between them. It exists because parallel sessions that only report at the
end produce conflicting edits and duplicated reasoning, and because a session that
silently accepts the other's premises is worth less than one that argues with them.

Read this before touching code. Read `AI_HANDOFF.md` for the map of the system and
`docs/AGENT_DIALOGUE.md` for what has already been decided and disputed.

---

## 1. The channel

There is no direct socket between the sessions. `docs/AGENT_DIALOGUE.md` is the
channel, and git is the transport. Append to it, commit, and the other session sees
your entry on its next pull of the working tree.

Append entries in this form:

```
## <ISO date> - <CLAUDE|CODEX>: <one-line subject>

<body>
```

Tag anything that needs the other session to act or reply, on its own line so it is
greppable:

| Tag | Meaning | Reply expected |
|---|---|---|
| `FINDING` | Something measured that the other session should know | No, but read it |
| `QUESTION` | You are blocked or genuinely unsure | **Yes** |
| `PUSHBACK` | You think a decision already taken is wrong | **Yes** |
| `PROPOSAL` | A design you intend to build unless argued with | **Yes, before it lands** |
| `DECISION` | Settled; here is what and why | No |
| `HANDOFF` | Ownership of a file or area is moving | Acknowledge |
| `BLOCKED` | You cannot proceed without the other session | **Yes, urgently** |

Check the file at the start of every working block and before starting anything large.
`git log --oneline docs/AGENT_DIALOGUE.md` shows whether there is anything new.

## 2. When to interrupt rather than continue

Stop and write a `QUESTION`, `PUSHBACK`, `PROPOSAL` or `BLOCKED` entry — do not just
keep building — when any of these is true:

- The change would alter a contract in `src/ads/contracts/`, a gate rule, or the
  meaning of an existing artifact field. These are load-bearing for both sessions.
- You are about to introduce a new dependency, especially a copyleft-licensed one.
  (Constraint: no AGPL/SUL/FSL. Verify licences against the actual repository, not
  memory — this project has already been bitten by n8n being SUL and PyCaret FSL.)
- You found evidence that contradicts something the other session recorded as settled.
- The work would take more than roughly an hour before producing anything reviewable.
- You are choosing between two designs and the choice is not obviously reversible.
- Something you found changes an assumption the other session is currently relying on.
  This one is urgent: they may be building on it right now.

Do **not** interrupt for: routine implementation, test failures you can diagnose,
naming, or anything where you would proceed the same way regardless of the answer.

## 3. What to argue about

Challenge the other session's conclusions. A conclusion that survives an argument is
worth more than one that was never examined. Specifically:

- If a claim is not backed by a measurement, ask what measured it.
- If a mechanism is described as a guarantee, ask what would have to be true for it to
  fail, and whether anything enforces that.
- If a design is justified by "best practice", ask what it buys *here*.
- Disagree in the dialogue file with your reasoning stated. Do not silently implement
  the opposite — that produces two systems.

When you are wrong, say so in one line and move on. No ceremony.

## 4. Ownership

**Changed 2026-08-15 by the project owner.** Codex implements; Claude reviews. The owner
communicates with Claude, and Claude communicates with Codex. Claude does not write feature
code — it specifies, reviews, verifies by measurement, and reports.

This is not a demotion of either session. It exists because the strongest quality signal in
this project so far has been review by a session that did not write the code: Codex found a
policy change that had never taken effect, Claude found an answer key printed on the benchmark
and PII promoted to a model feature. Neither would have caught their own. Separating the roles
makes that structural rather than accidental.

What Claude still does, and must keep doing:

- Run every command Codex cannot, and paste back real output including failures. Codex's
  sandbox cannot spawn processes on this host, so it can execute neither Python nor git.
- Commit Codex's work, attributed to Codex.
- Verify claims by measurement rather than by reading a green suite, and break a fix
  deliberately to check that its test can fail.
- Say plainly when Codex is right and Claude was wrong. That has happened repeatedly and the
  record of it is why the arrangement works.

What Claude does not do: write features, refactor, or "just fix" something in passing. A
blocking syntax error may be repaired to unblock a test run, and must be reported in the
dialogue as having been touched.

The table below now records *who reviews what*, since Codex writes all of it.

Previous split, retained for history:

| Area | Owner |
|---|---|
| `web/**` | Claude |
| `src/ads/api/**` | Claude |
| `src/ads/agents/**` | Codex |
| `src/ads/discovery/**`, `eda/`, `intake/`, `integration/` | Codex |
| `src/ads/orchestration/**`, `gates/**` | Codex |
| `src/ads/contracts/**` | **Shared — announce before editing** |
| `tests/**` | Whoever owns the code under test |
| `docs/AGENT_DIALOGUE.md` | Both, append-only |

`src/ads/api/static/**` is a build artifact. Never hand-edit it; it is produced by
`npm --prefix web run build`.

To move ownership, write a `HANDOFF` entry and wait for acknowledgement.

If you must touch the other session's area (a one-line fix, a blocking bug), keep it
minimal, say so in the dialogue, and do not restructure.

## 5. Commit discipline

- Commit frequently; small commits are easier to reconcile than large ones.
- Never `git push`, rebase, reset, or amend the other session's commits.
- Run `python -m pytest -q` and `python -m ruff check src tests` before committing.
- If the suite is red for a reason you did not cause, say so in the dialogue rather
  than working around it.
- Commit messages: what changed and *why it was wrong before*. The repository's
  existing history is the style reference.

## 6. Verification standard

This project has a specific failure history: three separate fixes recreated the defect
they closed, and the tests written alongside the code caught zero of eighteen real
defects. Both sessions are held to the same bar as a result:

- Measure before you claim. "This is faster/safer/more accurate" needs a number.
- A test that cannot fail proves nothing. When you add a test for a defect, break the
  fix deliberately and confirm the test goes red. Say in the commit that you did.
- Prefer a counter-test: if a change makes some signal go up, add the case where it
  must still go down.
- Never key a control decision off an incidental property of the data when the control
  flow already knows the answer. This is the exact mechanism behind the three
  recreated defects.

## 7. Environment hazards

- **Never start Docker Desktop from a shell.** `Start-Process "Docker Desktop.exe"`
  from an agent context corrupts the GUI and forces the user to factory-reset it. If
  Docker is needed and not running, ask the user, or use
  `explorer.exe "C:\Program Files\Docker\Docker\Docker Desktop.exe"`.
- The venv is `.venv/Scripts/python.exe`. `PYTHONPATH=src` for ad-hoc scripts.
- The UI server is `python scripts/serve_ui.py` on port 8077. Check the port is free
  before starting; a stale listener means a previous run is still bound.
- Everything is local. No cloud LLM APIs, air-gappable, no telemetry.
