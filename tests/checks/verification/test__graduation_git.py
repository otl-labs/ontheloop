"""Mirror test for scripts/checks/verification/_graduation_git.py (batched blob reader and shared worktrees)."""

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


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    (repo / "a.yaml").write_text("a: 1\n", encoding="utf-8")
    (repo / "b.yaml").write_text("b: café\n\n", encoding="utf-8")
    (repo / "empty.yaml").write_text("", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


class TestBatchedShardRead:
    def test_one_subprocess_reads_every_shard(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        paths = ["a.yaml", "b.yaml", "empty.yaml"]
        real_run = subprocess.run
        calls: list[list[str]] = []

        def spy(cmd, *a, **kw):
            calls.append(list(cmd))
            return real_run(cmd, *a, **kw)

        with mock.patch.object(gg.subprocess, "run", spy):
            got = gg.read_blobs_at_ref("HEAD", paths, repo)
        assert len(calls) == 1
        assert got == {p: _git_show(repo, p) for p in paths}

    def test_missing_header_omits_path(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        got = gg.read_blobs_at_ref("HEAD", ["a.yaml", "nope.yaml", "b.yaml"], repo)
        assert set(got) == {"a.yaml", "b.yaml"}

    def test_failed_subprocess_raises(self, tmp_path: Path) -> None:
        with pytest.raises(gg.GraduationError):
            gg.read_blobs_at_ref("HEAD", ["a.yaml"], tmp_path)

    def test_unstartable_subprocess_raises(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        with mock.patch.object(gg.subprocess, "run", side_effect=OSError("boom")):
            with pytest.raises(gg.GraduationError, match="could not start"):
                gg.read_blobs_at_ref("HEAD", ["a.yaml"], repo)

    def test_misframed_output_raises(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        bad = subprocess.CompletedProcess([], 0, stdout=b"abc blob 99\nshort\n", stderr=b"")
        with mock.patch.object(gg.subprocess, "run", return_value=bad):
            with pytest.raises(gg.GraduationError, match="misframed"):
                gg.read_blobs_at_ref("HEAD", ["a.yaml"], repo)

    def test_empty_paths_spawns_nothing(self, tmp_path: Path) -> None:
        assert gg.read_blobs_at_ref("HEAD", [], tmp_path) == {}


def _git_show(repo: Path, path: str) -> str:
    return subprocess.run(
        ["git", "show", f"HEAD:{path}"], cwd=str(repo), capture_output=True, text=True, encoding="utf-8"
    ).stdout


class TestSharedWorktrees:
    def test_reuse_per_ref(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        with gg.shared_worktrees(repo) as wt:
            first = wt.get("HEAD")
            second = wt.get("HEAD")
            assert first == second
            assert _git(repo, "worktree", "list").count("\n") == 1
        assert not first.exists()

    def test_unresolvable_ref_raises_and_adds_nothing(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        with gg.shared_worktrees(repo) as wt:
            with pytest.raises(gg.GraduationError):
                wt.get("refs/heads/nope")
            assert "\n" not in _git(repo, "worktree", "list")

    def test_restore_cleans_untracked_nested_repo_and_modified(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        with gg.shared_worktrees(repo) as wt:
            path = wt.get("HEAD")
            (path / "junk.txt").write_text("x", encoding="utf-8")
            nested = path / "nested"
            nested.mkdir()
            _git(nested, "init", "-q")
            (path / "a.yaml").write_text("changed\n", encoding="utf-8")
            again = wt.get("HEAD")
            assert not (again / "junk.txt").exists()
            assert not (again / "nested").exists()
            assert (again / "a.yaml").read_text(encoding="utf-8") == "a: 1\n"

    def test_restore_returns_head_to_recorded_sha(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        sha = _git(repo, "rev-parse", "HEAD")
        with gg.shared_worktrees(repo) as wt:
            path = wt.get("HEAD")
            (path / "new.txt").write_text("n", encoding="utf-8")
            _git(path, "add", "-A")
            _git(path, "-c", "user.email=t@example.com", "-c", "user.name=T", "commit", "-q", "-m", "moved")
            assert _git(path, "rev-parse", "HEAD") != sha
            wt.get("HEAD")
            assert _git(path, "rev-parse", "HEAD") == sha

    def test_restore_failure_raises(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        with gg.shared_worktrees(repo) as wt:
            wt.get("HEAD")
            real = gg.run_git

            def failing(args, cwd):
                if args[:1] == ["checkout"]:
                    return subprocess.CompletedProcess(args, 1, stdout="", stderr="no")
                return real(args, cwd)

            with mock.patch.object(gg, "run_git", failing):
                with pytest.raises(gg.GraduationError, match="restore"):
                    wt.get("HEAD")

    def test_restore_leftovers_raise(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        with gg.shared_worktrees(repo) as wt:
            wt.get("HEAD")
            real = gg.run_git

            def dirty(args, cwd):
                if args[:1] == ["status"]:
                    return subprocess.CompletedProcess(args, 0, stdout="?? junk.txt\n", stderr="")
                return real(args, cwd)

            with mock.patch.object(gg, "run_git", dirty):
                with pytest.raises(gg.GraduationError, match="not clean"):
                    wt.get("HEAD")

    def test_venv_symlink_provisioned_and_unlinked_before_remove(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        venv = repo / ".venv"
        venv.mkdir()
        (venv / "canary.txt").write_text("keep\n", encoding="utf-8")
        with gg.shared_worktrees(repo) as wt:
            path = wt.get("HEAD")
            assert (path / ".venv").is_symlink()
            again = wt.get("HEAD")
            assert (again / ".venv").is_symlink()
        assert (venv / "canary.txt").read_text(encoding="utf-8") == "keep\n"
        assert not path.exists()


class TestEdgeBranches:
    def test_git_worktree_adds_and_removes(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        with gg.git_worktree("HEAD", repo_root=repo) as wt:
            assert (wt / "a.yaml").exists()
        assert not wt.exists()

    def test_git_worktree_bad_ref_raises(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        with pytest.raises(gg.GraduationError, match="worktree add failed"):
            with gg.git_worktree("refs/heads/nope", repo_root=repo):
                pass  # pragma: no cover

    def test_remove_failure_falls_back_to_prune(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        real = gg.run_git
        calls: list[list[str]] = []

        def fake(args, cwd):
            calls.append(args)
            if args[:2] == ["worktree", "remove"]:
                return subprocess.CompletedProcess(args, 1, stdout="", stderr="no")
            return real(args, cwd)

        with mock.patch.object(gg, "run_git", fake):
            with gg.git_worktree("HEAD", repo_root=repo):
                pass
        assert ["worktree", "prune"] in calls

    def test_header_without_newline_raises(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        bad = subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")
        with mock.patch.object(gg.subprocess, "run", return_value=bad):
            with pytest.raises(gg.GraduationError, match="misframed before"):
                gg.read_blobs_at_ref("HEAD", ["a.yaml"], repo)

    def test_unexpected_header_raises(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        bad = subprocess.CompletedProcess([], 0, stdout=b"abc tree 5\nxxxxx\n", stderr=b"")
        with mock.patch.object(gg.subprocess, "run", return_value=bad):
            with pytest.raises(gg.GraduationError, match="unexpected header"):
                gg.read_blobs_at_ref("HEAD", ["a.yaml"], repo)

    def test_non_utf8_blob_raises(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        bad = subprocess.CompletedProcess([], 0, stdout=b"abc blob 2\n\xff\xfe\n", stderr=b"")
        with mock.patch.object(gg.subprocess, "run", return_value=bad):
            with pytest.raises(gg.GraduationError, match="not UTF-8"):
                gg.read_blobs_at_ref("HEAD", ["a.yaml"], repo)

    def test_restore_clean_failure_and_head_mismatch_raise(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        real = gg.run_git
        for failing, match in ((["clean"], "clean failed"), (["rev-parse", "HEAD"], "recorded base")):
            with gg.shared_worktrees(repo) as wt:
                wt.get("HEAD")

                def fake(args, cwd, failing=failing):
                    if args[: len(failing)] == failing and cwd != repo:
                        return subprocess.CompletedProcess(args, 0 if failing[0] == "rev-parse" else 1, "deadbeef\n", "no")
                    return real(args, cwd)

                with mock.patch.object(gg, "run_git", fake):
                    with pytest.raises(gg.GraduationError, match=match):
                        wt.get("HEAD")
