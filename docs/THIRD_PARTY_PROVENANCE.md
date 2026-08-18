# Third-Party Provenance

This ledger records externally maintained code added during the dependency
integration phase. Licence is bookkeeping for later commercial review, not an
adoption gate in this phase.

| Package | Pinned range | Upstream | Licence | ADS use |
|---|---:|---|---|---|
| `pydantic-ai-slim` | `>=2.31,<3` | [pydantic/pydantic-ai](https://github.com/pydantic/pydantic-ai) | MIT | Agent turn, tool-result, structured-output and corrective-retry loop; no provider, telemetry, UI or MCP extras |
| `pandera[pandas]` | `>=0.32,<0.33` | [pandera-dev/pandera](https://github.com/pandera-dev/pandera) | BSD-3-Clause | Non-coercing dataframe pre/postconditions at materialized-copy and feature-experiment boundaries |
| `cleanlab` | `>=2.9,<3` | [cleanlab/cleanlab](https://github.com/cleanlab/cleanlab) | Apache-2.0 | Row-free label-issue summaries from inner-fold out-of-sample class probabilities; never automatic label deletion or a gate verdict |
| `skrub` (sandbox image) | `==0.10.0` | [skrub-data/skrub](https://github.com/skrub-data/skrub) | BSD-3-Clause | Train-only dirty-string/datetime vectorization replayed on validation inside the no-network sandbox; exploratory output only |

## PII detector candidates reviewed but not imported

| Component | Upstream | Result |
|---|---|---|
| Microsoft Presidio | https://github.com/microsoft/presidio | Deferred: useful future free-text recognizer, but Turkish needs a separately configured local NLP model and it does not replace column-level calibration. |
| Capital One DataProfiler | https://github.com/capitalone/DataProfiler | Rejected for the intake core: trained labeler requires the TensorFlow installation and published labels are predominantly English/US. |
| GLiNER | https://github.com/urchade/GLiNER | Deferred: local multilingual span detector, but model/runtime weight and Turkish table-level false-positive behavior are not yet calibrated. |

Runtime dependency resolution and air-gap verification are recorded with the
implementation tests rather than inferred from this table.

On 2026-08-18, a fresh Python process replaced both `socket.socket.connect` and
`socket.create_connection` with a recorder that raises, then imported Pydantic
AI 2.31.0, Pandera 0.32.1, Cleanlab 2.9.0, and skrub 0.10.0. All four imports
succeeded with zero connection attempts. This proves only import-time behavior.
The skrub Docker image rebuild/live test remains unverified because Docker
Desktop's WSL backend failed before the build began.
