"""Run Experiment B locally on a port distinct from the base UI."""

from __future__ import annotations

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "alternatives.prefect_ui.app:app",
        host="127.0.0.1",
        port=8078,
        reload=False,
    )
