"""Shared fixture builders for the merge-step tests: a git repo with a local bare origin, commit and
branch helpers, a plan writer, a sidecar writer and a copy of bin/venv-python. Test-only."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

REAL_ROOT = Path(__file__).resolve().parents[2]


def git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com"}
    env |= {"GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    result = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8", env=env
    )
    if result.returncode != 0:
        raise AssertionError(f"git {args} failed: {result.stderr}")
    return result.stdout.strip()


def make_repo(tmp_path: Path) -> Path:
    """A working repo on `main` with one commit, pushed to a local bare origin."""
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    origin.mkdir()
    work.mkdir()
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)], check=True, capture_output=True)
    git(work, "init", "-b", "main")
    git(work, "remote", "add", "origin", str(origin))
    write(work, "README.md", "seed\n")
    git(work, "add", "-A")
    git(work, "commit", "-m", "seed")
    git(work, "push", "-u", "origin", "main")
    return work


def write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def commit_file(repo: Path, rel: str, text: str, message: str = "change") -> str:
    write(repo, rel, text)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def push_main(repo: Path) -> None:
    git(repo, "push", "origin", "HEAD:main")


def branch(repo: Path, name: str = "feature") -> None:
    git(repo, "checkout", "-b", name)


def plan_rel(slug: str) -> str:
    return f"docs/plans/PLAN-{slug}.yaml"


def plan_text(slug: str, bundled: list[str], declared: bool) -> str:
    return yaml.safe_dump(
        {"slug": slug, "bundled_recommendations": bundled, "implementation_declared": declared}, sort_keys=False
    )


def write_plan(repo: Path, slug: str, bundled: list[str], declared: bool) -> None:
    write(repo, plan_rel(slug), plan_text(slug, bundled, declared))


def sidecar_text(slug: str, blocks: list[dict[str, str]]) -> str:
    return yaml.safe_dump({"schema_version": 1, "slug": slug, "blocks": blocks}, sort_keys=False)


def write_sidecar(repo: Path, slug: str, blocks: list[dict[str, str]]) -> None:
    write(repo, f"docs/plans/approved/{slug}.yaml", sidecar_text(slug, blocks))


def clear_ci_env(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    monkeypatch.delenv("GITHUB_EVENT_BEFORE", raising=False)


def install_venv_python(repo: Path) -> None:
    """Copy bin/venv-python into the repo and point `.venv/bin/python` at this interpreter."""
    bin_dir = repo / "bin"
    bin_dir.mkdir(exist_ok=True)
    target = bin_dir / "venv-python"
    shutil.copy2(REAL_ROOT / "bin" / "venv-python", target)
    target.chmod(0o755)
    write(repo, ".gitignore", ".venv/\n")
    venv_bin = repo / ".venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    link = venv_bin / "python"
    if not link.exists():
        link.symlink_to(sys.executable)


def make_pr_branch(
    tmp_path: Path,
    monkeypatch,  # noqa: ANN001
    slug: str,
    bundled: list[str],
    *,
    declared: bool = True,
    message: str | None = None,
) -> Path:
    """Main carries the plan undeclared; a `feature` branch flips it (when `declared`) in one commit whose
    message is `message` (default: a Resolves trailer naming `bundled`)."""
    clear_ci_env(monkeypatch)
    repo = make_repo(tmp_path)
    write_plan(repo, slug, bundled, False)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "plan")
    push_main(repo)
    branch(repo)
    write_plan(repo, slug, bundled, declared)
    trailer = f"feat\n\nResolves: {', '.join(bundled)}\n" if bundled else "feat\n"
    commit_file(repo, f"src_{slug}.txt", "x\n", message if message is not None else trailer)
    return repo


def message_file(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "message.txt"
    path.write_text(text, encoding="utf-8")
    return path


def head_message(repo: Path) -> str:
    return git(repo, "log", "-1", "--format=%B")
