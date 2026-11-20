"""Mirror test for scripts/checks/verification/_graduation_git.py (batched blob reader, shared worktrees)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

import pytest

from scripts.checks.verification import _graduation_git as gg


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "T")
    (r / "a.txt").write_text("alpha\n", encoding="utf-8")
    (r / "b.txt").write_text("café ☃\nline2\n", encoding="utf-8")
    (r / "empty.txt").write_text("", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "base")
    return r


class TestBatchedShardRead:
    def test_one_subprocess_reads_every_shard(self, repo: Path) -> None:
        paths = ["a.txt", "b.txt", "empty.txt"]
        real_run = subprocess.run
        calls: list[list[str]] = []

        def spy(cmd, *args, **kwargs):
            calls.append(list(cmd))
            return real_run(cmd, *args, **kwargs)

        with mock.patch.object(gg.subprocess, "run", spy):
            blobs = gg.read_blobs_at_ref("HEAD", paths, repo)
        expected = {p: _git_show(repo, p) for p in paths}
        assert blobs == expected
        assert len(calls) == 1 and calls[0][:3] == ["git", "cat-file", "--batch"]

    def test_missing_header_omits_path(self, repo: Path) -> None:
        blobs = gg.read_blobs_at_ref("HEAD", ["a.txt", "nope.txt", "b.txt"], repo)
        assert sorted(blobs) == ["a.txt", "b.txt"]

    def test_failed_subprocess_raises(self, repo: Path) -> None:
        failed = subprocess.CompletedProcess(args=[], returncode=128, stdout=b"", stderr=b"boom")
        with mock.patch.object(gg.subprocess, "run", return_value=failed):
            with pytest.raises(gg.GraduationError, match="exited 128"):
                gg.read_blobs_at_ref("HEAD", ["a.txt"], repo)

    def test_unstartable_subprocess_raises(self, repo: Path) -> None:
        with mock.patch.object(gg.subprocess, "run", side_effect=OSError("no git")):
            with pytest.raises(gg.GraduationError, match="could not start"):
                gg.read_blobs_at_ref("HEAD", ["a.txt"], repo)

    def test_misframed_output_raises(self, repo: Path) -> None:
        bad = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"abc blob 99\nshort\n", stderr=b"")
        with mock.patch.object(gg.subprocess, "run", return_value=bad):
            with pytest.raises(gg.GraduationError, match="misframed"):
                gg.read_blobs_at_ref("HEAD", ["a.txt"], repo)

    def test_no_paths_spawns_nothing(self, repo: Path) -> None:
        with mock.patch.object(gg.subprocess, "run", side_effect=AssertionError("spawned")):
            assert gg.read_blobs_at_ref("HEAD", [], repo) == {}


def _git_show(repo: Path, path: str) -> str:
    return subprocess.run(
        ["git", "show", f"HEAD:{path}"], cwd=str(repo), capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout


class TestSharedWorktrees:
    def test_one_worktree_per_ref(self, repo: Path) -> None:
        with gg.shared_worktrees(repo) as wt:
            first = wt.get("HEAD")
            assert wt.get("HEAD") == first
            assert first.is_dir()
            assert _git(repo, "worktree", "list").count("verif-graduation-") == 1
        assert not first.exists()
        assert "verif-graduation-" not in _git(repo, "worktree", "list")

    def test_unresolvable_ref_raises_and_adds_nothing(self, repo: Path) -> None:
        with gg.shared_worktrees(repo) as wt:
            with pytest.raises(gg.GraduationError, match="does not resolve"):
                wt.get("refs/heads/nope")
            assert "verif-graduation-" not in _git(repo, "worktree", "list")

    def test_restore_removes_untracked_nested_repo_and_modifications(self, repo: Path) -> None:
        with gg.shared_worktrees(repo) as wt:
            path = wt.get("HEAD")
            (path / "junk.txt").write_text("x", encoding="utf-8")
            nested = path / "nested"
            nested.mkdir()
            _git(nested, "init", "-q")
            (nested / "f").write_text("y", encoding="utf-8")
            (path / "a.txt").write_text("changed\n", encoding="utf-8")
            assert wt.get("HEAD") == path
            assert not (path / "junk.txt").exists() and not nested.exists()
            assert (path / "a.txt").read_text(encoding="utf-8") == "alpha\n"

    def test_restore_moves_head_back_to_recorded_sha(self, repo: Path) -> None:
        sha = _git(repo, "rev-parse", "HEAD")
        with gg.shared_worktrees(repo) as wt:
            path = wt.get("HEAD")
            _git(path, "config", "user.email", "t@example.com")
            _git(path, "config", "user.name", "T")
            (path / "c.txt").write_text("c", encoding="utf-8")
            _git(path, "add", "-A")
            _git(path, "commit", "-q", "-m", "inside")
            assert _git(path, "rev-parse", "HEAD") != sha
            wt.get("HEAD")
            assert _git(path, "rev-parse", "HEAD") == sha

    def test_restore_that_cannot_reach_sha_raises(self, repo: Path) -> None:
        with gg.shared_worktrees(repo) as wt:
            wt.get("HEAD")
            real = gg.run_git

            def fake(args, cwd):
                if args[:2] == ["checkout", "-f"]:
                    return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="no")
                return real(args, cwd)

            with mock.patch.object(gg, "run_git", fake):
                with pytest.raises(gg.GraduationError, match="restore"):
                    wt.get("HEAD")

    def test_restore_leaving_residue_raises(self, repo: Path) -> None:
        with gg.shared_worktrees(repo) as wt:
            path = wt.get("HEAD")
            (path / "junk.txt").write_text("x", encoding="utf-8")
            real = gg.run_git

            def fake(args, cwd):
                if args[:1] == ["clean"]:
                    return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
                return real(args, cwd)

            with mock.patch.object(gg, "run_git", fake):
                with pytest.raises(gg.GraduationError, match="residue"):
                    wt.get("HEAD")

    def test_venv_symlink_provisioned_and_unlinked_before_remove(self, repo: Path) -> None:
        (repo / ".venv").mkdir()
        (repo / ".venv" / "marker").write_text("m", encoding="utf-8")
        with gg.shared_worktrees(repo) as wt:
            path = wt.get("HEAD")
            assert (path / ".venv").is_symlink()
            wt.get("HEAD")
            assert (path / ".venv").is_symlink()
        assert (repo / ".venv" / "marker").read_text(encoding="utf-8") == "m"
        assert not path.exists()


class TestGitWorktree:
    def test_adds_and_removes_with_venv_symlink(self, repo: Path) -> None:
        (repo / ".venv").mkdir()
        with gg.git_worktree("HEAD", repo_root=repo) as path:
            assert (path / "a.txt").is_file() and (path / ".venv").is_symlink()
        assert not path.exists() and (repo / ".venv").is_dir()

    def test_bad_ref_raises(self, repo: Path) -> None:
        with pytest.raises(gg.GraduationError, match="worktree add failed"):
            with gg.git_worktree("refs/heads/nope", repo_root=repo):
                pass  # pragma: no cover

    def test_prune_fallback_on_remove_failure(self, repo: Path) -> None:
        real = gg.run_git
        calls: list[list[str]] = []

        def fake(args, cwd):
            calls.append(args)
            if args[:2] == ["worktree", "remove"]:
                return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="no")
            return real(args, cwd)

        with mock.patch.object(gg, "run_git", fake):
            with gg.git_worktree("HEAD", repo_root=repo):
                pass
        assert any(a[:2] == ["worktree", "prune"] for a in calls)


class TestReaderEdges:
    def _read(self, repo: Path, stdout: bytes) -> dict[str, str]:
        proc = subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr=b"")
        with mock.patch.object(gg.subprocess, "run", return_value=proc):
            return gg.read_blobs_at_ref("HEAD", ["a.txt"], repo)

    def test_missing_newline_before_header(self, repo: Path) -> None:
        with pytest.raises(gg.GraduationError, match="misframed before"):
            self._read(repo, b"")

    def test_unexpected_header(self, repo: Path) -> None:
        with pytest.raises(gg.GraduationError, match="unexpected header"):
            self._read(repo, b"abc tree 3\nxyz\n")

    def test_non_utf8_blob(self, repo: Path) -> None:
        with pytest.raises(gg.GraduationError, match="not UTF-8"):
            self._read(repo, b"abc blob 2\n\xff\xfe\n")

    def test_trailing_output(self, repo: Path) -> None:
        with pytest.raises(gg.GraduationError, match="trailing"):
            self._read(repo, b"abc blob 1\nx\nextra")


def test_shared_worktree_add_failure_raises(repo: Path) -> None:
    real = gg.run_git

    def fake(args, cwd):
        if args[:2] == ["worktree", "add"]:
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="no add")
        return real(args, cwd)

    with gg.shared_worktrees(repo) as wt, mock.patch.object(gg, "run_git", fake):
        with pytest.raises(gg.GraduationError, match="worktree add failed"):
            wt.get("HEAD")


def test_restore_with_head_elsewhere_raises(repo: Path) -> None:
    real = gg.run_git

    def fake(args, cwd):
        if args[:2] == ["rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="deadbeef\n", stderr="")
        return real(args, cwd)

    with gg.shared_worktrees(repo) as wt:
        wt.get("HEAD")
        with mock.patch.object(gg, "run_git", fake):
            with pytest.raises(gg.GraduationError, match="left HEAD"):
                wt.get("HEAD")
