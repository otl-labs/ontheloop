"""The merge gate shim's fail-closed nodes and the registration nodes (settings.json and the rendered hooks.json)."""

from __future__ import annotations

import json
import re
import shutil
import stat
from pathlib import Path

import pytest

from tests.fixtures import merge_overlap_repos as fx

_MATCHER = "^mcp__github(-full)?__(merge_pull_request|enable_pr_auto_merge)$"
_SHIM = fx.REAL_ROOT / ".claude/hooks/merge_gate.sh"


@pytest.fixture(scope="module")
def base(tmp_path_factory: pytest.TempPathFactory) -> Path:
    tmp = tmp_path_factory.mktemp("shim")
    f = fx.Fixture(tmp)
    f.branch_pr(1, "a.txt", "pr\n")
    project = tmp / "template"
    project.mkdir()
    fx.install_project(project)
    fx.git(project, "init", "-b", "main")
    fx.git(project, "remote", "add", "origin", f.origin_url)
    f.set_github_identity(project)
    return project


@pytest.fixture
def project(base: Path, tmp_path: Path) -> Path:
    dest = tmp_path / "project"
    shutil.copytree(base, dest, symlinks=True)
    return dest


def _run(project: Path, payload: str | None = None, **kw):  # noqa: ANN003, ANN202
    return fx.run_shim(project, payload or fx.merge_payload(1), cwd=project, **kw)


def test_non_merge_payload_exits_zero_without_python(project: Path) -> None:
    (project / "bin/venv-python").unlink()
    r = _run(project, json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}}))
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")


def test_clean_pass_through(project: Path) -> None:
    r = _run(project)
    assert (r.returncode, r.stderr) == (0, "")


def test_adapter_crash_denies(project: Path) -> None:
    (project / ".claude/hooks/merge_gate.py").write_text("raise SystemExit(1)\n", encoding="utf-8")
    r = _run(project)
    assert r.returncode == 2 and "merge-gate unavailable (1)" in r.stderr


def test_adapter_traceback_denies(project: Path) -> None:
    (project / ".claude/hooks/merge_gate.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    assert _run(project).returncode == 2


def test_missing_interpreter_denies(project: Path) -> None:
    (project / "bin/venv-python").unlink()
    r = _run(project)
    assert r.returncode == 2 and "merge-gate unavailable" in r.stderr


def test_missing_adapter_denies(project: Path) -> None:
    (project / ".claude/hooks/merge_gate.py").unlink()
    r = _run(project)
    assert r.returncode == 2 and "merge-gate unavailable (missing adapter)" in r.stderr


def test_timeout_kill_denies(project: Path, tmp_path: Path) -> None:
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    stub = stub_dir / "timeout"
    stub.write_text("#!/usr/bin/env bash\nexit 124\n", encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    import os

    r = _run(project, extra_env={"PATH": f"{stub_dir}:{os.environ['PATH']}"})
    assert r.returncode == 2 and "merge-gate unavailable (124)" in r.stderr


def test_timeout_bound_is_pinned_below_the_hook_timeout() -> None:
    match = re.search(r"timeout -k (\d+) (\d+) ", _SHIM.read_text(encoding="utf-8"))
    assert match
    kill_after, limit = int(match.group(1)), int(match.group(2))
    settings = json.loads((fx.REAL_ROOT / ".claude/settings.json").read_text(encoding="utf-8"))
    group = next(g for g in settings["hooks"]["PreToolUse"] if g["matcher"] == _MATCHER)
    assert limit + kill_after < group["hooks"][0]["timeout"]


def test_github_actions_env_is_still_gated(project: Path) -> None:
    r = _run(project, extra_env={"GITHUB_ACTIONS": "true"})
    assert r.returncode == 0
    (project / ".claude/hooks/merge_gate.py").write_text("raise SystemExit(1)\n", encoding="utf-8")
    assert _run(project, extra_env={"GITHUB_ACTIONS": "true"}).returncode == 2


def test_planted_stdlib_name_is_not_loaded(project: Path, tmp_path: Path) -> None:
    marker = tmp_path / "planted"
    (project / ".claude/hooks/json.py").write_text(f"open({str(marker)!r}, 'w').close()\n", encoding="utf-8")
    (project / "json.py").write_text(f"open({str(marker)!r}, 'w').close()\n", encoding="utf-8")
    r = fx.run_shim(project, fx.merge_payload(1), cwd=project)
    assert r.returncode == 0 and not marker.exists()


def test_settings_registers_the_merge_group() -> None:
    settings = json.loads((fx.REAL_ROOT / ".claude/settings.json").read_text(encoding="utf-8"))
    groups = [g for g in settings["hooks"]["PreToolUse"] if g["matcher"] == _MATCHER]
    assert len(groups) == 1
    (hook,) = groups[0]["hooks"]
    assert hook["command"] == 'bash "$CLAUDE_PROJECT_DIR/.claude/hooks/merge_gate.sh"' and hook["timeout"] == 60
    pattern = re.compile(_MATCHER)
    for name in ("merge_pull_request", "enable_pr_auto_merge"):
        assert pattern.match(f"mcp__github__{name}") and pattern.match(f"mcp__github-full__{name}")
    assert not pattern.match("mcp__github__merge_pull_request_x")


def test_hooks_json_carries_the_same_matcher_and_timeout() -> None:
    hooks = json.loads((fx.REAL_ROOT / ".claude/hooks/hooks.json").read_text(encoding="utf-8"))
    groups = [g for g in hooks["hooks"]["PreToolUse"] if g["matcher"] == _MATCHER]
    assert len(groups) == 1
    (hook,) = groups[0]["hooks"]
    assert "merge_gate.sh" in hook["command"] and hook["timeout"] == 60


def test_shim_is_executable() -> None:
    assert _SHIM.stat().st_mode & stat.S_IXUSR
