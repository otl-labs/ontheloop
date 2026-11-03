"""correlation-surface tests: correlate_ci_rca_with_main soft/hard classification,
correlation-aware print_ci_rca_recs output, general correlate_recs_with_commits engine,
queue-wide relevance-triage surfacing (rec-2709 Wave 4).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

boto3 = pytest.importorskip("boto3")

from tests.fixtures.session_preflight_module import preflight as _preflight  # noqa: E402


class TestCiRcaCorrelation:
    """Tests for correlate_ci_rca_with_main() -- soft/hard classification."""

    def _make_rec(self, rec_id: str, file: str = "scripts/foo.py", created: str = "2026-06-10T10:00:00Z") -> dict:
        return {"id": rec_id, "file": file, "title": "CI failure", "priority": "critical", "created_timestamp": created}

    def _make_commit(self, sha: str, date: str, subject: str, files: list[str] | None = None) -> dict:
        return {"sha": sha, "date": date, "subject": subject, "files": files or []}

    def _make_closed_sibling(
        self,
        sib_id: str,
        file: str = "scripts/foo.py",
        title: str = "CI failure",
        closed: str = "2026-06-11T10:00:00Z",
    ) -> dict:
        return {
            "id": sib_id,
            "file": file,
            "title": title,
            "last_updated_timestamp": closed,
        }

    # --- LIKELY-RESOLVED cases ---

    def test_correlated_by_file_classified_likely_resolved(self) -> None:
        rec = self._make_rec("rec-2187", file="scripts/foo.py", created="2026-06-10T10:00:00Z")
        commit = self._make_commit("abc1234", "2026-06-11T10:00:00+00:00", "fix: repair foo", files=["scripts/foo.py"])
        result = _preflight.correlate_ci_rca_with_main([rec], [commit])
        assert result["likely_resolved"] == [rec]
        assert result["unresolved"] == []

    def test_correlated_by_rec_id_in_subject_classified_likely_resolved(self) -> None:
        rec = self._make_rec("rec-2188", file="scripts/bar.py", created="2026-06-10T10:00:00Z")
        commit = self._make_commit("def5678", "2026-06-11T10:00:00+00:00", "fix(ci): closes rec-2188 mypy issue", files=[])
        result = _preflight.correlate_ci_rca_with_main([rec], [commit])
        assert result["likely_resolved"] == [rec]
        assert result["unresolved"] == []

    # --- UNRESOLVED / HARD BLOCK retained ---

    def test_no_matching_commit_classified_unresolved(self) -> None:
        rec = self._make_rec("rec-2190", file="scripts/baz.py", created="2026-06-10T10:00:00Z")
        commit = self._make_commit(
            "fff9999", "2026-06-11T10:00:00+00:00", "feat: unrelated change", files=["scripts/other.py"]
        )
        result = _preflight.correlate_ci_rca_with_main([rec], [commit])
        assert result["unresolved"] == [rec]
        assert result["likely_resolved"] == []

    def test_commit_before_rec_creation_does_not_correlate(self) -> None:
        rec = self._make_rec("rec-2191", file="scripts/foo.py", created="2026-06-12T10:00:00Z")
        commit = self._make_commit("aaa1111", "2026-06-11T10:00:00+00:00", "fix: repair foo", files=["scripts/foo.py"])
        result = _preflight.correlate_ci_rca_with_main([rec], [commit])
        assert result["unresolved"] == [rec]
        assert result["likely_resolved"] == []

    def test_empty_recs_returns_empty(self) -> None:
        commit = self._make_commit("abc1234", "2026-06-11T10:00:00+00:00", "fix: something")
        result = _preflight.correlate_ci_rca_with_main([], [commit])
        assert result == {"likely_resolved": [], "unresolved": []}

    def test_empty_commits_all_unresolved(self) -> None:
        rec = self._make_rec("rec-9999", file="scripts/x.py")
        result = _preflight.correlate_ci_rca_with_main([rec], [])
        assert result["unresolved"] == [rec]
        assert result["likely_resolved"] == []

    # --- precision: component-boundary path matching ---

    def test_basename_substring_of_unrelated_path_not_correlated(self) -> None:
        # "utils.py" must NOT match "scripts/test_utils.py" (substring crosses component boundary).
        rec = self._make_rec("rec-2195a", file="utils.py", created="2026-06-10T10:00:00Z")
        commit = self._make_commit(
            "bbb1111", "2026-06-11T10:00:00+00:00", "fix: update test_utils", files=["scripts/test_utils.py"]
        )
        result = _preflight.correlate_ci_rca_with_main([rec], [commit])
        assert result["unresolved"] == [rec]
        assert result["likely_resolved"] == []

    def test_ci_py_not_matched_by_cli_py(self) -> None:
        # "ci.py" must NOT match "scripts/cli.py".
        rec = self._make_rec("rec-2195b", file="ci.py", created="2026-06-10T10:00:00Z")
        commit = self._make_commit("ccc2222", "2026-06-11T10:00:00+00:00", "feat: cli improvements", files=["scripts/cli.py"])
        result = _preflight.correlate_ci_rca_with_main([rec], [commit])
        assert result["unresolved"] == [rec]
        assert result["likely_resolved"] == []

    def test_basename_only_rec_matches_full_path_with_same_basename(self) -> None:
        # "preflight.py" (basename) must match "scripts/session/preflight.py". (Pre-RS-01, this
        # fixture used the flat "session_preflight.py" basename; the RS-01 session_* rename strips
        # the family prefix, so the file's basename is now "preflight.py", not "session_preflight.py"
        # -- a stale pre-move basename would no longer basename-match the post-move nested path.)
        rec = self._make_rec("rec-2195c", file="preflight.py", created="2026-06-10T10:00:00Z")
        commit = self._make_commit(
            "ddd3333", "2026-06-11T10:00:00+00:00", "fix: preflight update", files=["scripts/session/preflight.py"]
        )
        result = _preflight.correlate_ci_rca_with_main([rec], [commit])
        assert result["likely_resolved"] == [rec]
        assert result["unresolved"] == []

    def test_full_path_exact_match_correlated(self) -> None:
        # "scripts/session/preflight.py" must match "scripts/session/preflight.py" exactly.
        rec = self._make_rec("rec-2195d", file="scripts/session/preflight.py", created="2026-06-10T10:00:00Z")
        commit = self._make_commit(
            "eee4444", "2026-06-11T10:00:00+00:00", "fix: preflight update", files=["scripts/session/preflight.py"]
        )
        result = _preflight.correlate_ci_rca_with_main([rec], [commit])
        assert result["likely_resolved"] == [rec]
        assert result["unresolved"] == []

    # --- mixed batch ---

    def test_mixed_batch_split_correctly(self) -> None:
        rec_corr = self._make_rec("rec-100", file="scripts/a.py", created="2026-06-10T10:00:00Z")
        rec_not = self._make_rec("rec-101", file="scripts/b.py", created="2026-06-10T10:00:00Z")
        commit = self._make_commit("aaa2222", "2026-06-11T10:00:00+00:00", "fix: patch a", files=["scripts/a.py"])
        result = _preflight.correlate_ci_rca_with_main([rec_corr, rec_not], [commit])
        assert result["likely_resolved"] == [rec_corr]
        assert result["unresolved"] == [rec_not]

    # --- end-to-end derive->correlate regression (rec-2268 incident shape) ---

    def test_end_to_end_derive_to_correlate_classifies_rec_2268_shape(self) -> None:
        """rec-2268 shape: the open ci_rca rec's file was modified by a newer main commit, but
        _derive_ci_rca_open() previously dropped the `file` field so correlate_ci_rca_with_main()
        could not match it and the rec was incorrectly left as HARD BLOCK."""
        raw_row = {
            "id": "rec-2268",
            "title": "mypy failure in ci_rca_tier_map",
            "priority": "critical",
            "created_timestamp": "2026-06-17T08:00:00Z",
            "source": "ci_rca",
            "status": "open",
            "file": "scripts/ci_rca/tier_map.py",
        }
        derived = _preflight._derive_ci_rca_open([raw_row])
        assert len(derived) == 1
        assert derived[0]["file"] == "scripts/ci_rca/tier_map.py", "file must survive _derive_ci_rca_open projection"

        commit = self._make_commit(
            "e779dd30",
            "2026-06-18T09:00:00+00:00",
            "fix: add encoding utf-8 to ci_rca_tier_map (#184)",
            files=["scripts/ci_rca/tier_map.py"],
        )
        result = _preflight.correlate_ci_rca_with_main(derived, [commit])
        assert result["likely_resolved"] == derived, "rec-2268 shape must classify as likely_resolved end-to-end"
        assert result["unresolved"] == []

    # --- closed-sibling cluster tests ---

    def test_closed_sibling_cluster_positive_same_file_similar_title_sibling_after(self) -> None:
        """Positive: open rec with a closed sibling on the same file, similar title, sibling closed after rec created."""
        rec = self._make_rec("rec-2274", file="scripts/foo.py", created="2026-06-17T08:00:00Z")
        rec["title"] = "mypy failure in foo module"
        sibling = self._make_closed_sibling(
            "rec-2260", file="scripts/foo.py", title="mypy failure in foo module", closed="2026-06-18T09:00:00Z"
        )
        result = _preflight.correlate_ci_rca_with_main([rec], [], closed_ci_rca_recs=[sibling])
        assert len(result["likely_resolved"]) == 1
        assert result["likely_resolved"][0]["id"] == "rec-2274"
        assert "rec-2260" in result["likely_resolved"][0].get("_resolved_reason", "")
        assert result["unresolved"] == []

    def test_closed_sibling_cluster_negative_dissimilar_title(self) -> None:
        """Negative: same file but Jaccard < 0.5 (unrelated title) -- must NOT flag as likely_resolved."""
        rec = self._make_rec("rec-2275", file="scripts/foo.py", created="2026-06-17T08:00:00Z")
        rec["title"] = "mypy type annotation failure"
        sibling = self._make_closed_sibling(
            "rec-2261", file="scripts/foo.py", title="ruff import order violation", closed="2026-06-18T09:00:00Z"
        )
        result = _preflight.correlate_ci_rca_with_main([rec], [], closed_ci_rca_recs=[sibling])
        assert result["likely_resolved"] == []
        assert result["unresolved"] == [rec]

    def test_closed_sibling_cluster_negative_stale_sibling(self) -> None:
        """Negative: same file + similar title but sibling was closed BEFORE the open rec was created -- stale guard."""
        rec = self._make_rec("rec-2276", file="scripts/foo.py", created="2026-06-17T08:00:00Z")
        rec["title"] = "mypy failure in foo module"
        sibling = self._make_closed_sibling(
            "rec-2262", file="scripts/foo.py", title="mypy failure in foo module", closed="2026-06-16T07:00:00Z"
        )
        result = _preflight.correlate_ci_rca_with_main([rec], [], closed_ci_rca_recs=[sibling])
        assert result["likely_resolved"] == []
        assert result["unresolved"] == [rec]

    def test_closed_sibling_cluster_negative_null_timestamp(self) -> None:
        """Negative: same file + similar title but sibling has no last_updated_timestamp -- must NOT flag."""
        rec = self._make_rec("rec-2277", file="scripts/foo.py", created="2026-06-17T08:00:00Z")
        rec["title"] = "mypy failure in foo module"
        sibling = {
            "id": "rec-2263",
            "file": "scripts/foo.py",
            "title": "mypy failure in foo module",
            "last_updated_timestamp": None,
        }
        result = _preflight.correlate_ci_rca_with_main([rec], [], closed_ci_rca_recs=[sibling])
        assert result["likely_resolved"] == []
        assert result["unresolved"] == [rec]


class TestPrintCiRcaRecsWithCorrelation:
    """Tests for the new correlation-aware print_ci_rca_recs() output."""

    def _capture_output(self, recs: list[dict], correlation: dict | None) -> str:
        printed: list[str] = []

        def capture(*args: object, **kwargs: object) -> None:
            printed.append(" ".join(str(a) for a in args))

        with patch("builtins.print", side_effect=capture):
            _preflight.print_ci_rca_recs(recs, correlation=correlation)
        return "\n".join(printed)

    def test_hard_block_shown_for_unresolved_rec(self) -> None:
        rec = {"id": "rec-9999", "title": "CI broken", "priority": "critical", "created_timestamp": "2026-05-13"}
        correlation = {"likely_resolved": [], "unresolved": [rec]}
        output = self._capture_output([rec], correlation)
        assert "HARD BLOCK" in output
        assert "SOFT" not in output
        assert "rec-9999" in output

    def test_soft_prompt_shown_for_likely_resolved_rec(self) -> None:
        rec = {"id": "rec-2187", "title": "mypy fail", "priority": "critical", "created_timestamp": "2026-06-10"}
        correlation = {"likely_resolved": [rec], "unresolved": []}
        output = self._capture_output([rec], correlation)
        assert "SOFT" in output
        assert "LIKELY RESOLVED" in output
        assert "HARD BLOCK" not in output
        assert "rec-2187" in output
        assert "--update-rec rec-2187" in output

    def test_both_soft_and_hard_block_when_mixed(self) -> None:
        r_soft = {"id": "rec-100", "title": "old fail", "priority": "critical", "created_timestamp": "2026-06-10"}
        r_hard = {"id": "rec-101", "title": "new fail", "priority": "critical", "created_timestamp": "2026-06-12"}
        correlation = {"likely_resolved": [r_soft], "unresolved": [r_hard]}
        output = self._capture_output([r_soft, r_hard], correlation)
        assert "SOFT" in output
        assert "HARD BLOCK" in output
        assert "rec-100" in output
        assert "rec-101" in output

    def test_none_correlation_falls_back_to_all_hard_block(self) -> None:
        rec = {"id": "rec-999", "title": "CI broken", "priority": "critical", "created_timestamp": "2026-05-13"}
        output = self._capture_output([rec], correlation=None)
        assert "HARD BLOCK" in output
        assert "SOFT" not in output

    def test_empty_recs_shows_none(self) -> None:
        output = self._capture_output([], correlation={"likely_resolved": [], "unresolved": []})
        assert "(none)" in output


class TestCorrelateRecsWithCommits:
    """Tests for correlate_recs_with_commits() -- general engine (T3.8)."""

    def _rec(self, rec_id: str, file: str = "scripts/foo.py", created: str = "2026-06-10T10:00:00Z") -> dict:
        return {"id": rec_id, "file": file, "title": "Some recommendation", "created_timestamp": created}

    def _commit(self, sha: str, date: str, files: list[str]) -> dict:
        return {"sha": sha, "date": date, "subject": f"fix: {sha}", "files": files}

    def test_file_correlation_marks_likely_resolved(self) -> None:
        rec = self._rec("rec-001", file="scripts/foo.py")
        commit = self._commit("abc12345", "2026-06-11T10:00:00+00:00", ["scripts/foo.py"])
        result = _preflight.correlate_recs_with_commits([rec], [commit])
        assert result["likely_resolved"] == [rec]
        assert result["unresolved"] == []

    def test_id_in_commit_subject_marks_likely_resolved(self) -> None:
        rec = self._rec("rec-042")
        commit = {"sha": "bbb", "date": "2026-06-11T10:00:00+00:00", "subject": "fix: resolves rec-042", "files": []}
        result = _preflight.correlate_recs_with_commits([rec], [commit])
        assert result["likely_resolved"] == [rec]

    def test_no_match_marks_unresolved(self) -> None:
        rec = self._rec("rec-002", file="scripts/bar.py")
        commit = self._commit("ccc12345", "2026-06-11T10:00:00+00:00", ["scripts/other.py"])
        result = _preflight.correlate_recs_with_commits([rec], [commit])
        assert result["unresolved"] == [rec]

    def test_closed_sibling_cluster_signal(self) -> None:
        rec = self._rec("rec-003", file="scripts/foo.py", created="2026-06-10T10:00:00Z")
        rec["title"] = "Fix foo module failure"
        sibling = {
            "id": "rec-sib",
            "file": "scripts/foo.py",
            "title": "Fix foo module error",
            "last_updated_timestamp": "2026-06-11T10:00:00Z",
        }
        result = _preflight.correlate_recs_with_commits([rec], [], closed_recs=[sibling])
        assert len(result["likely_resolved"]) == 1

    def test_no_reader_call_made(self) -> None:
        """Serving from read-cache only; no warehouse re-fetch (Decision 88)."""
        rec = self._rec("rec-004", file="scripts/foo.py")
        commit = self._commit("ddd12345", "2026-06-11T10:00:00+00:00", ["scripts/foo.py"])
        with patch("scripts.preflight._common._make_reader") as mock_reader:
            _preflight.correlate_recs_with_commits([rec], [commit])
        mock_reader.assert_not_called()

    def test_correlate_ci_rca_wrapper_delegates(self) -> None:
        """correlate_ci_rca_with_main delegates to correlate_recs_with_commits."""
        rec = self._rec("rec-005", file="scripts/ci.py")
        commit = self._commit("eee12345", "2026-06-11T10:00:00+00:00", ["scripts/ci.py"])
        result = _preflight.correlate_ci_rca_with_main([rec], [commit], closed_ci_rca_recs=None)
        assert result["likely_resolved"] == [rec]


class TestSurfaceQueueRelevanceTriage:
    """Tests for surface_queue_relevance_triage() (T3.8 queue-wide surfacing)."""

    def _row(self, rec_id: str, status: str, source: str, file: str, created: str) -> dict:
        return {
            "id": rec_id,
            "status": status,
            "source": source,
            "file": file,
            "title": f"title {rec_id}",
            "created_timestamp": created,
            "last_updated_timestamp": created,
        }

    def test_returns_likely_resolved_for_open_non_ci_rca(self) -> None:
        cache = [
            self._row("rec-101", "open", "planning", "scripts/foo.py", "2026-06-10T10:00:00Z"),
        ]
        commits = [{"sha": "abc12345", "date": "2026-06-11T10:00:00+00:00", "subject": "fix", "files": ["scripts/foo.py"]}]
        result = _preflight.surface_queue_relevance_triage(cache, commits)
        assert any(r["id"] == "rec-101" for r in result)

    def test_ci_rca_recs_excluded_by_default(self) -> None:
        cache = [
            self._row("rec-200", "open", "ci_rca", "scripts/foo.py", "2026-06-10T10:00:00Z"),
        ]
        commits = [{"sha": "abc12345", "date": "2026-06-11T10:00:00+00:00", "subject": "fix", "files": ["scripts/foo.py"]}]
        result = _preflight.surface_queue_relevance_triage(cache, commits)
        assert all(r["id"] != "rec-200" for r in result)

    def test_no_reader_call_during_surfacing(self) -> None:
        """Surfacing is read-cache only; no DuckLake reader call (Decision 88)."""
        cache = [self._row("rec-300", "open", "planning", "scripts/bar.py", "2026-06-10T10:00:00Z")]
        commits = [{"sha": "def12345", "date": "2026-06-11T10:00:00+00:00", "subject": "fix", "files": ["scripts/bar.py"]}]
        with patch("scripts.preflight._common._make_reader") as mock_reader:
            _preflight.surface_queue_relevance_triage(cache, commits)
        mock_reader.assert_not_called()

    def test_cap_limits_results(self) -> None:
        cache = [self._row(f"rec-{i}", "open", "planning", f"scripts/f{i}.py", "2026-06-01T00:00:00Z") for i in range(20)]
        commits = [
            {
                "sha": "fff12345",
                "date": "2026-06-10T00:00:00+00:00",
                "subject": "fix all",
                "files": [f"scripts/f{i}.py" for i in range(20)],
            }
        ]
        result = _preflight.surface_queue_relevance_triage(cache, commits, cap=5)
        assert len(result) <= 5


class TestRedOverlapPointer:
    """Decision 226: the HARD BLOCK render points at the overlap check instead of a global /plan block."""

    def _render(self, recs: list[dict], correlation: dict | None) -> str:
        printed: list[str] = []
        with patch("builtins.print", side_effect=lambda *a, **k: printed.append(" ".join(str(x) for x in a))):
            _preflight.print_ci_rca_recs(recs, correlation=correlation)
        return "\n".join(printed)

    def test_both_paths_name_red_overlap(self) -> None:
        rec = {"id": "rec-1", "title": "CI broken", "priority": "Critical", "created_timestamp": "2026-10-18"}
        for correlation in (None, {"likely_resolved": [], "unresolved": [rec]}):
            output = self._render([rec], correlation)
            assert "scripts.ci_rca.red_overlap" in output
            assert "cannot scope unrelated work" not in output

    def test_abstention_advisory_names_decision_226(self) -> None:
        printed: list[str] = []
        rec = {"id": "rec-2", "title": "unsure", "priority": "Critical", "rca_confidence": "undetermined"}
        with patch("builtins.print", side_effect=lambda *a, **k: printed.append(" ".join(str(x) for x in a))):
            _preflight.print_ci_rca_undetermined_recs([rec])
        assert "Decision 226" in "\n".join(printed)
        assert "Decision 73 L5" in "\n".join(printed)
