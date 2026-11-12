"""Replay of rec-4869's incident: a second merge that repeats a first merge's edits is refused until updated."""

from __future__ import annotations

from pathlib import Path

from scripts.session import merge_overlap as mo
from tests.fixtures import merge_overlap_repos as fx

_BUDGETS = "s1: 16955\n"


def _shrink(repo: Path, line: int) -> None:
    lines = [f"line {i}\n" for i in range(10)]
    lines[line] = "x\n"
    fx.write(repo, "AGENTS.md", "".join(lines))
    fx.write(repo, "config/prose_budgets.yaml", "s1: 16951\n")
    fx.git(repo, "add", "-A")
    fx.git(repo, "commit", "-m", "shrink")


def test_second_identical_shrink_merge_is_refused_until_updated(tmp_path: Path) -> None:
    f = fx.Fixture(tmp_path)
    fx.write(f.work, "AGENTS.md", "".join(f"line {i}\n" for i in range(10)))
    fx.write(f.work, "config/prose_budgets.yaml", _BUDGETS)
    fx.git(f.work, "add", "-A")
    fx.git(f.work, "commit", "-m", "base")
    fx.git(f.work, "push", "origin", "main")

    for name, number, line in (("a", 1, 2), ("b", 2, 7)):
        fx.git(f.work, "checkout", "-q", "-b", name, "main")
        _shrink(f.work, line)
        fx.git(f.work, "push", "origin", f"HEAD:refs/pull/{number}/head")
        fx.git(f.work, "checkout", "-q", "main")
    fx.git(f.work, "merge", "-q", "--no-edit", "a")
    fx.git(f.work, "push", "origin", "main")

    project = tmp_path / "project"
    project.mkdir()
    fx.install_project(project)
    fx.git(project, "init", "-b", "main")
    fx.git(project, "remote", "add", "origin", f.origin_url)
    f.set_github_identity(project)

    f.fetch_refs(f.work, 2)
    verdict = mo.overlap(f.work, "refs/pr/2", "refs/remotes/origin/main")
    assert verdict.kind == mo.OVERLAP and verdict.files == ("AGENTS.md", "config/prose_budgets.yaml")
    denied = fx.run_shim(project, fx.merge_payload(2), cwd=tmp_path)
    assert denied.returncode == 2 and "AGENTS.md" in denied.stderr and "config/prose_budgets.yaml" in denied.stderr

    fx.git(f.work, "checkout", "-q", "b")
    fx.git(f.work, "merge", "-q", "--no-edit", "-X", "theirs", "main")
    fx.git(f.work, "push", "-f", "origin", "HEAD:refs/pull/2/head")
    fx.git(f.work, "checkout", "-q", "main")
    f.fetch_refs(f.work, 2)
    f.set_checks(f.head_of(2), f.green_runs())
    assert mo.overlap(f.work, "refs/pr/2", "refs/remotes/origin/main").kind == mo.CLEAR
    allowed = fx.run_shim(project, fx.merge_payload(2, expectedHeadSha=f.head_of(2)), cwd=tmp_path)
    assert (allowed.returncode, allowed.stderr) == (0, "")
