"""Tests for scripts/preflight/alerts.py's VTS-20 summary half (audit validate-test-suite-4df4d48).

_derive_budget_breach_recent / _check_budget_breach_summary mirror the pre-existing
_derive_budget_bypass_recent / _check_budget_bypass_alert pattern in this same module -- see
TestForwardFixRecursion / TestBudgetBypassAlert in tests/session/preflight/test_recs_cache.py for
that established convention. Imported directly from scripts.preflight.alerts (not via the
scripts.session.preflight facade): the plan deliberately omits a new facade re-export for these
two names to avoid touching tests/test_session_preflight_decomposition.py's symbol-list.
"""

from datetime import datetime, timezone
from unittest.mock import patch

from scripts.preflight import alerts


class TestDominantPhaseFromContext:
    """Tests for _dominant_phase_from_context() -- parses the marker _file_budget_breach_rec
    writes into a budget_breach rec's context."""

    def test_extracts_phase(self) -> None:
        context = "Branch: agent/test. Dominant phase: pytest_diff. Diff manifest (1 files): x."
        assert alerts._dominant_phase_from_context(context) == "pytest_diff"

    def test_unknown_when_marker_absent(self) -> None:
        assert alerts._dominant_phase_from_context("no marker in this context string") == "unknown"

    def test_unknown_on_empty_string(self) -> None:
        assert alerts._dominant_phase_from_context("") == "unknown"


class TestDeriveBudgetBreachRecent:
    """Tests for _derive_budget_breach_recent() -- client-side budget_breach_recent verb
    equivalent (7-day window, newest first)."""

    _NOW = datetime(2026, 5, 20, tzinfo=timezone.utc)

    def test_filters_to_source_budget_breach_only(self) -> None:
        rows = [
            {"source": "budget_bypass", "created_timestamp": "2026-05-19 10:00:00", "context": "Dominant phase: lint. "},
            {"source": "budget_breach", "created_timestamp": "2026-05-19 10:00:00", "context": "Dominant phase: lint. "},
        ]
        result = alerts._derive_budget_breach_recent(rows, now=self._NOW)
        assert len(result) == 1
        assert result[0]["source"] == "budget_breach"

    def test_excludes_rows_older_than_7_days(self) -> None:
        rows = [
            {"source": "budget_breach", "created_timestamp": "2026-05-19 10:00:00", "context": "Dominant phase: lint. "},
            {"source": "budget_breach", "created_timestamp": "2026-05-01 10:00:00", "context": "Dominant phase: lint. "},
        ]
        result = alerts._derive_budget_breach_recent(rows, now=self._NOW)
        assert len(result) == 1
        assert result[0]["created_timestamp"] == "2026-05-19 10:00:00"

    def test_newest_first_order(self) -> None:
        rows = [
            {"source": "budget_breach", "created_timestamp": "2026-05-17 10:00:00", "context": "Dominant phase: a. "},
            {"source": "budget_breach", "created_timestamp": "2026-05-19 10:00:00", "context": "Dominant phase: b. "},
            {"source": "budget_breach", "created_timestamp": "2026-05-18 10:00:00", "context": "Dominant phase: c. "},
        ]
        result = alerts._derive_budget_breach_recent(rows, now=self._NOW)
        assert [r["created_timestamp"] for r in result] == [
            "2026-05-19 10:00:00",
            "2026-05-18 10:00:00",
            "2026-05-17 10:00:00",
        ]

    def test_empty_rows_returns_empty(self) -> None:
        assert alerts._derive_budget_breach_recent([], now=self._NOW) == []


class TestCheckBudgetBreachSummary:
    """Tests for _check_budget_breach_summary() -- VTS-20 summary half.

    supplied rows -> derive (zero reader call); None -> None; sentinel (omitted) -> reader path,
    best-effort (Decision 88 warm-cache serving, mirrors _check_budget_bypass_alert).
    """

    def test_returns_none_on_empty_list(self) -> None:
        with patch("scripts.preflight.alerts._derive_budget_breach_recent", return_value=[]):
            assert alerts._check_budget_breach_summary([{"source": "budget_breach"}]) is None

    def test_returns_none_when_rows_is_none(self) -> None:
        assert alerts._check_budget_breach_summary(None) is None

    def test_returns_count_and_by_phase_histogram(self) -> None:
        derived_rows = [
            {"context": "... Dominant phase: pytest_diff. ..."},
            {"context": "... Dominant phase: pytest_diff. ..."},
            {"context": "... Dominant phase: lint. ..."},
        ]
        with patch("scripts.preflight.alerts._derive_budget_breach_recent", return_value=derived_rows) as mock_derive:
            result = alerts._check_budget_breach_summary([{"source": "budget_breach"}])

        mock_derive.assert_called_once()
        assert result == {"count": 3, "by_phase": {"pytest_diff": 2, "lint": 1}}

    def test_unknown_phase_when_context_missing_marker(self) -> None:
        derived_rows = [{"context": "no marker here"}]
        with patch("scripts.preflight.alerts._derive_budget_breach_recent", return_value=derived_rows):
            result = alerts._check_budget_breach_summary([{"source": "budget_breach"}])
        assert result == {"count": 1, "by_phase": {"unknown": 1}}

    def test_issues_no_reader_call_when_rows_supplied(self) -> None:
        derived_rows = [{"context": "Dominant phase: lint. "}]
        with (
            patch("scripts.preflight.alerts._derive_budget_breach_recent", return_value=derived_rows),
            patch("scripts.preflight._common._make_reader") as mock_reader,
        ):
            result = alerts._check_budget_breach_summary([{"source": "budget_breach"}])

        assert result is not None
        mock_reader.assert_not_called()

    def test_sentinel_default_makes_no_reader_call(self) -> None:
        with patch("scripts.preflight._common._make_reader") as MockReader:
            assert alerts._check_budget_breach_summary() is None
        MockReader.assert_not_called()

    def test_signal_rows_are_summarised_by_phase(self) -> None:
        from datetime import datetime, timezone

        rows = [
            {"source": "budget_breach", "context": "Dominant phase: lint. ", "created_timestamp": datetime.now(timezone.utc)}
        ]
        assert alerts._check_budget_breach_summary(rows) == {"count": 1, "by_phase": {"lint": 1}}

    def test_returns_none_when_reader_returns_empty(self) -> None:
        with patch("scripts.preflight._common._make_reader") as MockReader:
            MockReader.return_value.named.return_value = []
            result = alerts._check_budget_breach_summary()
        assert result is None
