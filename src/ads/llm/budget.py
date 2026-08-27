"""Per-user request budgets for agent calls.

The existing limiter is one bucket for the whole process. That is right when the
thing being protected is a single shared API key -- and wrong as soon as the
question is *who* is spending it. With one bucket, whoever starts a pipeline
first takes the whole minute's allowance and everyone else blocks behind them.
The limit is enforced but the fairness is not, and the person who waits has no
way to tell a slow model from a busy colleague.

So the budget is per user, and the multiplier is the exception. `ishak-ads` runs
the project and pays for the key; a flat allowance shared with four people would
make the owner the most throttled participant.

Identity arrives through a context variable rather than an argument. Agent calls
happen deep inside pipeline stages that have no business knowing about auth, and
threading a username through every one of them would put the concept in a dozen
signatures that do not otherwise care. The cost of that choice is that the
variable must be set deliberately at each entry point -- see `bind_user` -- and
a worker thread does not inherit it without `contextvars.copy_context()`.
"""

from __future__ import annotations

import contextvars
import os
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from ads.llm.client import LLMResponse, ModelProfile, StructuredLLM

#: Requests per minute allowed to one user before their calls start waiting.
DEFAULT_REQUESTS_PER_MINUTE = 20

#: Everyone who is not signed in shares this bucket. Deliberately one bucket
#: rather than one each: an unauthenticated path that minted a fresh allowance
#: per caller would be a way around the limit rather than an instance of it.
ANONYMOUS = "<anonymous>"

_CURRENT_USER: contextvars.ContextVar[str] = contextvars.ContextVar(
    "ads_current_user", default=ANONYMOUS
)


def current_user() -> str:
    return _CURRENT_USER.get()


@contextmanager
def bind_user(username: str | None) -> Iterator[None]:
    """Attribute agent calls in this context to `username`."""
    token = _CURRENT_USER.set(username or ANONYMOUS)
    try:
        yield
    finally:
        _CURRENT_USER.reset(token)


def start_worker(target: Callable[[], Any], *, name: str) -> threading.Thread:
    """Start a daemon thread that keeps the caller's user attribution.

    `threading.Thread` does not carry context variables across, so a run started
    by one person would otherwise execute as anonymous -- and every background
    run in the process would then share the anonymous bucket, which is exactly
    the contention this module exists to remove.
    """
    context = contextvars.copy_context()
    thread = threading.Thread(target=lambda: context.run(target), name=name, daemon=True)
    thread.start()
    return thread


def multipliers_from_env(raw: str | None = None) -> dict[str, int]:
    """Parse `ADS_AGENT_RPM_MULTIPLIERS`, e.g. ``ishak-ads=10,berkin-ads=2``.

    Malformed entries are skipped rather than raising. This is read at startup
    on a deployment that refuses to boot on a bad credential, and a typo in a
    fairness knob does not deserve the same treatment as a missing password --
    the consequence of ignoring it is that somebody gets the ordinary allowance.
    """
    source = os.environ.get("ADS_AGENT_RPM_MULTIPLIERS", "") if raw is None else raw
    multipliers: dict[str, int] = {}
    for entry in source.split(","):
        name, separator, value = entry.partition("=")
        if not separator:
            continue
        name = name.strip()
        try:
            factor = int(value.strip())
        except ValueError:
            continue
        if name and factor >= 1:
            multipliers[name] = factor
    return multipliers


class _Window:
    """A fixed-window counter for one user."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.calls: list[float] = []


class PerUserRateLimiter:
    """One request budget per user, blocking rather than failing.

    Blocking matches the existing limiter and is right for a pipeline stage: a
    run that pauses for a few seconds is fine, a run that aborts halfway through
    synthesis is not.
    """

    def __init__(
        self,
        *,
        requests_per_minute: int = DEFAULT_REQUESTS_PER_MINUTE,
        multipliers: Mapping[str, int] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if requests_per_minute < 1:
            raise ValueError("requests_per_minute must be at least 1")
        self.requests_per_minute = requests_per_minute
        self.multipliers = dict(multipliers or {})
        self._sleep = sleep
        self._windows: dict[str, _Window] = {}
        self._lock = threading.Lock()

    def limit_for(self, username: str | None) -> int:
        """The per-minute allowance for one user."""
        name = username or ANONYMOUS
        return self.requests_per_minute * self.multipliers.get(name, 1)

    def acquire(
        self,
        username: str | None = None,
        *,
        now: Callable[[], float] = time.monotonic,
    ) -> float:
        """Block until this user has a slot. Returns seconds waited."""
        name = username if username is not None else current_user()
        waited = 0.0
        while True:
            with self._lock:
                window = self._windows.get(name)
                if window is None or window.limit != self.limit_for(name):
                    # Rebuilt when the allowance changes so a multiplier added
                    # at runtime does not leave a stale ceiling in place.
                    window = _Window(self.limit_for(name))
                    self._windows[name] = window
                current = now()
                window.calls = [t for t in window.calls if current - t < 60.0]
                if len(window.calls) < window.limit:
                    window.calls.append(current)
                    return waited
                delay = 60.0 - (current - window.calls[0])
            pause = max(delay, 0.01)
            self._sleep(pause)
            waited += pause


class BudgetedLLM:
    """Charge one user's budget before each agent call.

    Wraps the backend rather than living inside it, so every backend is limited
    the same way. `claude_cli` and `ollama` are local or already-paid, but the
    reason to limit them is not cost -- it is that one runaway pipeline should
    not starve four other people of the same finite machine.
    """

    def __init__(self, inner: StructuredLLM, limiter: PerUserRateLimiter) -> None:
        self._inner = inner
        self._limiter = limiter

    def generate_structured(
        self,
        *,
        system: str,
        prompt: str,
        json_schema: dict[str, Any],
        profile: ModelProfile,
    ) -> LLMResponse:
        self._limiter.acquire()
        return self._inner.generate_structured(
            system=system, prompt=prompt, json_schema=json_schema, profile=profile
        )

    def __getattr__(self, name: str) -> Any:
        # Backends carry extras beyond the protocol (model names, close()).
        # Forwarding keeps the wrapper invisible to anything that reaches past
        # generate_structured.
        return getattr(self._inner, name)


__all__ = [
    "ANONYMOUS",
    "DEFAULT_REQUESTS_PER_MINUTE",
    "BudgetedLLM",
    "PerUserRateLimiter",
    "bind_user",
    "current_user",
    "multipliers_from_env",
    "start_worker",
]
