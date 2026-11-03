"""Shared fixtures for the merge-overlap tests: a local bare origin at <tmp>/otl-labs/ontheloop.git carrying main and
refs/pull/<N>/head, reached through a file:// URL, with remote.origin.url set to the GitHub https form and a
url.insteadOf mapping back to the file path. Test-only."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

REAL_ROOT = Path(__file__).resolve().parents[2]
GITHUB_URL = "https://github.com/otl-labs/ontheloop"
_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_ALLOW_PROTOCOL": "file",
}


def git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, **_ENV},
    )
    if check and result.returncode != 0:
        raise AssertionError(f"git {args} failed: {result.stderr}")
    return result.stdout.strip()


def write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def commit(repo: Path, rel: str, text: str, message: str = "change") -> str:
    write(repo, rel, text)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


class Fixture:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.origin = tmp / "otl-labs" / "ontheloop.git"
        self.origin_url = f"file://{self.origin}"
        self.work = tmp / "work"
        self.origin.parent.mkdir(parents=True)
        self.work.mkdir()
        subprocess.run(["git", "init", "--bare", "-b", "main", str(self.origin)], check=True, capture_output=True)
        git(self.work, "init", "-b", "main")
        git(self.work, "remote", "add", "origin", self.origin_url)
        commit(self.work, "a.txt", "a\n", "seed")
        commit(self.work, "b.txt", "b\n", "seed b")
        git(self.work, "push", "-u", "origin", "main")
        self.set_github_identity(self.work)

    def set_github_identity(self, repo: Path, url: str = GITHUB_URL) -> None:
        git(repo, "config", "remote.origin.url", url)
        git(repo, "config", f"url.{self.origin_url}.insteadOf", url)

    def branch_pr(self, number: int, rel: str, text: str, name: str = "pr") -> str:
        """Branch off main (the current main), commit one file, push it as refs/pull/<number>/head, back to main."""
        git(self.work, "checkout", "-q", "-b", name, "main")
        sha = commit(self.work, rel, text, f"pr {number}")
        git(self.work, "push", "origin", f"HEAD:refs/pull/{number}/head")
        git(self.work, "checkout", "-q", "main")
        return sha

    def advance_main(self, rel: str, text: str) -> str:
        git(self.work, "checkout", "-q", "main")
        sha = commit(self.work, rel, text, f"main {rel}")
        git(self.work, "push", "origin", "main")
        return sha

    def update_pr(self, number: int, name: str = "pr") -> str:
        """Merge main into the PR branch and republish refs/pull/<number>/head."""
        git(self.work, "checkout", "-q", name)
        git(self.work, "merge", "-q", "--no-edit", "-X", "theirs", "main")
        git(self.work, "push", "-f", "origin", f"HEAD:refs/pull/{number}/head")
        sha = git(self.work, "rev-parse", "HEAD")
        git(self.work, "checkout", "-q", "main")
        return sha

    def clone(self, dest_name: str, *, depth: int | None = None) -> Path:
        dest = self.tmp / dest_name
        args = ["clone", "-q", "-b", "main"]
        if depth:
            args += ["--depth", str(depth)]
        git(self.tmp, *args, self.origin_url, str(dest))
        self.set_github_identity(dest)
        return dest

    def fetch_refs(self, repo: Path, number: int) -> None:
        git(repo, "fetch", "-q", "origin", "main:refs/remotes/origin/main", f"+refs/pull/{number}/head:refs/pr/{number}")


def install_project(project: Path) -> None:
    """Copy the shim, adapter, predicate and bin/venv-python into a fixture project dir."""
    for rel in (
        ".claude/hooks/merge_gate.sh",
        ".claude/hooks/merge_gate.py",
        "scripts/session/merge_overlap.py",
        "bin/venv-python",
    ):
        dest = project / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REAL_ROOT / rel, dest)
    for pkg in ("scripts", "scripts/session"):
        (project / pkg / "__init__.py").touch()
    venv_bin = project / ".venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    link = venv_bin / "python"
    if not link.exists():
        import sys

        link.symlink_to(sys.executable)


def merge_payload(number: object = 1, *, tool: str = "mcp__github__merge_pull_request", **extra: object) -> str:
    import json

    tool_input: dict[str, object] = {"owner": "otl-labs", "repo": "ontheloop", **extra}
    if number is not None:
        tool_input["pullNumber"] = number
    return json.dumps({"tool_name": tool, "tool_input": tool_input})


def run_shim(project: Path, payload: str, *, cwd: Path, extra_env: dict[str, str] | None = None, timeout: int = 90):
    env = {**os.environ, **_ENV, "CLAUDE_PROJECT_DIR": str(project), **(extra_env or {})}
    return subprocess.run(
        ["bash", str(project / ".claude/hooks/merge_gate.sh")],
        input=payload,
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )
