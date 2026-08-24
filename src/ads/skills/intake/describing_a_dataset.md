---
skill_id: intake.describing_a_dataset
trigger: always
applies_to: [source_comprehension]
source: original
---

# Describing a dataset someone has never seen

The person reading this dropped in files they may not have made. Before any
model exists they need three things, in this order: what this data is about,
how the tables relate, and what is wrong with it. Nothing else earns space yet.

## What to say, and what not to

**Say what the data is about.** Two or three sentences. Name the entity each
table describes and the process the rows record — "one row per physician, plus
their compensation and the transactions they generated". Read the column names
and grains, not just the table names; a table called `t_dim_02` still has
columns.

**Do not restate the profile.** They can see that the table has 800 rows and
12 columns; the numbers are on the screen next to your text. Saying it back
costs their attention and buys nothing.

**Do not invent domain knowledge.** If the columns do not tell you what a code
means, say the code is unexplained rather than guessing what it stands for. An
invented meaning is worse than an admitted gap, because they will believe it.

## Relationships

For each measured relationship, say what it means in the domain: which entity
owns which rows, and whether it is one-to-one or one-to-many. A join found
between differently named columns is worth pointing out explicitly — the person
may not know those two columns are the same thing, and that is exactly the sort
of thing they hired the system to notice.

**Unmatched rows are the finding, not a footnote.** If 5.8% of ledger rows have
no matching physician, those rows will be dropped from the joined table and
disappear from the analysis. Say so, say how many, and say what they might be —
non-physician vendors, data-entry errors, a different id space. That single
sentence is often the most valuable thing on the screen.

## Quality

Lead with what would change a decision: a column that is mostly empty, a key
that is not unique, a date column that failed to parse. Anything below the
thresholds the profile already flags does not need a mention.

## Length

Shorter than you think. A person deciding whether to proceed reads the first
paragraph and skims the rest. Put the thing that would stop them at the top.
