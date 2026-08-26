# Team workflow: people report, agents fix, humans merge

Four people are testing this product and one week is not long. The bottleneck is
not finding bugs — it is that each fix costs someone an afternoon. This is the
arrangement that removes that cost without letting quality slide.

```text
person finds a problem
      └─► issue (structured form)
             └─► an agent claims it            (gh-tower lease, so two never collide)
                    └─► branch, fix, test
                           └─► PR + CI + conflict radar
                                  └─► a human reviews and merges
```

The human stays at both ends: reporting and merging. Nothing reaches `main`
because an agent thought it was ready.

## What is actually available

Worth stating plainly, because one obvious option is not on the table.

| Option | Status |
|---|---|
| **GitHub Copilot coding agent** (assign an issue to Copilot) | **Not available.** It is absent from this repo's assignable actors — not entitled on this account. |
| **GitHub Models** | Not available (`410 Gone`). |
| **gh-tower** | Available. It is our own, and it was built for exactly this. |
| **Claude Code / Codex, driven locally** | Available. This is what does the work. |

So the loop is: a person drives an agent on their own machine, and gh-tower
keeps those agents from standing on each other.

## Reporting

Two forms, under **Issues → New issue**:

- **Bug report** — something behaves wrongly.
- **UX change or recommendation** — it works but is the wrong shape.

Both ask for one thing that feels like bureaucracy and is not: **what should be
possible afterwards**. An agent cannot verify "make it nicer". It can verify
"the warnings are visible without scrolling". Issues that skip this come back as
plausible changes nobody asked for.

Report the symptom, not your diagnosis. "Upload does nothing" led to the real
cause — a silent `return` when a record had not loaded. A report saying "fix the
upload endpoint" would have sent the fix to the wrong file; the endpoint was
returning 200 the whole time.

## Claiming work

gh-tower is installed. Comment on the issue:

```text
/claim
```

That takes an advisory lease and records it on the `tower-state` branch, so
anyone — person or agent — can see who is touching what before they start.

```bash
gh tower status          # who is on what
gh tower claim 42
gh tower release 42 "done, tests missing"
```

Leases **warn, they do not block**. Velocity first; correctness is enforced
where it is cheap, which is CI. A stale lease is reclaimed automatically
(hourly, 240-minute TTL), so a forgotten claim does not park an issue forever.

On a collision: **humans outrank agents**, then the older claim wins. The
younger party rebases, splits the work, or picks something else.

## What an agent must do before opening a PR

Not optional, and not negotiable by the agent:

1. **A test that fails without the fix.** Delete the fix, watch it go red, put
   it back. A test that passes with the implementation removed is not a test.
2. **The full suite green**, plus `ruff check src tests`.
3. **Rebuild the frontend bundle** if anything under `web/src` changed —
   `npm --prefix web run build`, then commit `src/ads/api/static`. CI fails if
   the committed bundle does not match the sources, and the deployment has
   silently served a stale UI because of this before.
4. **A Turkish entry for every new UI string.** `web/src/lib/i18n.test.ts`
   enforces it. The catalogue falls back to English, so an untranslated string
   does not break anything — it just quietly ships in the wrong language, which
   is how a quarter of the interface ended up English while the language was set
   to Turkish.
5. **A PR body that says what was wrong**, not what was changed. The diff
   already says what was changed.

## Reviewing

Reviewers: check the test actually fails without the fix. That is the single
highest-value thing you can look at, and it is the one an agent is most likely
to have faked convincingly — a test asserting the same thing twice passes either
way.

The conflict radar leaves a sticky comment on every PR listing other open PRs
and active leases touching the same files. Read it before merging two things
that look independent.

`main` is protected: PRs only, `python` and `web` must pass, linear history, no
force-push. That applies to admins too.

## Merging

Squash merge. If a PR was stacked on another, expect the stacked one to conflict
the moment the base merges — squashing rewrites the commits it was built on.
Rebase the stacked branch onto the new `main` rather than trying to resolve it.

## Kill switch

If tower misbehaves, set the repository variable `TOWER_ENABLED` to `false`. All
four workflows check it and stop. Nothing else in the project depends on them.
