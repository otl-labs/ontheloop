"""Rule HB-1: every registered PreToolUse hook ends every block message with its own HOOK-BLOCK v1 marker line."""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
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


_CLEAN_ENV = {
    k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_", "CC_")) and k not in ("AWS_PROFILE", "GITHUB_ACTIONS")
}
_MARKER_LITERAL = "HOOK-BLOCK v1 hook="
_ADMIN_PROFILE = "agent_platform_" + "admin"
_PUSH = {"tool_name": "Bash", "tool_input": {"command": "git push origin HEAD"}}
_MERGE = {"tool_name": "mcp__github__merge_pull_request", "tool_input": {"pullNumber": 1}}
_TERRAFORM = {"tool_name": "Bash", "tool_input": {"command": "terraform plan"}}
_PAYLOADS = {"handoff_evidence_gate": _PUSH, "merge_gate": _MERGE, "protected_action_gate": _TERRAFORM}
_SKIPS = {
    "handoff_evidence_gate": {"tool_name": "Bash", "tool_input": {"command": "ls"}},
    "merge_gate": {"tool_name": "Bash", "tool_input": {"command": "ls"}},
}
_STUBS = {
    "deny": "import sys\nsys.stderr.write('stub deny\\n')\nsys.exit(2)\n",
    "exit1": "import sys\nsys.exit(1)\n",
    "exit124": "import sys\nsys.exit(124)\n",
    "allow": "import sys\nsys.exit(0)\n",
    "absent": None,
}


def _run_shim(
    tmp_path: Path, stem: str, payload: dict, stub: str | None, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    hooks = tmp_path / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    shutil.copy(HOOKS / f"{stem}.sh", hooks / f"{stem}.sh")
    if stub is not None:
        (hooks / f"{stem}.py").write_text(stub, encoding="utf-8")
    venv = tmp_path / "bin" / "venv-python"
    venv.parent.mkdir()
    venv.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    venv.chmod(0o755)
    env = {**_CLEAN_ENV, "CLAUDE_PROJECT_DIR": str(tmp_path), **(extra_env or {})}
    return subprocess.run(
        ["bash", str(hooks / f"{stem}.sh")],
        input=json.dumps(payload),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_rec_writer_tripwire_block_ends_with_its_marker(tmp_path: Path) -> None:
    payload = {"tool_name": "Bash", "tool_input": {"command": "curl -sS https://example.invalid/prod/ops/writer"}}
    done = subprocess.run(
        [sys.executable, str(HOOKS / "rec_writer_tripwire.py")],
        input=json.dumps(payload),
        cwd=tmp_path,
        env=_CLEAN_ENV,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert done.returncode == 2, done.stderr
    lines = done.stderr.rstrip("\n").split("\n")
    assert lines[0].startswith("rec-writer-tripwire: refused."), lines[0]
    assert lines[-1] == format_hook_block_marker("rec_writer_tripwire")
    assert hook_block_stems(done.stderr) == ["rec_writer_tripwire"]


@pytest.mark.parametrize("stub_kind", ["deny", "exit1", "exit124", "absent"])
@pytest.mark.parametrize("stem", sorted(_PAYLOADS))
def test_shim_deny_paths_end_with_one_marker(tmp_path: Path, stem: str, stub_kind: str) -> None:
    done = _run_shim(tmp_path, stem, _PAYLOADS[stem], _STUBS[stub_kind])
    assert done.returncode == 2, done.stderr
    assert done.stderr.rstrip("\n").split("\n")[-1] == format_hook_block_marker(stem)
    assert hook_block_stems(done.stderr) == [stem]
    if stub_kind == "deny":
        assert done.stderr.index("stub deny") < done.stderr.index(_MARKER_LITERAL)


def test_protected_action_gate_admin_profile_deny_ends_with_one_marker(tmp_path: Path) -> None:
    payload = {"tool_name": "Bash", "tool_input": {"command": "aws s3 ls"}}
    done = _run_shim(tmp_path, "protected_action_gate", payload, None, {"AWS_PROFILE": _ADMIN_PROFILE})
    assert done.returncode == 2, done.stderr
    assert hook_block_stems(done.stderr) == ["protected_action_gate"]
    assert done.stderr.rstrip("\n").split("\n")[-1] == format_hook_block_marker("protected_action_gate")


@pytest.mark.parametrize("stem", sorted(_PAYLOADS))
def test_shim_allow_paths_print_no_marker(tmp_path: Path, stem: str) -> None:
    done = _run_shim(tmp_path, stem, _PAYLOADS[stem], _STUBS["allow"])
    assert done.returncode == 0, done.stderr
    assert "HOOK-BLOCK" not in done.stderr


@pytest.mark.parametrize("stem", sorted(_SKIPS))
def test_shim_prefilter_skip_prints_no_marker(tmp_path: Path, stem: str) -> None:
    done = _run_shim(tmp_path, stem, _SKIPS[stem], _STUBS["deny"])
    assert done.returncode == 0, done.stderr
    assert "HOOK-BLOCK" not in done.stderr


def test_handoff_github_actions_carve_out_prints_no_marker(tmp_path: Path) -> None:
    done = _run_shim(tmp_path, "handoff_evidence_gate", _PUSH, _STUBS["deny"], {"GITHUB_ACTIONS": "true"})
    assert done.returncode == 0, done.stderr
    assert "HOOK-BLOCK" not in done.stderr


def test_protected_action_gate_fail_open_exit_zero_prints_no_marker(tmp_path: Path) -> None:
    payload = {"tool_name": "Bash", "tool_input": {"command": "ls"}}
    done = _run_shim(tmp_path, "protected_action_gate", payload, _STUBS["exit1"])
    assert done.returncode == 0, done.stderr
    assert "HOOK-BLOCK" not in done.stderr


_UNMARKED_EXEMPT: tuple[str, ...] = ()
_HOOK_PATH_RE = re.compile(r"\.claude/hooks/([a-z_]+)\.(py|sh)")


def _registered_pretooluse_hooks() -> list[tuple[str, str]]:
    stems: set[tuple[str, str]] = set()
    for name in (".claude/settings.json", ".claude/hooks/hooks.json"):
        data = json.loads((ROOT / name).read_text(encoding="utf-8"))
        for group in data["hooks"].get("PreToolUse", []):
            for hook in group["hooks"]:
                matches = _HOOK_PATH_RE.findall(hook["command"])
                assert matches, hook["command"]
                stems.add(matches[-1])
    return sorted(stems)


def _code_lines(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        doc = ast.get_docstring(ast.parse(text), clean=False)
        if doc:
            text = text.replace(doc, "", 1)
    return [line for line in text.splitlines() if not line.lstrip().startswith("#")]


def test_every_registered_pretooluse_hook_prints_its_own_marker() -> None:
    hooks = _registered_pretooluse_hooks()
    assert len(hooks) >= 8
    for stem, ext in hooks:
        if stem in _UNMARKED_EXEMPT:
            continue
        path = HOOKS / f"{stem}.{ext}"
        code = "\n".join(_code_lines(path))
        assert f"{_MARKER_LITERAL}{stem}" in code, f"{path.name} prints no own marker"
        adapter = HOOKS / f"{stem}.py"
        if path.suffix == ".sh" and adapter.exists():
            assert "HOOK-BLOCK v1" not in adapter.read_text(encoding="utf-8"), f"{adapter.name} prints a marker"
