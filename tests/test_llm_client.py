"""Sampling options the local client actually sends to Ollama.

A seed is the lever that makes a stage reproducible: temperature=0 alone does
not, because parallelized local inference accumulates floating-point in a
non-associative order and drifts run-to-run (#65). These tests pin that the
seed reaches the request when a profile carries one, and stays out when it does
not, so callers cannot silently lose determinism through the client.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from ads.llm import LARGE, ModelProfile, OllamaClient
from ads.llm.client import run_seed_scope


def _capturing_client(captured: dict[str, Any]) -> OllamaClient:
    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"message": {"content": "{}"}})

    transport = httpx.MockTransport(handler)
    return OllamaClient(client=httpx.Client(transport=transport))


def test_a_seeded_profile_sends_the_seed_to_ollama() -> None:
    captured: dict[str, Any] = {}
    client = _capturing_client(captured)

    client.generate_structured(
        system="s",
        prompt="p",
        json_schema={"type": "object"},
        profile=LARGE.with_seed(42),
    )

    assert captured["payload"]["options"]["seed"] == 42


def test_an_unseeded_profile_omits_the_seed() -> None:
    """Absence, not seed=0: sending 0 would silently pin every unseeded call."""
    captured: dict[str, Any] = {}
    client = _capturing_client(captured)

    client.generate_structured(
        system="s",
        prompt="p",
        json_schema={"type": "object"},
        profile=ModelProfile(name="test-model"),
    )

    assert "seed" not in captured["payload"]["options"]


def test_with_seed_leaves_temperature_and_context_intact() -> None:
    profile = ModelProfile(name="m", temperature=0.3, num_ctx=4096).with_seed(7)
    assert (profile.seed, profile.temperature, profile.num_ctx) == (7, 0.3, 4096)


def test_with_temperature_carries_an_existing_seed() -> None:
    profile = ModelProfile(name="m", seed=9).with_temperature(0.5)
    assert (profile.seed, profile.temperature) == (9, 0.5)


def test_a_run_scope_seeds_a_profile_that_carries_none() -> None:
    """Thirteen agent call sites passed a bare LARGE and drew unseeded (#201).

    Measured: the same file, twice -- one run reported no target and stopped,
    the other completed the workflow. Seeding at each call site would work
    until the fourteenth agent is added and forgets, which is how this
    happened: the mechanism existed and was used in exactly one place.
    """
    captured: dict[str, Any] = {}
    client = _capturing_client(captured)

    with run_seed_scope(500):
        client.generate_structured(
            system="s", prompt="p", json_schema={"type": "object"}, profile=LARGE
        )

    assert captured["payload"]["options"]["seed"] == 500


def test_an_explicit_seed_still_wins_inside_a_run() -> None:
    """A stage that pinned its own seed on purpose keeps it -- #65's verdict."""
    captured: dict[str, Any] = {}
    client = _capturing_client(captured)

    with run_seed_scope(500):
        client.generate_structured(
            system="s",
            prompt="p",
            json_schema={"type": "object"},
            profile=LARGE.with_seed(42),
        )

    assert captured["payload"]["options"]["seed"] == 42
