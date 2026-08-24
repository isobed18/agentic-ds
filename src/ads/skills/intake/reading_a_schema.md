---
skill_id: intake.reading_a_schema
trigger: always
applies_to: [schema_discovery, source_comprehension]
source: original
---

# Reading a schema that nobody documented

The sources are flat files. They declare no keys, no foreign keys, and no
relationships. Everything you know about how these tables connect was measured
from the values themselves.

## What a measured relationship is, and is not

An overlap is evidence, not proof. Three numbers matter and they say different
things:

- **Row overlap** — how many rows on the left find a match. High row overlap
  with low distinct overlap means a few frequent values carry the whole score;
  that is a coincidence, not a key.
- **Parent coverage** — how much of the right table is used. Low coverage means
  most of the parent is unreferenced, which is often a sign the join is wrong.
- **Orphan rate** — rows that match nothing. These vanish from the joined table.

## Names lie in both directions

`provider_ref` and `physician_id` are the same thing and share no characters.
`customer_id` in two tables may be two different id spaces. Judge the values;
use names only to explain a relationship you already measured, never to assert
one you did not.

## Direction and grain

Say which table owns the rows. "One physician has many transactions" is the
useful sentence; "these tables are related" is not. Where a join would multiply
rows, say so — silently fanning out a base table is how a row count triples
between stages and nobody notices until the model is strange.

## What to draw

The base entity, the tables joined onto it, and the measured strength of each
edge. An unmeasured or weak edge should look different from a strong one rather
than being omitted, because a person deciding whether to trust the plan needs to
see what was uncertain.
