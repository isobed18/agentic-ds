"""The DeepSeek backend: reasoning, budget, and who is allowed to spend the key.

Every assertion here was written against a behaviour measured on the live API,
not against the documentation, because the documentation and the API disagree
in ways that cost money:

- ``reasoning_effort: minimal``/``low`` and ``enable_thinking: false`` are
  accepted and then ignored. Only ``thinking: {"type": "disabled"}`` works.
- ``response_format: {"type": "json_schema"}`` is rejected outright.
- With reasoning on and a small budget, the model spends the entire allowance
  thinking and returns **empty content** with ``finish_reason: "length"``.

The suite must not touch the network, so the client takes an injected transport.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from ads.llm import DeepSeekClient, RateLimiter
from ads.llm.client import ModelProfile

SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
}


class FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self) -> dict[str, Any]:
        return self._payload


def _completion(content: str, *, finish_reason: str = "stop", **usage: Any) -> dict[str, Any]:
    return {
        "model": "deepseek-v4-flash",
        "choices": [{"finish_reason": finish_reason, "message": {"content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, **usage},
    }


def _client(response: dict[str, Any], status_code: int = 200):
    sent: list[dict[str, Any]] = []

    def transport(url, *, json=None, headers=None, timeout=None):  # noqa: A002
        sent.append({"url": url, "body": json, "headers": headers})
        return FakeResponse(response, status_code)

    client = DeepSeekClient(api_key="test-key", transport=transport)
    return client, sent


def test_reasoning_is_disabled_with_the_only_switch_that_works() -> None:
    """`reasoning_effort` and `enable_thinking` are silently ignored by the API.

    Measured: identical prompt, 174 reasoning tokens by default versus 0 with
    `thinking.disabled` -- and 200 completion tokens versus 21.
    """
    client, sent = _client(_completion('{"summary":"ok"}'))
    client.generate_structured(
        system="s", prompt="p", json_schema=SCHEMA, profile=ModelProfile(name="ignored")
    )

    body = sent[0]["body"]
    assert body["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in body, "this parameter does not work; do not imply it does"
    assert "enable_thinking" not in body, "this parameter does not work either"


def test_schema_travels_in_the_prompt_because_strict_mode_is_unavailable() -> None:
    """`json_schema` response_format returns 'unavailable now', so the contract
    has to reach the model as text and be validated on the way back."""
    client, sent = _client(_completion('{"summary":"ok"}'))
    client.generate_structured(
        system="Be terse.", prompt="p", json_schema=SCHEMA, profile=ModelProfile(name="ignored")
    )

    body = sent[0]["body"]
    assert body["response_format"] == {"type": "json_object"}
    assert "summary" in body["messages"][0]["content"], "the schema must reach the model"


def test_a_json_reply_is_parsed_and_reported_as_valid() -> None:
    client, _ = _client(_completion('{"summary":"1200 rows"}'))
    response = client.generate_structured(
        system="s", prompt="p", json_schema=SCHEMA, profile=ModelProfile(name="ignored")
    )
    assert response.parsed == {"summary": "1200 rows"}
    assert response.is_valid_json
    assert response.metadata["backend"] == "deepseek_api"


def test_non_json_is_reported_not_raised() -> None:
    """A malformed body is the agent runtime's problem to repair, not a crash."""
    client, _ = _client(_completion("Here you go: {nope}"))
    response = client.generate_structured(
        system="s", prompt="p", json_schema=SCHEMA, profile=ModelProfile(name="ignored")
    )
    assert response.parsed is None
    assert response.parse_error


def test_budget_exhausted_by_reasoning_says_so_instead_of_returning_nothing() -> None:
    """The exact live failure: `finish_reason: length` with empty content.

    Returning that as-is produces a confusing downstream parse error about an
    empty string, which says nothing about the cause.
    """
    client, _ = _client(
        _completion("", finish_reason="length", completion_tokens_details={"reasoning_tokens": 200})
    )
    with pytest.raises(RuntimeError) as caught:
        client.generate_structured(
            system="s", prompt="p", json_schema=SCHEMA, profile=ModelProfile(name="ignored")
        )
    assert "token limit" in str(caught.value)
    assert "200" in str(caught.value), "name the reasoning spend that caused it"


def test_an_api_error_surfaces_its_status_and_body() -> None:
    client, _ = _client({"error": {"message": "nope"}}, status_code=429)
    with pytest.raises(RuntimeError) as caught:
        client.generate_structured(
            system="s", prompt="p", json_schema=SCHEMA, profile=ModelProfile(name="ignored")
        )
    assert "429" in str(caught.value)


class TestRateLimiter:
    def test_requests_within_the_budget_do_not_wait(self) -> None:
        limiter = RateLimiter(requests_per_minute=3, sleep=lambda _: None)
        clock = iter([0.0, 1.0, 2.0])
        assert all(limiter.acquire(now=lambda: next(clock)) == 0.0 for _ in range(3))

    def test_the_fourth_request_in_a_minute_blocks(self) -> None:
        """Blocking beats failing: a stage that pauses is fine, a run that dies
        halfway through synthesis is not."""
        slept: list[float] = []
        limiter = RateLimiter(requests_per_minute=2, sleep=slept.append)

        times = [0.0, 1.0, 2.0, 61.0]
        index = {"i": 0}

        def clock() -> float:
            value = times[min(index["i"], len(times) - 1)]
            index["i"] += 1
            return value

        limiter.acquire(now=clock)
        limiter.acquire(now=clock)
        waited = limiter.acquire(now=clock)

        assert slept, "the third call in the window must wait"
        assert waited > 0

    def test_a_zero_budget_is_rejected_rather_than_deadlocking(self) -> None:
        with pytest.raises(ValueError):
            RateLimiter(requests_per_minute=0)


# ------------------------------------------------- who may spend the key

import json as _json  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from ads.api import create_app  # noqa: E402
from ads.api.auth import hash_password  # noqa: E402

_PASSWORD = "correct-horse-battery"


def _app(
    tmp_path, monkeypatch, *, users: dict[str, str], allowed: str | None, backend: str = "deepseek"
):
    monkeypatch.setenv("ADS_LLM_BACKEND", backend)
    monkeypatch.setenv("DEEPSEEK_API", "test-key")
    monkeypatch.setenv("ADS_AUTH_SECRET", "test-secret")
    monkeypatch.setenv("ADS_AUTH_SECURE_COOKIE", "0")
    monkeypatch.delenv("ADS_AUTH_USERNAME", raising=False)
    monkeypatch.delenv("ADS_AUTH_PASSWORD_HASH", raising=False)
    monkeypatch.setenv(
        "ADS_AUTH_USERS_JSON",
        _json.dumps({name: hash_password(password) for name, password in users.items()}),
    )
    if allowed is None:
        monkeypatch.delenv("ADS_DEEPSEEK_USERS", raising=False)
    else:
        monkeypatch.setenv("ADS_DEEPSEEK_USERS", allowed)
    # The guard is a status-code contract. Without this, an unrelated 500
    # from a backend that is not running here is re-raised and hides it.
    return TestClient(create_app(tmp_path), follow_redirects=False, raise_server_exceptions=False)


def _login(client: TestClient, username: str) -> None:
    res = client.post("/api/auth/login", json={"username": username, "password": _PASSWORD})
    assert res.status_code == 200, res.text


def test_a_teammate_cannot_spend_the_owners_deepseek_key(tmp_path, monkeypatch) -> None:
    """The point of the allowlist. Without it any signed-in teammate could start
    a run that bills one person's API key, which is how a shared key quietly
    becomes an expensive one."""
    client = _app(
        tmp_path,
        monkeypatch,
        users={"ishak-ads": _PASSWORD, "emre-ads": _PASSWORD},
        allowed="ishak-ads",
    )
    _login(client, "emre-ads")

    res = client.post("/api/runs/staged", json={"source_id": "whatever"})
    assert res.status_code == 403
    assert "ishak-ads" in res.json()["detail"]
    assert "emre-ads" in res.json()["detail"], "say which account was refused"


def test_the_owner_is_not_blocked(tmp_path, monkeypatch) -> None:
    client = _app(tmp_path, monkeypatch, users={"ishak-ads": _PASSWORD}, allowed="ishak-ads")
    _login(client, "ishak-ads")

    res = client.post("/api/runs/staged", json={"source_id": "does-not-exist"})
    assert res.status_code != 403, "the owner must pass the guard"


def test_the_allowlist_defaults_to_the_key_owner(tmp_path, monkeypatch) -> None:
    """Unset means ishak-ads only -- not 'everyone', which would be the
    dangerous default for a paid backend."""
    client = _app(
        tmp_path,
        monkeypatch,
        users={"ishak-ads": _PASSWORD, "gonenc-ads": _PASSWORD},
        allowed=None,
    )
    _login(client, "gonenc-ads")
    assert client.post("/api/runs/staged", json={"source_id": "x"}).status_code == 403


def test_planner_chat_is_gated_too(tmp_path, monkeypatch) -> None:
    """Chat reaches the model just as a run does, so gating only runs would
    leave the key spendable through the other door."""
    client = _app(
        tmp_path,
        monkeypatch,
        users={"ishak-ads": _PASSWORD, "berkin-ads": _PASSWORD},
        allowed="ishak-ads",
    )
    _login(client, "berkin-ads")
    assert client.post("/api/planner/chat", json={"message": "hi"}).status_code == 403


def test_a_free_backend_gates_nobody(tmp_path, monkeypatch) -> None:
    """The guard must be specific to the paid backend. Ollama is local and the
    Claude CLI is a shared subscription; restricting those would be pointless."""
    client = _app(
        tmp_path,
        monkeypatch,
        users={"emre-ads": _PASSWORD},
        allowed="ishak-ads",
        backend="ollama",
    )
    _login(client, "emre-ads")
    assert client.post("/api/planner/chat", json={"message": "hi"}).status_code != 403
