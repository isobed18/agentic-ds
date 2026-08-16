"""Docker-backed persistent Jupyter sandbox with a narrow runtime boundary."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ads.sandbox.models import (
    DataFrameOutput,
    ErrorOutput,
    ExecutionResult,
    FigureOutput,
    SandboxOutput,
    SandboxSession,
    TextOutput,
)

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
_SAFE_RUN_ID = re.compile(r"[^a-z0-9_.-]+")


@dataclass(frozen=True)
class SandboxConfig:
    data_dir: Path
    artifacts_dir: Path
    image: str = "ads-sandbox:latest"
    memory: str = "1g"
    cpus: float = 1.0
    pids_limit: int = 128
    tmpfs_size: str = "256m"
    user: str = "65532:65532"
    container_prefix: str = "ads-run"
    startup_timeout: float = 20.0

    def security_policy(self) -> dict[str, Any]:
        """Machine-readable summary of the enforced container boundary."""
        return {
            "network": "none",
            "root_filesystem": "read-only",
            "capabilities": "all dropped",
            "no_new_privileges": True,
            "user": self.user,
            "memory": self.memory,
            "cpus": self.cpus,
            "pids_limit": self.pids_limit,
            "tmpfs_size": self.tmpfs_size,
            "mounts": [
                {"target": "/data", "access": "read-only"},
                {"target": "/artifacts", "access": "read-write"},
            ],
            "kernel": "persistent Jupyter kernel per run",
        }


class SandboxManager:
    """Create, drive, and destroy one locked-down kernel container per run."""

    def __init__(
        self,
        config: SandboxConfig,
        *,
        command_runner: CommandRunner = subprocess.run,
    ) -> None:
        self.config = config
        self._run = command_runner

    def build_create_command(self, run_id: str) -> list[str]:
        """Assemble the security boundary without contacting a Docker daemon."""
        data = self._mount_source(self.config.data_dir, "data_dir")
        artifacts = self._mount_source(self.config.artifacts_dir, "artifacts_dir")
        if data == artifacts:
            raise ValueError("data_dir and artifacts_dir must be different directories")
        name = self._container_name(run_id)
        return [
            "docker",
            "run",
            "--detach",
            "--rm",
            "--name",
            name,
            "--network=none",
            "--read-only",
            "--tmpfs",
            f"/tmp:rw,nosuid,nodev,noexec,size={self.config.tmpfs_size}",
            "--tmpfs",
            "/run:rw,nosuid,nodev,noexec,size=16m",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--user",
            self.config.user,
            "--memory",
            self.config.memory,
            "--cpus",
            str(self.config.cpus),
            "--pids-limit",
            str(self.config.pids_limit),
            "--workdir",
            "/tmp",
            "--env",
            "HOME=/tmp",
            "--env",
            "JUPYTER_RUNTIME_DIR=/tmp/jupyter",
            "--env",
            "MPLCONFIGDIR=/tmp/matplotlib",
            "--mount",
            f"type=bind,source={data},target=/data,readonly",
            "--mount",
            f"type=bind,source={artifacts},target=/artifacts",
            self.config.image,
            "sleep",
            "infinity",
        ]

    def create_session(self, run_id: str) -> SandboxSession:
        command = self.build_create_command(run_id)
        self._checked(command)
        session = SandboxSession(
            run_id=run_id,
            container_name=self._container_name(run_id),
            connection_file="/tmp/kernel.json",
        )
        try:
            self._checked(
                [
                    "docker",
                    "exec",
                    "--detach",
                    "--user",
                    self.config.user,
                    session.container_name,
                    "python",
                    "-m",
                    "ipykernel_launcher",
                    "-f",
                    session.connection_file,
                ]
            )
            self._wait_until_ready(session)
        except Exception:
            self.destroy(session)
            raise
        return session

    def execute(
        self, session: SandboxSession, code: str, timeout: float = 30.0
    ) -> ExecutionResult:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        command = [
            "docker",
            "exec",
            "--interactive",
            "--user",
            self.config.user,
            session.container_name,
            "python",
            "/opt/ads/kernel_client.py",
            session.connection_file,
            str(timeout),
        ]
        completed = self._run(
            command,
            input=json.dumps({"code": code}),
            capture_output=True,
            text=True,
            timeout=timeout + 10,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"sandbox client failed ({completed.returncode}): {completed.stderr.strip()}"
            )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("sandbox client returned invalid JSON") from exc
        return self._decode_result(payload)

    def destroy(self, session: SandboxSession) -> None:
        expected = self._container_name(session.run_id)
        if session.container_name != expected:
            raise ValueError("refusing to destroy a container not derived from this run id")
        try:
            self._run(
                ["docker", "rm", "--force", session.container_name],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            # Teardown is idempotent. A missing/unresponsive daemon means there
            # is no reachable container for this process to remove.
            return

    def docker_available(self) -> bool:
        try:
            result = self._run(
                ["docker", "info"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0

    def image_available(self) -> bool:
        if not self.docker_available():
            return False
        result = self._run(
            ["docker", "image", "inspect", self.config.image],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return result.returncode == 0

    def _wait_until_ready(self, session: SandboxSession) -> None:
        deadline = time.monotonic() + self.config.startup_timeout
        command = [
            "docker",
            "exec",
            "--user",
            self.config.user,
            session.container_name,
            "test",
            "-s",
            session.connection_file,
        ]
        while time.monotonic() < deadline:
            remaining = max(deadline - time.monotonic(), 0.1)
            try:
                result = self._run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=min(2.0, remaining),
                    check=False,
                )
            except subprocess.TimeoutExpired:
                continue
            if result.returncode == 0:
                return
            time.sleep(0.1)
        raise TimeoutError("Jupyter kernel did not create its connection file in time")

    def _checked(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        return self._run(
            command,
            capture_output=True,
            text=True,
            timeout=self.config.startup_timeout,
            check=True,
        )

    def _container_name(self, run_id: str) -> str:
        if not run_id.strip():
            raise ValueError("run_id must not be empty")
        slug = _SAFE_RUN_ID.sub("-", run_id.lower()).strip("-.")[:32] or "run"
        digest = hashlib.sha256(run_id.encode()).hexdigest()[:10]
        return f"{self.config.container_prefix}-{slug}-{digest}"

    @staticmethod
    def _mount_source(path: Path, label: str) -> str:
        resolved = path.resolve(strict=True)
        if not resolved.is_dir():
            raise ValueError(f"{label} must be a directory")
        rendered = str(resolved)
        if "," in rendered:
            raise ValueError(f"{label} cannot contain a comma when used as a Docker mount")
        return rendered

    @staticmethod
    def _decode_result(payload: dict[str, Any]) -> ExecutionResult:
        outputs: list[SandboxOutput] = []
        for item in payload.get("outputs", []):
            kind = item.get("kind")
            if kind == "text":
                outputs.append(TextOutput(text=item["text"], stream=item["stream"]))
            elif kind == "error":
                outputs.append(
                    ErrorOutput(
                        name=item["name"],
                        value=item["value"],
                        traceback=tuple(item.get("traceback", ())),
                    )
                )
            elif kind == "figure":
                outputs.append(FigureOutput(png_base64=item["png_base64"]))
            elif kind == "dataframe":
                outputs.append(
                    DataFrameOutput(
                        columns=tuple(item["columns"]),
                        index=tuple(item["index"]),
                        data=tuple(tuple(row) for row in item["data"]),
                    )
                )
        return ExecutionResult(
            execution_count=payload.get("execution_count"),
            outputs=tuple(outputs),
            timed_out=bool(payload.get("timed_out", False)),
        )


__all__ = ["SandboxConfig", "SandboxManager"]
