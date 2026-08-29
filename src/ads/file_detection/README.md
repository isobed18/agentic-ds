# File detection

This subsystem identifies an unfamiliar file from its content and routes it
before the data pipeline reads it. When the evidence cannot determine a safe
route, it records why and asks for adjudication instead of guessing.

## Layers

| Layer | Responsibility | Cost | Decision source |
|---|---|---:|---|
| Magika | Candidate file format | ~4 ms | deterministic |
| `text_shape.py` | Table, fixed width, log, key/value, or prose | microseconds | deterministic |
| `evidence.py` | Encoding, delimiter, and number format | microseconds | deterministic |
| `adjudication.py` | Resolve only the remaining ambiguous files | human response time | explicit human choice |

Adjudication deliberately lives outside the deterministic server. It receives
a closed list of measured options and never turns a model opinion into
gate-eligible evidence. An opt-in unattended mode uses a fixed fallback chain
and labels its results as automatic defaults.

The deterministic sample batch currently routes all 25 files correctly, with
22 decided automatically and 3 escalated. The three escalations are genuinely
ambiguous: a one-word text file, a four-row CSV Magika labels incorrectly with
0.953 confidence, and bytes that may be binary or incorrectly encoded text.

## Format-specific behavior

- Image and scanned-PDF content goes through OCR. Table geometry and numbers
  remain usable, but text is escalated when the recognition model's dictionary
  cannot emit every Turkish character. Confidence cannot replace this check:
  the bundled model produced a wrong Turkish word with 0.828 confidence.
- XLSX is measured as a workbook, not treated as a generic ZIP container.
  Sheets are opened and inspected; choosing among several tabular sheets is a
  preference question.
- Parquet is accepted only when its leading and trailing `PAR1` signatures are
  present. A detector label alone is not enough.
- PDF routing measures the text layer. A scanned PDF is not silently sent to a
  text extractor that would return nothing.

OCR is an optional installation because its dependencies add roughly 245 MB:

```bash
pip install -e ".[kesif-ocr]"
```

Models are bundled and no runtime download occurs. Set
`KESIF_OCR_REC_MODEL` to evaluate another recognition model; capability is
measured from its dictionary rather than assumed from a confidence threshold.

## Design rules

1. Measure; do not guess. Every result carries its evidence.
2. Do not decide when evidence is insufficient. Record the reason.
3. Treat the filename extension as a claim, not a measurement.
4. Parse what can be parsed. Successful structural parsing outranks a label.
5. Apply the cheap pass to every file and expensive work only where selected.
6. Measure factual questions and ask preference questions.
7. Keep adjudication distinct from measurement in persisted provenance.

## Package map

```text
src/ads/file_detection/
  models.py        findings, evidence, options, and reports
  evidence.py      encoding, delimiter, and numeric-format measurements
  text_shape.py    structural text-shape measurement
  router.py        deterministic routing, inventory, and option construction
  readers.py       bounded previews after a choice is made
  adjudication.py  human and opt-in deterministic fallback decisions
  server.py        optional MCP server
  sample_batch.py  deterministic mixed-format regression corpus
  formats/
    text.py        CSV, TSV, and text inspection
    pdf.py         text-layer measurement and scanned-PDF handling
    image.py       OCR, table geometry, and Turkish-character capability
    tabular.py     XLSX/XLSM sheet inspection
```

The `ads.kesif` package is a temporary import-compatibility layer while
repository consumers migrate to this English package.
