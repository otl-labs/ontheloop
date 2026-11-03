"""Unit tests for scripts.convergence_health.escalate (rec-2709 Wave 6 package-mirror; migrated
onto scripts.rec_episode.run_episode by rec-3291 / rec-3563).

Idempotent tf_convergence_stale file/update/close escalation. Free of live dependencies: the
portal caller and open-recs rows are injected (plus a few live-path tests that patch the real
ops-portal / DuckLake-reader call sites directly).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import scripts.convergence_health as ch
from scripts.convergence_health import HealthVerdict, escalate
from scripts.convergence_health.escalate import _RESOLUTION_PENDING_CODIFICATION, _TITLE_PENDING_CODIFICATION


def test_red_backlog_trips_over_threshold_before_age() -> None:
    """Decision 190: a red record with a non-zero unapplied backlog files/updates a rec well
    below the 6h age threshold -- Decision 154 point 7 notes severity 'files nothing on its own',
    so this trigger (not assess.py's severity fix alone) is what actually fixes the incident's
    real harm (three applies refused, 2.6h, zero recs filed)."""
    calls: list[tuple[str, dict[str, Any]]] = []

    def _caller(action: str, fields: dict[str, Any]) -> Any:
        calls.append((action, fields))
        return "rec-901"

    verdict = HealthVerdict(status="red", red_age_hours=0.5, unapplied_backlog=3, severity="high")
    result = escalate(verdict, portal_caller=_caller, open_recs=[], threshold_hours=6.0)
    assert result == {"action": "file", "rec_id": "rec-901"}
    assert calls[0][0] == "file"


def test_pending_codification_selfclear_writes_closure_stamp() -> None:
    """Decision 190 / Decision 55 anti-hiding: a self-cleared pending_codification episode closes
    with a durable closure-stamp resolution, never a silent revert with no trace."""
    calls: list[tuple[str, dict[str, Any]]] = []

    def _caller(action: str, fields: dict[str, Any]) -> Any:
        calls.append((action, fields))
        return None

    existing = {
        "id": "rec-950",
        "source": "tf_convergence_stale",
        "status": "open",
        "title": _TITLE_PENDING_CODIFICATION,
    }
    cleared_verdict = HealthVerdict(status="green", red_age_hours=0.0, unapplied_backlog=0, severity="none")
    result = escalate(cleared_verdict, portal_caller=_caller, open_recs=[existing])
    assert result == {"action": "close", "rec_id": "rec-950"}
    assert calls[0][0] == "close"
    assert calls[0][1]["resolution"] == _RESOLUTION_PENDING_CODIFICATION


class TestFindOpenConvergenceStaleRec:
    """rec-3563: the previously-blind dedup lookup now resolves the already-open rec instead of
    filing a duplicate. Retained under this class name because rec-3563's acceptance probe names
    it verbatim (Decision 103) -- re-pointed at escalate()'s real behaviour now that
    find_open_convergence_stale_rec no longer exists (migrated onto
    scripts.rec_episode.find_rec's source-scoped structural read)."""

    def test_escalate_updates_existing_rec_instead_of_filing_duplicate(self) -> None:
        calls: list[tuple[str, dict[str, Any]]] = []

        def _caller(action: str, fields: dict[str, Any]) -> Any:
            calls.append((action, fields))
            return None

        existing_row = {
            "id": "rec-101",
            "source": "tf_convergence_stale",
            "status": "open",
            "title": ch._TITLE_PERSISTENTLY_RED,
        }
        verdict = HealthVerdict(status="red", red_age_hours=10.0, unapplied_backlog=0, severity="high")
        result = escalate(verdict, portal_caller=_caller, open_recs=[existing_row])
        assert result == {"action": "update", "rec_id": "rec-101"}
        assert calls[0][0] == "update"

    def test_scoped_read_never_matches_a_different_source(self) -> None:
        calls: list[tuple[str, dict[str, Any]]] = []

        def _caller(action: str, fields: dict[str, Any]) -> Any:
            calls.append((action, fields))
            return "rec-999"

        other_source_row = {"id": "rec-100", "source": "ci_rca", "status": "open"}
        verdict = HealthVerdict(status="red", red_age_hours=10.0, unapplied_backlog=0, severity="high")
        result = escalate(verdict, portal_caller=_caller, open_recs=[other_source_row])
        assert result["action"] == "file"

    def test_scripts_convergence_health_no_longer_exports_fetch_open_recs(self) -> None:
        assert "_fetch_open_recs" not in ch.__all__
        assert not hasattr(ch, "_fetch_open_recs")


class TestEscalate:
    def _make_verdict(self, red_age: float = 10.0, status: str = "red", unapplied_backlog: int = 0) -> HealthVerdict:
        return HealthVerdict(
            status=status,
            red_age_hours=red_age,
            unapplied_backlog=unapplied_backlog,
            stuck_approvals=[],
            severity="high" if red_age >= 6 else "low",
        )

    def test_files_rec_when_over_threshold_and_no_open_rec(self) -> None:
        calls: list[tuple[str, dict[str, Any]]] = []

        def _caller(action: str, fields: dict[str, Any]) -> Any:
            calls.append((action, fields))
            return "rec-999"

        verdict = self._make_verdict(red_age=10.0)
        result = escalate(verdict, portal_caller=_caller, open_recs=[])
        assert result["action"] == "file"
        assert result["rec_id"] == "rec-999"
        assert calls[0][0] == "file"
        assert calls[0][1]["source"] == "tf_convergence_stale"
        assert calls[0][1]["priority"] == "High"

    def test_updates_when_over_threshold_and_open_rec_exists(self) -> None:
        calls: list[tuple[str, dict[str, Any]]] = []

        def _caller(action: str, fields: dict[str, Any]) -> Any:
            calls.append((action, fields))
            return None

        existing = {"id": "rec-888", "source": "tf_convergence_stale", "status": "open"}
        verdict = self._make_verdict(red_age=10.0)
        result = escalate(verdict, portal_caller=_caller, open_recs=[existing])
        assert result["action"] == "update"
        assert result["rec_id"] == "rec-888"
        assert calls[0][0] == "update"

    def test_closes_when_under_threshold_and_open_rec_exists(self) -> None:
        calls: list[tuple[str, dict[str, Any]]] = []

        def _caller(action: str, fields: dict[str, Any]) -> Any:
            calls.append((action, fields))
            return None

        existing = {"id": "rec-777", "source": "tf_convergence_stale", "status": "open"}
        verdict = self._make_verdict(red_age=2.0)
        result = escalate(verdict, portal_caller=_caller, open_recs=[existing])
        assert result["action"] == "close"
        assert result["rec_id"] == "rec-777"
        assert calls[0][0] == "close"

    def test_no_action_when_under_threshold_and_no_rec(self) -> None:
        calls: list[tuple[str, dict[str, Any]]] = []

        def _caller(action: str, fields: dict[str, Any]) -> Any:
            calls.append((action, fields))
            return None

        verdict = self._make_verdict(red_age=2.0)
        result = escalate(verdict, portal_caller=_caller, open_recs=[])
        assert result["action"] == "none"
        assert not calls

    def test_no_rec_filed_when_status_is_green(self) -> None:
        calls: list[tuple[str, dict[str, Any]]] = []

        def _caller(action: str, fields: dict[str, Any]) -> Any:
            calls.append((action, fields))
            return None

        verdict = HealthVerdict(
            status="green",
            red_age_hours=0.0,
            unapplied_backlog=0,
            stuck_approvals=[],
            severity="none",
        )
        result = escalate(verdict, portal_caller=_caller, open_recs=[])
        assert result["action"] == "none"
        assert not calls

    def test_stuck_approval_triggers_escalation_below_age_threshold(self) -> None:
        calls: list[tuple[str, dict[str, Any]]] = []

        def _caller(action: str, fields: dict[str, Any]) -> Any:
            calls.append((action, fields))
            return "rec-555"

        verdict = HealthVerdict(
            status="red",
            red_age_hours=3.0,
            unapplied_backlog=0,
            stuck_approvals=[{"run_id": 1, "age_hours": 8.0, "url": "https://example.com"}],
            severity="high",
        )
        result = escalate(verdict, portal_caller=_caller, open_recs=[], threshold_hours=6.0)
        assert result["action"] == "file"

    def test_dedup_second_tick_updates_not_files(self) -> None:
        calls: list[tuple[str, dict[str, Any]]] = []

        def _caller(action: str, fields: dict[str, Any]) -> Any:
            calls.append((action, fields))
            return None

        existing = {"id": "rec-444", "source": "tf_convergence_stale", "status": "open"}
        verdict = self._make_verdict(red_age=12.0)
        result = escalate(verdict, portal_caller=_caller, open_recs=[existing])
        assert result["action"] == "update"
        assert len(calls) == 1
        assert calls[0][0] == "update"


class TestEscalateUnknownActionFallsThroughToSkipped:
    """decide()'s truth table has exactly four outcomes (file/update/close/none); this exercises
    run_episode's (and so escalate()'s) defensive fallback for anything else it might ever
    return."""

    def test_unknown_action_falls_through_to_skipped(self) -> None:
        verdict = HealthVerdict(status="red", red_age_hours=10.0, unapplied_backlog=0, severity="high")
        with patch("scripts.rec_episode.decide", return_value="bogus"):
            result = escalate(verdict, portal_caller=lambda a, f: None, open_recs=[])
        assert result == {"action": "skipped", "rec_id": None}


class TestEscalateReconcileInFlight:
    def _make_verdict(self, red_age: float = 10.0, status: str = "red") -> HealthVerdict:
        return HealthVerdict(
            status=status,
            red_age_hours=red_age,
            unapplied_backlog=0,
            stuck_approvals=[],
            severity="high" if red_age >= 6 else "low",
        )

    def test_does_not_double_file_when_reconcile_in_flight(self) -> None:
        calls: list[tuple[str, dict[str, Any]]] = []

        def _caller(action: str, fields: dict[str, Any]) -> Any:
            calls.append((action, fields))
            return "rec-999"

        verdict = self._make_verdict(red_age=10.0)
        result = escalate(verdict, portal_caller=_caller, open_recs=[], reconcile_in_flight=True)
        assert result["action"] == "skipped_suppressed"
        assert result["rec_id"] is None
        assert not calls, "must not file a rec while a Reconcile run is in-flight for this episode"

    def test_files_normally_when_reconcile_not_in_flight(self) -> None:
        calls: list[tuple[str, dict[str, Any]]] = []

        def _caller(action: str, fields: dict[str, Any]) -> Any:
            calls.append((action, fields))
            return "rec-999"

        verdict = self._make_verdict(red_age=10.0)
        result = escalate(verdict, portal_caller=_caller, open_recs=[], reconcile_in_flight=False)
        assert result["action"] == "file"
        assert calls

    def test_reconcile_in_flight_does_not_suppress_update_of_existing_rec(self) -> None:
        # Suppression applies ONLY to a fresh "file" -- an already-open rec still updates (not a
        # double-file; it's the same rec being refreshed).
        calls: list[tuple[str, dict[str, Any]]] = []

        def _caller(action: str, fields: dict[str, Any]) -> Any:
            calls.append((action, fields))
            return None

        existing = {"id": "rec-888", "source": "tf_convergence_stale", "status": "open"}
        verdict = self._make_verdict(red_age=10.0)
        result = escalate(verdict, portal_caller=_caller, open_recs=[existing], reconcile_in_flight=True)
        assert result["action"] == "update"
        assert result["rec_id"] == "rec-888"

    def test_reconcile_in_flight_does_not_suppress_close(self) -> None:
        calls: list[tuple[str, dict[str, Any]]] = []

        def _caller(action: str, fields: dict[str, Any]) -> Any:
            calls.append((action, fields))
            return None

        existing = {"id": "rec-777", "source": "tf_convergence_stale", "status": "open"}
        verdict = self._make_verdict(red_age=2.0)
        result = escalate(verdict, portal_caller=_caller, open_recs=[existing], reconcile_in_flight=True)
        assert result["action"] == "close"

    def test_reconcile_in_flight_irrelevant_when_under_threshold(self) -> None:
        verdict = self._make_verdict(red_age=2.0)
        result = escalate(verdict, portal_caller=lambda a, f: None, open_recs=[], reconcile_in_flight=True)
        assert result["action"] == "none"


class TestEscalateGreenClosesOpenRec:
    """Acceptance criterion 3: a green record with an open rec closes that rec."""

    def test_green_status_with_open_rec_closes(self) -> None:
        calls: list[tuple[str, dict[str, Any]]] = []

        def _caller(action: str, fields: dict[str, Any]) -> Any:
            calls.append((action, fields))
            return None

        existing = {"id": "rec-555", "source": "tf_convergence_stale", "status": "open"}
        verdict = HealthVerdict(status="green", red_age_hours=0.0, unapplied_backlog=0, severity="none")
        result = escalate(verdict, portal_caller=_caller, open_recs=[existing])
        assert result["action"] == "close"
        assert result["rec_id"] == "rec-555"
        assert calls[0][0] == "close"


class TestEscalateGreenStuckApproval:
    def _make_verdict(self) -> HealthVerdict:
        return HealthVerdict(
            status="green",
            red_age_hours=0.0,
            unapplied_backlog=0,
            stuck_approvals=[{"run_id": 1, "age_hours": 8.0, "url": "https://example.com"}],
            severity="high",
        )

    def test_files_rec_for_green_stuck_approval(self) -> None:
        calls: list[tuple[str, dict[str, Any]]] = []

        def _caller(action: str, fields: dict[str, Any]) -> Any:
            calls.append((action, fields))
            return "rec-701"

        result = escalate(self._make_verdict(), portal_caller=_caller, open_recs=[])
        assert result == {"action": "file", "rec_id": "rec-701"}
        assert calls[0][0] == "file"
        assert calls[0][1]["title"] == ch._TITLE_STUCK_APPROVAL
        assert "waiting on" in calls[0][1]["context"]

    def test_second_tick_updates_not_files(self) -> None:
        calls: list[tuple[str, dict[str, Any]]] = []

        def _caller(action: str, fields: dict[str, Any]) -> Any:
            calls.append((action, fields))
            return None

        existing = {"id": "rec-702", "source": "tf_convergence_stale", "status": "open", "title": ch._TITLE_STUCK_APPROVAL}
        result = escalate(self._make_verdict(), portal_caller=_caller, open_recs=[existing])
        assert result == {"action": "update", "rec_id": "rec-702"}
        assert len(calls) == 1
        assert calls[0][0] == "update"

    def test_clearing_approvals_closes_with_stuck_approval_resolution(self) -> None:
        calls: list[tuple[str, dict[str, Any]]] = []

        def _caller(action: str, fields: dict[str, Any]) -> Any:
            calls.append((action, fields))
            return None

        existing = {"id": "rec-703", "source": "tf_convergence_stale", "status": "open", "title": ch._TITLE_STUCK_APPROVAL}
        cleared_verdict = HealthVerdict(status="green", red_age_hours=0.0, unapplied_backlog=0, severity="none")
        result = escalate(cleared_verdict, portal_caller=_caller, open_recs=[existing])
        assert result == {"action": "close", "rec_id": "rec-703"}
        assert calls[0][1]["resolution"] == ch._RESOLUTION_STUCK_APPROVAL


class TestEscalateGreenStaleBacklog:
    def _make_verdict(self, record_age_hours: float) -> HealthVerdict:
        return HealthVerdict(
            status="green",
            red_age_hours=0.0,
            unapplied_backlog=2,
            severity="high" if record_age_hours >= ch.STALE_GREEN_BACKLOG_THRESHOLD_HOURS else "none",
            record_age_hours=record_age_hours,
        )

    def test_files_rec_at_or_over_threshold(self) -> None:
        calls: list[tuple[str, dict[str, Any]]] = []

        def _caller(action: str, fields: dict[str, Any]) -> Any:
            calls.append((action, fields))
            return "rec-801"

        verdict = self._make_verdict(record_age_hours=ch.STALE_GREEN_BACKLOG_THRESHOLD_HOURS)
        result = escalate(verdict, portal_caller=_caller, open_recs=[])
        assert result == {"action": "file", "rec_id": "rec-801"}
        assert calls[0][1]["title"] == ch._TITLE_STALE_GREEN_BACKLOG
        assert "backlog" in calls[0][1]["context"]

    def test_no_file_under_threshold(self) -> None:
        calls: list[tuple[str, dict[str, Any]]] = []

        def _caller(action: str, fields: dict[str, Any]) -> Any:
            calls.append((action, fields))
            return "rec-802"

        verdict = self._make_verdict(record_age_hours=ch.STALE_GREEN_BACKLOG_THRESHOLD_HOURS - 0.5)
        result = escalate(verdict, portal_caller=_caller, open_recs=[])
        assert result == {"action": "none", "rec_id": None}
        assert not calls

    def test_second_tick_updates_not_files(self) -> None:
        calls: list[tuple[str, dict[str, Any]]] = []

        def _caller(action: str, fields: dict[str, Any]) -> Any:
            calls.append((action, fields))
            return None

        existing = {
            "id": "rec-803",
            "source": "tf_convergence_stale",
            "status": "open",
            "title": ch._TITLE_STALE_GREEN_BACKLOG,
        }
        verdict = self._make_verdict(record_age_hours=ch.STALE_GREEN_BACKLOG_THRESHOLD_HOURS + 1.0)
        result = escalate(verdict, portal_caller=_caller, open_recs=[existing])
        assert result == {"action": "update", "rec_id": "rec-803"}
        assert len(calls) == 1

    def test_backlog_drained_closes_with_stale_backlog_resolution(self) -> None:
        calls: list[tuple[str, dict[str, Any]]] = []

        def _caller(action: str, fields: dict[str, Any]) -> Any:
            calls.append((action, fields))
            return None

        existing = {
            "id": "rec-804",
            "source": "tf_convergence_stale",
            "status": "open",
            "title": ch._TITLE_STALE_GREEN_BACKLOG,
        }
        drained_verdict = HealthVerdict(
            status="green",
            red_age_hours=0.0,
            unapplied_backlog=0,
            severity="none",
            record_age_hours=ch.STALE_GREEN_BACKLOG_THRESHOLD_HOURS + 5.0,
        )
        result = escalate(drained_verdict, portal_caller=_caller, open_recs=[existing])
        assert result == {"action": "close", "rec_id": "rec-804"}
        assert calls[0][1]["resolution"] == ch._RESOLUTION_STALE_GREEN_BACKLOG


class TestEscalatePendingCodification:
    """Decision 190: the fourth, orthogonal escalation condition -- a benign code-behind-state
    delta tracked independently of status."""

    def _marker(self) -> dict[str, Any]:
        return {"first_seen": "2026-09-13T00:00:00Z", "last_seen": "2026-09-13T00:00:00Z", "run_url": "https://x/1"}

    def test_files_rec_for_pending_codification(self) -> None:
        calls: list[tuple[str, dict[str, Any]]] = []

        def _caller(action: str, fields: dict[str, Any]) -> Any:
            calls.append((action, fields))
            return "rec-960"

        verdict = HealthVerdict(
            status="green", red_age_hours=0.0, unapplied_backlog=0, severity="none", pending_codification=self._marker()
        )
        result = escalate(verdict, portal_caller=_caller, open_recs=[])
        assert result == {"action": "file", "rec_id": "rec-960"}
        assert calls[0][1]["title"] == _TITLE_PENDING_CODIFICATION
        assert "resource_changes" in calls[0][1]["context"]

    def test_second_tick_updates_not_files(self) -> None:
        calls: list[tuple[str, dict[str, Any]]] = []

        def _caller(action: str, fields: dict[str, Any]) -> Any:
            calls.append((action, fields))
            return None

        existing = {
            "id": "rec-961",
            "source": "tf_convergence_stale",
            "status": "open",
            "title": _TITLE_PENDING_CODIFICATION,
        }
        verdict = HealthVerdict(
            status="green", red_age_hours=0.0, unapplied_backlog=0, severity="none", pending_codification=self._marker()
        )
        result = escalate(verdict, portal_caller=_caller, open_recs=[existing])
        assert result == {"action": "update", "rec_id": "rec-961"}
        assert len(calls) == 1

    def test_pending_codification_takes_priority_over_stale_green_backlog(self) -> None:
        """The marker is checked BEFORE stale_green_backlog so it is never shadowed by a
        co-occurring status-keyed condition."""
        calls: list[tuple[str, dict[str, Any]]] = []

        def _caller(action: str, fields: dict[str, Any]) -> Any:
            calls.append((action, fields))
            return "rec-962"

        verdict = HealthVerdict(
            status="green",
            red_age_hours=0.0,
            unapplied_backlog=5,
            severity="high",
            record_age_hours=ch.STALE_GREEN_BACKLOG_THRESHOLD_HOURS + 1.0,
            pending_codification=self._marker(),
        )
        result = escalate(verdict, portal_caller=_caller, open_recs=[])
        assert result["action"] == "file"
        assert calls[0][1]["title"] == _TITLE_PENDING_CODIFICATION


class TestAcceptanceLint:
    """VP step 1 / AC1: every _build_rec_fields acceptance must pass the REAL linter, no mocking."""

    def test_escalate_acceptance_lint_valid(self) -> None:
        from scripts.convergence_health.escalate import _build_rec_fields
        from scripts.executor.acceptance_lint import lint_acceptance_command

        verdict = HealthVerdict(
            status="red",
            red_age_hours=10.0,
            unapplied_backlog=2,
            stuck_approvals=[{"run_id": 1, "age_hours": 8.0, "url": "https://example.com"}],
            severity="high",
            record_age_hours=ch.STALE_GREEN_BACKLOG_THRESHOLD_HOURS,
        )
        for condition in ("persistently_red", "stale_green_backlog", "stuck_approval", "pending_codification"):
            fields = _build_rec_fields(verdict, condition)
            assert lint_acceptance_command(fields["acceptance"]) == (True, None), condition


class TestEscalatePortalValidators:
    """VP step 2 / AC2: escalate() files (never raises) on a persistently-red high-severity
    verdict, with the portal's REAL row-rule gate applied to the built fields."""

    def test_persistent_red_files_rec(self) -> None:
        from scripts.ops_portal.risk_scoring import _derive_computed_fields
        from scripts.ops_portal.write_validators import gate_write

        captured: dict[str, Any] = {}

        def _caller(action: str, fields: dict[str, Any]) -> Any:
            captured["fields"] = fields
            return "rec-1234"

        verdict = HealthVerdict(
            status="red",
            red_age_hours=10.0,
            unapplied_backlog=0,
            stuck_approvals=[],
            severity="high",
        )
        result = escalate(verdict, portal_caller=_caller, open_recs=[])
        assert result == {"action": "file", "rec_id": "rec-1234"}
        # Mirrors file_rec()'s own pre-validation step: it derives risk/automatable/
        # created_timestamp in-place before running the row-rule gate.
        _derive_computed_fields(captured["fields"])
        gate_write("ops_recommendations", captured["fields"], insert=True)


class TestEscalateLiveFetchAndPortal:
    """Cover the production paths: live open-recs fetch and the real ops-portal calls."""

    def _verdict(self, status: str = "red", red_age: float = 10.0) -> HealthVerdict:
        return HealthVerdict(status=status, red_age_hours=red_age, unapplied_backlog=0, severity="high")

    def test_escalate_fetches_via_scoped_current_state_read_when_not_injected(self) -> None:
        reader = MagicMock()
        reader.current_state.return_value = []
        with (
            patch("src.common.ducklake_reader_client.make_reader", return_value=reader) as mk,
            patch("scripts.ops_data_portal.file_rec", return_value="rec-live") as fr,
        ):
            result = escalate(self._verdict())
        mk.assert_called_once_with(profile=None)
        reader.current_state.assert_called_once_with("ops_recommendations", row_filter="source = 'tf_convergence_stale'")
        fr.assert_called_once()
        assert result == {"action": "file", "rec_id": "rec-live"}

    def test_escalate_update_uses_real_portal_when_no_caller(self) -> None:
        existing = {"id": "rec-200", "source": "tf_convergence_stale", "status": "open"}
        with patch("scripts.ops_data_portal.update_rec") as ur:
            result = escalate(self._verdict(), open_recs=[existing])
        ur.assert_called_once()
        assert result == {"action": "update", "rec_id": "rec-200"}

    def test_escalate_close_uses_real_portal_when_no_caller(self) -> None:
        existing = {"id": "rec-300", "source": "tf_convergence_stale", "status": "open"}
        with patch("scripts.ops_data_portal.update_rec") as ur:
            result = escalate(self._verdict(status="green", red_age=0.0), open_recs=[existing])
        ur.assert_called_once()
        assert result == {"action": "close", "rec_id": "rec-300"}

    def test_reader_unreachable_raises_instead_of_degrading_to_empty(self) -> None:
        """rec-3291 / rec-3563: an unreachable reader must raise, never be mistaken for 'no open
        rec' -- a deliberate divergence from two of the three other current_state call sites in
        this repo that fail open."""
        reader = MagicMock()
        reader.current_state.side_effect = RuntimeError("ducklake_reader unreachable")
        with (
            patch("src.common.ducklake_reader_client.make_reader", return_value=reader),
            pytest.raises(RuntimeError, match="ducklake_reader unreachable"),
        ):
            escalate(self._verdict())
