"""Subprocess tests for .claude/hooks/never_on_main.py path scoping on main."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

_HOOK = Path(__file__).parent.parent.parent / ".claude" / "hooks" / "never_on_main.py"
GIT_ENV = ["-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]
_PATH_KEY = {"Edit": "file_path", "Write": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", *GIT_ENV, "-C", str(repo), *args], capture_output=True, text=True, check=True, timeout=30)
    return done.stdout.strip()


def _run(payload: dict | str, cwd: Path, branch: str | None = "main") -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_HOOK_BRANCH_OVERRIDE"}
    if branch is not None:
        env["CLAUDE_HOOK_BRANCH_OVERRIDE"] = branch
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        ["python3", str(_HOOK)], input=stdin, cwd=cwd, env=env, capture_output=True, text=True, timeout=30, check=False
    )


def _tool(tool: str, path: str | None) -> dict:
    return {"tool_name": tool, "tool_input": {} if path is None else {_PATH_KEY[tool]: path}}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    (root / "f.txt").write_text("x", encoding="utf-8")
    _git(root, "add", "f.txt")
    _git(root, "commit", "-q", "-m", "init")
    return root.resolve()


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    d = tmp_path / "outside"
    d.mkdir()
    return d


TOOLS = list(_PATH_KEY)


@pytest.mark.parametrize("tool", TOOLS)
class TestWritesOnMain:
    def test_outside_repo_passes(self, tool: str, repo: Path, outside: Path) -> None:
        assert _run(_tool(tool, str(outside / "m.md")), repo).returncode == 0

    def test_inside_repo_blocked(self, tool: str, repo: Path) -> None:
        assert _run(_tool(tool, str(repo / "f.txt")), repo).returncode == 2

    def test_relative_path_blocked(self, tool: str, repo: Path) -> None:
        assert _run(_tool(tool, "f.txt"), repo).returncode == 2

    def test_dotdot_traversal_into_repo_blocked(self, tool: str, repo: Path, outside: Path) -> None:
        sneaky = f"{outside}/../repo/f.txt"
        assert _run(_tool(tool, sneaky), repo).returncode == 2

    def test_symlink_into_repo_blocked(self, tool: str, repo: Path, outside: Path) -> None:
        link = outside / "link.txt"
        link.symlink_to(repo / "f.txt")
        assert _run(_tool(tool, str(link)), repo).returncode == 2

    def test_missing_path_key_blocked(self, tool: str, repo: Path) -> None:
        assert _run(_tool(tool, None), repo).returncode == 2

    def test_unknown_toplevel_blocked(self, tool: str, outside: Path) -> None:
        assert _run(_tool(tool, str(outside / "m.md")), outside).returncode == 2

    def test_sibling_prefix_path_passes(self, tool: str, repo: Path) -> None:
        sibling = Path(str(repo) + "-x")
        sibling.mkdir()
        assert _run(_tool(tool, str(sibling / "f")), repo).returncode == 0

    def test_off_main_always_passes(self, tool: str, repo: Path) -> None:
        assert _run(_tool(tool, str(repo / "f.txt")), repo, branch="claude/x").returncode == 0

    def test_linked_worktree_path_blocked(self, tool: str, repo: Path, tmp_path: Path) -> None:
        wt = tmp_path / "wt"
        _git(repo, "worktree", "add", "-q", "-b", "side", str(wt))
        wt = wt.resolve()
        assert _run(_tool(tool, str(wt / "f.txt")), wt).returncode == 2


class TestDenyMessage:
    def test_message_drops_branch_creation_advice(self, repo: Path) -> None:
        result = _run(_tool("Write", str(repo / "f.txt")), repo)
        assert result.returncode == 2
        assert "git-ops.yaml" in result.stderr
        assert "git branch --show-current" in result.stderr
        import re

        assert not re.search(r"assigned .?claude/", result.stderr)
        assert "checkout -b claude/" not in result.stderr


class TestBash:
    @pytest.mark.parametrize("command", ["git commit -m x", "git push origin HEAD"])
    def test_commit_push_blocked_on_main(self, command: str, repo: Path) -> None:
        assert _run({"tool_name": "Bash", "tool_input": {"command": command}}, repo).returncode == 2

    def test_status_passes_on_main(self, repo: Path) -> None:
        assert _run({"tool_name": "Bash", "tool_input": {"command": "git status"}}, repo).returncode == 0

    def test_commit_passes_off_main(self, repo: Path) -> None:
        payload = {"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}}
        assert _run(payload, repo, branch="claude/x").returncode == 0


def test_malformed_json_fails_open(repo: Path) -> None:
    assert _run("{not json", repo).returncode == 0
