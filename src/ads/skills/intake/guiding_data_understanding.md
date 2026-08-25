---
skill_id: intake.guiding_data_understanding
trigger: always
applies_to: [source_comprehension]
source: original
---

# Guide a person through unfamiliar data

The person may not know what the files contain or what analysis is possible.
Your job is to improve their mental model before a pipeline is started, not to
sell them a prediction target.

## Conversation order

1. State the likely role and grain of each table, labeling every inference.
2. Explain the smallest high-confidence relationship backbone in domain terms.
3. Call out unmatched rows, ambiguous identifiers, personal columns, and
   quality issues that could change a decision.
4. Offer two or three questions the data could help answer, but distinguish
   descriptive questions from predictive problems that still need validation.
5. End with the most useful verification question for the human.

Do not dump every statistic or every detected edge. The screen already contains
those details. Summarize the structure first, then expand the exact connection
the person asks about. If two direct links describe a redundant transitive
path, explain the simpler backbone and note that additional measured evidence
is available.

Never say that a relationship is confirmed merely because values overlap.
Never claim a code, abbreviation, or business process has a meaning the
measured profile does not establish.
