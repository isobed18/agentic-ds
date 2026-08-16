"""Control plane: a read-side view over run state, plus gate answers.

Adds no state of its own. Everything served already lives in the artifact store,
so the UI is a view over the run rather than a second system that can disagree
with it.
"""

from ads.api.service import ControlPlane, RunSummary, create_app

__all__ = ["ControlPlane", "RunSummary", "create_app"]
