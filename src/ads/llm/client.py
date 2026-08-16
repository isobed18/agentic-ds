"""Local LLM client with grammar-constrained structured output.

Constrained decoding is not an optimization here, it is what makes the typed
contract architecture viable on locally-served models. Ollama compiles a JSON
schema passed in ``format`` into a GBNF grammar and masks invalid tokens during
sampling, so the model *cannot* emit malformed JSON.

The client is deliberately thin and sits behind :class:`StructuredLLM`. That
keeps two options open: PydanticAI can implement the same protocol when we want
its tool-calling and MCP client, and vLLM can replace Ollama for the 8xH100
deployment without touching agent code.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx

DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_TIMEOUT = 300.0


@dataclass(frozen=True)
class ModelProfile:
    """Which local model to use for a task, and how to sample from it.

    Model routing is a first-class concern: classification and extraction run on
    a small fast model while planning and code generation use the large one. On a
    single 24GB GPU that difference is the gap between a usable loop and an
    unusable one.
    """

    name: str
    temperature: float = 0.0
    num_ctx: int = 16384
    keep_alive: str = "30m"
    """Keep weights resident between calls; reloading a 17GB model dominates latency."""

    def with_temperature(self, temperature: float) -> ModelProfile:
        return ModelProfile(
            name=self.name,
            temperature=temperature,
            num_ctx=self.num_ctx,
            keep_alive=self.keep_alive,
        )


# Defaults tuned for a 24GB dev GPU. Production (8xH100) can point LARGE at a
# 70B-class model without any change above this line.
LARGE = ModelProfile(name="qwen3.6:27b")
SMALL = ModelProfile(name="qwen2.5vl:3b", num_ctx=8192)


@dataclass
class LLMResponse:
    """One completion, with the timing and token counts we need for gate signals."""

    text: str
    model: str
    latency_s: float
    prompt_tokens: int = 0
    completion_tokens: int = 0
    parsed: dict[str, Any] | None = None
    parse_error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_valid_json(self) -> bool:
        return self.parsed is not None


@runtime_checkable
class StructuredLLM(Protocol):
    """Minimal surface every LLM backend must provide.

    Agents depend on this, never on a concrete client, so tests can inject a
    deterministic fake and production can swap Ollama for vLLM.
    """

    def generate_structured(
        self,
        *,
        system: str,
        prompt: str,
        json_schema: dict[str, Any],
        profile: ModelProfile,
    ) -> LLMResponse: ...


def dereference_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline ``$ref``/``$defs`` produced by Pydantic into a self-contained schema.

    Grammar compilers handle inlined schemas far more reliably than reference
    indirection, and nested contracts (a ProblemCandidate containing a
    ProblemSupport) always produce refs. Recursive models are left as-is rather
    than expanded infinitely; we have none, and a cycle would be a design smell.
    """
    defs = schema.get("$defs", {})

    def resolve(node: Any, seen: frozenset[str]) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                name = ref.split("/")[-1]
                if name in seen or name not in defs:
                    return {"type": "object"}
                merged = resolve(defs[name], seen | {name})
                # Preserve sibling keys (e.g. `description`) alongside the ref.
                extras = {k: v for k, v in node.items() if k != "$ref"}
                return {**merged, **extras} if extras else merged
            return {k: resolve(v, seen) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [resolve(item, seen) for item in node]
        return node

    resolved = resolve(schema, frozenset())
    return resolved if isinstance(resolved, dict) else schema


class OllamaClient:
    """Structured-output client for a local Ollama server."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=timeout)

    def generate_structured(
        self,
        *,
        system: str,
        prompt: str,
        json_schema: dict[str, Any],
        profile: ModelProfile,
    ) -> LLMResponse:
        payload = {
            "model": profile.name,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            # Grammar-constrained decoding: the model cannot emit invalid JSON.
            "format": dereference_schema(json_schema),
            "keep_alive": profile.keep_alive,
            "options": {
                "temperature": profile.temperature,
                "num_ctx": profile.num_ctx,
            },
        }

        started = time.perf_counter()
        response = self._client.post(f"{self.base_url}/api/chat", json=payload)
        response.raise_for_status()
        body = response.json()
        latency = time.perf_counter() - started

        text = body.get("message", {}).get("content", "")
        parsed: dict[str, Any] | None = None
        parse_error: str | None = None
        try:
            candidate = json.loads(text)
            if isinstance(candidate, dict):
                parsed = candidate
            else:
                parse_error = f"Expected a JSON object, got {type(candidate).__name__}"
        except json.JSONDecodeError as exc:
            parse_error = f"JSONDecodeError: {exc}"

        return LLMResponse(
            text=text,
            model=profile.name,
            latency_s=round(latency, 3),
            prompt_tokens=int(body.get("prompt_eval_count", 0)),
            completion_tokens=int(body.get("eval_count", 0)),
            parsed=parsed,
            parse_error=parse_error,
        )

    def is_available(self) -> bool:
        try:
            return self._client.get(f"{self.base_url}/api/tags", timeout=5.0).is_success
        except httpx.HTTPError:
            return False

    def available_models(self) -> list[str]:
        response = self._client.get(f"{self.base_url}/api/tags", timeout=10.0)
        response.raise_for_status()
        return [m["name"] for m in response.json().get("models", [])]

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OllamaClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


__all__ = [
    "DEFAULT_BASE_URL",
    "LARGE",
    "SMALL",
    "LLMResponse",
    "ModelProfile",
    "OllamaClient",
    "StructuredLLM",
    "dereference_schema",
]
