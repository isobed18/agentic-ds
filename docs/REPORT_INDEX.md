# Report index

## Canonical current reports

- [`SYSTEM_ARCHITECTURE_REPORT.md`](SYSTEM_ARCHITECTURE_REPORT.md) — current product and technical
  architecture, detailed flow, major modules, deployment, constraints, and next risks.
- [`PRODUCT_DELIVERY_REPORT.md`](PRODUCT_DELIVERY_REPORT.md) — current delivered behavior,
  verification, known gaps, and recommended next delivery.

## Presentation

- [`sunum.html`](sunum.html) — architecture walkthrough for a technical audience. A single
  self-contained HTML deck: the whole pipeline is drawn once and the slides move a camera over it,
  zooming into each stage and its internal modules. Open it in a browser; there is no build step.
  Content lives in the `NODES` / `EDGES` / `SLIDES` arrays near the top of the script, so a stage's
  wording is a one-line edit and nobody touches SVG coordinates.

## Supporting evidence and research

- [`assurance-benchmark-report.md`](assurance-benchmark-report.md) — product differentiation and
  assurance benchmark design; this is evidence/research, not the current architecture.
- [`AGENT_INFRASTRUCTURE_RESEARCH.md`](AGENT_INFRASTRUCTURE_RESEARCH.md) — agent runtime research.
- [`PII_DETECTION_RESEARCH.md`](PII_DETECTION_RESEARCH.md) — privacy-detection research.
- [`DETERMINISTIC_ENGINE_HARDENING.md`](DETERMINISTIC_ENGINE_HARDENING.md) and
  [`HARDENING_TRACK.md`](HARDENING_TRACK.md) — hardening work and remaining risks.
- [`THIRD_PARTY_PROVENANCE.md`](THIRD_PARTY_PROVENANCE.md) — third-party provenance.
- [`workflow-framework-decision.md`](workflow-framework-decision.md) — historical framework decision
  retained because it records rationale not duplicated in the current reports.

Historical architecture and implementation reports were removed after their still-valid material
was consolidated into the canonical reports above. New architecture updates should edit the
canonical report rather than create another date-suffixed copy.
