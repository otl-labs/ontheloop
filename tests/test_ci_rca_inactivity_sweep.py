"""Unit tests for scripts.ci_rca.inactivity_sweep (ci-rca-identity-lifecycle, Decision 142).

The pure filter/close helpers are tested directly (no live DuckLake reader or portal writes);
run_sweep() and main() are tested with both list_open_ci_rca_recs and update_rec injected.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from scripts.ci_rca.inactivity_sweep import close_inactive_recs, find_inactive_recs, main, run_sweep
from tests.fixtures.ops_rec_rows import reader_rec_row


def _row(rec_id: str, created: str, last_seen: str | None = None):
    ctx = {"last_seen": last_seen} if last_seen else {}
    return {"id": rec_id, "status": "open", "created_timestamp": created, "_ctx": ctx}


class TestFindInactiveRecs:
    def test_filters_to_inactive_only(self):
        now = datetime.now(timezone.utc)
        old = (now - timedelta(days=45)).isoformat()
        recent = (now - timedelta(days=2)).isoformat()
        rows = [_row("rec-1", old, last_seen=old), _row("rec-2", recent, last_seen=recent)]
        inactive = find_inactive_recs(rows, now=now)
        assert [r["id"] for r in inactive] == ["rec-1"]

    def test_empty_rows_yields_empty(self):
        assert find_inactive_recs([]) == []

    def test_missing_ctx_key_falls_back_to_created(self):
        now = datetime.now(timezone.utc)
        old = (now - timedelta(days=45)).isoformat()
        rows = [{"id": "rec-1", "created_timestamp": old}]
        inactive = find_inactive_recs(rows, now=now)
        assert [r["id"] for r in inactive] == ["rec-1"]


class TestCloseInactiveRecs:
    def test_closes_via_portal_with_recorded_proof(self):
        now = datetime.now(timezone.utc)
        old = (now - timedelta(days=45)).isoformat()
        rows = [_row("rec-1", old, last_seen=old)]
        with patch("scripts.ops_data_portal.update_rec") as mock_update:
            closed = close_inactive_recs(rows)
        assert closed == ["rec-1"]
        mock_update.assert_called_once()
        call_args, call_kwargs = mock_update.call_args
        assert call_args[0] == "rec-1"
        assert call_args[1]["status"] == "closed"
        assert "stale_no_recurrence" in call_args[1]["resolution"]

    def test_dry_run_closes_nothing(self):
        now = datetime.now(timezone.utc)
        old = (now - timedelta(days=45)).isoformat()
        rows = [_row("rec-1", old, last_seen=old)]
        with patch("scripts.ops_data_portal.update_rec") as mock_update:
            closed = close_inactive_recs(rows, dry_run=True)
        assert closed == []
        mock_update.assert_not_called()

    def test_multiple_rows_all_closed(self):
        now = datetime.now(timezone.utc)
        old = (now - timedelta(days=45)).isoformat()
        rows = [_row("rec-1", old, last_seen=old), _row("rec-2", old, last_seen=old)]
        with patch("scripts.ops_data_portal.update_rec"):
            closed = close_inactive_recs(rows)
        assert closed == ["rec-1", "rec-2"]

    def test_sweep_supplies_stale_no_recurrence_waiver(self):
        """Decision 186: the sweep passes a well-formed stale_no_recurrence waiver through
        update_rec, so an escape-classified inactive rec still closes rather than wedging this
        scheduled workflow (the gate's exemption is necessary and kept)."""
        now = datetime.now(timezone.utc)
        old = (now - timedelta(days=45)).isoformat()
        rows = [_row("rec-1", old, last_seen=old)]
        with patch("scripts.ops_data_portal.update_rec") as mock_update:
            close_inactive_recs(rows)
        _, call_kwargs = mock_update.call_args
        assert call_kwargs["closure_waiver_category"] == "stale_no_recurrence"
        assert call_kwargs["closure_waiver_reason"]
        assert "stale_no_recurrence" in call_kwargs["closure_waiver_reason"]

    def test_sweep_closes_escape_classified_rec_end_to_end(self, tmp_path):
        """The scheduled sweep still closes an escape-classified inactive rec THROUGH THE REAL
        closure gate (update_rec unmocked): the programmatic waiver actually satisfies Decision
        186, it is not merely threaded and never checked."""
        now = datetime.now(timezone.utc)
        old = (now - timedelta(days=45)).isoformat()
        rows = [_row("rec-1", old, last_seen=old)]
        existing = reader_rec_row(id="rec-1", context_v2_json=json.dumps({"escape_class": "no-edge"}))
        recs_file = tmp_path / "recs.jsonl"
        with (
            patch("scripts.ops_data_portal._fetch_rec_from_reader", return_value=existing),
            patch("scripts.ops_data_portal._ducklake_write", return_value={"ok": True}) as mock_write,
            patch("scripts.ops_data_portal._sync_table"),
            patch("scripts.ops_data_portal.RECS_JSONL", recs_file),
        ):
            closed = close_inactive_recs(rows)
        assert closed == ["rec-1"]
        mock_write.assert_called_once()


class TestRunSweep:
    def test_run_sweep_reports_counts(self):
        now = datetime.now(timezone.utc)
        old = (now - timedelta(days=45)).isoformat()
        recent = (now - timedelta(days=2)).isoformat()
        rows = [_row("rec-1", old, last_seen=old), _row("rec-2", recent, last_seen=recent)]
        with (
            patch("scripts.ci_rca.inactivity_sweep.list_open_ci_rca_recs", return_value=rows),
            patch("scripts.ops_data_portal.update_rec"),
        ):
            result = run_sweep()
        assert result["open_count"] == 2
        assert result["inactive_count"] == 1
        assert result["closed"] == ["rec-1"]
        assert result["dry_run"] is False

    def test_run_sweep_dry_run(self):
        now = datetime.now(timezone.utc)
        old = (now - timedelta(days=45)).isoformat()
        rows = [_row("rec-1", old, last_seen=old)]
        with (
            patch("scripts.ci_rca.inactivity_sweep.list_open_ci_rca_recs", return_value=rows),
            patch("scripts.ops_data_portal.update_rec") as mock_update,
        ):
            result = run_sweep(dry_run=True)
        assert result["closed"] == []
        assert result["dry_run"] is True
        mock_update.assert_not_called()

    def test_run_sweep_passes_profile_through(self):
        with patch("scripts.ci_rca.inactivity_sweep.list_open_ci_rca_recs", return_value=[]) as mock_list:
            run_sweep(profile="custom_profile")
        mock_list.assert_called_once_with(profile="custom_profile")


class TestMain:
    def test_main_prints_json_report(self, capsys):
        with patch("scripts.ci_rca.inactivity_sweep.run_sweep", return_value={"open_count": 0, "closed": []}):
            rc = main([])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out == {"open_count": 0, "closed": []}

    def test_main_dry_run_flag_passed_through(self):
        with patch("scripts.ci_rca.inactivity_sweep.run_sweep", return_value={}) as mock_run:
            main(["--dry-run"])
        mock_run.assert_called_once_with(profile=None, dry_run=True)

    def test_main_profile_flag_passed_through(self):
        with patch("scripts.ci_rca.inactivity_sweep.run_sweep", return_value={}) as mock_run:
            main(["--profile", "agent_platform"])
        mock_run.assert_called_once_with(profile="agent_platform", dry_run=False)


class TestListOpenCiRcaRecsIntegration:
    """Smoke-test the real list_open_ci_rca_recs import path used by run_sweep's default call."""

    def test_list_open_ci_rca_recs_reachable_and_mockable(self):
        reader = MagicMock()
        reader.current_state.return_value = []
        with patch("src.common.ducklake_reader_client.make_reader", return_value=reader):
            with patch("scripts.ops_data_portal.update_rec"):
                result = run_sweep()
        assert result["open_count"] == 0
