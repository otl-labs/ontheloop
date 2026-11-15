#!/usr/bin/env python3
"""PreToolUse hook: block edits to files outside the active plan's declared Scope.

Activation:
  Set CLAUDE_ACTIVE_PLAN env var to the path of a PLAN-*.yaml (relative to repo root
  or absolute), OR write the path to .claude/active_plan (marker-file fallback).

Behaviour:
  No active plan declared  -> exit 0 ALLOW  (never wedges /plan, /orient, or ad-hoc work)
  Active plan declared:
    File in the plan's Scope -> exit 0 ALLOW
    File NOT in scope        -> exit 2 DENY  (fail-closed)
    Plan unreadable/invalid  -> exit 2 DENY  (fail-closed)

Applies to: Edit, Write, MultiEdit, NotebookEdit tools only.
Bash is NOT gated -- the hook is for file mutations, not shell commands.

Failure-mode: malformed input JSON -> exit 0 ALLOW (defensive, per never_on_main precedent).

## Known limitations (deliberate)
Activation (how /implement sets CLAUDE_ACTIVE_PLAN or the marker file) is a separate
integration concern.  This baseline ships the mechanism + registration + tests, inert until
a plan context is declared.

Rule HB-1: every block message ends with its own versioned marker line; src/turn_capture/markers.py holds the grammar.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_MUTATING_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
_ROOT = Path(__file__).parent.parent.parent  # .claude/hooks/ -> project root
_MARKER_FILE = _ROOT / ".claude" / "active_plan"


def _active_plan_path() -> Path | None:
    """Return the active plan path from env var or marker file, or None if not set."""
    env = os.environ.get("CLAUDE_ACTIVE_PLAN", "").strip()
    if env:
        p = Path(env)
        return p if p.is_absolute() else _ROOT / p

    if _MARKER_FILE.exists():
        raw = _MARKER_FILE.read_text(encoding="utf-8").strip()
        if raw:
            p = Path(raw)
            return p if p.is_absolute() else _ROOT / p

    return None


def _scope_from_plan(plan_path: Path) -> list[str] | None:
    """Parse the plan YAML and return the list of in-scope file paths, or None on parse error."""
    try:
        import yaml  # noqa: PLC0415

        data = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        scope_entries = data.get("scope") or []
        paths: list[str] = []
        for entry in scope_entries:
            if isinstance(entry, dict):
                f = entry.get("file", "")
                if f:
                    paths.append(str(f))
            elif isinstance(entry, str):
                paths.append(entry)
        return paths
    except Exception:
        return None


def _file_is_in_scope(file_path: str, scope: list[str]) -> bool:
    """Return True if file_path matches any scope entry (exact or prefix match)."""
    norm = file_path.lstrip("/")
    for entry in scope:
        entry_norm = entry.lstrip("/")
        if norm == entry_norm or norm.startswith(entry_norm.rstrip("/") + "/"):
            return True
    return False


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # malformed input -- allow (defensive)

    tool_name = payload.get("tool_name", "")
    if tool_name not in _MUTATING_TOOLS:
        return 0  # not a file-mutation tool -- always allow

    active = _active_plan_path()
    if active is None:
        return 0  # no active plan -- allow all edits

    # Active plan declared: parse scope and enforce.
    scope = _scope_from_plan(active)
    if scope is None:
        sys.stderr.write(
            f"BLOCKED by .claude/hooks/edit_scope_guard.py: "
            f"active plan {active} could not be read or parsed (fail-closed).\n"
            "Fix the plan file or unset CLAUDE_ACTIVE_PLAN / remove .claude/active_plan.\n"
            "HOOK-BLOCK v1 hook=edit_scope_guard\n"
        )
        return 2

    tool_input = payload.get("tool_input") or {}
    file_path = tool_input.get("file_path") or tool_input.get("path") or ""
    if not file_path:
        return 0  # no file path to check -- allow (e.g. Bash commands)

    # Normalise: strip root prefix so both absolute and relative paths compare cleanly.
    rel_path = file_path
    root_str = str(_ROOT)
    if rel_path.startswith(root_str):
        rel_path = rel_path[len(root_str) :].lstrip("/")

    if _file_is_in_scope(rel_path, scope):
        return 0

    # Exempt the active plan's own path: the implement flow edits the plan file itself (to set
    # implementation_declared after the graduation bookkeeping walk), and that edit must not
    # require every plan to list its own path in its own Scope as boilerplate.
    active_rel = str(active)
    if active_rel.startswith(root_str):
        active_rel = active_rel[len(root_str) :].lstrip("/")
    if rel_path == active_rel:
        return 0

    sys.stderr.write(
        f"BLOCKED by .claude/hooks/edit_scope_guard.py: "
        f"{file_path!r} is outside the active plan's declared Scope.\n"
        f"Active plan: {active}\n"
        "To edit out-of-scope files: unset CLAUDE_ACTIVE_PLAN / remove .claude/active_plan, "
        "or add the file to the plan's Scope and re-activate.\n"
        "HOOK-BLOCK v1 hook=edit_scope_guard\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
