"""Keep the tunnel deployment current with `main`.

GitHub Actions runs the tests; nothing was moving the result onto the machine
that serves it. The deployment worktree sat wherever it was last left, which is
how it ended up three commits behind `main` while appearing perfectly healthy --
the failure mode of manual deployment is not an error, it is silence.

This closes that loop by pull rather than push. The serving host is behind a
tunnel with no inbound path of its own, so there is nothing for CI to deploy
*to*; the host has to ask. It polls `origin/main`, fast-forwards the worktree,
and restarts the server.

Deliberately narrow, because this runs unattended against a live deployment:

* **Fast-forward only.** A diverged deployment branch means somebody committed
  on the serving host, and silently discarding that is worse than stopping.
* **Never restarts on an unchanged commit.** Restarting drops in-flight runs,
  so a no-op poll must cost nothing.
* **Verifies the gate after restart**, not just the port. A server that answers
  200 on `/api/runs` is one with authentication off, and this refuses to leave
  that running -- see `--rollback-on-failure`.

    python scripts/deploy_main.py --once      # one cycle, for cron
    python scripts/deploy_main.py             # poll forever
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_INTERVAL = 120
DEFAULT_PORT = 8077
# The gate must answer 401 here. Any authenticated route would do; this one is
# cheap and does not touch the run store.
GATED_PATH = "/api/runs"
HEALTH_PATH = "/api/health"


class DeployError(RuntimeError):
    """A deployment step failed in a way that needs a human."""


def _git(worktree: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(worktree), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise DeployError(f"git {' '.join(args)} failed: {result.stderr.strip()[:400]}")
    return result.stdout.strip()


def local_head(worktree: Path) -> str:
    return _git(worktree, "rev-parse", "HEAD")


def remote_head(worktree: Path, branch: str) -> str:
    _git(worktree, "fetch", "origin", branch, "--quiet")
    return _git(worktree, "rev-parse", f"origin/{branch}")


def status_code(port: int, path: str, timeout: float = 10.0) -> int:
    """HTTP status without raising on 4xx/5xx -- a 401 is the expected answer."""
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, OSError):
        return 0


def gate_is_closed(port: int) -> bool:
    """Whether the server is up *and* still demanding a password.

    Both halves matter. Checking only the port would let a server with
    authentication switched off count as a successful deployment, which is the
    one outcome worth refusing outright when the thing is on the internet.
    """
    return status_code(port, HEALTH_PATH) == 200 and status_code(port, GATED_PATH) == 401


def wait_for_gate(port: int, timeout: float, poll: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if gate_is_closed(port):
            return True
        time.sleep(poll)
    return False


def fast_forward(worktree: Path, branch: str) -> str:
    """Advance the worktree, refusing anything that is not a fast-forward."""
    if _git(worktree, "status", "--porcelain"):
        raise DeployError(
            "the deployment worktree has uncommitted changes; refusing to move it"
        )
    _git(worktree, "merge", "--ff-only", f"origin/{branch}")
    return local_head(worktree)


def restart(restart_command: list[str], port: int, timeout: float) -> None:
    subprocess.run(restart_command, check=False)
    if not wait_for_gate(port, timeout):
        raise DeployError(
            f"after restart, health/gate did not reach 200/401 on port {port} "
            f"within {timeout:.0f}s"
        )


def deploy_once(
    worktree: Path,
    *,
    branch: str,
    port: int,
    restart_command: list[str],
    timeout: float,
    rollback_on_failure: bool,
) -> dict[str, str]:
    before = local_head(worktree)
    target = remote_head(worktree, branch)
    if before == target:
        return {"status": "unchanged", "head": before}

    after = fast_forward(worktree, branch)
    try:
        restart(restart_command, port, timeout)
    except DeployError:
        if not rollback_on_failure:
            raise
        # Put the code back before re-raising. The server is already unhealthy;
        # leaving it on a commit nobody chose makes the next diagnosis harder.
        _git(worktree, "reset", "--hard", before)
        try:
            restart(restart_command, port, timeout)
        except DeployError as rollback_failure:
            # Worth distinguishing loudly: the deployment is down and going back
            # did not fix it, so the cause is not the new commit and whoever
            # reads this should stop looking there.
            raise DeployError(
                f"{after[:8]} failed its post-restart check, and the rollback to "
                f"{before[:8]} did not come up either: {rollback_failure}"
            ) from None
        raise DeployError(
            f"{after[:8]} failed its post-restart check; rolled back to {before[:8]}"
        ) from None
    return {"status": "deployed", "from": before, "head": after}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worktree", type=Path, required=True)
    parser.add_argument("--branch", default="main")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--once", action="store_true", help="one cycle, then exit")
    parser.add_argument(
        "--restart-command",
        required=True,
        help="shell-free command to restart the server, e.g. "
        '"powershell -File scripts/start_public.ps1"',
    )
    parser.add_argument(
        "--rollback-on-failure",
        action="store_true",
        help="return to the previous commit if the restarted server fails its check",
    )
    args = parser.parse_args()

    restart_command = args.restart_command.split()

    while True:
        try:
            result = deploy_once(
                args.worktree,
                branch=args.branch,
                port=args.port,
                restart_command=restart_command,
                timeout=args.timeout,
                rollback_on_failure=args.rollback_on_failure,
            )
            if result["status"] != "unchanged":
                print(json.dumps(result), flush=True)
        except DeployError as exc:
            print(json.dumps({"status": "failed", "error": str(exc)}), file=sys.stderr, flush=True)
            if args.once:
                return 1
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
