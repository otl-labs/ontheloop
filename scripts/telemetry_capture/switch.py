"""The capture switch and guards (rule SW-1, SW-3) and the synthetic-bound smoke override (rule SW-2). Stdlib and yaml only."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from pathlib import Path

import yaml

SWITCH_FILE = Path("config") / "telemetry" / "capture.yaml"
SMOKE_ENV = "OTL_TURN_CAPTURE_SMOKE_REF"
SMOKE_PREFIX = "synthetic:"
OFF, ON, SMOKE = "off", "on", "smoke"
GIT_TIMEOUT_S = 2.0
REFS = (("origin/main", "origin_main"), ("main", "main"))
WORKING_TREE = "working_tree"


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    """One bounded git call; None when git is absent or the call timed out (both count as unresolved)."""
    try:
        return subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _enabled(text: str) -> bool:
    try:
        data = yaml.safe_load(text)
    except (ValueError, yaml.YAMLError):
        return False
    return isinstance(data, dict) and data.get("enabled") is True


def _is_toplevel(repo_root: Path) -> bool:
    top = _git(repo_root, "rev-parse", "--show-toplevel")
    return top is not None and top.returncode == 0 and Path(top.stdout.strip()).resolve() == repo_root.resolve()


def switch_state(repo_root: Path) -> tuple[bool, str]:
    """Rule SW-3: (enabled, source). origin/main, then main, then the working tree; a resolving ref is decisive."""
    if _is_toplevel(repo_root):
        for ref, source in REFS:
            found = _git(repo_root, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}")
            if found is None or found.returncode != 0:
                continue
            shown = _git(repo_root, "show", f"{ref}:{SWITCH_FILE.as_posix()}")
            if shown is None:
                continue
            return shown.returncode == 0 and _enabled(shown.stdout), source
    try:
        return _enabled((repo_root / SWITCH_FILE).read_text(encoding="utf-8")), WORKING_TREE
    except (OSError, ValueError):
        return False, WORKING_TREE


def switch_enabled(repo_root: Path) -> bool:
    return switch_state(repo_root)[0]


def smoke_ref(env: Mapping[str, str]) -> str | None:
    ref = env.get(SMOKE_ENV) or ""
    return ref if ref.startswith(SMOKE_PREFIX) and len(ref) > len(SMOKE_PREFIX) else None


def switch_mode(env: Mapping[str, str], repo_root: Path) -> tuple[str, str | None]:
    """(off | on | smoke, the switch source or None when no switch read happened); smoke only for the smoke-pinned tree."""
    if env.get("GITHUB_ACTIONS") == "true" or env.get("OTL_TURN_CAPTURE") == "off":
        return OFF, None
    if smoke_ref(env) is not None:
        return SMOKE, None
    if env.get("CLAUDE_CODE_REMOTE") != "true":
        return OFF, None
    enabled, source = switch_state(repo_root)
    return (ON if enabled else OFF), source


def capture_mode(env: Mapping[str, str], repo_root: Path) -> str:
    return switch_mode(env, repo_root)[0]


def capture_allowed(env: Mapping[str, str], repo_root: Path) -> bool:
    return capture_mode(env, repo_root) != OFF
