"""The container entry point, and the four ways a deploy can go wrong quietly.

A PaaS hands out a public URL the moment the process answers a health check.
That changes the cost of every misconfiguration here: on the tunnel deployment a
bad boot meant a broken localhost, and here it can mean the control plane is on
the internet, or the session key changes on every deploy, or the backend cannot
possibly work and every run fails one at a time instead of once at startup.

So this refuses to start rather than starting wrong, and each refusal says which
variable to set. These tests are about the refusals; nothing here binds a port.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "serve_container", ROOT / "scripts" / "serve_container.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


container = _load()

AUTH_KEYS = (
    "ADS_AUTH_USERS_JSON",
    "ADS_AUTH_USERNAME",
    "ADS_AUTH_PASSWORD_HASH",
    "ADS_AUTH_SECRET",
    "ADS_LLM_BACKEND",
)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for key in AUTH_KEYS:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def _run(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(sys, "argv", ["serve_container.py", "--data", str(tmp_path)])
    return container.main()


# ------------------------------------------------------------ credentials


def test_no_credential_refuses_to_start(clean_env, tmp_path: Path) -> None:
    """The one that matters most. A PaaS publishes a URL immediately, so booting
    without a credential puts the control plane on the internet -- a far worse
    outcome than a failed deploy."""
    with pytest.raises(SystemExit) as exit_info:
        _run(clean_env, tmp_path)
    assert "ADS_AUTH_USERS_JSON" in str(exit_info.value)


def test_a_users_json_credential_is_accepted(clean_env) -> None:
    clean_env.setenv("ADS_AUTH_USERS_JSON", '{"ishak-ads":"scrypt$..."}')
    assert container._configured_credential() is True


def test_the_legacy_pair_is_still_accepted(clean_env) -> None:
    """Deployments configured by `set_password.py` predate the multi-user file
    and must not be locked out by this."""
    clean_env.setenv("ADS_AUTH_USERNAME", "ishak-ads")
    clean_env.setenv("ADS_AUTH_PASSWORD_HASH", "scrypt$...")
    assert container._configured_credential() is True


def test_half_the_legacy_pair_is_not_a_credential(clean_env) -> None:
    """A username with no hash authenticates nobody, and treating it as
    configured would boot an unprotected server."""
    clean_env.setenv("ADS_AUTH_USERNAME", "ishak-ads")
    assert container._configured_credential() is False


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_variable_is_not_a_credential(clean_env, blank: str) -> None:
    """An unset variable on a PaaS often arrives as an empty string rather than
    absent, so emptiness has to count as missing."""
    clean_env.setenv("ADS_AUTH_USERS_JSON", blank)
    assert container._configured_credential() is False


# ---------------------------------------------------------------- secrets


def test_a_missing_session_secret_refuses_to_start(clean_env, tmp_path: Path) -> None:
    """Without it the signing key is regenerated per process, so every deploy
    silently logs the whole team out."""
    clean_env.setenv("ADS_AUTH_USERS_JSON", '{"ishak-ads":"scrypt$..."}')
    with pytest.raises(SystemExit) as exit_info:
        _run(clean_env, tmp_path)
    assert "ADS_AUTH_SECRET" in str(exit_info.value)


# --------------------------------------------------------------- backends


@pytest.mark.parametrize("backend", ["claude_cli", "ollama", "CLAUDE_CLI", "Ollama"])
def test_a_backend_that_cannot_work_here_is_refused_at_startup(
    clean_env, tmp_path: Path, backend: str
) -> None:
    """`claude_cli` needs a logged-in Claude Code session and `ollama` needs a
    model server; a container has neither. Failing once at boot beats failing
    on every run, one confusing error at a time."""
    clean_env.setenv("ADS_AUTH_USERS_JSON", '{"ishak-ads":"scrypt$..."}')
    clean_env.setenv("ADS_AUTH_SECRET", "a-secret")
    clean_env.setenv("ADS_LLM_BACKEND", backend)

    with pytest.raises(SystemExit) as exit_info:
        _run(clean_env, tmp_path)
    message = str(exit_info.value)
    assert backend.casefold() in message
    assert "deepseek" in message, "say which backend does work"


def test_the_refusal_names_the_variable_to_change(clean_env, tmp_path: Path) -> None:
    clean_env.setenv("ADS_AUTH_USERS_JSON", '{"ishak-ads":"scrypt$..."}')
    clean_env.setenv("ADS_AUTH_SECRET", "a-secret")
    clean_env.setenv("ADS_LLM_BACKEND", "ollama")
    with pytest.raises(SystemExit) as exit_info:
        _run(clean_env, tmp_path)
    assert "ADS_LLM_BACKEND" in str(exit_info.value)
