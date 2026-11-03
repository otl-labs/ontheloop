"""Hermetic: the git channel over tmp git repos (explicit identity, signing off, writes only under tmp_path)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from scripts.recs_related import history as hist
from scripts.recs_related.envelope import CHANNEL_OK

pytestmark = pytest.mark.unit

_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main", *args],
        cwd=cwd,
        env=_ENV,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return done.stdout


def _commit(
    repo: Path, message: str, writes: dict[str, str | None] | None = None, renames: dict[str, str] | None = None
) -> None:
    for old, new in (renames or {}).items():
        (repo / new).parent.mkdir(parents=True, exist_ok=True)
        _git(repo, "mv", old, new)
    for rel, text in (writes or {}).items():
        target = repo / rel
        if text is None:
            _git(repo, "rm", "-q", rel)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        _git(repo, "add", rel)
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)


def _tracked(repo: Path) -> frozenset[str]:
    return frozenset(_git(repo, "ls-files").split())


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q")
    return path


class TestHistory:
    def test_coupling_counts_and_confidence(self, repo: Path):
        _commit(repo, "c1", {"a.txt": "1", "b.txt": "1"})
        _commit(repo, "c2", {"a.txt": "2", "b.txt": "2", "c.txt": "2"})
        _commit(repo, "c3", {"a.txt": "3", "d.txt": "3"})
        _commit(repo, "c4", {"b.txt": "4"})
        got = hist.load_history(repo).for_file("a.txt", _tracked(repo))
        assert got.status == CHANNEL_OK
        by_path = {c.path: c for c in got.coupling}
        assert by_path["b.txt"].shared == 2
        assert by_path["b.txt"].confidence == pytest.approx(2 / 3)
        assert by_path["c.txt"].shared == 1
        assert by_path["c.txt"].confidence == pytest.approx(1 / 3)
        assert by_path["d.txt"].shared == 1
        assert [c.path for c in got.coupling] == ["b.txt", "c.txt", "d.txt"]
        assert "a.txt" not in by_path

    def test_commits_touching_more_than_30_files_are_ignored(self, repo: Path):
        _commit(repo, "small", {"a.txt": "1", "small.txt": "1"})
        _commit(repo, "exactly 30", {"a.txt": "2", **{f"edge{i}.txt": "x" for i in range(29)}})
        _commit(repo, "bulk", {"a.txt": "3", **{f"bulk{i}.txt": "x" for i in range(30)}})
        got = hist.load_history(repo).for_file("a.txt", _tracked(repo))
        paths = {c.path for c in got.coupling}
        assert "small.txt" in paths and "edge0.txt" in paths and "bulk0.txt" not in paths
        assert all(c.confidence == pytest.approx(0.5) for c in got.coupling)

    def test_only_first_parent_commits_count(self, repo: Path):
        _commit(repo, "base", {"a.txt": "1"})
        _git(repo, "checkout", "-q", "-b", "side")
        _commit(repo, "side1", {"a.txt": "side", "temp.txt": "1"})
        _commit(repo, "side2", {"temp.txt": None, "kept.txt": "1"})
        _git(repo, "checkout", "-q", "main")
        _commit(repo, "main work", {"main_partner.txt": "1"})
        _git(repo, "merge", "-q", "--no-ff", "-m", "merge side", "side")
        paths = {c.path for c in hist.load_history(repo).for_file("a.txt", _tracked(repo)).coupling}
        assert "kept.txt" in paths  # the merge commit carries the side branch's net change
        assert "temp.txt" not in paths  # a side-branch intermediate commit is not first-parent history

    def test_rename_chain_yields_earlier_names(self, repo: Path):
        body = "x" * 400
        _commit(repo, "add", {"first.txt": body})
        _commit(repo, "rename 1", renames={"first.txt": "second.txt"})
        _commit(repo, "rename 2", renames={"second.txt": "dir/third.txt"})
        got = hist.load_history(repo).for_file("dir/third.txt", _tracked(repo))
        assert got.aliases == ["second.txt", "first.txt"]

    def test_pre_split_module_path_is_an_alias_of_a_file_in_the_replacing_package(self, repo: Path):
        _commit(repo, "monolith", {"pkg/widget.py": "x = 1\n" * 50, "other.py": "y\n"})
        _commit(repo, "split", {"pkg/widget.py": None, "pkg/widget/__init__.py": "", "pkg/widget/core.py": "x = 1\n" * 50})
        index = hist.load_history(repo)
        tracked = _tracked(repo)
        assert "pkg/widget.py" in index.for_file("pkg/widget/core.py", tracked).aliases
        assert "pkg/widget.py" in index.for_file("pkg/widget/__init__.py", tracked).aliases
        assert index.for_file("other.py", tracked).aliases == []

    def test_a_same_named_module_that_still_exists_is_not_an_alias(self, repo: Path):
        _commit(repo, "both", {"pkg/widget.py": "a\n", "pkg/widget/core.py": "b\n"})
        assert hist.load_history(repo).for_file("pkg/widget/core.py", _tracked(repo)).aliases == []

    def test_untracked_query_file_reports_unavailable(self, repo: Path):
        _commit(repo, "c1", {"a.txt": "1"})
        got = hist.load_history(repo).for_file("not_yet.txt", _tracked(repo))
        assert got.status.startswith("unavailable: ") and got.coupling == [] and got.aliases == []

    def test_shallow_clone_marks_the_channel_unavailable_never_zero(self, repo: Path, tmp_path: Path):
        _commit(repo, "c1", {"a.txt": "1", "b.txt": "1"})
        _commit(repo, "c2", {"a.txt": "2", "b.txt": "2"})
        clone = tmp_path / "clone"
        _git(tmp_path, "clone", "-q", "--depth", "1", f"file://{repo}", str(clone))
        got = hist.load_history(clone).for_file("a.txt", _tracked(clone))
        assert got.status.startswith("unavailable: shallow clone") and got.coupling == []

    def test_directory_that_is_not_a_repo_marks_the_channel_unavailable(self, tmp_path: Path):
        index = hist.load_history(tmp_path)
        assert index.status.startswith("unavailable: git failed")

    def test_repo_without_commits_marks_the_channel_unavailable(self, repo: Path):
        index = hist.load_history(repo)
        assert index.status.startswith("unavailable: git log failed")

    def test_git_that_cannot_run_marks_the_channel_unavailable(self, repo: Path, monkeypatch: pytest.MonkeyPatch):
        def boom(*_a, **_k):
            raise FileNotFoundError("git")

        monkeypatch.setattr(hist.subprocess, "run", boom)
        assert hist.load_history(repo).status.startswith("unavailable: git could not run")

    def test_log_step_failure_after_a_good_shallow_probe_is_reported(self, repo: Path, monkeypatch: pytest.MonkeyPatch):
        _commit(repo, "c1", {"a.txt": "1"})
        real = hist.subprocess.run
        calls: list[int] = []

        def flaky(cmd, **kw):
            calls.append(1)
            if "log" in cmd:
                raise subprocess.TimeoutExpired(cmd, 1)
            return real(cmd, **kw)

        monkeypatch.setattr(hist.subprocess, "run", flaky)
        assert hist.load_history(repo).status.startswith("unavailable: git could not run")
        assert len(calls) == 2

    def test_history_window_is_bounded(self, repo: Path, monkeypatch: pytest.MonkeyPatch):
        for i in range(3):
            _commit(repo, f"c{i}", {"a.txt": str(i), f"p{i}.txt": "1"})
        monkeypatch.setattr(hist, "HISTORY_COMMITS", 2)
        paths = {c.path for c in hist.load_history(repo).for_file("a.txt", _tracked(repo)).coupling}
        assert paths == {"p1.txt", "p2.txt"}

    def test_lines_before_the_first_commit_marker_are_ignored(self):
        index = hist._parse("stray\tline\n@@abc\n\nM\tx.txt\n")
        assert [set(c) for c in index.commits] == [{"x.txt"}]
