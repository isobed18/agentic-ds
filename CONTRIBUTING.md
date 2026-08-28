# Contributing

For everyone — the four of us and the agents working alongside us. If you are an
agent, read [`AGENTS.md`](AGENTS.md) first; it covers claiming work and the
coordination protocol. This file is setup and conventions.

## Getting it running

```bash
python -m venv .venv && .venv/Scripts/activate     # Windows; use bin/activate elsewhere
pip install -e ".[api,dev,sandbox,kesif]"
npm --prefix web ci
```

`kesif` is not optional for development. Without it the discovery tests **skip
silently** and the escalation-rate regression never runs, so the suite reports
green while covering less than you think. CI installs it for that reason.

Check it works:

```bash
pytest
ruff check src tests
npm --prefix web run test
npm --prefix web run build
```

`pyproject.toml` already sets `addopts = "-q"`. Adding another `-q` makes it
`-qq` and suppresses the pass/fail summary — run plain `pytest` when you want
the counts.

## Where things live

`AI_HANDOFF.md` has the full map and is worth ten minutes. The short version:

| | |
|---|---|
| `src/ads/contracts/` | every artifact shape; the gate's vocabulary |
| `src/ads/gates/` | the deterministic rules |
| `src/ads/intake/` | loading, routing, profiling |
| `src/ads/api/service.py` | the control plane and every route |
| `web/src/` | the frontend |
| `benchmarks/` | measuring instruments, not tests |
| `docs/SYSTEM_ARCHITECTURE_REPORT.md` | the current architecture; update it rather than writing a new dated report |

## Reporting something

Use the issue forms. Two rules that matter more than the fields:

- **Symptom, not diagnosis.** "Upload does nothing" is a fact; "the upload
  endpoint is broken" is a guess. In that case the endpoint was returning 200
  and the defect was elsewhere entirely.
- **Say what should be possible afterwards.** It is the only acceptance test the
  issue will have.

## Branches, commits, PRs

Branch from `main`: `fix/short-description`, `feat/…`, `docs/…`, `chore/…`.

Commit subjects are a single imperative line, sentence case, no prefix or tag.
The body explains **why** — the defect, the measurement, the reasoning. The diff
already covers what changed.

```text
Stop the Data projects upload from failing silently

Reported as "I can't upload any data". The API was fine -- POST /api/uploads
returns 200 -- and so was the client call. The handler was the problem: ...
```

Do not add `Co-Authored-By` or other attribution trailers. This history has
never used them.

Open a PR against `main`. It is protected: PRs only, `python` and `web` must
pass, linear history, no force-push, admins included. Squash merge.

## The bar for a change

**A test that fails without the fix.** Delete the fix, watch it go red, put it
back. A test that passes with the implementation removed is not a test, and
several tests here carry a comment naming the exact defect they were written
against. Match that.

Worth knowing how this goes wrong: a cache test that compared two profiles
passed whether or not the cache was consulted, because re-profiling unchanged
files returns the same answer either way. Removing the entire feature left it
green. Assert the mechanism — count the expensive call — not the output.

**Rebuild the bundle when you touch `web/src`.** The compiled frontend is
committed so the server runs from a checkout, and CI compares a fresh build
against it. A source change without a rebuild means the deployment silently
serves the previous UI. Line endings are pinned to LF by `.gitattributes`.

Two frontend PRs open at once **will** conflict, and it is not bad luck. Vite
names its output by content hash, so any two branches that both rebuild produce
differently-named files; whichever lands second collides on every asset plus
`index.html`. Auto-merge cannot resolve it, and the PR sits at `DIRTY` until
somebody intervenes. This has cost a manual pass on three PRs so far.

Nothing in the conflict is worth reading. The bundle is generated, so the
resolution is always to regenerate it rather than to pick sides:

```bash
git rebase origin/main                        # conflicts, all under src/ads/api/static
git checkout origin/main -- src/ads/api/static
npm --prefix web run build
git add -A src/ads/api/static
git rebase --continue
git push --force-with-lease
```

If a source file also conflicts, resolve that one normally first -- the recipe
above is only for the generated output.

The scheduling consequence is worth knowing before you start: land frontend
changes one at a time. Two people rebuilding in parallel is a guaranteed manual
merge for the second one, every time, regardless of whether the changes touch
the same components.

**Every new UI string needs a Turkish entry** in `web/src/lib/i18n.ts`.
`i18n.test.ts` enforces it. The catalogue falls back to English, so a missing
entry breaks nothing and quietly ships in the wrong language.

**Comments explain why, not what.** Where a bug motivated the code, say which
bug and what was measured.

**Commit by pathspec.** Two agents have shared this working tree, and a blanket
`git add -A` has swept away another agent's staged work.

## Invariants nobody may quietly relax

- Agents never see raw rows — not in prompts, not in error messages, not in
  panels.
- The gate is deterministic. An agent may challenge a verdict through a
  registered mechanism; it may not overrule one. Autonomy settings are the
  weakest input and can never weaken a hard rule.
- Unreviewed PDF tables and chart values are not training data.
- Artifact ids are derived, never chosen.
- Surface partial failures. Never let a source silently disappear, and never let
  one bad file take the others down with it.

The full list is in `AI_HANDOFF.md`. If a change requires breaking one, that is
a conversation, not a commit.

## Measuring

`benchmarks/understanding_v0` scores data understanding against real downloaded
data, with an answer key computed from the files rather than written. If you
change intake, profiling or schema discovery, run it and put the number in the
PR.

It found two defects on its first run that the whole existing suite missed —
both because every fixture here was written by someone who already knew the
schema. Yours will have the same blind spot.
