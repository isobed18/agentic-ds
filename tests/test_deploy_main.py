"""Continuous deployment by pull, and the ways it must refuse to act.

This runs unattended against the live deployment, so most of what matters is
what it declines to do. The dangerous outcomes are not crashes -- they are a
deployment that silently discards someone's commit, restarts on every poll, or
reports success while serving with the password gate open.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    # scripts/ is not a package; load the module by path, as the other script
    # tests do, rather than adding an __init__.py that would change packaging.
    spec = importlib.util.spec_from_file_location(
        "deploy_main", ROOT / "scripts" / "deploy_main.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


deploy_main = _load()


def _run(cwd: Path, *args: str) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repos(tmp_path: Path) -> tuple[Path, Path]:
    """An origin with two commits and a worktree parked on the first."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _run(origin, "git", "init", "--quiet", "--initial-branch=main")
    _run(origin, "git", "config", "user.email", "t@example.com")
    _run(origin, "git", "config", "user.name", "t")
    (origin / "app.txt").write_text("v1", encoding="utf-8")
    _run(origin, "git", "add", ".")
    _run(origin, "git", "commit", "--quiet", "-m", "v1")

    work = tmp_path / "work"
    _run(tmp_path, "git", "clone", "--quiet", str(origin), str(work))
    _run(work, "git", "config", "user.email", "t@example.com")
    _run(work, "git", "config", "user.name", "t")

    (origin / "app.txt").write_text("v2", encoding="utf-8")
    _run(origin, "git", "add", ".")
    _run(origin, "git", "commit", "--quiet", "-m", "v2")
    return origin, work


def _deploy(work: Path, monkeypatch, *, gate=True, rollback=False, calls=None, gates=None):
    """Run a cycle with the restart stubbed out.

    Only `restart` is replaced -- patching subprocess.run wholesale would also
    intercept the git calls this depends on, and the test would pass without
    exercising any of the real fast-forward logic.
    """

    outcomes = list(gates) if gates is not None else None

    def fake_restart(command, port, timeout):
        if calls is not None:
            calls.append(command)
        healthy = outcomes.pop(0) if outcomes else gate
        if not healthy:
            raise deploy_main.DeployError("gate check failed")

    monkeypatch.setattr(deploy_main, "restart", fake_restart)
    return deploy_main.deploy_once(
        work,
        branch="main",
        port=8077,
        restart_command=["restart"],
        timeout=1.0,
        rollback_on_failure=rollback,
    )


def test_a_new_commit_is_deployed(repos, monkeypatch) -> None:
    _, work = repos
    result = _deploy(work, monkeypatch)
    assert result["status"] == "deployed"
    assert (work / "app.txt").read_text(encoding="utf-8") == "v2"


def test_an_unchanged_head_does_not_restart(repos, monkeypatch) -> None:
    """Restarting drops in-flight runs, so the common case -- nothing new --
    has to cost nothing."""
    _, work = repos
    _deploy(work, monkeypatch)
    calls: list = []
    result = _deploy(work, monkeypatch, calls=calls)
    assert result["status"] == "unchanged"
    assert calls == [], "a no-op poll must not restart the server"


def test_a_diverged_worktree_is_refused(repos, monkeypatch) -> None:
    """Someone committed on the serving host. Fast-forwarding over that would
    destroy it, so stop instead."""
    _, work = repos
    (work / "local.txt").write_text("edited here", encoding="utf-8")
    _run(work, "git", "add", ".")
    _run(work, "git", "commit", "--quiet", "-m", "local change")
    with pytest.raises(deploy_main.DeployError):
        _deploy(work, monkeypatch)


def test_uncommitted_changes_are_refused(repos, monkeypatch) -> None:
    _, work = repos
    (work / "app.txt").write_text("dirty", encoding="utf-8")
    with pytest.raises(deploy_main.DeployError, match="uncommitted"):
        _deploy(work, monkeypatch)


def test_a_server_that_fails_its_check_is_an_error(repos, monkeypatch) -> None:
    _, work = repos
    with pytest.raises(deploy_main.DeployError):
        _deploy(work, monkeypatch, gate=False)


def test_rollback_returns_to_the_previous_commit(repos, monkeypatch) -> None:
    """A bad deploy should not be left running. The new commit fails its check,
    the old one comes back up."""
    _, work = repos
    before = deploy_main.local_head(work)
    with pytest.raises(deploy_main.DeployError, match="rolled back"):
        _deploy(work, monkeypatch, rollback=True, gates=[False, True])
    assert deploy_main.local_head(work) == before
    assert (work / "app.txt").read_text(encoding="utf-8") == "v1"


def test_a_failed_rollback_says_so_distinctly(repos, monkeypatch) -> None:
    """If going back does not fix it either, the new commit is not the cause --
    and the message must not claim a rollback succeeded."""
    _, work = repos
    with pytest.raises(deploy_main.DeployError, match="did not come up either"):
        _deploy(work, monkeypatch, rollback=True, gates=[False, False])


# ---------------------------------------------------------------- the gate


def test_an_open_gate_is_not_a_healthy_server(monkeypatch) -> None:
    """The check this exists for. A 200 on a gated route means authentication
    is off -- the one outcome worth refusing outright on a public deployment."""
    monkeypatch.setattr(
        deploy_main,
        "status_code",
        lambda port, path, timeout=10.0: 200,  # health 200 *and* gate 200
    )
    assert deploy_main.gate_is_closed(8077) is False


def test_a_closed_gate_with_a_healthy_server_passes(monkeypatch) -> None:
    codes = {deploy_main.HEALTH_PATH: 200, deploy_main.GATED_PATH: 401}
    monkeypatch.setattr(
        deploy_main, "status_code", lambda port, path, timeout=10.0: codes[path]
    )
    assert deploy_main.gate_is_closed(8077) is True


def test_a_server_that_is_down_is_not_healthy(monkeypatch) -> None:
    monkeypatch.setattr(deploy_main, "status_code", lambda port, path, timeout=10.0: 0)
    assert deploy_main.gate_is_closed(8077) is False
