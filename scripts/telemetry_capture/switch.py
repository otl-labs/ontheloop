"""The capture switch and guards (rule SW-1, SW-3) and the synthetic-bound smoke override (rule SW-2). Stdlib and yaml only."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

SWITCH_FILE = Path("config") / "telemetry" / "capture.yaml"
SMOKE_ENV = "OTL_TURN_CAPTURE_SMOKE_REF"
SMOKE_PREFIX = "synthetic:"
OFF, ON, SMOKE, CI = "off", "on", "smoke", "ci"
GIT_TIMEOUT_S = 2.0
REFS = (("origin/main", "origin_main"), ("main", "main"))
WORKING_TREE = "working_tree"
CI_MAIN_REF = "refs/heads/main"
CI_CONTENT_POLICIES = ("full", "withheld")
AGENT_KEY = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


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


def _load(text: str) -> dict | None:
    try:
        data = yaml.safe_load(text)
    except (ValueError, yaml.YAMLError):
        return None
    return data if isinstance(data, dict) else None


def _is_toplevel(repo_root: Path) -> bool:
    top = _git(repo_root, "rev-parse", "--show-toplevel")
    return top is not None and top.returncode == 0 and Path(top.stdout.strip()).resolve() == repo_root.resolve()


def read_repo_file(repo_root: Path, rel: Path) -> tuple[str | None, str]:
    """Rule SW-3 for any repo file: (its text or None, source). origin/main, then main, then the working tree; a resolving
    ref is decisive, and a failed read of the file at a resolved ref (including a timeout) is None, never a fall-through."""
    if _is_toplevel(repo_root):
        for ref, source in REFS:
            found = _git(repo_root, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}")
            if found is None or found.returncode != 0:
                continue
            shown = _git(repo_root, "show", f"{ref}:{rel.as_posix()}")
            return (shown.stdout if shown is not None and shown.returncode == 0 else None), source
    try:
        return (repo_root / rel).read_text(encoding="utf-8"), WORKING_TREE
    except (OSError, ValueError):
        return None, WORKING_TREE


def _read_switch(repo_root: Path) -> tuple[dict | None, str]:
    text, source = read_repo_file(repo_root, SWITCH_FILE)
    return (None if text is None else _load(text)), source


def switch_state(repo_root: Path) -> tuple[bool, str]:
    """Rule SW-3: (enabled, source)."""
    data, source = _read_switch(repo_root)
    return data is not None and data.get("enabled") is True, source


def switch_enabled(repo_root: Path) -> bool:
    return switch_state(repo_root)[0]


def smoke_ref(env: Mapping[str, str]) -> str | None:
    ref = env.get(SMOKE_ENV) or ""
    return ref if ref.startswith(SMOKE_PREFIX) and len(ref) > len(SMOKE_PREFIX) else None


@dataclass(frozen=True)
class CiGate:
    mode: str
    entry: dict | None
    source: str | None
    reason: str | None


def _ci_entry(data: Mapping, workflow_path: str) -> tuple[dict | None, str | None]:
    rows = data.get("ci_workflows")
    named = [r for r in rows if isinstance(r, dict) and r.get("workflow") == workflow_path] if isinstance(rows, list) else []
    if not named:
        return None, "not_allow_listed"
    entry = named[0]
    agent, policy = entry.get("agent"), entry.get("content_policy")
    if len(named) != 1 or not isinstance(agent, str) or not AGENT_KEY.match(agent) or policy not in CI_CONTENT_POLICIES:
        return None, "malformed_entry"
    return {"workflow": workflow_path, "agent": agent, "content_policy": policy}, None


def ci_gate(repo_root: Path, *, workflow_path: str, ref: str, repository: str) -> CiGate:
    """One read of capture.yaml by the SW-3 order; mode ci only for an allow-listed workflow on main of the CI repository."""
    data, source = _read_switch(repo_root)
    if data is None or data.get("enabled") is not True:
        return CiGate(OFF, None, source, "switch_off")
    if data.get("ci_repository") != repository:
        return CiGate(OFF, None, source, "repository_mismatch")
    if ref != CI_MAIN_REF:
        return CiGate(OFF, None, source, "ref_not_main")
    entry, reason = _ci_entry(data, workflow_path)
    if entry is None:
        return CiGate(OFF, None, source, reason)
    return CiGate(CI, entry, source, None)


def switch_mode(env: Mapping[str, str], repo_root: Path, ci: CiGate | None = None) -> tuple[str, str | None]:
    """(off | on | smoke | ci, the switch source or None when no switch read happened); smoke only for the smoke-pinned tree.

    A CiGate (the ci-pass caller only) skips the GITHUB_ACTIONS veto and the remote/smoke branches; every hook caller
    passes none, so hook capture stays off in Actions."""
    if env.get("OTL_TURN_CAPTURE") == "off":
        return OFF, None
    if ci is not None:
        return ci.mode, ci.source
    if env.get("GITHUB_ACTIONS") == "true":
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
