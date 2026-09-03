"""A live, in-process feed of tool calls, readable while a run is still going.

Tool use was only ever visible after the fact, as a count on a finished
stage's artifact ("Tool calls: 7") -- never while it was happening, and never
naming the tool (#411). The information existed the whole time: every agent
reaches its tools through :class:`~ads.tools.broker.PermissionBroker`, which
already records each decision into an audit log. What was missing is a way to
read that log *during* the run rather than after it.

This is that reader. The broker publishes each decision here as it makes it,
and the API serves what has arrived since the caller's last cursor.

Deliberately small and deliberately lossy:

* **In-process and in-memory.** The durable record is the agent audit artifact
  the stage already writes; this is the live view of a run happening in *this*
  process's worker threads. A restart loses the feed and loses nothing else --
  the pipeline itself is already gone with it.
* **Bounded per run.** A long investigation makes hundreds of calls and nobody
  scrolls back through them, so the buffer keeps the most recent
  :data:`RUN_CAPACITY` and drops the rest from the front. A reader whose cursor
  falls off the back is told so rather than being handed a silent gap.
* **Sequence numbers, not timestamps, as the cursor.** Two calls in the same
  millisecond are ordinary, and a cursor that cannot separate them either
  repeats an event or skips one.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from typing import Any

#: Events kept per run. Roughly a long investigation's worth of tool calls.
RUN_CAPACITY = 400
#: Runs tracked at once, oldest evicted first. A host serves a handful.
RUN_LIMIT = 32


@dataclass(frozen=True)
class ToolActivityEvent:
    """One tool call, as it happened."""

    seq: int
    agent_id: str
    tool_id: str
    #: "allowed", "denied", or "failed" -- the broker's own vocabulary.
    decision: str
    at: str
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "agent": self.agent_id,
            "tool": self.tool_id,
            "decision": self.decision,
            "at": self.at,
            "detail": self.detail,
        }


class ToolActivityFeed:
    """Per-run ring buffers of tool calls, safe to write from worker threads."""

    def __init__(self, run_capacity: int = RUN_CAPACITY, run_limit: int = RUN_LIMIT) -> None:
        self._run_capacity = run_capacity
        self._run_limit = run_limit
        self._runs: dict[str, deque[ToolActivityEvent]] = {}
        self._next_seq = 0
        self._lock = Lock()

    def record(
        self,
        run_id: str,
        agent_id: str,
        tool_id: str,
        decision: str,
        detail: str | None = None,
    ) -> None:
        if not run_id:
            return
        with self._lock:
            self._next_seq += 1
            events = self._runs.get(run_id)
            if events is None:
                # Oldest run first: dict preserves insertion order, and a run
                # that has not produced a tool call in a while is the one whose
                # panel nobody is watching.
                while len(self._runs) >= self._run_limit:
                    self._runs.pop(next(iter(self._runs)))
                events = deque(maxlen=self._run_capacity)
                self._runs[run_id] = events
            events.append(
                ToolActivityEvent(
                    seq=self._next_seq,
                    agent_id=agent_id,
                    tool_id=tool_id,
                    decision=decision,
                    at=datetime.now(UTC).isoformat(),
                    detail=detail,
                )
            )

    def since(self, run_id: str, after: int) -> tuple[list[ToolActivityEvent], bool]:
        """Events after `after`, and whether anything was dropped before them.

        The flag matters: a caller that has been away long enough for the ring
        to wrap would otherwise read a contiguous list and believe it had seen
        everything.
        """
        with self._lock:
            events = list(self._runs.get(run_id, ()))
        fresh = [event for event in events if event.seq > after]
        # Something was lost only if the caller had already read past the point
        # the buffer now starts from.
        dropped = bool(events) and after > 0 and events[0].seq > after + 1
        return fresh, dropped

    def forget(self, run_id: str) -> None:
        """Drop a run's feed -- it was deleted, or it finished long ago."""
        with self._lock:
            self._runs.pop(run_id, None)


#: The process-wide feed. A module singleton rather than something threaded
#: through nine `PermissionBroker` construction sites: the broker is built per
#: agent call, deep inside stage code that has no reference to the control
#: plane, and the alternative is a constructor argument every one of those
#: sites has to remember to pass.
FEED = ToolActivityFeed()


def publish(
    run_id: str,
    agent_id: str,
    tool_id: str,
    decision: str,
    detail: str | None = None,
) -> None:
    """Record a tool decision on the process-wide feed. Never raises."""
    try:
        FEED.record(run_id, agent_id, tool_id, decision, detail)
    except Exception:  # pragma: no cover - a feed for a side panel never breaks a run
        return


__all__ = ["FEED", "RUN_CAPACITY", "RUN_LIMIT", "ToolActivityEvent", "ToolActivityFeed", "publish"]
