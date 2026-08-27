"""Per-user agent budgets, and the multiplier for the person paying for them.

One bucket for the whole process enforces a ceiling but not fairness: whoever
starts a pipeline first takes the minute's allowance and everyone else waits
behind them, with no way to tell a slow model from a busy colleague. These tests
are mostly about isolation between users, and about identity actually arriving
where the limiter can see it -- a run that executes as anonymous would silently
share one bucket with every other unattributed run, which is the bug this is
meant to remove.
"""

from __future__ import annotations

import contextvars

import pytest

from ads.llm.budget import (
    ANONYMOUS,
    BudgetedLLM,
    PerUserRateLimiter,
    bind_user,
    current_user,
    multipliers_from_env,
    start_worker,
)


class _Clock:
    """Monotonic time the test controls, so nothing actually sleeps."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept += seconds
        self.now += seconds


def _limiter(limit=2, multipliers=None):
    clock = _Clock()
    return (
        PerUserRateLimiter(
            requests_per_minute=limit, multipliers=multipliers or {}, sleep=clock.sleep
        ),
        clock,
    )


# ------------------------------------------------------------- allowances


def test_the_multiplier_raises_one_users_allowance() -> None:
    limiter, _ = _limiter(limit=20, multipliers={"ishak-ads": 10})
    assert limiter.limit_for("ishak-ads") == 200
    assert limiter.limit_for("berkin-ads") == 20


def test_everyone_else_gets_the_base_allowance() -> None:
    limiter, _ = _limiter(limit=20, multipliers={"ishak-ads": 10})
    for name in ("gonenc-ads", "emre-ads", None):
        assert limiter.limit_for(name) == 20


def test_a_limit_below_one_is_refused() -> None:
    """Zero would mean nobody can ever call, which is a configuration mistake
    rather than a budget."""
    with pytest.raises(ValueError):
        PerUserRateLimiter(requests_per_minute=0)


# -------------------------------------------------------------- isolation


def test_one_user_exhausting_their_budget_does_not_block_another() -> None:
    """The whole point. With a single shared bucket this test cannot pass."""
    limiter, clock = _limiter(limit=2)
    for _ in range(2):
        limiter.acquire("gonenc-ads", now=clock)
    assert clock.slept == 0.0

    waited = limiter.acquire("berkin-ads", now=clock)
    assert waited == 0.0, "a different user must not wait on somebody else's usage"


def test_exceeding_your_own_budget_waits() -> None:
    limiter, clock = _limiter(limit=2)
    for _ in range(2):
        limiter.acquire("gonenc-ads", now=clock)
    waited = limiter.acquire("gonenc-ads", now=clock)
    assert waited > 0.0


def test_the_window_frees_up_after_a_minute() -> None:
    limiter, clock = _limiter(limit=2)
    for _ in range(2):
        limiter.acquire("gonenc-ads", now=clock)
    clock.now += 61.0
    assert limiter.acquire("gonenc-ads", now=clock) == 0.0


def test_the_multiplied_user_gets_proportionally_more_calls() -> None:
    limiter, clock = _limiter(limit=2, multipliers={"ishak-ads": 10})
    for _ in range(20):
        limiter.acquire("ishak-ads", now=clock)
    assert clock.slept == 0.0, "20 calls must fit inside 2 x 10"
    assert limiter.acquire("ishak-ads", now=clock) > 0.0, "the 21st must wait"


def test_everyone_unauthenticated_shares_one_bucket() -> None:
    """Deliberate: a per-caller anonymous allowance would be a way around the
    limit rather than an instance of it."""
    limiter, clock = _limiter(limit=2)
    limiter.acquire(None, now=clock)
    limiter.acquire(ANONYMOUS, now=clock)
    assert limiter.acquire(None, now=clock) > 0.0


# --------------------------------------------------------------- identity


def test_the_limiter_uses_the_bound_user_when_none_is_passed() -> None:
    limiter, clock = _limiter(limit=2, multipliers={"ishak-ads": 10})
    with bind_user("ishak-ads"):
        for _ in range(20):
            limiter.acquire(now=clock)
    assert clock.slept == 0.0


def test_binding_is_undone_on_exit() -> None:
    with bind_user("ishak-ads"):
        assert current_user() == "ishak-ads"
    assert current_user() == ANONYMOUS


def test_a_worker_thread_keeps_the_callers_identity() -> None:
    """A plain threading.Thread would not, and every background run in the
    process would then be charged to the anonymous bucket."""
    seen: list[str] = []
    with bind_user("emre-ads"):
        thread = start_worker(lambda: seen.append(current_user()), name="t")
    thread.join(timeout=5)
    assert seen == ["emre-ads"]


def test_a_plain_thread_would_lose_it() -> None:
    """Pins why start_worker exists rather than assuming it is obvious."""
    import threading

    seen: list[str] = []
    with bind_user("emre-ads"):
        thread = threading.Thread(target=lambda: seen.append(current_user()))
        thread.start()
    thread.join(timeout=5)
    assert seen == [ANONYMOUS]


def test_context_is_not_leaked_between_workers() -> None:
    first: list[str] = []
    second: list[str] = []
    with bind_user("gonenc-ads"):
        t1 = start_worker(lambda: first.append(current_user()), name="a")
    t1.join(timeout=5)
    t2 = contextvars.copy_context().run(
        lambda: start_worker(lambda: second.append(current_user()), name="b")
    )
    t2.join(timeout=5)
    assert first == ["gonenc-ads"]
    assert second == [ANONYMOUS]


# ----------------------------------------------------------------- config


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ishak-ads=10", {"ishak-ads": 10}),
        ("ishak-ads=10,berkin-ads=2", {"ishak-ads": 10, "berkin-ads": 2}),
        (" ishak-ads = 10 ", {"ishak-ads": 10}),
        ("", {}),
        ("ishak-ads", {}),
        ("ishak-ads=abc", {}),
        ("ishak-ads=0", {}),
        ("ishak-ads=-3", {}),
    ],
)
def test_multiplier_parsing(raw: str, expected: dict[str, int]) -> None:
    """Malformed entries are skipped, not raised: the consequence of ignoring a
    typo here is that somebody gets the ordinary allowance, which does not
    deserve the same treatment as a missing credential."""
    assert multipliers_from_env(raw) == expected


# ---------------------------------------------------------------- wrapper


class _Fake:
    def __init__(self) -> None:
        self.calls = 0
        self.model = "fake-model"

    def generate_structured(self, *, system, prompt, json_schema, profile):
        self.calls += 1
        return "response"


def test_the_wrapper_charges_the_budget_and_forwards() -> None:
    limiter, clock = _limiter(limit=2)
    inner = _Fake()
    llm = BudgetedLLM(inner, limiter)
    with bind_user("gonenc-ads"):
        for _ in range(2):
            llm.generate_structured(system="s", prompt="p", json_schema={}, profile=None)
        assert inner.calls == 2
        assert clock.slept == 0.0


def test_the_wrapper_is_transparent_to_backend_extras() -> None:
    """Backends carry attributes beyond the protocol; callers reach for them."""
    limiter, _ = _limiter()
    assert BudgetedLLM(_Fake(), limiter).model == "fake-model"
