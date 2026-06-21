"""
romion-deploy - bounded docker compose runner.

Runs `docker compose -f <stack_dir>/docker-compose.yml <action>` with NO shell,
a fixed action vocabulary, timeout and output caps. The caller never supplies a
command or a path — only an allowlisted stack name (resolved to a dir elsewhere)
and an action from ALLOWED_ACTIONS.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

ALLOWED_ACTIONS = {"up", "down", "restart", "ps", "logs"}
DEFAULT_TIMEOUT = 300
MAX_TIMEOUT = 1800
MAX_OUTPUT_CHARS = 100_000

# docker reaches the daemon via the unix socket (group membership), so the child
# needs almost no environment. Pass only what's required to find the binary.
_SAFE_ENV_KEYS = {"PATH", "HOME", "DOCKER_HOST", "DOCKER_CONFIG", "LANG", "LC_ALL"}


def _clean_env() -> dict[str, str]:
    return {k: os.environ[k] for k in _SAFE_ENV_KEYS if k in os.environ}


def effective_dir(stack_dir: str) -> str:
    """For release-managed stacks, run compose inside the active release.

    If <stack_dir>/current is a pointer file holding a release sha and
    <stack_dir>/releases/<sha> exists, return that; otherwise the stack_dir
    itself (simple, non-release-managed stacks keep working unchanged).
    """
    ptr = Path(stack_dir) / "current"
    if ptr.is_file():
        sha = ptr.read_text(encoding="utf-8").strip()
        cand = Path(stack_dir) / "releases" / sha
        if sha and cand.is_dir():
            return str(cand)
    return stack_dir


def build_compose_args(stack_dir: str, action: str, tail: int = 200) -> list[str]:
    """Pure: build the argv for a compose action. Validates action."""
    if action not in ALLOWED_ACTIONS:
        raise ValueError(f"action not allowed: {action}")
    compose_file = str(Path(stack_dir) / "docker-compose.yml")
    argv = ["docker", "compose", "-f", compose_file]
    if action == "up":
        argv += ["up", "-d", "--build"]
    elif action == "down":
        argv += ["down"]
    elif action == "restart":
        argv += ["restart"]
    elif action == "ps":
        argv += ["ps"]
    elif action == "logs":
        tail = max(1, min(int(tail), 5000))
        argv += ["logs", "--no-color", "--tail", str(tail)]
    return argv


def _truncate(s: str) -> tuple[str, bool]:
    return (s[:MAX_OUTPUT_CHARS], True) if len(s) > MAX_OUTPUT_CHARS else (s, False)


def run_compose(
    stack_dir: str,
    action: str,
    tail: int = 200,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Run one bounded compose action. No shell."""
    stack_dir = effective_dir(stack_dir)
    argv = build_compose_args(stack_dir, action, tail)
    compose_file = Path(stack_dir) / "docker-compose.yml"
    if not compose_file.is_file():
        return {
            "status": "error",
            "action": action,
            "stack_dir": stack_dir,
            "error": f"docker-compose.yml not found in {stack_dir}",
        }

    timeout = max(1, min(int(timeout), MAX_TIMEOUT))
    start = time.monotonic()
    timed_out = False
    try:
        proc = subprocess.run(
            argv, cwd=stack_dir, capture_output=True, text=True,
            timeout=timeout, shell=False, encoding="utf-8", errors="replace",
            env=_clean_env(),
        )
        rc, out, err = proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired as e:
        timed_out, rc = True, None
        out = e.stdout if isinstance(e.stdout, str) else (e.stdout or b"").decode("utf-8", "replace")
        err = e.stderr if isinstance(e.stderr, str) else (e.stderr or b"").decode("utf-8", "replace")
    except FileNotFoundError as e:
        return {"status": "error", "action": action, "stack_dir": stack_dir,
                "error": f"docker not found: {e}"}

    out, out_trunc = _truncate(out)
    err, err_trunc = _truncate(err)
    return {
        "status": "timeout" if timed_out else ("ok" if rc == 0 else "nonzero_exit"),
        "action": action,
        "stack_dir": stack_dir,
        "exit_code": rc,
        "timed_out": timed_out,
        "duration_s": round(time.monotonic() - start, 3),
        "stdout": out,
        "stdout_truncated": out_trunc,
        "stderr": err,
        "stderr_truncated": err_trunc,
    }
