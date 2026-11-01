"""Unit tests for scripts/rec_relevance.py (T3.8 / CD.36).

Covers all 8 relevance verdicts and the deterministic-vs-semantic closure split.
100% per-file coverage of scripts/rec_relevance.py required by test_coverage_checker.
"""

from __future__ import annotations

from unittest.mock import patch

from scripts.rec_relevance import (
    RELEVANCE_VERDICTS,
    _file_paths_correlate,
    _run_acceptance_probe,
    _scan_decision_contradiction,
    _title_jaccard,
    evaluate_rec_relevance,
)


class TestRelevanceVerdictsEnum:
    def test_has_all_eight_verdicts(self):
        expected = {
            "relevant",
            "satisfied",
            "superseded",
            "duplicate",
            "contradicted",
            "stale_target",
            "blocked_by_decision",
            "unknown",
        }
        assert RELEVANCE_VERDICTS == expected

    def test_is_frozenset(self):
        assert isinstance(RELEVANCE_VERDICTS, frozenset)


class TestFilePathsCorrelate:
    def test_exact_match(self):
        assert _file_paths_correlate("scripts/foo.py", "scripts/foo.py")

    def test_suffix_match(self):
        assert _file_paths_correlate("foo.py", "scripts/foo.py")

    def test_no_match(self):
        assert not _file_paths_correlate("scripts/bar.py", "scripts/foo.py")

    def test_partial_path_match(self):
        assert _file_paths_correlate("scripts/foo.py", "scripts/foo.py")

    def test_different_dirs_same_basename(self):
        assert _file_paths_correlate("foo.py", "other/foo.py")


class TestTitleJaccard:
    def test_identical_titles(self):
        assert _title_jaccard("foo bar baz", "foo bar baz") == 1.0

    def test_no_overlap(self):
        assert _title_jaccard("alpha beta", "gamma delta") == 0.0

    def test_empty_strings(self):
        assert _title_jaccard("", "") == 0.0

    def test_partial_overlap(self):
        score = _title_jaccard("add feature X", "add feature Y")
        assert 0.0 < score < 1.0

    def test_high_similarity(self):
        score = _title_jaccard("fix cache refresh bug", "fix cache refresh issue")
        assert score >= 0.5


class TestRunAcceptanceProbe:
    def test_returns_true_on_exit_0(self):
        with patch("scripts.rec_relevance.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            assert _run_acceptance_probe("true") is True

    def test_returns_false_on_nonzero_exit(self):
        with patch("scripts.rec_relevance.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            assert _run_acceptance_probe("false") is False

    def test_returns_false_on_timeout(self):
        import subprocess

        with patch("scripts.rec_relevance.subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 10)):
            assert _run_acceptance_probe("sleep 999", timeout=1) is False

    def test_returns_false_on_os_error(self):
        with patch("scripts.rec_relevance.subprocess.run", side_effect=OSError("no such file")):
            assert _run_acceptance_probe("nonexistent_cmd") is False


class TestScanDecisionContradiction:
    def _dec(self, num: int, status: str) -> dict:
        return {"decision_id": num, "status": status}

    def test_superseded_decision_yields_contradicted(self):
        rec = {"context": "see Decision 42 for context"}
        decisions = [self._dec(42, "Superseded by Decision 55")]
        result = _scan_decision_contradiction(rec, decisions)
        assert result is not None
        verdict, evidence = result
        assert verdict == "contradicted"
        assert "42" in evidence

    def test_pending_decision_yields_blocked(self):
        rec = {"context": "Decision 99 pending human review"}
        decisions = [self._dec(99, "Agent-decided -- pending human review")]
        result = _scan_decision_contradiction(rec, decisions)
        assert result is not None
        verdict, evidence = result
        assert verdict == "blocked_by_decision"
        assert "99" in evidence

    def test_decided_decision_yields_none(self):
        rec = {"context": "Decision 10 is active"}
        decisions = [self._dec(10, "Decided")]
        assert _scan_decision_contradiction(rec, decisions) is None

    def test_no_decision_ids_in_rec_yields_none(self):
        rec = {"context": "no decision citations here"}
        decisions = [self._dec(1, "pending")]
        assert _scan_decision_contradiction(rec, decisions) is None

    def test_empty_decisions_list_yields_none(self):
        rec = {"context": "Decision 5 referenced"}
        assert _scan_decision_contradiction(rec, []) is None

    def test_cd_prefix_also_matched(self):
        rec = {"context": "CD.36 gates this work"}
        decisions = [self._dec(36, "Superseded by Decision 99")]
        result = _scan_decision_contradiction(rec, decisions)
        assert result is not None
        assert result[0] == "contradicted"

    def test_acceptance_field_searched(self):
        rec = {"acceptance": "Decision 7 must be ratified first", "context": None}
        decisions = [self._dec(7, "pending")]
        result = _scan_decision_contradiction(rec, decisions)
        assert result is not None
        assert result[0] == "blocked_by_decision"

    def test_title_field_searched(self):
        rec = {"title": "Implement Decision 3 outcome", "context": None}
        decisions = [self._dec(3, "Superseded by Decision 10")]
        result = _scan_decision_contradiction(rec, decisions)
        assert result is not None
        assert result[0] == "contradicted"

    def test_non_matching_decision_id_ignored(self):
        rec = {"context": "Decision 5 is important"}
        decisions = [self._dec(99, "pending")]
        assert _scan_decision_contradiction(rec, decisions) is None


class TestEvaluateRecRelevance:
    def _base_rec(self, **kwargs) -> dict:
        defaults: dict = {
            "id": "rec-001",
            "title": "Test recommendation",
            "file": None,
            "acceptance": None,
            "context": None,
            "created_timestamp": None,
        }
        defaults.update(kwargs)
        return defaults

    def test_verdict_satisfied_deterministic_acceptance_probe(self, tmp_path):
        rec = self._base_rec(acceptance="echo ok")
        with patch("scripts.rec_relevance._run_acceptance_probe", return_value=True):
            verdict, evidence = evaluate_rec_relevance(rec, run_acceptance_probe=True, repo_root=tmp_path)
        assert verdict == "satisfied"
        assert "acceptance probe passed" in evidence

    def test_acceptance_probe_not_run_when_flag_false(self, tmp_path):
        rec = self._base_rec(acceptance="echo ok", file=None)
        with patch("scripts.rec_relevance._run_acceptance_probe") as mock_probe:
            evaluate_rec_relevance(rec, run_acceptance_probe=False, repo_root=tmp_path)
        mock_probe.assert_not_called()

    def test_acceptance_probe_skipped_when_acceptance_none(self, tmp_path):
        rec = self._base_rec(acceptance=None, file=None)
        with patch("scripts.rec_relevance._run_acceptance_probe") as mock_probe:
            evaluate_rec_relevance(rec, run_acceptance_probe=True, repo_root=tmp_path)
        mock_probe.assert_not_called()

    def test_verdict_unknown_when_acceptance_none_and_no_signals(self, tmp_path):
        rec = self._base_rec(acceptance=None, file=None)
        verdict, evidence = evaluate_rec_relevance(rec, run_acceptance_probe=True, repo_root=tmp_path)
        assert verdict == "unknown"
        assert "no signals" in evidence

    def test_verdict_stale_target(self, tmp_path):
        rec = self._base_rec(file="nonexistent/path.py")
        verdict, evidence = evaluate_rec_relevance(rec, repo_root=tmp_path)
        assert verdict == "stale_target"
        assert "target file absent" in evidence

    def test_verdict_stale_target_not_fired_when_file_exists(self, tmp_path):
        existing = tmp_path / "scripts" / "foo.py"
        existing.parent.mkdir(parents=True)
        existing.write_text("# ok", encoding="utf-8")
        rec = self._base_rec(file="scripts/foo.py")
        verdict, _ = evaluate_rec_relevance(rec, repo_root=tmp_path)
        assert verdict != "stale_target"

    def test_verdict_contradicted(self, tmp_path):
        rec = self._base_rec(context="see Decision 5 for rationale")
        decisions = [{"decision_id": 5, "status": "Superseded by Decision 10"}]
        verdict, evidence = evaluate_rec_relevance(rec, decisions=decisions, repo_root=tmp_path)
        assert verdict == "contradicted"
        assert "5" in evidence

    def test_verdict_blocked_by_decision(self, tmp_path):
        rec = self._base_rec(context="Decision 7 pending review")
        decisions = [{"decision_id": 7, "status": "pending human review"}]
        verdict, evidence = evaluate_rec_relevance(rec, decisions=decisions, repo_root=tmp_path)
        assert verdict == "blocked_by_decision"

    def test_verdict_satisfied_semantic_commit_correlation(self, tmp_path):
        rec = self._base_rec(file="scripts/foo.py", created_timestamp="2026-01-01T00:00:00+00:00")
        commits = [{"sha": "abc12345", "date": "2026-06-01T00:00:00+00:00", "files": ["scripts/foo.py"]}]
        existing = tmp_path / "scripts" / "foo.py"
        existing.parent.mkdir(parents=True)
        existing.write_text("# ok", encoding="utf-8")
        verdict, evidence = evaluate_rec_relevance(rec, recent_commits=commits, repo_root=tmp_path)
        assert verdict == "satisfied"
        assert "semantic" in evidence
        assert "abc12345"[:8] in evidence

    def test_semantic_commit_skipped_when_commit_before_rec(self, tmp_path):
        rec = self._base_rec(file="scripts/foo.py", created_timestamp="2026-06-01T00:00:00+00:00")
        old_commit = {"sha": "old00000", "date": "2026-01-01T00:00:00+00:00", "files": ["scripts/foo.py"]}
        existing = tmp_path / "scripts" / "foo.py"
        existing.parent.mkdir(parents=True)
        existing.write_text("# ok", encoding="utf-8")
        verdict, _ = evaluate_rec_relevance(rec, recent_commits=[old_commit], repo_root=tmp_path)
        assert verdict != "satisfied"

    def test_verdict_superseded_closed_sibling(self, tmp_path):
        rec = self._base_rec(title="Fix cache refresh bug", file="scripts/foo.py")
        existing = tmp_path / "scripts" / "foo.py"
        existing.parent.mkdir(parents=True)
        existing.write_text("# ok", encoding="utf-8")
        closed = [{"id": "rec-002", "title": "Fix cache refresh issue", "file": "scripts/foo.py"}]
        verdict, evidence = evaluate_rec_relevance(rec, closed_recs=closed, repo_root=tmp_path)
        assert verdict == "superseded"
        assert "rec-002" in evidence

    def test_verdict_duplicate_open_sibling(self, tmp_path):
        rec = self._base_rec(id="rec-001", title="Refactor authentication logic", file="scripts/auth.py")
        existing = tmp_path / "scripts" / "auth.py"
        existing.parent.mkdir(parents=True)
        existing.write_text("# ok", encoding="utf-8")
        open_recs = [{"id": "rec-999", "title": "Refactor authentication logic", "file": "scripts/auth.py"}]
        verdict, evidence = evaluate_rec_relevance(rec, open_recs=open_recs, repo_root=tmp_path)
        assert verdict == "duplicate"
        assert "rec-999" in evidence

    def test_verdict_relevant_when_signals_present_but_no_match(self, tmp_path):
        rec = self._base_rec(file="scripts/foo.py", title="Unique title that matches nothing")
        existing = tmp_path / "scripts" / "foo.py"
        existing.parent.mkdir(parents=True)
        existing.write_text("# ok", encoding="utf-8")
        commits = [{"sha": "aaa", "date": "2026-12-01", "files": ["scripts/other.py"]}]
        verdict, evidence = evaluate_rec_relevance(rec, recent_commits=commits, closed_recs=[], repo_root=tmp_path)
        assert verdict == "relevant"
        assert "no resolution signals" in evidence

    def test_verdict_unknown_when_no_signals_provided(self, tmp_path):
        rec = self._base_rec()
        verdict, evidence = evaluate_rec_relevance(rec, repo_root=tmp_path)
        assert verdict == "unknown"
        assert "no signals" in evidence

    def test_duplicate_check_skips_self(self, tmp_path):
        rec = self._base_rec(id="rec-001", title="Same title as self", file=None)
        open_recs = [{"id": "rec-001", "title": "Same title as self", "file": None}]
        verdict, _ = evaluate_rec_relevance(rec, open_recs=open_recs, repo_root=tmp_path)
        assert verdict != "duplicate"

    def test_probe_fails_yields_stale_target_when_file_absent(self, tmp_path):
        rec = self._base_rec(acceptance="echo ok", file="missing/file.py")
        with patch("scripts.rec_relevance._run_acceptance_probe", return_value=False):
            verdict, _ = evaluate_rec_relevance(rec, run_acceptance_probe=True, repo_root=tmp_path)
        assert verdict == "stale_target"


class TestDeterministicVsSemanticClosureSplit:
    """Explicit test of the closure-split contract (CD.36).

    Deterministic satisfied: acceptance probe passes -> auto-close candidate.
    Semantic satisfied: commit correlation -> close_proposed only.
    Evidence string encodes which path fired.
    """

    def test_deterministic_satisfied_evidence_prefix(self, tmp_path):
        rec = {
            "id": "rec-001",
            "title": "t",
            "file": None,
            "acceptance": "echo ok",
            "context": None,
            "created_timestamp": None,
        }
        with patch("scripts.rec_relevance._run_acceptance_probe", return_value=True):
            verdict, evidence = evaluate_rec_relevance(rec, run_acceptance_probe=True, repo_root=tmp_path)
        assert verdict == "satisfied"
        assert evidence.startswith("acceptance probe passed:")

    def test_semantic_satisfied_evidence_prefix(self, tmp_path):
        existing = tmp_path / "scripts" / "foo.py"
        existing.parent.mkdir(parents=True)
        existing.write_text("# ok", encoding="utf-8")
        rec = {
            "id": "rec-001",
            "title": "t",
            "file": "scripts/foo.py",
            "acceptance": None,
            "context": None,
            "created_timestamp": "2026-01-01T00:00:00+00:00",
        }
        commits = [{"sha": "cafecafe", "date": "2026-06-01T00:00:00+00:00", "files": ["scripts/foo.py"]}]
        verdict, evidence = evaluate_rec_relevance(rec, recent_commits=commits, repo_root=tmp_path)
        assert verdict == "satisfied"
        assert evidence.startswith("semantic:")


class _StubReader:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def named(self, verb: str, **kwargs):
        assert verb == "recs_by_ids"
        wanted = kwargs["ids"].split(",")
        return [r for r in self._rows if r["id"] in wanted]


def _rec(rec_id: str, **kwargs) -> dict:
    row = {"id": rec_id, "title": "t", "file": None, "acceptance": None, "context": None, "created_timestamp": None}
    row.update(kwargs)
    return row


class TestBundledRecNeverClosedBeforeMerge:
    """rec-4592: the /implement re-check reports; it never closes (the merged trailer does)."""

    def test_deterministic_satisfied_never_writes(self):
        from scripts.rec_relevance import implement_recheck

        reader = _StubReader([_rec("rec-1", acceptance="echo ok")])
        with (
            patch("scripts.rec_relevance._run_acceptance_probe", return_value=True),
            patch("scripts.ops_data_portal.update_rec") as update,
            patch("scripts.ops_data_portal.propose_or_close_rec") as propose,
        ):
            results = implement_recheck(["rec-1"], reader=reader)
        assert results[0][0] == "rec-1" and results[0][1] == "satisfied" and results[0][3] is None
        update.assert_not_called()
        propose.assert_not_called()

    def test_superseded_yields_non_deterministic_proposal(self):
        from scripts.rec_relevance import implement_recheck

        reader = _StubReader([_rec("rec-2", file="nonexistent/path.py")])
        with patch("scripts.ops_data_portal.update_rec") as update:
            results = implement_recheck(["rec-2"], reader=reader)
        assert results[0][1] == "stale_target"
        assert results[0][3] is not None and "--status closed" in results[0][3]
        update.assert_not_called()

    def test_missing_id_reported(self):
        from scripts.rec_relevance import implement_recheck

        assert implement_recheck(["rec-9"], reader=_StubReader([])) == [("rec-9", "missing", "", None)]

    def test_default_reader_is_made_lazily(self):
        from scripts.rec_relevance import implement_recheck

        with patch("src.common.ducklake_reader_client.make_reader", return_value=_StubReader([_rec("rec-3")])):
            assert implement_recheck(["rec-3"])[0][1] == "unknown"

    def test_cli_prints_one_line_per_rec_and_missing_exits_1(self, capsys):
        from scripts import rec_relevance

        rows = [_rec("rec-1", file="nonexistent/path.py"), _rec("rec-2")]
        with patch("src.common.ducklake_reader_client.make_reader", return_value=_StubReader(rows)):
            code = rec_relevance.main(["--implement-recheck", "rec-1,rec-2,rec-7"])
        out = capsys.readouterr().out.splitlines()
        assert code == 1
        assert out[0].startswith("rec-1 stale_target | target file absent")
        assert "--status closed" in out[1]
        assert out[2].startswith("rec-2 unknown |")
        assert out[3] == "rec-7 missing"

    def test_cli_exit_zero_when_all_present(self, capsys, monkeypatch):
        from scripts import rec_relevance

        monkeypatch.setattr("sys.argv", ["rec_relevance", "--implement-recheck", "rec-2"])
        with patch("src.common.ducklake_reader_client.make_reader", return_value=_StubReader([_rec("rec-2")])):
            assert rec_relevance.main() == 0

    def test_closed_sibling_on_unrelated_file_is_skipped(self, tmp_path):
        (tmp_path / "a.py").write_text("x", encoding="utf-8")
        rec = _rec("rec-4", title="same title words here", file="a.py")
        sibling = {"id": "rec-5", "title": "same title words here", "file": "other/b.py"}
        verdict, _ = evaluate_rec_relevance(rec, closed_recs=[sibling], decisions=[{"decision_id": "1"}], repo_root=tmp_path)
        assert verdict == "relevant"

    def test_open_duplicate_on_unrelated_file_is_skipped(self, tmp_path):
        (tmp_path / "a.py").write_text("x", encoding="utf-8")
        rec = _rec("rec-4", title="same title words here", file="a.py")
        other = {"id": "rec-6", "title": "same title words here", "file": "other/b.py"}
        verdict, _ = evaluate_rec_relevance(rec, open_recs=[other], decisions=[{"decision_id": "1"}], repo_root=tmp_path)
        assert verdict == "relevant"
