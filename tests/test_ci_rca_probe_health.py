"""Unit tests for scripts.ci_rca.probe_health.

All tests are free of live AWS, network, and DuckLake-reader dependencies:
open_recs is always injected (never fetched), and the portal caller is injected.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from scripts.ci_rca.probe_health import (
    ABSTENTION_MIN_SAMPLE,
    ABSTENTION_RATE_THRESHOLD,
    _build_context,
    _build_rec_fields,
    _parse_ts_utc,
    _row_ts,
    assert_clear_exit_code,
    compute_abstention_rate,
    compute_escape_mode_abstention_rate,
    escalate,
    escalation_action,
    find_open_probe_health_rec,
    main,
)
from scripts.executor.acceptance_lint import lint_acceptance_command
from scripts.preflight.recs_signals_source import RecsSignalInputs

NOW = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)


def _rec(
    source: str,
    created_days_ago: float,
    rca_confidence: str | None = None,
    status: str = "open",
    escape_mode: str | None = None,
) -> dict:
    ts = NOW.replace(hour=0) - __import__("datetime").timedelta(days=created_days_ago)
    ctx: dict = {}
    if rca_confidence is not None:
        ctx["rca_confidence"] = rca_confidence
    if escape_mode is not None:
        ctx["detection_gap"] = {"escape_mode": escape_mode}
    return {
        "id": f"rec-{hash((source, created_days_ago, rca_confidence, escape_mode)) % 10000}",
        "source": source,
        "status": status,
        "created_timestamp": ts.isoformat(),
        "context_v2_json": json.dumps(ctx) if ctx else "",
    }


# ---------------------------------------------------------------------------
# _parse_ts_utc / _row_ts (pre-existing gaps, now in-diff since probe_health.py is touched)
# ---------------------------------------------------------------------------


class TestParseTsUtc:
    def test_parses_z_suffix_iso(self) -> None:
        dt = _parse_ts_utc("2026-01-01T00:00:00Z")
        assert dt is not None
        assert dt.tzinfo is not None

    def test_parses_naive_date_only_format_gets_utc_tzinfo(self) -> None:
        dt = _parse_ts_utc("2026-01-01")
        assert dt is not None
        assert dt.tzinfo == timezone.utc

    def test_unparseable_timestamp_returns_none(self) -> None:
        assert _parse_ts_utc("not-a-timestamp-at-all") is None

    def test_empty_string_returns_none(self) -> None:
        assert _parse_ts_utc("") is None


class TestRowTs:
    def test_missing_field_returns_none(self) -> None:
        assert _row_ts({}) is None

    def test_datetime_instance_field(self) -> None:
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert _row_ts({"created_timestamp": dt}) == dt

    def test_datetime_instance_field_naive_gets_utc(self) -> None:
        dt = datetime(2026, 1, 1)
        result = _row_ts({"created_timestamp": dt})
        assert result is not None
        assert result.tzinfo == timezone.utc

    def test_object_with_isoformat_parsed(self) -> None:
        class _FakeDate:
            def isoformat(self) -> str:
                return "2026-01-01T00:00:00"

        result = _row_ts({"created_timestamp": _FakeDate()})
        assert result is not None
        assert result.tzinfo == timezone.utc

    def test_object_with_isoformat_that_fails_to_parse_returns_none(self) -> None:
        class _BadDate:
            def isoformat(self) -> str:
                return "not-a-real-date"

        assert _row_ts({"created_timestamp": _BadDate()}) is None

    def test_string_field_delegates_to_parse_ts_utc(self) -> None:
        result = _row_ts({"created_timestamp": "2026-01-01T00:00:00Z"})
        assert result is not None


# ---------------------------------------------------------------------------
# compute_abstention_rate
# ---------------------------------------------------------------------------


class TestComputeAbstentionRateDefaultNow:
    def test_default_now_used_when_not_provided(self) -> None:
        """compute_abstention_rate([]) with no `now` kwarg exercises the datetime.now() default."""
        undetermined, total, rate = compute_abstention_rate([])
        assert (undetermined, total, rate) == (0, 0, 0.0)


class TestComputeAbstentionRate:
    def test_zero_total_guard(self) -> None:
        undetermined, total, rate = compute_abstention_rate([], window_days=14, now=NOW)
        assert (undetermined, total, rate) == (0, 0, 0.0)

    def test_counts_undetermined_within_window(self) -> None:
        rows = [
            _rec("ci_rca", 1, rca_confidence="undetermined"),
            _rec("ci_rca", 2, rca_confidence="high"),
            _rec("ci_rca", 3, rca_confidence="undetermined"),
            _rec("ci_rca", 5, rca_confidence=None),
        ]
        undetermined, total, rate = compute_abstention_rate(rows, window_days=14, now=NOW)
        assert undetermined == 2
        assert total == 4
        assert rate == pytest.approx(0.5)

    def test_window_filtering_excludes_old_rows(self) -> None:
        rows = [
            _rec("ci_rca", 1, rca_confidence="undetermined"),
            _rec("ci_rca", 30, rca_confidence="undetermined"),  # outside 14d window
        ]
        undetermined, total, rate = compute_abstention_rate(rows, window_days=14, now=NOW)
        assert total == 1
        assert undetermined == 1

    def test_ignores_non_ci_rca_source(self) -> None:
        rows = [
            _rec("ci_rca_probe_health", 1, rca_confidence="undetermined"),
            _rec("manual", 1, rca_confidence="undetermined"),
        ]
        undetermined, total, rate = compute_abstention_rate(rows, window_days=14, now=NOW)
        assert (undetermined, total, rate) == (0, 0, 0.0)

    def test_malformed_context_v2_json_treated_as_not_undetermined(self) -> None:
        rows = [
            {
                "source": "ci_rca",
                "status": "open",
                "created_timestamp": NOW.isoformat(),
                "context_v2_json": "{not valid json",
            }
        ]
        undetermined, total, rate = compute_abstention_rate(rows, window_days=14, now=NOW)
        assert total == 1
        assert undetermined == 0


# ---------------------------------------------------------------------------
# compute_escape_mode_abstention_rate (T1.13:c9 repair -- a SEPARATE gauge from
# compute_abstention_rate above; reads detection_gap.escape_mode, never rca_confidence)
# ---------------------------------------------------------------------------


class TestComputeEscapeModeAbstentionRate:
    def test_zero_total_guard(self) -> None:
        undetermined, total, rate = compute_escape_mode_abstention_rate([], window_days=14, now=NOW)
        assert (undetermined, total, rate) == (0, 0, 0.0)

    def test_counts_escape_mode_undetermined_within_window(self) -> None:
        rows = [
            _rec("ci_rca", 1, escape_mode="undetermined"),
            _rec("ci_rca", 2, escape_mode="tier_misplaced"),
            _rec("ci_rca", 3, escape_mode="undetermined"),
            _rec("ci_rca", 5, escape_mode=None),
        ]
        undetermined, total, rate = compute_escape_mode_abstention_rate(rows, window_days=14, now=NOW)
        assert undetermined == 2
        assert total == 4
        assert rate == pytest.approx(0.5)

    def test_window_filtering_excludes_old_rows(self) -> None:
        rows = [
            _rec("ci_rca", 1, escape_mode="undetermined"),
            _rec("ci_rca", 30, escape_mode="undetermined"),  # outside 14d window
        ]
        undetermined, total, rate = compute_escape_mode_abstention_rate(rows, window_days=14, now=NOW)
        assert total == 1
        assert undetermined == 1

    def test_ignores_non_ci_rca_source(self) -> None:
        rows = [
            _rec("ci_rca_probe_health", 1, escape_mode="undetermined"),
            _rec("manual", 1, escape_mode="undetermined"),
        ]
        undetermined, total, rate = compute_escape_mode_abstention_rate(rows, window_days=14, now=NOW)
        assert (undetermined, total, rate) == (0, 0, 0.0)

    def test_malformed_context_v2_json_treated_as_not_undetermined(self) -> None:
        rows = [
            {
                "source": "ci_rca",
                "status": "open",
                "created_timestamp": NOW.isoformat(),
                "context_v2_json": "{not valid json",
            }
        ]
        undetermined, total, rate = compute_escape_mode_abstention_rate(rows, window_days=14, now=NOW)
        assert total == 1
        assert undetermined == 0

    def test_never_reads_rca_confidence(self) -> None:
        """Load-bearing discriminator: a row with rca_confidence='high' (the OLD gauge scores
        this 0%) but escape_mode='undetermined' (the NEW gauge scores this 100%) -- proves the
        new gauge is pointed at detection_gap.escape_mode, never a rename of the old one."""
        rows = [_rec("ci_rca", 1, rca_confidence="high", escape_mode="undetermined")]
        old = compute_abstention_rate(rows, window_days=14, now=NOW)
        new = compute_escape_mode_abstention_rate(rows, window_days=14, now=NOW)
        assert old == (0, 1, 0.0)
        assert new == (1, 1, 1.0)


# ---------------------------------------------------------------------------
# escalation_action truth table (mirrors convergence_health.escalation_action)
# ---------------------------------------------------------------------------


class TestEscalationAction:
    def test_file_when_over_threshold_and_no_open_rec(self) -> None:
        assert escalation_action(over_threshold=True, open_rec_exists=False) == "file"

    def test_update_when_over_threshold_and_open_rec_exists(self) -> None:
        assert escalation_action(over_threshold=True, open_rec_exists=True) == "update"

    def test_close_when_under_threshold_and_open_rec_exists(self) -> None:
        assert escalation_action(over_threshold=False, open_rec_exists=True) == "close"

    def test_none_when_under_threshold_and_no_open_rec(self) -> None:
        assert escalation_action(over_threshold=False, open_rec_exists=False) == "none"


# ---------------------------------------------------------------------------
# find_open_probe_health_rec
# ---------------------------------------------------------------------------


class TestFindOpenProbeHealthRec:
    def test_finds_matching_open_rec(self) -> None:
        rows = [
            {"id": "rec-1", "source": "ci_rca", "status": "open"},
            {"id": "rec-2", "source": "ci_rca_probe_health", "status": "open"},
        ]
        found = find_open_probe_health_rec(rows)
        assert found is not None
        assert found["id"] == "rec-2"

    def test_ignores_closed_probe_health_rec(self) -> None:
        rows = [{"id": "rec-2", "source": "ci_rca_probe_health", "status": "closed"}]
        assert find_open_probe_health_rec(rows) is None

    def test_returns_none_when_absent(self) -> None:
        assert find_open_probe_health_rec([]) is None


# ---------------------------------------------------------------------------
# escalate() dispatch -- dedup, no reader construction
# ---------------------------------------------------------------------------


class TestEscalate:
    def test_files_when_over_threshold_and_no_open_rec(self) -> None:
        portal_caller = MagicMock(return_value="rec-9001")
        result = escalate(
            undetermined_count=5,
            total_count=10,
            rate=0.5,
            open_recs=[],
            portal_caller=portal_caller,
        )
        assert result == {"action": "file", "rec_id": "rec-9001"}
        portal_caller.assert_called_once()
        assert portal_caller.call_args[0][0] == "file"
        fields = portal_caller.call_args[0][1]
        assert fields["source"] == "ci_rca_probe_health"
        assert fields["status"] == "open"

    def test_dedup_updates_instead_of_filing_when_open_rec_exists(self) -> None:
        portal_caller = MagicMock()
        open_recs = [{"id": "rec-777", "source": "ci_rca_probe_health", "status": "open"}]
        result = escalate(
            undetermined_count=6,
            total_count=10,
            rate=0.6,
            open_recs=open_recs,
            portal_caller=portal_caller,
        )
        assert result == {"action": "update", "rec_id": "rec-777"}
        portal_caller.assert_called_once()
        assert portal_caller.call_args[0][0] == "update"

    def test_closes_when_under_threshold_and_open_rec_exists(self) -> None:
        portal_caller = MagicMock()
        open_recs = [{"id": "rec-777", "source": "ci_rca_probe_health", "status": "open"}]
        result = escalate(
            undetermined_count=1,
            total_count=20,
            rate=0.05,
            open_recs=open_recs,
            portal_caller=portal_caller,
        )
        assert result == {"action": "close", "rec_id": "rec-777"}
        portal_caller.assert_called_once()
        assert portal_caller.call_args[0][0] == "close"

    def test_none_when_under_threshold_and_no_open_rec(self) -> None:
        portal_caller = MagicMock()
        result = escalate(
            undetermined_count=0,
            total_count=20,
            rate=0.0,
            open_recs=[],
            portal_caller=portal_caller,
        )
        assert result == {"action": "none", "rec_id": None}
        portal_caller.assert_not_called()

    def test_min_sample_guard_suppresses_escalation_on_small_totals(self) -> None:
        """A 100% abstention rate on a single sample must not escalate (min_sample guard)."""
        portal_caller = MagicMock()
        result = escalate(
            undetermined_count=1,
            total_count=1,
            rate=1.0,
            open_recs=[],
            portal_caller=portal_caller,
            min_sample=ABSTENTION_MIN_SAMPLE,
        )
        assert result == {"action": "none", "rec_id": None}
        portal_caller.assert_not_called()

    def test_no_duplicate_filing_when_called_twice_with_same_open_rec_state(self) -> None:
        """Calling escalate() again with the now-filed rec injected dedupes to 'update', not a second 'file'."""
        portal_caller = MagicMock(return_value="rec-9002")
        first = escalate(undetermined_count=5, total_count=10, rate=0.5, open_recs=[], portal_caller=portal_caller)
        assert first["action"] == "file"

        second_open_recs = [{"id": first["rec_id"], "source": "ci_rca_probe_health", "status": "open"}]
        second = escalate(
            undetermined_count=6,
            total_count=10,
            rate=0.6,
            open_recs=second_open_recs,
            portal_caller=portal_caller,
        )
        assert second["action"] == "update"
        assert second["rec_id"] == first["rec_id"]
        assert portal_caller.call_count == 2

    def test_default_portal_caller_uses_file_rec(self) -> None:
        with patch("scripts.ops_data_portal.file_rec", return_value="rec-9003") as mock_file_rec:
            result = escalate(undetermined_count=5, total_count=10, rate=0.5, open_recs=[])
        assert result == {"action": "file", "rec_id": "rec-9003"}
        mock_file_rec.assert_called_once()

    def test_default_portal_caller_uses_update_rec_for_update(self) -> None:
        existing = [{"id": "rec-777", "source": "ci_rca_probe_health", "status": "open"}]
        with patch("scripts.ops_data_portal.update_rec") as mock_update_rec:
            result = escalate(undetermined_count=6, total_count=10, rate=0.6, open_recs=existing)
        assert result == {"action": "update", "rec_id": "rec-777"}
        mock_update_rec.assert_called_once()
        assert mock_update_rec.call_args[0][0] == "rec-777"

    def test_default_portal_caller_uses_update_rec_for_close(self) -> None:
        existing = [{"id": "rec-777", "source": "ci_rca_probe_health", "status": "open"}]
        with patch("scripts.ops_data_portal.update_rec") as mock_update_rec:
            result = escalate(undetermined_count=1, total_count=20, rate=0.05, open_recs=existing)
        assert result == {"action": "close", "rec_id": "rec-777"}
        mock_update_rec.assert_called_once()
        assert mock_update_rec.call_args[0][0] == "rec-777"
        assert mock_update_rec.call_args[0][1]["status"] == "closed"

    def test_no_reader_constructed_in_read_path(self) -> None:
        """Critical divergence from convergence_health: escalate() never builds a DuckLake reader.

        Patches make_reader to raise; escalate() must still run correctly from the injected
        open_recs list without ever touching the reader (Decision 88 zero-egress claim).
        """

        def _boom(*args, **kwargs):
            raise AssertionError("escalate() must not construct a DuckLake reader")

        with patch("src.common.ducklake_reader_client.make_reader", side_effect=_boom):
            portal_caller = MagicMock(return_value="rec-9004")
            result = escalate(
                undetermined_count=5,
                total_count=10,
                rate=0.5,
                open_recs=[{"id": "rec-1", "source": "ci_rca", "status": "open"}],
                portal_caller=portal_caller,
            )
        assert result == {"action": "file", "rec_id": "rec-9004"}

    def test_close_records_rate_as_proof(self) -> None:
        portal_caller = MagicMock()
        open_recs = [{"id": "rec-777", "source": "ci_rca_probe_health", "status": "open"}]
        escalate(
            undetermined_count=1,
            total_count=20,
            rate=0.05,
            open_recs=open_recs,
            portal_caller=portal_caller,
        )
        updates = portal_caller.call_args[0][1]
        assert "5%" in updates["resolution"] or "0.05" in updates["resolution"] or "5.0%" in updates["resolution"]
        assert updates["status"] == "closed"

    def test_unknown_action_falls_through_to_skipped(self) -> None:
        """escalation_action's truth table has exactly four outcomes (file/update/close/none);
        this exercises escalate()'s defensive fallback for anything else it might ever return."""
        with patch("scripts.ci_rca.probe_health.escalation_action", return_value="bogus"):
            result = escalate(undetermined_count=1, total_count=1, rate=1.0, open_recs=[])
        assert result == {"action": "skipped", "rec_id": None}

    def test_threshold_boundary_over_threshold_when_rate_equals_threshold(self) -> None:
        portal_caller = MagicMock(return_value="rec-9005")
        result = escalate(
            undetermined_count=3,
            total_count=10,
            rate=ABSTENTION_RATE_THRESHOLD,
            open_recs=[],
            portal_caller=portal_caller,
        )
        assert result["action"] == "file"


# ---------------------------------------------------------------------------
# PLAN-ci-rca-abstention-and-citation AC4/VP3: the gauge counts rca_confidence in {low,
# undetermined}, and the auto-filed rec's prose describes that widened set truthfully.
# ---------------------------------------------------------------------------


class TestAbstentionGaugeWidened:
    def test_low_confidence_row_contributes_to_count_and_rate(self) -> None:
        rows = [
            _rec("ci_rca", 1, rca_confidence="low"),
            _rec("ci_rca", 2, rca_confidence="high"),
        ]
        undetermined, total, rate = compute_abstention_rate(rows, window_days=14, now=NOW)
        assert undetermined == 1
        assert total == 2
        assert rate == pytest.approx(0.5)

    def test_low_and_undetermined_both_contribute(self) -> None:
        rows = [
            _rec("ci_rca", 1, rca_confidence="low"),
            _rec("ci_rca", 2, rca_confidence="undetermined"),
            _rec("ci_rca", 3, rca_confidence="medium"),
        ]
        undetermined, total, rate = compute_abstention_rate(rows, window_days=14, now=NOW)
        assert undetermined == 2
        assert total == 3

    def test_build_context_does_not_claim_probe_abstained(self) -> None:
        context = _build_context(5, 10, 0.5, 14)
        assert "abstained" not in context.lower()

    def test_build_context_does_not_point_at_evidence_py(self) -> None:
        context = _build_context(5, 10, 0.5, 14)
        assert "scripts/ci_rca/evidence.py" not in context

    def test_build_context_describes_self_rated_confidence(self) -> None:
        context = _build_context(5, 10, 0.5, 14)
        assert "low" in context.lower()
        assert "undetermined" in context.lower()

    def test_build_rec_fields_file_not_evidence_py(self) -> None:
        fields = _build_rec_fields(5, 10, 0.5, 14)
        assert fields["file"] != "scripts/ci_rca/evidence.py"

    def test_build_rec_fields_title_does_not_claim_abstention(self) -> None:
        fields = _build_rec_fields(5, 10, 0.5, 14)
        assert "abstention" not in fields["title"].lower() and "abstained" not in fields["title"].lower()

    def test_filed_rec_context_and_title_travel_together(self) -> None:
        """The auto-filed rec (via escalate) carries the same non-abstention-claiming prose."""
        portal_caller = MagicMock(return_value="rec-9010")
        escalate(
            undetermined_count=5,
            total_count=10,
            rate=0.5,
            open_recs=[],
            portal_caller=portal_caller,
        )
        fields = portal_caller.call_args[0][1]
        assert "abstained" not in fields["context"].lower()
        assert "scripts/ci_rca/evidence.py" not in fields["context"]
        assert fields["file"] != "scripts/ci_rca/evidence.py"


class TestAcceptanceLint:
    """VP step 1 / AC1: the builder's acceptance must pass the REAL linter, no mocking."""

    def test_probe_health_acceptance_lint_valid(self) -> None:
        fields = _build_rec_fields(5, 10, 0.5, 14)
        assert lint_acceptance_command(fields["acceptance"]) == (True, None)


class TestAssertClearCli:
    """VP step 6 / AC5: --assert-clear inverts correctly, reading only injected rows."""

    def test_assert_clear_exit_codes(self) -> None:
        clear_rows = [_rec("ci_rca", 1, rca_confidence="high") for _ in range(ABSTENTION_MIN_SAMPLE)]
        over_rows = [_rec("ci_rca", 1, rca_confidence="undetermined") for _ in range(ABSTENTION_MIN_SAMPLE)]
        assert assert_clear_exit_code(clear_rows, now=NOW) == 0
        assert assert_clear_exit_code(over_rows, now=NOW) != 0


class TestMain:
    def test_no_flag_prints_help_and_returns_2(self, capsys) -> None:
        assert main([]) == 2
        assert "usage:" in capsys.readouterr().out


class TestAssertClearReaderBacked:
    """--assert-clear reads signal rows via fetch_recs_signal_inputs, fails closed, never opens the cache file."""

    _FETCH = "scripts.preflight.recs_signals_source.fetch_recs_signal_inputs"

    @staticmethod
    def _inputs(status: str, rows: list[dict] | None, error: str | None = None) -> RecsSignalInputs:
        return RecsSignalInputs(status, {"open_count": 0} if rows is not None else None, rows, error)

    def _undetermined_rows(self) -> list[dict]:
        now = datetime.now(timezone.utc).isoformat()
        ctx = json.dumps({"rca_confidence": "undetermined"})
        return [
            {"source": "ci_rca", "status": "open", "created_timestamp": now, "context_v2_json": ctx}
            for _ in range(ABSTENTION_MIN_SAMPLE)
        ]

    def test_exits_1_when_signal_inputs_unavailable(self, capsys) -> None:
        with patch(self._FETCH, return_value=self._inputs("unavailable", None, "ReaderInvokeError: down")):
            assert main(["--assert-clear"]) == 1
        assert "unavailable" in capsys.readouterr().err

    def test_exits_nonzero_while_abstention_holds(self) -> None:
        with patch(self._FETCH, return_value=self._inputs("ok", self._undetermined_rows())):
            assert main(["--assert-clear"]) != 0

    def test_exits_0_when_clear(self) -> None:
        with patch(self._FETCH, return_value=self._inputs("ok", [])):
            assert main(["--assert-clear"]) == 0

    def test_never_opens_the_cache_file(self, tmp_path) -> None:
        stale = tmp_path / "recs.jsonl"
        stale.write_text(json.dumps(self._undetermined_rows()[0]) + "\n", encoding="utf-8")
        with (
            patch("scripts.executor.jsonl_store.RECS_JSONL", stale),
            patch(self._FETCH, return_value=self._inputs("ok", [])),
        ):
            assert main(["--assert-clear"]) == 0


class TestEscapeModeRowAndDefaultNow:
    def test_row_escape_mode_reads_detection_gap(self) -> None:
        from scripts.ci_rca.probe_health import _row_escape_mode

        row = {"context_v2_json": json.dumps({"detection_gap": {"escape_mode": "undetermined"}})}
        assert _row_escape_mode(row) == "undetermined"
        assert _row_escape_mode({"context_v2_json": json.dumps({"detection_gap": "x"})}) is None

    def test_escape_mode_rate_default_now(self) -> None:
        assert compute_escape_mode_abstention_rate([]) == (0, 0, 0.0)
