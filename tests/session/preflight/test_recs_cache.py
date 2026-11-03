"""recs_cache-surface tests: graceful-missing-files, non-automatable recommendation counting
and softcap, forward-fix recursion alert, budget-bypass alert, DuckLake-reader recommendation
counting, recs-read-status degradation, latest-decision-timestamp lookup (rec-2709 Wave 4).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

boto3 = pytest.importorskip("boto3")

from tests.fixtures.session_preflight_module import preflight as _preflight  # noqa: E402


class TestNonAutomatableRecommendations:
    def test_non_automatable_fields_in_preflight_output(self, tmp_path: Path) -> None:
        """non_automatable_recommendations and non_automatable_details appear in preflight JSON."""
        preflight_report = tmp_path / ".preflight-report.json"
        with (
            patch("scripts.preflight.env_git.check_venv", return_value=True),
            patch("scripts.preflight.env_git.get_git_status", return_value=("agent/test", False, [])),
            patch("scripts.preflight.aws_infra.check_terraform_pending", return_value=False),
            patch("scripts.preflight.aws_infra.check_credentials", return_value="ok"),
            patch(
                "scripts.preflight.session_history.read_session_history",
                return_value={"status": "empty", "window_days": 2, "recent_sessions": [], "last_session": ""},
            ),
            patch(
                "scripts.preflight.recs_cache._count_recommendations_reader",
                return_value=(2, 0, 1, [{"id": "rec-001", "title": "Manual", "context_excerpt": "ctx"}]),
            ),
            patch("session_preflight._sync_ops_pull", return_value={}),
            patch(
                "scripts.preflight.context_docs.read_context_files",
                return_value={
                    "roadmap_phase": "Phase 1.5",
                    "open_decisions_count": 0,
                    "strategic_review_due": False,
                    "recommendations_count": 0,
                },
            ),
            patch("scripts.preflight.ci_rca_signals._check_ci_rca_liveness", return_value=None),
            patch("session_preflight.PREFLIGHT_REPORT", preflight_report),
            patch("builtins.print"),
        ):
            _preflight.main()

        data = json.loads(preflight_report.read_text(encoding="utf-8"))
        assert data["non_automatable_recommendations"] == 1
        assert "non_automatable_details" not in data, (
            "non_automatable_details is intentionally dropped from the slim report "
            "(Decision 73: individual rec review suspended)."
        )


class TestNonAutomatableSoftcap:
    """Tests for _check_non_automatable_softcap() and _NON_AUTOMATABLE_SOFTCAP constant."""

    def test_below_cap_returns_false(self) -> None:
        assert _preflight._check_non_automatable_softcap(249) is False

    def test_above_cap_returns_true(self) -> None:
        assert _preflight._check_non_automatable_softcap(251) is True

    def test_at_cap_returns_false(self) -> None:
        assert _preflight._check_non_automatable_softcap(250) is False

    def test_constant_is_250(self) -> None:
        assert _preflight._NON_AUTOMATABLE_SOFTCAP == 250


class TestForwardFixRecursion:
    """Tests for _check_forward_fix_recursion() -- forward_fix_recursion named verb."""

    def test_alert_set_at_threshold(self) -> None:
        rows = [{"file": "scripts/validate.py", "cnt": "3"}]
        with patch("scripts.preflight._common._make_reader") as MockReader:
            MockReader.return_value.named.return_value = rows
            result = _preflight._check_forward_fix_recursion()
        assert result is not None
        assert result["file"] == "scripts/validate.py"
        assert result["count"] == 3
        assert result["threshold"] == 3
        verb, kwargs = MockReader.return_value.named.call_args[0][0], MockReader.return_value.named.call_args.kwargs
        assert verb == "forward_fix_recursion"
        assert "since_ts" in kwargs  # the 24h cutoff is bound as a verb param

    def test_alert_none_when_no_groups(self) -> None:
        with patch("scripts.preflight._common._make_reader") as MockReader:
            MockReader.return_value.named.return_value = []
            result = _preflight._check_forward_fix_recursion()
        assert result is None

    def test_alert_none_when_reader_unavailable(self) -> None:
        with patch("scripts.preflight._common._make_reader") as MockReader:
            MockReader.return_value.named.side_effect = RuntimeError("reader down")
            result = _preflight._check_forward_fix_recursion()
        assert result is None


class TestBudgetBypassAlert:
    """Tests for _check_budget_bypass_alert() -- budget_bypass_recent named verb."""

    def test_returns_none_under_threshold(self) -> None:
        """Returns None when fewer than 3 bypass recs exist in 7 days."""
        rows = [
            {"id": "rec-001", "context": "bypass 1", "created_timestamp": "2026-05-12 10:00:00"},
            {"id": "rec-002", "context": "bypass 2", "created_timestamp": "2026-05-11 10:00:00"},
        ]
        with patch("scripts.preflight._common._make_reader") as MockReader:
            MockReader.return_value.named.return_value = rows
            result = _preflight._check_budget_bypass_alert()
        assert result is None
        MockReader.return_value.named.assert_called_once_with("budget_bypass_recent")

    def test_returns_dict_at_threshold(self) -> None:
        """Returns dict with count and entries when >= 3 bypass recs exist."""
        rows = [
            {"id": "rec-001", "context": "bypass 1", "created_timestamp": "2026-05-12 10:00:00"},
            {"id": "rec-002", "context": "bypass 2", "created_timestamp": "2026-05-11 10:00:00"},
            {"id": "rec-003", "context": "bypass 3", "created_timestamp": "2026-05-10 10:00:00"},
        ]
        with patch("scripts.preflight._common._make_reader") as MockReader:
            MockReader.return_value.named.return_value = rows
            result = _preflight._check_budget_bypass_alert()
        assert result is not None
        assert result["count"] == 3
        assert len(result["entries"]) == 3

    def test_returns_none_on_reader_failure(self) -> None:
        """Returns None (not raises) when reader raises an exception (Decision 55)."""
        with patch("scripts.preflight._common._make_reader") as MockReader:
            MockReader.return_value.named.side_effect = RuntimeError("reader unreachable")
            result = _preflight._check_budget_bypass_alert()
        assert result is None

    def test_returns_none_when_verb_returns_empty(self) -> None:
        """Returns None when the verb returns an empty row list (count 0 < 3)."""
        with patch("scripts.preflight._common._make_reader") as MockReader:
            MockReader.return_value.named.return_value = []
            result = _preflight._check_budget_bypass_alert()
        assert result is None


class TestCountRecommendationsReader:
    """_count_recommendations_reader() serves the open_recs_tally row (Decision 215)."""

    _TALLY = {"open_count": 2, "aging_count": 1, "non_automatable_count": 1}

    def test_tally_row_becomes_the_four_tuple_with_empty_details(self) -> None:
        assert _preflight._count_recommendations_reader(dict(self._TALLY)) == (2, 1, 1, [])

    def test_none_tally_is_reader_unreachable_never_a_false_zero(self) -> None:
        assert _preflight._count_recommendations_reader(None) == "reader_unreachable"

    def test_no_argument_makes_no_reader_call(self) -> None:
        with patch("scripts.preflight._common._make_reader") as MockReader:
            assert _preflight._count_recommendations_reader() == "reader_unreachable"
        MockReader.assert_not_called()

    def test_zero_tally_is_a_real_zero(self) -> None:
        tally = {"open_count": 0, "aging_count": 0, "non_automatable_count": 0}
        assert _preflight._count_recommendations_reader(tally) == (0, 0, 0, [])


class TestRecsReadStatusDegradation:
    """Verify that reader outage produces a loud recs_read_status signal (Decision 55)."""

    def test_reader_unreachable_yields_loud_degraded_signal_not_false_zero(self, tmp_path: Path) -> None:
        """reader_unreachable -> report shows recs_read_status=reader_unreachable (Decision 55 / T2.19)."""
        preflight_report = tmp_path / ".preflight-report.json"

        with (
            patch("scripts.preflight.env_git.check_venv", return_value=True),
            patch("scripts.preflight.env_git.get_git_status", return_value=("claude/test", False, [])),
            patch(
                "scripts.preflight.env_git.check_main_freshness",
                return_value={
                    "status": "ok",
                    "fetched_at": "2026-06-09T00:00:00+00:00",
                    "commits_behind": 0,
                    "commits_ahead": 0,
                    "main_files_changed_since_branch": [],
                },
            ),
            patch("scripts.preflight.aws_infra.check_terraform_pending", return_value=False),
            patch("scripts.preflight.aws_infra.check_credentials", return_value="ok"),
            patch(
                "scripts.preflight.session_history.read_session_history",
                return_value={"status": "empty", "window_days": 2, "recent_sessions": [], "last_session": ""},
            ),
            patch("scripts.preflight.recs_cache._count_recommendations_reader", return_value="reader_unreachable"),
            patch("session_preflight._sync_ops_pull", return_value={}),
            patch("scripts.preflight.priority_queue.read_priority_queue", return_value=[]),
            patch("scripts.preflight.priority_queue.print_priority_queue"),
            patch(
                "scripts.preflight.context_docs.read_context_files",
                return_value={
                    "roadmap_phase": "Phase 2",
                    "open_decisions_count": 0,
                    "strategic_review_due": False,
                    "recommendations_count": 0,
                },
            ),
            patch(
                "scripts.preflight.context_docs.check_data_quality_coverage",
                return_value={"tables_covered": 0, "checks_defined": 0, "last_run": None},
            ),
            patch("scripts.preflight.ci_rca_signals._check_ci_rca_liveness", return_value=None),
            patch("scripts.preflight.ci_rca_signals._fetch_ci_rca_recs", return_value=[]),
            patch("session_preflight.PREFLIGHT_REPORT", preflight_report),
            patch("builtins.print"),
        ):
            _preflight.main()

        data = json.loads(preflight_report.read_text(encoding="utf-8"))
        assert data.get("recs_read_status") == "reader_unreachable"
        assert data.get("open_recommendations") is None


class TestGetLatestDecisionTs:
    """_get_latest_decision_ts() reads via the decisions_max_updated verb."""

    def test_returns_ts_from_first_row(self) -> None:
        with patch("scripts.preflight._common._make_reader") as MockReader:
            MockReader.return_value.named.return_value = [{"ts": "2026-06-10T12:00:00+00:00"}]
            result = _preflight._get_latest_decision_ts()
        assert result == "2026-06-10T12:00:00+00:00"
        MockReader.assert_called_once_with(table="ops_decisions")
        MockReader.return_value.named.assert_called_once_with("decisions_max_updated")

    def test_returns_none_on_empty_rows(self) -> None:
        with patch("scripts.preflight._common._make_reader") as MockReader:
            MockReader.return_value.named.return_value = []
            assert _preflight._get_latest_decision_ts() is None

    def test_returns_none_on_empty_ts_value(self) -> None:
        with patch("scripts.preflight._common._make_reader") as MockReader:
            MockReader.return_value.named.return_value = [{"ts": ""}]
            assert _preflight._get_latest_decision_ts() is None

    def test_returns_none_on_reader_failure(self) -> None:
        with patch("scripts.preflight._common._make_reader") as MockReader:
            MockReader.return_value.named.side_effect = RuntimeError("reader down")
            assert _preflight._get_latest_decision_ts() is None
