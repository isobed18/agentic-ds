# Third-Party Notices

This project is released under the MIT License (see [`LICENSE`](LICENSE)). It
depends on, and in one case redistributes, third-party software under the
licences recorded here.

`docs/THIRD_PARTY_PROVENANCE.md` records *why* each dependency was adopted and
what it is allowed to do inside the system. This file records the licence
obligations that travel with redistribution.

## Redistributed in this repository

The built frontend bundle under `src/ads/api/static/assets/` is committed to
the repository (CI requires the built asset to match `web/src`). That bundle
therefore contains third-party code:

### elkjs — Eclipse Layout Kernel

* Upstream: <https://github.com/kieler/elkjs>
* Version: 0.12.0
* Licence: **EPL-2.0 OR GPL-3.0-or-later** (dual-licensed; this project relies
  on the **EPL-2.0** option)
* EPL-2.0 text: <https://www.eclipse.org/legal/epl-2.0/>
* Source: available from the upstream repository above and from
  <https://registry.npmjs.org/elkjs/-/elkjs-0.12.0.tgz>

elkjs is used unmodified, for graph layout in the pipeline canvas. It is
minified into `src/ads/api/static/assets/elk.bundled-*.js` by the bundler,
which strips the upstream licence header; this notice preserves the
attribution that header carried.

EPL-2.0 is a file-scoped ("weak") copyleft licence. It applies to the elkjs
files themselves, not to the rest of this project: combining EPL-2.0 code with
separately licensed code in a larger work is permitted, and this project's own
source stays under the MIT License above. Anyone redistributing this
repository, or a build made from it, must keep this notice and must make the
elkjs source available on request.

The remainder of the bundled frontend tree is permissive — MIT, ISC,
Apache-2.0, BSD-3-Clause and CC-BY-4.0 — and imposes attribution only.
Notable direct dependencies: React and React DOM (MIT), React Router (MIT),
`@xyflow/react` (MIT), Tailwind CSS (MIT).

## Runtime dependencies (not redistributed)

Installed from PyPI at setup time rather than vendored here, so their licence
texts ship with the installed packages:

| Package | Licence |
|---|---|
| pydantic, pydantic-ai-slim | MIT |
| pandas, numpy, scikit-learn, joblib, httpx | BSD-3-Clause |
| pandera, skrub | BSD-3-Clause |
| polars, duckdb, openpyxl, pyyaml | MIT |
| pyarrow, cleanlab | Apache-2.0 |
| pypdf | BSD-3-Clause |
| magika (optional, file detection) | Apache-2.0 |
| docling (optional, PDF extraction) | MIT |
| rapidocr-onnxruntime, opencv-python-headless (optional, OCR) | Apache-2.0 |
| mcp (optional) | MIT |

All of the above are permissive. No GPL, AGPL or LGPL dependency is used.
