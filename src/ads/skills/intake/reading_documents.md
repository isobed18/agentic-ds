---
skill_id: intake.reading_documents
trigger: always
applies_to:
  - source_comprehension
source: Agentic DS document-understanding policy
---

When PDF evidence is present:

1. Identify every claim as document metadata, extracted page text, measured structured-data evidence, or inference.
2. Cite document claims with the supplied file name and page number.
3. Describe what the document appears to contain, its sections, decisions, entities, metrics, charts, and possible connections to structured tables.
4. Treat a chart, image, or table count as an inventory signal only. Do not claim its values were extracted or are fit for training unless a deterministic extraction artifact is supplied.
5. If pages have no text layer, say that OCR or a vision model is required. Never infer their content from the file name.
6. Prefer a compact answer: document purpose, relevant findings, connection hypotheses, unresolved questions, and recommended verification.
7. Do not repeat personal or sensitive passages. Summarize their role instead.
