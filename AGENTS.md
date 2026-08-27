# AGENTS.md — read this before you touch anything

You are a coding agent working on Agentic DS with three other people and, quite
possibly, other agents at the same time. This file is the operating manual for
that. `AI_HANDOFF.md` tells you what the system *is*; this tells you how to work
on it without standing on someone.

Read this, then `AI_HANDOFF.md`, then the code.

## The loop you are part of

```text
a person tests the product and finds something
      └─► an issue                      (they file it, or they describe it to you and you file it)
             └─► an agent claims it     (/claim — a lease, so two of us never collide)
                    └─► branch, fix, prove the fix
                           └─► PR + CI + conflict radar
                                  └─► a human reviews and merges
```

You do not merge. You do not push to `main` — it is protected and will refuse
you. A human decides what lands.

## Filing an issue from what a supervisor told you

You will often be handed a problem in conversation rather than in writing:
*"upload is broken on the projects page"*. Turn that into an issue before you
start, because the issue is what another agent will read if you are interrupted,
and what a reviewer will check your PR against.

Use the forms in `.github/ISSUE_TEMPLATE/` — **Bug report** or **UX change** —
and fill them the way you would want to receive them:

- **Write the symptom, not your diagnosis.** *"Upload does nothing"* is a fact.
  *"The upload endpoint is broken"* is a guess, and in that exact case it was
  wrong: the endpoint returned 200 the whole time and the defect was a silent
  `return` in the handler. A diagnosis in the title sends the next person to the
  wrong file.
- **Say what should be possible afterwards.** This is the closest thing to an
  acceptance test the issue will get. "Make it nicer" cannot be verified; "the
  warnings are visible without scrolling" can.
- **Ask the supervisor the question now**, while they are still in the
  conversation. You cannot ask them at 2am when another agent picks this up.
- **One issue per problem.** Two bugs in one issue means one of them gets fixed
  and the issue gets closed.

If you are unsure whether it is a bug or a design opinion, file it as a UX
change. A wrong label is cheap; a lost report is not.

## Claiming work

gh-tower is installed. Before editing anything:

```bash
gh tower status          # who is touching what, right now
gh tower claim 42        # take issue #42
gh tower release 42 "done; tests still missing"
```

`/claim` as an issue comment does the same thing. It takes an advisory lease
recorded on the `tower-state` branch.

- **Leases warn, they do not block.** Velocity first. Correctness is enforced
  where it is cheap, which is CI.
- **Humans outrank agents.** On a collision, then the older claim wins. If you
  are the younger claim, rebase, split the work, or take something else.
- **One lease at a time.** No hold-and-wait, so no deadlock.
- A stale lease is reclaimed hourly (240-minute TTL), so a claim you forget does
  not park an issue forever.
- Check `gh tower status` before you start, not after you have a diff.

## Before you open a PR

Not negotiable, and not for you to decide otherwise:

**1. A test that fails without your fix.** Delete the fix, watch it go red, put
it back. A test that passes with the implementation removed is not a test.

This is the check reviewers are told to make first, because it is the one most
often faked convincingly. It has already caught me: a cache test compared two
profiles and passed whether or not the cache was consulted — removing the whole
feature left it green. Count the expensive call, or assert the specific wrong
output, not "the answer is still the answer".

**2. The full suite, plus ruff.**

```bash
pytest
ruff check src tests
```

**3. Rebuild the bundle if you touched `web/src`.**

```bash
npm --prefix web run build     # writes src/ads/api/static
git add src/ads/api/static
```

CI fails when the committed bundle does not match the sources, and the
deployment has silently served a stale UI because of this. Line endings are
pinned to LF by `.gitattributes`; do not fight it.

**4. A Turkish entry for every new UI string.** `web/src/lib/i18n.test.ts`
enforces it. The catalogue falls back to English, so an untranslated string
breaks nothing — it just ships in the wrong language, which is how a quarter of
the interface ended up English while the language was set to Turkish.

**5. A PR body that says what was *wrong*.** The diff already says what changed.
Say what the defect was, how you know, and what you measured.

## Things that will waste your day

- **The deployment does not run from the main checkout.** It serves from a git
  worktree on the `deploy` branch. Switching branches there changes what is
  being served.
- **Two repos, diverged histories.** `agentic-ds` is the curated line with
  protected `main`; `agentic-ds-dev` is the development line. Neither is an
  ancestor of the other, so porting is a cherry-pick, never a merge.
- **`pyproject.toml` sets `addopts = "-q"`.** Passing another `-q` makes it
  `-qq` and hides the pass/fail summary. Run plain `pytest` when you want counts.
- **Stacked PRs conflict the moment the base merges**, because squashing
  rewrites the commits yours was built on. Rebase onto the new `main` rather
  than resolving.
- **Column headers are slugged to snake_case, camelCase split first.**
  `movieId` becomes `movie_id`. Several heuristics read names as tokens, and
  folding that boundary away once made every integer key invisible to
  relationship detection.
- **Agents never see raw rows.** Not in prompts, not in error messages, not in
  panels. This is the product's reason to exist; see the invariants in
  `AI_HANDOFF.md`.

## Measuring, not asserting

`benchmarks/understanding_v0` scores data understanding against real downloaded
data with an answer key computed from the files. If you change intake,
profiling, or schema discovery, run it and put the number in your PR.

It found two defects on its first run that 900+ existing tests missed, both for
the same reason: every fixture in this repository was written by someone who
already knew the schema. Fixtures you write will have the same blind spot.

## Kill switch

If tower misbehaves, set the repository variable `TOWER_ENABLED` to `false`.
Nothing else depends on it.

---

`CONTRIBUTING.md` covers setup and conventions. `docs/TEAM_WORKFLOW.md` is the
same loop written for the humans in it.
