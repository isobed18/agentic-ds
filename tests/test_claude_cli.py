from __future__ import annotations

import json
import subprocess
from pathlib import Path

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
