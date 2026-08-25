"""Control-plane tests.

The load-bearing assertion is negative: the API must not serve raw data. A
dashboard that renders customer rows in a browser would undo the two-plane
separation the whole architecture rests on, and it would do so invisibly.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ads.api import ControlPlane
from ads.contracts.base import ArtifactType
from ads.contracts.gates import (
    DecisionOption,
    GateDecision,
    GateVerdict,
    HumanPrompt,
)
from ads.contracts.validation import SplitStrategy, ValidationStrategy
from ads.store import ArtifactStore


@pytest.fixture
def store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(tmp_path / "artifacts")


@pytest.fixture
def plane(store: ArtifactStore) -> ControlPlane:
    return ControlPlane(store=store)


def _decision(**kwargs) -> GateDecision:
    base = dict(
        stage_id="problem_discovery",
        attempt=1,
        verdict=GateVerdict.AUTO_PROCEED,
        reason_code="no_rule_triggered",
    )
    return GateDecision(**{**base, **kwargs})


def _escalation() -> GateDecision:
    return _decision(
        verdict=GateVerdict.ESCALATE,
        reason_code="risk_class_gate",
        triggered_rules=["risk_class_gate", "profile_checkpoint"],
        human_prompt=HumanPrompt(
            stage_id="problem_discovery",
            question="Pick a problem to pursue.",
            context_summary="3 of 3 candidates are statistically supported.",
            options=[
                DecisionOption(
                    option_id="approve",
                    label="Approve and continue",
                    consequence="Accept and proceed.",
                ),
            ],
        ),
    )


class TestRunListing:
    def test_empty_store_has_no_runs(self, plane: ControlPlane) -> None:
        assert plane.list_runs() == []

    def test_run_appears_once_artifacts_exist(
        self, plane: ControlPlane, store: ArtifactStore
    ) -> None:
        store.put(
            ValidationStrategy(strategy=SplitStrategy.RANDOM, rationale="x"),
            run_id="run1",
            stage_exec_id="validation_strategy",
        )
        runs = plane.list_runs()
        assert len(runs) == 1
        assert runs[0].run_id == "run1"
        assert runs[0].stages == ["validation_strategy"]

    def test_escalation_surfaces_as_awaiting_human(
        self, plane: ControlPlane, store: ArtifactStore
    ) -> None:
        store.put(_escalation(), run_id="run1", stage_exec_id="problem_discovery")
        run = plane.list_runs()[0]
        assert run.status == "awaiting_human"
        assert run.pending_question is not None
        assert run.pending_question["reason_code"] == "risk_class_gate"

    def test_clean_run_is_not_flagged(self, plane: ControlPlane, store: ArtifactStore) -> None:
        store.put(_decision(), run_id="run1", stage_exec_id="problem_discovery")
        run = plane.list_runs()[0]
        assert run.status == "auto_proceed"
        assert run.pending_question is None

    def test_runs_are_isolated(self, plane: ControlPlane, store: ArtifactStore) -> None:
        store.put(_decision(), run_id="run1")
        store.put(_escalation(), run_id="run2")
        by_id = {r.run_id: r for r in plane.list_runs()}
        assert by_id["run1"].status == "auto_proceed"
        assert by_id["run2"].status == "awaiting_human"


class TestGateHistory:
    def test_decisions_are_read_from_the_store(
        self, plane: ControlPlane, store: ArtifactStore
    ) -> None:
        """The decisions *are* artifacts, so the UI cannot diverge from the audit."""
        store.put(_decision(attempt=1), run_id="run1")
        store.put(
            _decision(attempt=2, verdict=GateVerdict.RETRY, reason_code="leakage_detected"),
            run_id="run1",
        )
        decisions = plane.gate_decisions("run1")
        assert [d["attempt"] for d in decisions] == [1, 2]
        assert decisions[1]["reason_code"] == "leakage_detected"

    def test_triggered_rules_are_exposed(self, plane: ControlPlane, store: ArtifactStore) -> None:
        """The audit trail is the point; a verdict without its rules is not one."""
        store.put(_escalation(), run_id="run1")
        assert plane.gate_decisions("run1")[0]["triggered_rules"] == [
            "risk_class_gate",
            "profile_checkpoint",
        ]

    def test_correction_instructions_are_exposed(
        self, plane: ControlPlane, store: ArtifactStore
    ) -> None:
        store.put(
            _decision(
                verdict=GateVerdict.RETRY,
                reason_code="leakage_detected",
                correction_instructions=["drop_feature: total_comp_ytd"],
            ),
            run_id="run1",
        )
        instructions = plane.gate_decisions("run1")[0]["correction_instructions"]
        assert instructions == ["drop_feature: total_comp_ytd"]


class TestArtifactAccess:
    def test_metadata_lists_summaries_only(self, plane: ControlPlane, store: ArtifactStore) -> None:
        store.put(
            ValidationStrategy(strategy=SplitStrategy.RANDOM, rationale="iid"),
            run_id="run1",
            name="validation",
        )
        entry = plane.artifacts("run1")[0]
        assert entry["name"] == "validation"
        assert entry["summary"]["strategy"] == "random"

    def test_payload_is_fetched_explicitly(self, plane: ControlPlane, store: ArtifactStore) -> None:
        ref = store.put(
            ValidationStrategy(strategy=SplitStrategy.RANDOM, rationale="iid"),
            run_id="run1",
        )
        assert plane.artifact_payload(ref.artifact_id)["rationale"] == "iid"

    def test_unknown_artifact_raises(self, plane: ControlPlane) -> None:
        with pytest.raises(KeyError):
            plane.artifact_payload("0" * 64)


class TestNoRawDataIsServed:
    """The negative assertion this module exists for."""

    def test_datacard_payloads_carry_no_pii_values(
        self, plane: ControlPlane, store: ArtifactStore, cards
    ) -> None:
        master = next(c for c in cards if "master" in c.table_name)
        store.put(master, run_id="run1", name=master.table_name)
        ref = plane.artifacts("run1")[0]
        payload = plane.artifact_payload(ref["artifact_id"])

        rendered = str(payload)
        assert "Dr. Physician" not in rendered
        assert "example-clinic.test" not in rendered

        pii = [c for c in payload["columns"] if c["sensitivity"] == "pii"]
        assert pii, "the fixture must contain PII columns for this to mean anything"
        for column in pii:
            assert column["sample_values"] == []
            assert column["top_values"] == []

    def test_no_endpoint_returns_a_dataframe(self) -> None:
        """Structural: the read side only knows about artifacts and decisions."""
        methods = {m for m in dir(ControlPlane) if not m.startswith("__")}
        for forbidden in ("rows", "dataframe", "sample_data", "raw"):
            assert not any(forbidden in m for m in methods)


class TestHttpApp:
    def test_app_builds_and_serves_the_dashboard(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from ads.api import create_app

        client = TestClient(create_app(tmp_path / "artifacts"))
        assert client.get("/api/runs").json() == []

        # The UI is a React bundle, so "/" serves a shell and the markup is
        # built at runtime. Asserting on rendered text here would only be
        # asserting on index.html; what this endpoint owes the browser is a
        # mount point and a reachable bundle.
        page = client.get("/")
        assert page.status_code == 200
        assert 'id="root"' in page.text

        asset = re.search(r'src="(/assets/[^"]+\.js)"', page.text)
        assert asset, f"index.html references no script bundle: {page.text}"
        assert client.get(asset.group(1)).status_code == 200

        # A client-side route must survive a hard refresh, and must not
        # shadow the API.
        assert client.get("/datasets").status_code == 200
        assert 'id="root"' in client.get("/datasets").text
        assert client.get("/api/does-not-exist").status_code == 404

    def test_unknown_run_is_404(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from ads.api import create_app

        client = TestClient(create_app(tmp_path / "artifacts"))
        assert client.get("/api/runs/nope").status_code == 404

    def test_run_detail_round_trips(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from ads.api import create_app

        artifacts = tmp_path / "artifacts"
        ArtifactStore(artifacts).put(_escalation(), run_id="run1")
        client = TestClient(create_app(artifacts))

        listing = client.get("/api/runs").json()
        assert listing[0]["status"] == "awaiting_human"
        detail = client.get("/api/runs/run1").json()
        assert detail["decisions"][0]["human_prompt"]["question"]

    def test_artifact_endpoint_returns_the_payload(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from ads.api import create_app

        artifacts = tmp_path / "artifacts"
        ref = ArtifactStore(artifacts).put(
            ValidationStrategy(strategy=SplitStrategy.RANDOM, rationale="iid"),
            run_id="run1",
        )
        client = TestClient(create_app(artifacts))
        assert client.get(f"/api/artifacts/{ref.artifact_id}").json()["rationale"] == "iid"

    def test_gate_decision_type_is_registered(self) -> None:
        """A decision the store cannot resolve would vanish from the UI."""
        from ads.store.artifacts import _TYPE_REGISTRY

        assert ArtifactType.GATE_DECISION in _TYPE_REGISTRY
