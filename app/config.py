"""
romion-deploy - stack allowlist config.

The deploy channel can only act on stacks declared here. A stack is a name ->
directory mapping; the directory must contain a docker-compose.yml. No arbitrary
paths are ever accepted from the API — callers reference a stack by name only.

Config source: DEPLOY_STACKS_CONFIG env (path to JSON), else app/stacks.json.

stacks.json shape:
{
  "stacks": {
    "demo": { "dir": "/home/deploy/apps/demo" }
  }
}
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

_DEFAULT_CONFIG = Path(__file__).resolve().parent / "stacks.json"
CONFIG_PATH = Path(os.environ.get("DEPLOY_STACKS_CONFIG", str(_DEFAULT_CONFIG)))

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class ConfigError(Exception):
    pass


def load_stacks() -> dict[str, dict[str, Any]]:
    """Load and validate the stack allowlist. Raises ConfigError on problems."""
    if not CONFIG_PATH.is_file():
        return {}
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ConfigError(f"cannot read stacks config: {e}") from e

    stacks = raw.get("stacks") or {}
    if not isinstance(stacks, dict):
        raise ConfigError("'stacks' must be an object")

    out: dict[str, dict[str, Any]] = {}
    for name, spec in stacks.items():
        if not _NAME_RE.match(str(name)):
            raise ConfigError(f"invalid stack name: {name!r}")
        if not isinstance(spec, dict) or "dir" not in spec:
            raise ConfigError(f"stack {name!r} must have a 'dir'")
        out[name] = {"dir": str(spec["dir"])}
    return out


def resolve_stack(name: str) -> str:
    """Return the absolute compose directory for an allowlisted stack name.

    Raises KeyError if the name is not in the allowlist. The name is never
    treated as a path, so traversal is impossible.
    """
    stacks = load_stacks()
    if name not in stacks:
        raise KeyError(name)
    return stacks[name]["dir"]
