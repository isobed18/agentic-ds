"""Safety boundary and optional live tests for the persistent Docker sandbox."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ads.sandbox import (
    DataFrameOutput,
    ErrorOutput,
    FigureOutput,
    SandboxConfig,
    SandboxManager,
    SandboxSession,
    TextOutput,
)


def _manager(tmp_path: Path, **kwargs) -> SandboxManager:
    data = tmp_path / "data"
    artifacts = tmp_path / "artifacts"
    data.mkdir()
    artifacts.mkdir()
    return SandboxManager(SandboxConfig(data_dir=data, artifacts_dir=artifacts, **kwargs))


def _option_value(command: list[str], option: str) -> str:
    return command[command.index(option) + 1]


def test_create_command_enforces_the_security_boundary(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    command = manager.build_create_command("run/with unsafe spaces")

    assert command[:3] == ["docker", "run", "--detach"]
    assert "--network=none" in command
    assert "--read-only" in command
    assert "--cap-drop=ALL" in command
    assert "--security-opt=no-new-privileges" in command
    assert _option_value(command, "--user") == "65532:65532"
    assert _option_value(command, "--memory") == "1g"
    assert _option_value(command, "--cpus") == "1.0"
    assert _option_value(command, "--pids-limit") == "128"
    assert "--privileged" not in command
    environment = [
        command[index + 1] for index, value in enumerate(command) if value == "--env"
    ]
    assert environment == [
        "HOME=/tmp",
        "JUPYTER_RUNTIME_DIR=/tmp/jupyter",
        "MPLCONFIGDIR=/tmp/matplotlib",
    ]

    tmpfs = [command[index + 1] for index, value in enumerate(command) if value == "--tmpfs"]
    assert tmpfs == [
        "/tmp:rw,nosuid,nodev,noexec,size=256m",
        "/run:rw,nosuid,nodev,noexec,size=16m",
    ]
    mounts = [command[index + 1] for index, value in enumerate(command) if value == "--mount"]
    assert mounts == [
        f"type=bind,source={(tmp_path / 'data').resolve()},target=/data,readonly",
        f"type=bind,source={(tmp_path / 'artifacts').resolve()},target=/artifacts",
    ]
    assert all("docker.sock" not in mount for mount in mounts)


def test_mount_sources_must_exist_and_be_distinct(tmp_path: Path) -> None:
    missing = SandboxManager(
        SandboxConfig(data_dir=tmp_path / "missing", artifacts_dir=tmp_path)
    )
    with pytest.raises(FileNotFoundError):
        missing.build_create_command("run")

    same = SandboxManager(SandboxConfig(data_dir=tmp_path, artifacts_dir=tmp_path))
    with pytest.raises(ValueError, match="different directories"):
        same.build_create_command("run")


def test_execute_decodes_streams_errors_figures_and_dataframes(tmp_path: Path) -> None:
    payload = {
        "execution_count": 7,
        "timed_out": False,
        "outputs": [
            {"kind": "text", "stream": "stdout", "text": "hello\n"},
            {"kind": "text", "stream": "stderr", "text": "warning\n"},
            {"kind": "figure", "png_base64": "iVBORw0KGgo="},
            {
                "kind": "dataframe",
                "columns": ["x", "label"],
                "index": [0, 1],
                "data": [[1, "a"], [2, "b"]],
            },
            {
                "kind": "error",
                "name": "ValueError",
                "value": "bad value",
                "traceback": ["Traceback...", "ValueError: bad value"],
            },
        ],
    }
    calls: list[tuple[list[str], dict]] = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    manager = SandboxManager(
        SandboxConfig(data_dir=tmp_path, artifacts_dir=tmp_path / "artifacts"),
        command_runner=fake_run,
    )
    (tmp_path / "artifacts").mkdir()
    session = SandboxSession("r1", manager._container_name("r1"), "/tmp/kernel.json")

    result = manager.execute(session, "print('hello')", timeout=4)

    assert result.execution_count == 7
    assert result.stdout == "hello\n"
    assert any(isinstance(output, TextOutput) for output in result.outputs)
    assert any(isinstance(output, FigureOutput) for output in result.outputs)
    frame = next(output for output in result.outputs if isinstance(output, DataFrameOutput))
    assert frame.columns == ("x", "label")
    assert frame.data == ((1, "a"), (2, "b"))
    assert isinstance(result.errors[0], ErrorOutput)
    assert calls[0][0][:3] == ["docker", "exec", "--interactive"]
    assert json.loads(calls[0][1]["input"]) == {"code": "print('hello')"}


def test_destroy_rejects_an_unrelated_container_name(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    with pytest.raises(ValueError, match="refusing to destroy"):
        manager.destroy(SandboxSession("r1", "some-other-container", "/tmp/kernel.json"))


def test_live_kernel_preserves_state_across_executions(tmp_path: Path) -> None:
    """One opt-in integration test; ordinary CI never needs a Docker daemon/image."""
    manager = _manager(tmp_path)
    if not manager.docker_available():
        pytest.skip("Docker daemon is unavailable")
    if not manager.image_available():
        pytest.skip("ads-sandbox:latest is not built")

    session = manager.create_session("live-statefulness")
    try:
        first = manager.execute(
            session,
            (
                "import pandas as pd\n"
                "frame = pd.DataFrame({'x': [2, 3, 5]})\n"
                "print('loaded', len(frame))"
            ),
        )
        second = manager.execute(session, "print('sum', frame['x'].sum())\nframe")
    finally:
        manager.destroy(session)

    assert first.stdout == "loaded 3\n"
    assert second.stdout == "sum 10\n"
    frame = next(output for output in second.outputs if isinstance(output, DataFrameOutput))
    assert frame.data == ((2,), (3,), (5,))
