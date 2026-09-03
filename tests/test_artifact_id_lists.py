"""The artifact id lists the canvas reveals one at a time (#446).

A duplicate in one of these lists is not a cosmetic redundancy. The client
reveals ids one per tick and marks the gap with a pulsing "one more is coming"
placeholder; a repeat can never be revealed, so the placeholder rendered on
every frame with no timer left to schedule, and the node's own count reported
one fewer artifact than it held, permanently.

Artifact ids are content-addressed, which is what makes this reachable: a stage
retried into identical content produces the *same id*, and these lists flatten
across every attempt of a stage.
"""

from __future__ import annotations

from ads.api.service import _distinct


class TestDistinct:
    def test_a_retry_that_re_emits_the_same_content_is_listed_once(self) -> None:
        # The reported shape: two attempts of `intake`, the second producing a
        # byte-identical DataCard and therefore the same artifact id.
        assert _distinct(["card-a", "card-a"]) == ["card-a"]

    def test_arrival_order_survives(self) -> None:
        # These lists are rendered in sequence and the sequence is a record of
        # when work happened -- a `set()` would scramble it, which is the one
        # thing the reveal is built never to do.
        assert _distinct(["c", "a", "c", "b", "a"]) == ["c", "a", "b"]

    def test_a_generator_is_consumed_exactly_once(self) -> None:
        # Both call sites pass a comprehension over `runtime.state.attempts`.
        assert _distinct(item for item in ["a", "b", "a"]) == ["a", "b"]

    def test_nothing_in_means_nothing_out(self) -> None:
        assert _distinct([]) == []


class TestTheStagingSnapshotDedupes:
    def test_both_attempt_flattenings_go_through_it(self) -> None:
        # Deduping in the client alone would leave the ids themselves wrong:
        # the workspace payload is read by more than the reveal.
        from pathlib import Path

        service = Path("src/ads/api/service.py").read_text(encoding="utf-8")
        persist = service[
            service.index("def _persist_staging_workspace(") : service.index(
                "def _staging_component_outputs("
            )
        ]
        assert "intake_ids = _distinct(" in persist
        assert "schema_ids = _distinct(" in persist
