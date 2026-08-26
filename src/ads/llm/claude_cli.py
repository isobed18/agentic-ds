"""Structured LLM adapter for an authenticated Claude Code CLI session.

This is an opt-in private-deployment backend. It never reads or accepts an API
key: the child process is stripped of API/provider variables and must already be
logged in through ``claude auth login``. Prompts run with tools, MCP, project
settings, and repository context disabled so the CLI behaves as a bounded
structured inference process rather than as a coding agent.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ads.llm.client import LLMResponse, ModelProfile, dereference_schema

DEFAULT_CLAUDE_MODEL = "haiku"
DEFAULT_CLAUDE_EFFORT = "low"
DEFAULT_CLAUDE_TIMEOUT = 300.0


def _claude_executable(explicit: str | None = None) -> str:
    if explicit:
        candidate = Path(explicit).expanduser()
        if candidate.is_file():
            return str(candidate.resolve())
        raise FileNotFoundError(f"Claude CLI executable does not exist: {candidate}")
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        native = (
            Path(appdata) / "npm" / "node_modules" / "@anthropic-ai" / "claude-code"
            / "bin" / "claude.exe"
            if appdata
            else None
        )
        if native is not None and native.is_file():
            return str(native)
    found = shutil.which("claude")
    if found:
        return found
    raise FileNotFoundError("Claude CLI is not installed or is not on PATH")


def _subscription_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for key in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
    ):
        environment.pop(key, None)
    return environment


class ClaudeCliClient:
    """Use Claude Code's logged-in subscription for typed planner responses."""

    def __init__(
        self,
        *,
        executable: str | None = None,
        model: str = DEFAULT_CLAUDE_MODEL,
        effort: str = DEFAULT_CLAUDE_EFFORT,
        timeout: float = DEFAULT_CLAUDE_TIMEOUT,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self.executable = _claude_executable(executable)
        self.model = model
        self.effort = effort
        self.timeout = timeout
        self._runner = runner

    def generate_structured(
        self,
        *,
        system: str,
        prompt: str,
        json_schema: dict[str, Any],
        profile: ModelProfile,
    ) -> LLMResponse:
        del profile  # Deployment routing is explicit; local model aliases do not apply here.
        schema = json.dumps(dereference_schema(json_schema), separators=(",", ":"))
        with tempfile.TemporaryDirectory(prefix="ads-claude-") as temporary:
            system_path = Path(temporary) / "system.txt"
            system_path.write_text(system, encoding="utf-8")
            command = [
                self.executable,
                "--print",
                "--model",
                self.model,
                "--effort",
                self.effort,
                "--output-format",
                "json",
                "--json-schema",
                schema,
                "--system-prompt-file",
                str(system_path),
                "--tools",
                "",
                "--setting-sources",
                "",
                "--strict-mcp-config",
                "--permission-mode",
                "dontAsk",
            ]
            started = time.perf_counter()
            try:
                result = self._runner(
                    command,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    cwd=temporary,
                    env=_subscription_environment(),
                    timeout=self.timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                # `subprocess.run` raises this rather than returning, so without
                # catching it the timeout escaped as a bare TimeoutExpired and
                # surfaced in the UI as an unhandled error naming a command line
                # nobody could act on. The staging synthesis is the call that
                # hits it: one bounded request against every routed source, and
                # a deployment configured well below the 300s default has no
                # slack for a large mixed-source project.
                raise RuntimeError(
                    f"Claude CLI did not answer within {self.timeout:.0f}s "
                    f"(model {self.model}, effort {self.effort}). Raise "
                    f"ADS_CLAUDE_TIMEOUT, lower the effort, or reduce the "
                    f"number of sources in one run."
                ) from exc
            latency = time.perf_counter() - started
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()[-2_000:]
            raise RuntimeError(f"Claude CLI failed with exit {result.returncode}: {detail}")
        try:
            body = json.loads(result.stdout or "")
        except json.JSONDecodeError as exc:
            raise RuntimeError("Claude CLI returned an invalid JSON envelope") from exc
        parsed = body.get("structured_output")
        if not isinstance(parsed, dict):
            raise RuntimeError(
                f"Claude CLI did not return structured output: {body.get('subtype', 'unknown')}"
            )
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        model_usage = body.get("modelUsage") if isinstance(body.get("modelUsage"), dict) else {}
        actual_model = next(iter(model_usage), self.model)
        return LLMResponse(
            text=json.dumps(parsed, ensure_ascii=False),
            model=actual_model,
            latency_s=round(latency, 3),
            prompt_tokens=int(usage.get("input_tokens") or 0),
            completion_tokens=int(usage.get("output_tokens") or 0),
            parsed=parsed,
            metadata={
                "backend": "claude_cli_subscription",
                "duration_api_ms": body.get("duration_api_ms"),
                "estimated_cost_usd": body.get("total_cost_usd"),
            },
        )

    def is_available(self) -> bool:
        try:
            result = self._runner(
                [self.executable, "auth", "status"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=_subscription_environment(),
                timeout=15,
                check=False,
            )
            if result.returncode != 0:
                return False
            status = json.loads(result.stdout or "")
            return bool(status.get("loggedIn")) and status.get("authMethod") == "claude.ai"
        except (OSError, ValueError, subprocess.SubprocessError):
            return False


__all__ = ["DEFAULT_CLAUDE_TIMEOUT", "ClaudeCliClient"]
