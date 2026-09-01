# PDF extraction v0

This benchmark proves that the core, offline document engine can recover one
structured table from the same local demo offered in the product. The PDF and
its CSV oracle live together under `src/ads/testing/fixtures/pdf_demo`; neither
the UI nor this scorer downloads data.

Run it from the repository root:

```powershell
.venv\Scripts\python.exe benchmarks\pdf_extraction_v0\score.py
```

Success requires exactly one candidate table, the expected page provenance,
exact columns and row count, and 100% cell agreement with the CSV oracle. The
printed result contains metrics only, never the source rows.
