"""Rule HB-1: each of the four blocking hooks ends every block message with its own HOOK-BLOCK v1 marker line."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.turn_capture.markers import format_hook_block_marker, hook_block_stems

ROOT = Path(__file__).resolve().parents[2]
HOOKS = ROOT / ".claude" / "hooks"
STEMS = ("never_on_main", "fresh_branch_base", "edit_scope_guard", "scheduled_agent_log_only")
_BASE_ENV = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_", "CC_"))}


def _run(stem: str, payload: dict, env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HOOKS / f"{stem}.py")],
        input=json.dumps(payload),
        cwd=cwd,
        env={**_BASE_ENV, **env},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _assert_marked(done: subprocess.CompletedProcess[str], stem: str) -> None:
    assert done.returncode == 2, done.stderr
    lines = done.stderr.rstrip("\n").split("\n")
    assert lines[0].startswith(f"BLOCKED by .claude/hooks/{stem}.py: "), lines[0]
    assert lines[-1] == format_hook_block_marker(stem)
    assert hook_block_stems(done.stderr) == [stem]


def test_never_on_main_block_ends_with_its_marker(tmp_path: Path) -> None:
    payload = {"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}}
    _assert_marked(_run("never_on_main", payload, {"CLAUDE_HOOK_BRANCH_OVERRIDE": "main"}, tmp_path), "never_on_main")


def test_fresh_branch_base_block_ends_with_its_marker(tmp_path: Path) -> None:
    payload = {"tool_name": "Bash", "tool_input": {"command": "git checkout -b foo main"}}
    _assert_marked(_run("fresh_branch_base", payload, {"CLAUDE_HOOK_BRANCH_OVERRIDE": "main"}, tmp_path), "fresh_branch_base")


def test_edit_scope_guard_unreadable_plan_ends_with_its_marker(tmp_path: Path) -> None:
    payload = {"tool_name": "Write", "tool_input": {"file_path": "x.txt"}}
    env = {"CLAUDE_ACTIVE_PLAN": str(tmp_path / "missing-plan.yaml")}
    _assert_marked(_run("edit_scope_guard", payload, env, tmp_path), "edit_scope_guard")


def test_edit_scope_guard_out_of_scope_path_ends_with_its_marker(tmp_path: Path) -> None:
    plan = tmp_path / "plan.yaml"
    plan.write_text("scope:\n  - file: allowed.txt\n", encoding="utf-8")
    payload = {"tool_name": "Write", "tool_input": {"file_path": "elsewhere.txt"}}
    _assert_marked(_run("edit_scope_guard", payload, {"CLAUDE_ACTIVE_PLAN": str(plan)}, tmp_path), "edit_scope_guard")


def test_scheduled_agent_log_only_block_ends_with_its_marker(tmp_path: Path) -> None:
    payload = {"tool_name": "Write", "tool_input": {"file_path": "src/x.py"}}
    env = {"CC_SCHEDULED_AGENT_NAME": "probe-agent"}
    _assert_marked(_run("scheduled_agent_log_only", payload, env, tmp_path), "scheduled_agent_log_only")


@pytest.mark.parametrize("stem", STEMS)
def test_every_block_literal_carries_the_marker(stem: str) -> None:
    text = (HOOKS / f"{stem}.py").read_text(encoding="utf-8")
    assert text.count("BLOCKED by .claude/hooks/") == text.count("HOOK-BLOCK v1 hook=") >= 1
    assert text.count(f"HOOK-BLOCK v1 hook={stem}\\n") == text.count("HOOK-BLOCK v1 hook=")
