"""Relocated TestPushContextBase from the retired tests/test_checks_registry.py monolith
(Decision 169, amends Decision 104).

push_context_base() (Decision 104/148 sole-home): returns a base ONLY in push context
(GITHUB_EVENT_NAME=="push", OR current branch=="main" AND merge-base(origin/main,HEAD)==HEAD),
None otherwise -- and the PR-context invariance of get_changed_files()/get_status_aware_diff()
(Decision 135 pt 3: contract unchanged for existing callers outside push context).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import scripts.checks._common as _common


class TestPushContextBase:
    def _git(self, repo: Path, args: list[str]) -> str:
        result = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, encoding="utf-8")
        assert result.returncode == 0, f"git {args} failed: {result.stderr}"
        return result.stdout.strip()

    def _commit(self, repo: Path, name: str, message: str) -> str:
        (repo / name).write_text(name, encoding="utf-8")
        self._git(repo, ["add", "-A"])
        self._git(repo, ["commit", "-q", "-m", message])
        return self._git(repo, ["rev-parse", "HEAD"])

    def _init_repo(self, repo: Path, branch: str) -> None:
        repo.mkdir(parents=True, exist_ok=True)
        self._git(repo, ["init", "-q", "-b", branch])
        self._git(repo, ["config", "user.email", "test@example.com"])
        self._git(repo, ["config", "user.name", "Test"])

    def test_push_event_env_var_returns_base_regardless_of_branch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC1/AC3: GITHUB_EVENT_NAME=push activates push context on ANY branch name."""
        repo = tmp_path / "repo"
        self._init_repo(repo, "feature/not-main")
        first = self._commit(repo, "a.txt", "first")
        self._commit(repo, "b.txt", "second")

        monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
        with patch("scripts.checks._common.ROOT", repo):
            base = _common.push_context_base()
        assert base == first

    def test_on_main_merge_base_equals_head_returns_head_tilde_1(self, tmp_path: Path) -> None:
        """AC1: on main with origin/main == HEAD (post-merge), returns HEAD~1."""
        repo = tmp_path / "repo"
        self._init_repo(repo, "main")
        first = self._commit(repo, "a.txt", "first")
        second = self._commit(repo, "b.txt", "second")
        self._git(repo, ["update-ref", "refs/remotes/origin/main", second])

        with patch("scripts.checks._common.ROOT", repo):
            base = _common.push_context_base()
        assert base == first

    def test_session_branch_false_positive_regression(self, tmp_path: Path) -> None:
        """AC2 (the false positive this design exists to avoid): a fresh session branch (NOT
        named main) with zero commits of its own has merge-base(origin/main, HEAD) == HEAD too --
        without the branch-name conjunct this would wrongly match push context."""
        repo = tmp_path / "repo"
        self._init_repo(repo, "main")
        head = self._commit(repo, "a.txt", "first")
        self._git(repo, ["update-ref", "refs/remotes/origin/main", head])
        self._git(repo, ["checkout", "-q", "-b", "claude/some-session-branch"])

        with patch("scripts.checks._common.ROOT", repo):
            base = _common.push_context_base()
        assert base is None

    def test_pr_context_diverged_branch_returns_none(self, tmp_path: Path) -> None:
        """A normal feature branch with its own commits (merge-base != HEAD) is PR context."""
        repo = tmp_path / "repo"
        self._init_repo(repo, "main")
        first = self._commit(repo, "a.txt", "first")
        self._git(repo, ["update-ref", "refs/remotes/origin/main", first])
        self._git(repo, ["checkout", "-q", "-b", "feature/x"])
        self._commit(repo, "b.txt", "own work")

        with patch("scripts.checks._common.ROOT", repo):
            base = _common.push_context_base()
        assert base is None

    def test_github_event_before_preferred_over_head_tilde_1(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = tmp_path / "repo"
        self._init_repo(repo, "main")
        first = self._commit(repo, "a.txt", "first")
        self._commit(repo, "b.txt", "second")
        self._commit(repo, "c.txt", "third")

        monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
        monkeypatch.setenv("GITHUB_EVENT_BEFORE", first)
        with patch("scripts.checks._common.ROOT", repo):
            base = _common.push_context_base()
        assert base == first

    def test_github_event_before_zero_sha_falls_back_to_head_tilde_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A force-push/new-branch push sets GITHUB_EVENT_BEFORE to the all-zero SHA."""
        repo = tmp_path / "repo"
        self._init_repo(repo, "main")
        first = self._commit(repo, "a.txt", "first")
        self._commit(repo, "b.txt", "second")

        monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
        monkeypatch.setenv("GITHUB_EVENT_BEFORE", "0" * 40)
        with patch("scripts.checks._common.ROOT", repo):
            base = _common.push_context_base()
        assert base == first

    def test_head_tilde_1_unresolvable_returns_none_with_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """A root commit (single commit, shallow-equivalent): HEAD~1 does not resolve."""
        repo = tmp_path / "repo"
        self._init_repo(repo, "main")
        self._commit(repo, "a.txt", "only commit")

        monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
        with patch("scripts.checks._common.ROOT", repo):
            base = _common.push_context_base()
        assert base is None
        assert "WARNING" in capsys.readouterr().err

    def test_pr_context_invariance_get_changed_files_diffs_origin_main_directly(self, tmp_path: Path) -> None:
        """Decision 135 pt 3: outside push context, get_changed_files() still diffs origin/main
        DIRECTLY (never merge-base) -- unchanged for callers, including diverged branches."""
        with (
            patch("scripts.checks._common.push_context_base", return_value=None),
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks._common.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            _common.get_changed_files()
        called = mock_run.call_args_list[0].args[0]
        assert called == ["git", "diff", "--name-only", "origin/main"]

    def test_pr_context_invariance_get_status_aware_diff_uses_merge_base(self, tmp_path: Path) -> None:
        """Outside push context, get_status_aware_diff() keeps its own merge-base-or-HEAD base."""
        with (
            patch("scripts.checks._common.push_context_base", return_value=None),
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks._common.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            _common.get_status_aware_diff()
        first_call = mock_run.call_args_list[0].args[0]
        assert first_call == ["git", "merge-base", "origin/main", "HEAD"]

    def test_push_context_get_changed_files_uses_push_base(self, tmp_path: Path) -> None:
        """In push context, get_changed_files() diffs against the push base instead."""
        with (
            patch("scripts.checks._common.push_context_base", return_value="deadbeef"),
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks._common.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            _common.get_changed_files()
        called = mock_run.call_args_list[0].args[0]
        assert called == ["git", "diff", "--name-only", "deadbeef"]

    def test_push_context_get_status_aware_diff_uses_push_base(self, tmp_path: Path) -> None:
        with (
            patch("scripts.checks._common.push_context_base", return_value="deadbeef"),
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks._common.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            _common.get_status_aware_diff()
        diff_call = next(c for c in mock_run.call_args_list if c.args[0][:2] == ["git", "diff"])
        assert diff_call.args[0] == ["git", "diff", "--name-status", "--no-renames", "deadbeef"]

    def _push_repo(self, tmp_path: Path) -> tuple[Path, list[str]]:
        repo = tmp_path / "repo"
        self._init_repo(repo, "feature/not-main")
        shas = [self._commit(repo, f"f{i}.txt", f"c{i}") for i in range(4)]
        return repo, shas

    def test_full_tier_prev_ancestor_is_returned(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        repo, shas = self._push_repo(tmp_path)
        monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
        monkeypatch.setenv("FULL_TIER_PREV", shas[0])
        with patch("scripts.checks._common.ROOT", repo):
            assert _common.push_context_base() == shas[0]

    def test_full_tier_prev_falls_to_last_green(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        repo, shas = self._push_repo(tmp_path)
        monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
        monkeypatch.setenv("FULL_TIER_PREV", "f" * 40)
        monkeypatch.setenv("FULL_TIER_LAST_GREEN", shas[1])
        with patch("scripts.checks._common.ROOT", repo):
            assert _common.push_context_base() == shas[1]

    def test_neither_qualifying_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        repo, shas = self._push_repo(tmp_path)
        monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
        monkeypatch.setenv("FULL_TIER_PREV", "f" * 40)
        monkeypatch.setenv("FULL_TIER_LAST_GREEN", "")
        with patch("scripts.checks._common.ROOT", repo), pytest.raises(RuntimeError, match="FULL_TIER_PREV"):
            _common.push_context_base()

    def test_full_tier_prev_equal_to_head_is_not_strict_ancestor(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo, shas = self._push_repo(tmp_path)
        monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
        monkeypatch.setenv("FULL_TIER_PREV", shas[-1])
        with patch("scripts.checks._common.ROOT", repo), pytest.raises(RuntimeError):
            _common.push_context_base()

    def test_full_tier_prev_outside_push_context_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        repo, shas = self._push_repo(tmp_path)
        monkeypatch.setenv("FULL_TIER_PREV", shas[0])
        with patch("scripts.checks._common.ROOT", repo):
            assert _common.push_context_base() is None

    def test_unset_or_empty_prev_keeps_today_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        repo, shas = self._push_repo(tmp_path)
        monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
        monkeypatch.setenv("FULL_TIER_PREV", "")
        with patch("scripts.checks._common.ROOT", repo):
            assert _common.push_context_base() == shas[-2]
        monkeypatch.setenv("GITHUB_EVENT_BEFORE", shas[0])
        monkeypatch.delenv("FULL_TIER_PREV")
        with patch("scripts.checks._common.ROOT", repo):
            assert _common.push_context_base() == shas[0]


class TestRootScopedProbes:
    """rec-3166: push_context_base(root=...) must resolve the INJECTED root's git state, never
    the real repository's -- for BOTH push-context triggers (GITHUB_EVENT_NAME=push, and the
    on-main branch + merge-base==HEAD git-state path). `_common.ROOT` is patched to a DECOY repo
    in every test here to prove the probe never falls back to reading it.
    """

    def _git(self, repo: Path, args: list[str]) -> str:
        result = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, encoding="utf-8")
        assert result.returncode == 0, f"git {args} failed: {result.stderr}"
        return result.stdout.strip()

    def _commit(self, repo: Path, name: str, message: str) -> str:
        (repo / name).write_text(name, encoding="utf-8")
        self._git(repo, ["add", "-A"])
        self._git(repo, ["commit", "-q", "-m", message])
        return self._git(repo, ["rev-parse", "HEAD"])

    def _init_repo(self, repo: Path, branch: str) -> None:
        repo.mkdir(parents=True, exist_ok=True)
        self._git(repo, ["init", "-q", "-b", branch])
        self._git(repo, ["config", "user.email", "test@example.com"])
        self._git(repo, ["config", "user.name", "Test"])

    def test_push_event_root_scopes_to_injected_repo(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        decoy = tmp_path / "decoy"
        self._init_repo(decoy, "main")
        self._commit(decoy, "decoy-only.txt", "decoy first")
        self._commit(decoy, "decoy-second.txt", "decoy second")

        fixture = tmp_path / "fixture"
        self._init_repo(fixture, "feature/not-main")
        fixture_first = self._commit(fixture, "a.txt", "first")
        self._commit(fixture, "b.txt", "second")

        monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
        with patch("scripts.checks._common.ROOT", decoy):
            base = _common.push_context_base(root=fixture)
        assert base == fixture_first

    def test_on_main_root_scopes_to_injected_repo(self, tmp_path: Path) -> None:
        decoy = tmp_path / "decoy"
        self._init_repo(decoy, "main")
        decoy_head = self._commit(decoy, "decoy.txt", "decoy only commit")
        self._git(decoy, ["update-ref", "refs/remotes/origin/main", decoy_head])

        fixture = tmp_path / "fixture"
        self._init_repo(fixture, "main")
        fixture_first = self._commit(fixture, "a.txt", "first")
        fixture_second = self._commit(fixture, "b.txt", "second")
        self._git(fixture, ["update-ref", "refs/remotes/origin/main", fixture_second])

        with patch("scripts.checks._common.ROOT", decoy):
            base = _common.push_context_base(root=fixture)
        assert base == fixture_first

    def test_root_none_default_still_reads_root(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Byte-identical-for-existing-callers guardrail: omitting root keeps reading ROOT."""
        repo = tmp_path / "repo"
        self._init_repo(repo, "feature/not-main")
        first = self._commit(repo, "a.txt", "first")
        self._commit(repo, "b.txt", "second")

        monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
        with patch("scripts.checks._common.ROOT", repo):
            base = _common.push_context_base()
        assert base == first


def test_full_tier_vars_absent_inside_a_test() -> None:
    for name in ("FULL_TIER_PREV", "FULL_TIER_LAST_GREEN", "CI_RCA_RANGE_PREV"):
        assert name not in os.environ


def test_conftest_strips_full_tier_vars_from_job_env() -> None:
    root = Path(__file__).resolve().parents[3]
    env = {**os.environ, "FULL_TIER_PREV": "x", "FULL_TIER_LAST_GREEN": "y", "CI_RCA_RANGE_PREV": "z"}
    node = "tests/checks/_common/test_push_context_base.py::test_full_tier_vars_absent_inside_a_test"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", node, "-q"],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stdout[-800:]
