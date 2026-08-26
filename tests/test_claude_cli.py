from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ads.llm import ClaudeCliClient, ModelProfile


def test_claude_cli_uses_subscription_auth_and_disables_tools(tmp_path: Path) -> None:
    executable = tmp_path / "claude.exe"
    executable.write_bytes(b"")
    calls: list[dict] = []

    def runner(command, **kwargs):
        calls.append({"command": command, **kwargs})
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "subtype": "success",
                    "structured_output": {"answer": "ready"},
                    "modelUsage": {"claude-haiku-test": {}},
                    "usage": {"input_tokens": 12, "output_tokens": 4},
                }
            ),
            stderr="",
        )

    client = ClaudeCliClient(executable=str(executable), runner=runner)
    response = client.generate_structured(
        system="Return a result.",
        prompt="Analyze this safe summary.",
        json_schema={
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        },
        profile=ModelProfile(name="local-model-name-must-not-leak"),
    )

    call = calls[0]
    assert response.parsed == {"answer": "ready"}
    assert response.model == "claude-haiku-test"
    assert call["input"] == "Analyze this safe summary."
    assert call["command"][call["command"].index("--tools") + 1] == ""
    assert call["command"][call["command"].index("--setting-sources") + 1] == ""
    assert call["encoding"] == "utf-8"
    assert call["errors"] == "replace"
    assert "ANTHROPIC_API_KEY" not in call["env"]
    assert "local-model-name-must-not-leak" not in call["command"]


def test_timeout_becomes_an_actionable_error_not_a_bare_timeoutexpired(tmp_path: Path) -> None:
    """The defect this covers: `subprocess.run` *raises* on timeout rather than
    returning, and only the non-zero-exit and bad-JSON paths were handled. A
    staging synthesis that ran past the budget therefore surfaced a raw
    `TimeoutExpired` carrying the whole command line -- unreadable, and with no
    hint that the deployment's own `ADS_CLAUDE_TIMEOUT` was the thing to change.
    """
    executable = tmp_path / "claude.exe"
    executable.write_bytes(b"")

    def runner(command, **kwargs):
        raise subprocess.TimeoutExpired(cmd=command, timeout=kwargs["timeout"])

    client = ClaudeCliClient(
        executable=str(executable), runner=runner, timeout=12, model="haiku", effort="low"
    )

    with pytest.raises(RuntimeError) as caught:
        client.generate_structured(
            system="Return a result.",
            prompt="Summarize the routed sources.",
            json_schema={"type": "object", "properties": {}},
            profile=ModelProfile(name="local-model-name-must-not-leak"),
        )

    message = str(caught.value)
    assert not isinstance(caught.value, subprocess.TimeoutExpired)
    assert "12" in message, "the operator cannot act without knowing the budget that expired"
    assert "ADS_CLAUDE_TIMEOUT" in message, "name the knob that changes it"


def test_service_does_not_undercut_the_client_timeout_budget() -> None:
    """90s was the service default while the client's own default was 300s.

    Nothing reconciled the two, so the deployment silently ran on a third of
    the budget the client was written for and expired mid-synthesis.
    """
    import os
    from unittest.mock import patch

    from ads.llm import DEFAULT_CLAUDE_TIMEOUT

    source = Path(__file__).resolve().parents[1] / "src" / "ads" / "api" / "service.py"
    text = source.read_text(encoding="utf-8")
    assert 'os.environ.get("ADS_CLAUDE_TIMEOUT", "90")' not in text
    assert "ADS_CLAUDE_TIMEOUT" in text and "DEFAULT_CLAUDE_TIMEOUT" in text

    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("ADS_CLAUDE_TIMEOUT", None)
        assert float(os.environ.get("ADS_CLAUDE_TIMEOUT", str(DEFAULT_CLAUDE_TIMEOUT))) == (
            DEFAULT_CLAUDE_TIMEOUT
        )
