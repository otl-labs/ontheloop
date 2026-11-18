"""Tests for scripts/checks/decisions/_baseline.py (PLAN-decision-entry-flow-governance,
Decision 153 fast-tier budget) -- the shared, memoized origin/main decision-number baseline
reader both --pre DECISIONS consumers (validate_decision_entry_conformance,
validate_decisions_size) share.

Mirrors tests/checks/verification/test_validate_graduation_completeness.py's real-throwaway-repo
pattern for the reachable-origin branch, plus the no-git-repo unreachable-sentinel case, plus a
memoization test proving the two consumers share ONE baseline read per root."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from scripts.checks.decisions import _baseline


def _git(repo: Path, args: list[str]) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, ["init", "-q"])
    _git(repo, ["config", "user.email", "test@example.com"])
    _git(repo, ["config", "user.name", "Test"])


def _commit_all(repo: Path, message: str) -> str:
    _git(repo, ["add", "-A"])
    _git(repo, ["commit", "-q", "-m", message])
    return _git(repo, ["rev-parse", "HEAD"]).stdout.strip()


def _write_decisions(root: Path, live: str, archive: str = "") -> None:
    docs = root / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "DECISIONS.md").write_text(live, encoding="utf-8")
    (docs / "DECISIONS_ARCHIVE.md").write_text(archive, encoding="utf-8")


class TestUnreachableSentinel:
    def test_no_git_repo_returns_none(self, tmp_path: Path) -> None:
        _baseline._cache.clear()
        assert _baseline.baseline_decision_numbers(tmp_path) is None

    def test_unreachable_result_is_cached(self, tmp_path: Path) -> None:
        _baseline._cache.clear()
        first = _baseline.baseline_decision_numbers(tmp_path)
        second = _baseline.baseline_decision_numbers(tmp_path)
        assert first is None
        assert second is None
        assert tmp_path in _baseline._cache


class TestReachableRealRepo:
    def test_reads_decision_numbers_from_both_files_at_origin_main_ref(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        _write_decisions(
            repo,
            "## Decision 1: Base entry (Decided)\n\n**Status:** Decided\n",
            archive="## Decision 2: Archive entry (Decided)\n\n**Status:** Decided\n",
        )
        sha = _commit_all(repo, "base")
        _git(repo, ["update-ref", "refs/remotes/origin/main", sha])
        _baseline._cache.clear()
        assert _baseline.baseline_decision_numbers(repo) == {1, 2}

    def test_missing_decisions_files_at_origin_main_yields_empty_set_not_none(self, tmp_path: Path) -> None:
        """origin/main IS reachable but neither DECISIONS file exists there -- the 'git show'
        non-zero-exit continue branch fires for both, yielding an empty set (not the None
        advisory-skip sentinel, since origin/main itself resolves fine)."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / "README.md").write_text("base\n", encoding="utf-8")
        sha = _commit_all(repo, "base")
        _git(repo, ["update-ref", "refs/remotes/origin/main", sha])
        _baseline._cache.clear()
        assert _baseline.baseline_decision_numbers(repo) == set()


class TestMemoization:
    def test_second_call_on_same_root_performs_no_further_git_invocations(self, tmp_path: Path) -> None:
        """Simulates both --pre consumers calling baseline_decision_numbers(root) against the
        SAME root -- the second call (the second consumer) must add zero further subprocess
        invocations beyond the first population (Decision 153 fast-tier budget: one shared git
        read, not one per consumer)."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        _write_decisions(repo, "## Decision 1: Base entry (Decided)\n\n**Status:** Decided\n")
        sha = _commit_all(repo, "base")
        _git(repo, ["update-ref", "refs/remotes/origin/main", sha])
        _baseline._cache.clear()

        real_run = _baseline._common.run
        with patch("scripts.checks.decisions._baseline._common.run", wraps=real_run) as mock_run:
            first = _baseline.baseline_decision_numbers(repo)
            calls_after_first = mock_run.call_count
            second = _baseline.baseline_decision_numbers(repo)
            calls_after_second = mock_run.call_count

        assert first == {1}
        assert second == {1}
        assert calls_after_first > 0
        assert calls_after_second == calls_after_first

    def test_different_roots_are_cached_independently(self, tmp_path: Path) -> None:
        repo_a = tmp_path / "a"
        repo_b = tmp_path / "b"
        for repo, n in ((repo_a, 1), (repo_b, 2)):
            _init_repo(repo)
            _write_decisions(repo, f"## Decision {n}: Entry (Decided)\n\n**Status:** Decided\n")
            sha = _commit_all(repo, "base")
            _git(repo, ["update-ref", "refs/remotes/origin/main", sha])
        _baseline._cache.clear()
        assert _baseline.baseline_decision_numbers(repo_a) == {1}
        assert _baseline.baseline_decision_numbers(repo_b) == {2}


class TestBaselineReaderFnType:
    def test_type_alias_is_a_callable_annotation(self) -> None:
        """BaselineReaderFn is the shared injection-seam type both --pre consumers import."""
        assert _baseline.BaselineReaderFn is not None

    def test_body_reader_type_alias_is_exported(self) -> None:
        """BaselineBodyReaderFn is the companion body-reader seam this module exports."""
        assert _baseline.BaselineBodyReaderFn is not None


class TestBaselineBodies:
    """baseline_decision_bodies -- the per-number body reader the append-only lock consumes."""

    def test_no_git_repo_returns_none_sentinel(self, tmp_path: Path) -> None:
        _baseline._body_cache.clear()
        assert _baseline.baseline_decision_bodies(tmp_path) is None

    def test_returns_heading_inclusive_blocks_keyed_by_corpus_file(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        _write_decisions(
            repo,
            "## Decision 1: Live entry (Decided)\n\n**Status:** Decided\n\nBody line.\n",
            archive="## Decision 2: Archived entry (Decided)\n\n**Status:** Superseded\n",
        )
        sha = _commit_all(repo, "base")
        _git(repo, ["update-ref", "refs/remotes/origin/main", sha])
        _baseline._body_cache.clear()

        bodies = _baseline.baseline_decision_bodies(repo)

        assert bodies is not None
        assert set(bodies) == {"docs/DECISIONS.md", "docs/DECISIONS_ARCHIVE.md"}
        assert set(bodies["docs/DECISIONS.md"]) == {1}
        assert set(bodies["docs/DECISIONS_ARCHIVE.md"]) == {2}
        live_block = bodies["docs/DECISIONS.md"][1]
        assert live_block.startswith("## Decision 1: Live entry (Decided)")
        assert "Body line." in live_block

    def test_multiple_entries_split_at_heading_boundaries(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        _write_decisions(
            repo,
            "## Decision 1: First (Decided)\n\nAlpha.\n\n---\n\n## Decision 2: Second (Decided)\n\nBeta.\n",
        )
        sha = _commit_all(repo, "base")
        _git(repo, ["update-ref", "refs/remotes/origin/main", sha])
        _baseline._body_cache.clear()

        bodies = _baseline.baseline_decision_bodies(repo)

        assert bodies is not None
        live = bodies["docs/DECISIONS.md"]
        assert set(live) == {1, 2}
        assert "Alpha." in live[1] and "Beta." not in live[1]
        assert "Beta." in live[2] and "Alpha." not in live[2]

    def test_missing_corpus_file_yields_empty_dict_not_none(self, tmp_path: Path) -> None:
        """origin/main resolves but the corpus files are absent there: every number is new.
        Distinct from the None advisory-skip sentinel."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / "README.md").write_text("base\n", encoding="utf-8")
        sha = _commit_all(repo, "base")
        _git(repo, ["update-ref", "refs/remotes/origin/main", sha])
        _baseline._body_cache.clear()

        bodies = _baseline.baseline_decision_bodies(repo)

        assert bodies is not None
        assert bodies["docs/DECISIONS.md"] == {}
        assert bodies["docs/DECISIONS_ARCHIVE.md"] == {}

    def test_second_call_performs_no_further_git_invocations(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo(repo)
        _write_decisions(repo, "## Decision 1: Entry (Decided)\n\n**Status:** Decided\n")
        sha = _commit_all(repo, "base")
        _git(repo, ["update-ref", "refs/remotes/origin/main", sha])
        _baseline._body_cache.clear()

        real_run = _baseline._common.run
        with patch("scripts.checks.decisions._baseline._common.run", wraps=real_run) as mock_run:
            first = _baseline.baseline_decision_bodies(repo)
            calls_after_first = mock_run.call_count
            second = _baseline.baseline_decision_bodies(repo)
            calls_after_second = mock_run.call_count

        assert first is not None and second is not None
        assert calls_after_first > 0
        assert calls_after_second == calls_after_first

    def test_body_cache_is_independent_of_numbers_cache(self, tmp_path: Path) -> None:
        """The two readers memoize separately -- clearing one never silently serves the other
        a stale or missing entry."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        _write_decisions(repo, "## Decision 1: Entry (Decided)\n\n**Status:** Decided\n")
        sha = _commit_all(repo, "base")
        _git(repo, ["update-ref", "refs/remotes/origin/main", sha])
        _baseline._cache.clear()
        _baseline._body_cache.clear()

        assert _baseline.baseline_decision_numbers(repo) == {1}
        assert repo in _baseline._cache
        assert repo not in _baseline._body_cache

        bodies = _baseline.baseline_decision_bodies(repo)
        assert bodies is not None
        assert repo in _baseline._body_cache


class TestBaseFileText:
    """base_file_text -- one memoized git show per (root, path), shared by every consumer."""

    def _repo(self, tmp_path: Path) -> Path:
        repo = tmp_path / "repo"
        _init_repo(repo)
        (repo / "a.yaml").write_text("k: 1\n", encoding="utf-8")
        sha = _commit_all(repo, "base")
        _git(repo, ["update-ref", "refs/remotes/origin/main", sha])
        _baseline._file_cache.clear()
        return repo

    def test_returns_the_text_at_origin_main(self, tmp_path: Path) -> None:
        assert _baseline.base_file_text(self._repo(tmp_path), "a.yaml") == "k: 1\n"

    def test_none_when_the_path_is_absent_there(self, tmp_path: Path) -> None:
        assert _baseline.base_file_text(self._repo(tmp_path), "missing.yaml") is None

    def test_none_when_git_fails_and_the_result_is_memoized(self, tmp_path: Path) -> None:
        _baseline._file_cache.clear()
        failing = subprocess.CompletedProcess([], 128, stdout="", stderr="fatal")
        with patch("scripts.checks.decisions._baseline._common.run", return_value=failing) as run:
            assert _baseline.base_file_text(tmp_path, "a.yaml") is None
            assert _baseline.base_file_text(tmp_path, "a.yaml") is None
        assert run.call_count == 1
