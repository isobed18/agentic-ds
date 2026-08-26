"""Structured LLM adapter for the DeepSeek API.

This is a **remote, paid** inference path. It is opt-in, it is not the default,
and -- like the Claude CLI backend -- it must never be described as local.

Three properties of the API shaped this adapter, all measured against the live
endpoint rather than assumed:

*Reasoning is on by default and it is expensive.* On an identical prompt,
``deepseek-v4-flash`` spent 174 reasoning tokens and 200 completion tokens with
default settings, and 0 reasoning tokens and 21 completion tokens with reasoning
disabled -- the same answer for a tenth of the budget. Worse, with a small
``max_tokens`` the reasoning consumed the entire allowance and the reply came
back with **empty content** and ``finish_reason: "length"``. Reasoning is
therefore disabled here, not merely lowered.

*Only one of the documented switches actually works.* ``reasoning_effort``
(both ``minimal`` and ``low``) and ``enable_thinking: false`` are accepted
without error and then ignored -- the model reasons anyway. ``thinking:
{"type": "disabled"}`` is the one that takes effect. An unknown parameter is
not rejected, so "the request succeeded" proves nothing here.

*Strict schemas are unavailable.* ``response_format: {"type": "json_schema"}``
returns ``"This response_format type is unavailable now"``. Only ``json_object``
is supported, so the schema travels in the system prompt and the response is
validated by the caller, which ``ads.agents.base`` already does for every
backend.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

import httpx

from ads.llm.client import LLMResponse, ModelProfile, dereference_schema

DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"
DEFAULT_DEEPSEEK_TIMEOUT = 300.0
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"

#: Requests per minute. DeepSeek publishes no hard per-key ceiling, so this is a
#: self-imposed budget: it stops one runaway pipeline from exhausting a shared
#: key, and it makes cost predictable rather than discovered afterwards.
DEFAULT_REQUESTS_PER_MINUTE = 20


class RateLimiter:
    """A fixed-window limiter that blocks rather than failing.

    Blocking is the right behaviour for a pipeline stage: a run that pauses for
    a few seconds is fine, a run that aborts halfway through synthesis is not.
    Thread-safe because the runner may execute stages from a worker thread.
    """

    def __init__(
        self,
        *,
        requests_per_minute: int,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if requests_per_minute < 1:
            raise ValueError("requests_per_minute must be at least 1")
        self.requests_per_minute = requests_per_minute
        self._sleep = sleep
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self, *, now: Callable[[], float] = time.monotonic) -> float:
        """Block until a slot is free. Returns how long it waited, in seconds."""
        waited = 0.0
        while True:
            with self._lock:
                current = now()
                while self._calls and current - self._calls[0] >= 60.0:
                    self._calls.popleft()
                if len(self._calls) < self.requests_per_minute:
                    self._calls.append(current)
                    return waited
                delay = 60.0 - (current - self._calls[0])
            self._sleep(max(delay, 0.01))
            waited += max(delay, 0.01)


def resolve_api_key(explicit: str | None = None) -> str:
    key = explicit or os.environ.get("DEEPSEEK_API") or os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise RuntimeError("No DeepSeek API key. Set DEEPSEEK_API in .env or the environment.")
    return key.strip()


class DeepSeekClient:
    """Typed responses from DeepSeek, with reasoning off and a request budget."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_DEEPSEEK_MODEL,
        timeout: float = DEFAULT_DEEPSEEK_TIMEOUT,
        base_url: str = DEFAULT_DEEPSEEK_BASE_URL,
        requests_per_minute: int = DEFAULT_REQUESTS_PER_MINUTE,
        limiter: RateLimiter | None = None,
        transport: Callable[..., httpx.Response] | None = None,
    ) -> None:
        self.api_key = resolve_api_key(api_key)
        self.model = model
        self.timeout = timeout
        self.base_url = base_url.rstrip("/")
        self.limiter = limiter or RateLimiter(requests_per_minute=requests_per_minute)
        self._transport = transport

    def generate_structured(
        self,
        *,
        system: str,
        prompt: str,
        json_schema: dict[str, Any],
        profile: ModelProfile,
    ) -> LLMResponse:
        del profile  # Deployment routing is explicit; local model aliases do not apply.
        schema = dereference_schema(json_schema)
        instructions = (
            f"{system}\n\n"
            "Reply with a single JSON object and nothing else. No prose, no code "
            "fences. It must validate against this JSON Schema:\n"
            f"{json.dumps(schema, separators=(',', ':'))}"
        )
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
            # See the module docstring: the only switch the API honours.
            "thinking": {"type": "disabled"},
        }

        waited = self.limiter.acquire()
        started = time.perf_counter()
        response = self._request("chat/completions", payload)
        latency = time.perf_counter() - started

        if response.status_code != 200:
            detail = response.text.strip()[-2_000:]
            raise RuntimeError(f"DeepSeek returned {response.status_code}: {detail}")

        body = response.json()
        choice = (body.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content") or ""
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        details = usage.get("completion_tokens_details") or {}

        if choice.get("finish_reason") == "length" and not content.strip():
            # The measured failure: reasoning ate the whole budget and the model
            # never got to the answer. Saying so beats returning empty content
            # and letting the contract validator report a mystery parse error.
            raise RuntimeError(
                "DeepSeek stopped at the token limit before writing any content "
                f"(reasoning tokens: {details.get('reasoning_tokens')}). The "
                "prompt is too large for the budget, or reasoning was re-enabled."
            )

        parsed: dict[str, Any] | None = None
        parse_error: str | None = None
        try:
            candidate = json.loads(content)
            if isinstance(candidate, dict):
                parsed = candidate
            else:
                parse_error = f"expected a JSON object, got {type(candidate).__name__}"
        except json.JSONDecodeError as exc:
            parse_error = str(exc)

        return LLMResponse(
            text=content,
            model=str(body.get("model") or self.model),
            latency_s=round(latency, 3),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            parsed=parsed,
            parse_error=parse_error,
            metadata={
                "backend": "deepseek_api",
                "rate_limit_wait_s": round(waited, 3),
                "reasoning_tokens": details.get("reasoning_tokens"),
                "cached_prompt_tokens": usage.get("prompt_cache_hit_tokens"),
            },
        )

    def _request(self, path: str, payload: dict[str, Any] | None) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        url = f"{self.base_url}/{path}"
        if self._transport is not None:
            return self._transport(url, json=payload, headers=headers, timeout=self.timeout)
        if payload is None:
            return httpx.get(url, headers=headers, timeout=self.timeout)
        headers["Content-Type"] = "application/json"
        return httpx.post(url, json=payload, headers=headers, timeout=self.timeout)

    def is_available(self) -> bool:
        try:
            return self._request("models", None).status_code == 200
        except Exception:
            return False


__all__ = [
    "DEFAULT_DEEPSEEK_MODEL",
    "DEFAULT_DEEPSEEK_TIMEOUT",
    "DEFAULT_REQUESTS_PER_MINUTE",
    "DeepSeekClient",
    "RateLimiter",
    "resolve_api_key",
]
