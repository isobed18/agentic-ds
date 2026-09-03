"""Tool use is readable while it happens, not only once a stage has finished (#411).

The counts on a finished stage's artifact were the only trace a tool call ever
left in the interface: after the fact, and silent about which tool. Every agent
reaches its tools through one broker, so that is where the live feed is fed
from.
"""

from __future__ import annotations

import pytest

from ads.contracts.gates import PermissionTier
from ads.tools.activity import ToolActivityFeed
from ads.tools.broker import PermissionBroker
from ads.tools.models import (
    ToolExecutionError,
    ToolPayload,
    ToolPermissionError,
    ToolRuntime,
)
from ads.tools.registry import ToolDefinition, ToolRegistry


class _Agent:
    id = "eda_investigator"
    allowed_tools = frozenset({"profile_table", "explode"})
    max_tool_tier = PermissionTier.READ_META


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            tool_id="profile_table",
            tier=PermissionTier.READ_META,
            description="measure a table",
            handler=lambda runtime, arguments: ToolPayload(summary="measured"),
        )
    )
    registry.register(
        ToolDefinition(
            tool_id="explode",
            tier=PermissionTier.READ_META,
            description="always raises",
            handler=lambda runtime, arguments: (_ for _ in ()).throw(RuntimeError("boom")),
        )
    )
    registry.register(
        ToolDefinition(
            tool_id="forbidden",
            tier=PermissionTier.READ_META,
            description="not on the allowlist",
            handler=lambda runtime, arguments: ToolPayload(summary="never"),
        )
    )
    return registry


def test_feed_returns_only_what_is_past_the_cursor() -> None:
    feed = ToolActivityFeed()
    feed.record("run-1", "eda_investigator", "profile_table", "allowed")
    feed.record("run-1", "eda_investigator", "execute_python", "allowed")

    events, dropped = feed.since("run-1", 0)
    assert [event.tool_id for event in events] == ["profile_table", "execute_python"]
    assert dropped is False

    # Sequence numbers, not timestamps: two calls in the same millisecond are
    # ordinary, and a timestamp cursor would repeat or skip one.
    events, _ = feed.since("run-1", events[0].seq)
    assert [event.tool_id for event in events] == ["execute_python"]


def test_feed_keeps_runs_apart() -> None:
    feed = ToolActivityFeed()
    feed.record("run-1", "a", "one", "allowed")
    feed.record("run-2", "b", "two", "allowed")

    assert [event.tool_id for event in feed.since("run-1", 0)[0]] == ["one"]
    assert [event.tool_id for event in feed.since("run-2", 0)[0]] == ["two"]
    assert feed.since("run-unknown", 0) == ([], False)


def test_a_reader_who_fell_behind_is_told_rather_than_handed_a_gap() -> None:
    feed = ToolActivityFeed(run_capacity=3)
    for index in range(6):
        feed.record("run-1", "a", f"tool_{index}", "allowed")

    # The ring wrapped past this reader's cursor. The list it gets back is
    # contiguous, so without the flag it would look complete.
    events, dropped = feed.since("run-1", 1)
    assert dropped is True
    assert [event.tool_id for event in events] == ["tool_3", "tool_4", "tool_5"]

    # A reader who is up to date is not told anything was lost.
    assert feed.since("run-1", events[-1].seq) == ([], False)


def test_an_untracked_run_never_grows_the_feed() -> None:
    # `ToolRuntime.run_id` defaults to a placeholder for direct tool use in
    # tests and scripts; those calls are nobody's live view.
    feed = ToolActivityFeed()
    feed.record("", "a", "one", "allowed")
    assert feed.since("", 0) == ([], False)


def test_feed_evicts_the_least_recently_started_run() -> None:
    feed = ToolActivityFeed(run_limit=2)
    feed.record("run-1", "a", "one", "allowed")
    feed.record("run-2", "a", "two", "allowed")
    feed.record("run-3", "a", "three", "allowed")

    assert feed.since("run-1", 0) == ([], False)
    assert [event.tool_id for event in feed.since("run-3", 0)[0]] == ["three"]


def test_the_broker_publishes_every_decision_it_makes(monkeypatch: pytest.MonkeyPatch) -> None:
    # One choke point for every agent: allowed, refused, and failed alike. A
    # refusal is exactly what somebody watching a stalled run needs to see, so
    # it is published rather than quietly dropped.
    feed = ToolActivityFeed()
    monkeypatch.setattr("ads.tools.activity.FEED", feed)
    broker = PermissionBroker(_registry())
    runtime = ToolRuntime(run_id="run-1")
    agent = _Agent()

    broker.invoke(agent, "profile_table", runtime)
    with pytest.raises(ToolPermissionError):
        broker.invoke(agent, "forbidden", runtime)
    with pytest.raises(ToolExecutionError):
        broker.invoke(agent, "explode", runtime)

    events, _ = feed.since("run-1", 0)
    assert [(event.tool_id, event.decision) for event in events] == [
        ("profile_table", "allowed"),
        ("forbidden", "denied"),
        ("explode", "allowed"),
        ("explode", "failed"),
    ]
    assert {event.agent_id for event in events} == {"eda_investigator"}

    # The durable audit log is unchanged by any of this; the feed is the live
    # half and is allowed to be lossy and to disappear on a restart.
    assert [item.tool_id for item in broker.audit_log] == [
        "profile_table",
        "forbidden",
        "explode",
        "explode",
    ]


def test_a_broken_feed_never_breaks_a_run(monkeypatch: pytest.MonkeyPatch) -> None:
    class Exploding:
        def record(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("feed is down")

    monkeypatch.setattr("ads.tools.activity.FEED", Exploding())
    broker = PermissionBroker(_registry())
    # A side panel's status line is never a reason for a tool call to fail.
    result = broker.invoke(_Agent(), "profile_table", ToolRuntime(run_id="run-1"))
    assert result.summary == "measured"


def test_the_endpoint_serves_the_feed_with_a_cursor(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from ads.api import create_app
    from ads.tools import activity

    feed = ToolActivityFeed()
    monkeypatch.setattr(activity, "FEED", feed)
    # The control plane holds its own reference, taken at import; point it at
    # the same feed so the endpoint reads what the broker just wrote.
    from ads.api import service as service_module

    monkeypatch.setattr(service_module, "tool_activity_feed", feed)
    client = TestClient(create_app(tmp_path / "artifacts"))

    # An unknown run is not an error: the feed is in-memory and a panel may ask
    # about a run this process never executed. An empty, inactive answer stops
    # its polling, where a 404 would make it retry.
    first = client.get("/api/runs/run-1/tool-activity").json()
    assert first == {
        "run_id": "run-1",
        "events": [],
        "cursor": 0,
        "dropped": False,
        "active": False,
    }

    feed.record("run-1", "eda_investigator", "profile_table", "allowed")
    served = client.get("/api/runs/run-1/tool-activity").json()
    assert [(item["agent"], item["tool"], item["decision"]) for item in served["events"]] == [
        ("eda_investigator", "profile_table", "allowed")
    ]
    assert served["cursor"] > 0

    # Polling from the cursor returns nothing until something new happens.
    assert client.get(
        f"/api/runs/run-1/tool-activity?after={served['cursor']}"
    ).json()["events"] == []
