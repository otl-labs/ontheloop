"""ci_rca_gauges-surface tests: CI-RCA probe abstention gauge (compute/escalate/print/report),
CI-RCA telemetry section (recurrence distribution, warn-mode-reject rate, dispute/backlog/
override counts, report JSON), CI-RCA back-validation section (derive/print/report)
(rec-2709 Wave 4).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

boto3 = pytest.importorskip("boto3")

from scripts.preflight import ci_rca_gauges  # noqa: E402
from scripts.preflight.recs_signals_source import RecsSignalInputs  # noqa: E402
from tests.fixtures.session_preflight_module import preflight as _preflight  # noqa: E402

# TestAbstentionLabelAccuracy lives in test_ci_rca_gauges_abstention_label.py -- this file's
# module-level `boto3 = pytest.importorskip("boto3")` guard (line 16 above) would skip it on the
# fast pr-validate tier (no boto3 there), which the interactive VP-replay gate reads as a hard
# failure rather than a benign skip.


class TestAbstentionGauge:
    """T1.13 c12(i): _compute_ci_rca_abstention / _escalate_ci_rca_probe_health / preflight report JSON."""

    def test_compute_returns_none_when_cache_unavailable(self) -> None:
        assert _preflight._compute_ci_rca_abstention(None) is None

    def test_compute_delegates_to_ci_rca_probe_health(self) -> None:
        with patch("scripts.ci_rca.probe_health.compute_abstention_rate", return_value=(2, 5, 0.4)) as mock_compute:
            gauge = _preflight._compute_ci_rca_abstention([{"id": "rec-1"}], window_days=14)
        mock_compute.assert_called_once_with([{"id": "rec-1"}], window_days=14)
        assert gauge == {
            "low_or_undetermined_count": 2,
            "total_count": 5,
            "rate": 0.4,
            "window_days": 14,
        }

    def test_escalate_skipped_when_creds_not_ok(self) -> None:
        with patch("scripts.ci_rca.probe_health.escalate") as mock_escalate:
            result = _preflight._escalate_ci_rca_probe_health(
                "unavailable",
                [{"id": "rec-1"}],
                {"low_or_undetermined_count": 1, "total_count": 1, "rate": 1.0, "window_days": 14},
            )
        assert result is None
        mock_escalate.assert_not_called()

    def test_escalate_skipped_when_cache_unavailable(self) -> None:
        with patch("scripts.ci_rca.probe_health.escalate") as mock_escalate:
            result = _preflight._escalate_ci_rca_probe_health(
                "ok", None, {"low_or_undetermined_count": 1, "total_count": 1, "rate": 1.0, "window_days": 14}
            )
        assert result is None
        mock_escalate.assert_not_called()

    def test_escalate_skipped_when_gauge_is_none(self) -> None:
        with patch("scripts.ci_rca.probe_health.escalate") as mock_escalate:
            result = _preflight._escalate_ci_rca_probe_health("ok", [{"id": "rec-1"}], None)
        assert result is None
        mock_escalate.assert_not_called()

    def test_escalate_invoked_on_warm_cache_path(self) -> None:
        cache_rows = [
            {"id": "rec-1", "status": "open"},
            {"id": "rec-2", "status": "closed"},
        ]
        gauge = {"low_or_undetermined_count": 3, "total_count": 6, "rate": 0.5, "window_days": 14}
        with patch(
            "scripts.ci_rca.probe_health.escalate", return_value={"action": "file", "rec_id": "rec-9"}
        ) as mock_escalate:
            result = _preflight._escalate_ci_rca_probe_health("ok", cache_rows, gauge)
        assert result == {"action": "file", "rec_id": "rec-9"}
        mock_escalate.assert_called_once()
        args, kwargs = mock_escalate.call_args
        assert args[0] == 3
        assert args[1] == 6
        assert args[2] == 0.5
        # open_recs filtered to status='open' only, from the injected cache -- no reader call.
        assert args[3] == [{"id": "rec-1", "status": "open"}]

    def test_escalate_transient_swallowed(self) -> None:
        """VP step 8 / AC4: a classified-transient reader outage (Decision 155) is still
        swallowed to a [WARN], not raised."""
        gauge = {"low_or_undetermined_count": 1, "total_count": 1, "rate": 1.0, "window_days": 14}
        transient = RuntimeError("ducklake_writer file_ops ops_recommendations failed (HTTP 503): backend unavailable")
        with patch("scripts.ci_rca.probe_health.escalate", side_effect=transient):
            result = _preflight._escalate_ci_rca_probe_health("ok", [], gauge)
        assert result is None

    def test_escalate_non_transient_reraises(self) -> None:
        """VP step 8 / AC4: replaces the old test_escalate_failure_is_non_fatal, which asserted
        a non-transient RuntimeError("portal down") WAS swallowed -- the exact inverse of AC4.
        is_reader_unavailable(RuntimeError("portal down")) is False, so after the narrowing this
        propagates instead of degrading to a [WARN]."""
        gauge = {"low_or_undetermined_count": 1, "total_count": 1, "rate": 1.0, "window_days": 14}
        with patch("scripts.ci_rca.probe_health.escalate", side_effect=RuntimeError("portal down")):
            with pytest.raises(RuntimeError, match="portal down"):
                _preflight._escalate_ci_rca_probe_health("ok", [], gauge)

    def test_print_gauge_line_format(self, capsys: pytest.CaptureFixture) -> None:
        gauge = {"low_or_undetermined_count": 2, "total_count": 8, "rate": 0.25, "window_days": 14}
        _preflight.print_ci_rca_abstention_gauge(gauge)
        out = capsys.readouterr().out
        assert "CI-RCA agent rca_confidence abstention (last 14d): 2/8 low-confidence/undetermined (25%)" in out

    def test_print_gauge_noop_when_none(self, capsys: pytest.CaptureFixture) -> None:
        _preflight.print_ci_rca_abstention_gauge(None)
        out = capsys.readouterr().out
        assert out == ""

    def test_main_report_contains_abstention_gauge_fields(self, tmp_path: Path) -> None:
        """The gauge fields appear in the preflight report JSON, computed from the warm cache."""
        preflight_report = tmp_path / ".preflight-report.json"
        cache_rows = [
            {
                "id": "rec-1",
                "source": "ci_rca",
                "status": "open",
                "created_timestamp": datetime.now(timezone.utc).isoformat(),
                "context_v2_json": "",
            }
        ]
        recs_inputs = RecsSignalInputs("ok", {"open_count": 0, "aging_count": 0, "non_automatable_count": 0}, cache_rows)
        with (
            patch("scripts.preflight.env_git.check_venv", return_value=True),
            patch("scripts.preflight.env_git.get_git_status", return_value=("main", False, [])),
            patch("scripts.preflight.aws_infra.check_terraform_pending", return_value=False),
            patch("scripts.preflight.aws_infra.check_credentials", return_value="ok"),
            patch(
                "scripts.preflight.session_history.read_session_history",
                return_value={"status": "empty", "window_days": 2, "recent_sessions": [], "last_session": ""},
            ),
            patch("scripts.preflight.priority_queue.read_priority_queue", return_value=[]),
            patch("session_preflight._sync_ops_pull", return_value={}),
            patch("session_preflight.fetch_recs_signal_inputs", return_value=recs_inputs),
            patch(
                "scripts.preflight.context_docs.read_context_files",
                return_value={
                    "roadmap_phase": "Phase 2",
                    "open_decisions_count": 0,
                    "strategic_review_due": False,
                    "recommendations_count": 0,
                },
            ),
            patch("scripts.preflight.ci_rca_signals._check_ci_rca_liveness", return_value=None),
            patch("scripts.ci_rca.probe_health.escalate", return_value={"action": "none", "rec_id": None}) as mock_escalate,
            patch("session_preflight.PREFLIGHT_REPORT", preflight_report),
            patch("builtins.print"),
        ):
            _preflight.main()

        data = json.loads(preflight_report.read_text(encoding="utf-8"))
        assert "ci_rca_abstention_gauge" in data
        gauge = data["ci_rca_abstention_gauge"]
        assert gauge["total_count"] == 1
        assert gauge["low_or_undetermined_count"] == 0
        assert data["ci_rca_probe_health_escalation"] == {"action": "none", "rec_id": None}
        mock_escalate.assert_called_once()


class TestEscapeModeGauge:
    """T1.13:c9 repair: _compute_ci_rca_escape_mode_abstention / print_ci_rca_escape_mode_abstention_gauge
    -- a SEPARATE gauge from TestAbstentionGauge above (which covers the rca_confidence gauge)."""

    def test_compute_returns_none_when_cache_unavailable(self) -> None:
        assert _preflight._compute_ci_rca_escape_mode_abstention(None) is None

    def test_compute_delegates_to_ci_rca_probe_health(self) -> None:
        with patch(
            "scripts.ci_rca.probe_health.compute_escape_mode_abstention_rate", return_value=(3, 8, 0.375)
        ) as mock_compute:
            gauge = _preflight._compute_ci_rca_escape_mode_abstention([{"id": "rec-1"}], window_days=14)
        mock_compute.assert_called_once_with([{"id": "rec-1"}], window_days=14)
        assert gauge == {
            "undetermined_count": 3,
            "total_count": 8,
            "rate": 0.375,
            "window_days": 14,
        }

    def test_print_gauge_line_format(self, capsys: pytest.CaptureFixture) -> None:
        gauge = {"undetermined_count": 2, "total_count": 8, "rate": 0.25, "window_days": 14}
        _preflight.print_ci_rca_escape_mode_abstention_gauge(gauge)
        out = capsys.readouterr().out
        assert "CI-RCA probe escape_mode abstention (last 14d): 2/8 detection_gap.escape_mode=undetermined (25%)" in out

    def test_print_gauge_noop_when_none(self, capsys: pytest.CaptureFixture) -> None:
        _preflight.print_ci_rca_escape_mode_abstention_gauge(None)
        out = capsys.readouterr().out
        assert out == ""

    def test_main_report_contains_escape_mode_gauge_at_a_different_rate_than_confidence_gauge(self, tmp_path: Path) -> None:
        """VP step 6: the report carries BOTH gauge keys, computed from the SAME cache row but
        reporting DIFFERENT rates -- the single strongest disproof that the new gauge is a rename
        of the old one."""
        preflight_report = tmp_path / ".preflight-report.json"
        cache_rows = [
            {
                "id": "rec-1",
                "source": "ci_rca",
                "status": "open",
                "created_timestamp": datetime.now(timezone.utc).isoformat(),
                "context_v2_json": json.dumps({"rca_confidence": "high", "detection_gap": {"escape_mode": "undetermined"}}),
            }
        ]
        recs_inputs = RecsSignalInputs("ok", {"open_count": 0, "aging_count": 0, "non_automatable_count": 0}, cache_rows)
        with (
            patch("scripts.preflight.env_git.check_venv", return_value=True),
            patch("scripts.preflight.env_git.get_git_status", return_value=("main", False, [])),
            patch("scripts.preflight.aws_infra.check_terraform_pending", return_value=False),
            patch("scripts.preflight.aws_infra.check_credentials", return_value="ok"),
            patch(
                "scripts.preflight.session_history.read_session_history",
                return_value={"status": "empty", "window_days": 2, "recent_sessions": [], "last_session": ""},
            ),
            patch("scripts.preflight.priority_queue.read_priority_queue", return_value=[]),
            patch("session_preflight._sync_ops_pull", return_value={}),
            patch("session_preflight.fetch_recs_signal_inputs", return_value=recs_inputs),
            patch(
                "scripts.preflight.context_docs.read_context_files",
                return_value={
                    "roadmap_phase": "Phase 2",
                    "open_decisions_count": 0,
                    "strategic_review_due": False,
                    "recommendations_count": 0,
                },
            ),
            patch("scripts.preflight.ci_rca_signals._check_ci_rca_liveness", return_value=None),
            patch("scripts.ci_rca.probe_health.escalate", return_value={"action": "none", "rec_id": None}),
            patch("session_preflight.PREFLIGHT_REPORT", preflight_report),
            patch("builtins.print"),
        ):
            _preflight.main()

        data = json.loads(preflight_report.read_text(encoding="utf-8"))
        assert "ci_rca_escape_mode_gauge" in data
        escape_gauge = data["ci_rca_escape_mode_gauge"]
        confidence_gauge = data["ci_rca_abstention_gauge"]
        assert escape_gauge["total_count"] == 1
        assert escape_gauge["undetermined_count"] == 1
        assert confidence_gauge["low_or_undetermined_count"] == 0
        assert escape_gauge["rate"] != confidence_gauge["rate"]


class TestCiRcaTelemetrySection:
    """T1.13 c1/c3: _compute_ci_rca_telemetry / print_ci_rca_telemetry / preflight report JSON.

    Re-grounded from the originally-scoped query-engine design to warm-cache-derived surfacing
    (Decision 88 zero-egress).
    """

    NOW = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)

    def _row(
        self,
        source: str = "ci_rca",
        created_days_ago: float = 1,
        recurrence_class: str | None = None,
        warn_mode_reject: bool = False,
        upload_status: str | None = None,
        override: bool = False,
    ) -> dict:
        import json as _json
        from datetime import timedelta as _timedelta

        created = (self.NOW - _timedelta(days=created_days_ago)).isoformat()
        ctx: dict = {}
        if recurrence_class is not None:
            ctx["recurrence_class"] = recurrence_class
        if warn_mode_reject:
            ctx["warn_mode_reject"] = {"reasons": ["schema_deficiency"], "mode_at_write": "warn"}
        if upload_status is not None:
            ctx["evidence_bundle_ref"] = {"upload_status": upload_status}
        if override:
            ctx["why_chain_terminus_override"] = {"reason": "test override"}
        return {
            "source": source,
            "created_timestamp": created,
            "context_v2_json": _json.dumps(ctx) if ctx else "",
        }

    def test_returns_none_when_cache_unavailable(self) -> None:
        assert _preflight._compute_ci_rca_telemetry(None) is None

    def test_recurrence_class_distribution(self) -> None:
        rows = [
            self._row(recurrence_class="novel"),
            self._row(recurrence_class="novel"),
            self._row(recurrence_class="instance_of_known_pattern"),
            self._row(recurrence_class="regression"),
        ]
        telemetry = _preflight._compute_ci_rca_telemetry(rows, window_days=7, now=self.NOW)
        assert telemetry["recurrence_class_distribution"] == {"novel": 2, "instance_of_known_pattern": 1, "regression": 1}
        assert telemetry["ci_rca_total"] == 4

    def test_warn_mode_reject_rate_computed_from_markers(self) -> None:
        rows = [
            self._row(warn_mode_reject=True),
            self._row(warn_mode_reject=False),
            self._row(warn_mode_reject=False),
            self._row(warn_mode_reject=False),
        ]
        telemetry = _preflight._compute_ci_rca_telemetry(rows, window_days=7, now=self.NOW)
        assert telemetry["warn_mode_reject_count"] == 1
        assert telemetry["ci_rca_total"] == 4
        assert telemetry["warn_mode_reject_rate"] == pytest.approx(0.25)

    def test_threshold_note_over_25_percent(self) -> None:
        rows = [self._row(warn_mode_reject=True), self._row(warn_mode_reject=True), self._row(warn_mode_reject=False)]
        telemetry = _preflight._compute_ci_rca_telemetry(rows, window_days=7, now=self.NOW)
        assert telemetry["warn_mode_reject_rate"] == pytest.approx(2 / 3)
        assert telemetry["warn_mode_reject_threshold_note"] == "enforcement may need tuning"

    def test_threshold_note_at_or_under_5_percent(self) -> None:
        rows = [self._row(warn_mode_reject=False) for _ in range(20)]
        telemetry = _preflight._compute_ci_rca_telemetry(rows, window_days=7, now=self.NOW)
        assert telemetry["warn_mode_reject_rate"] == 0.0
        assert telemetry["warn_mode_reject_threshold_note"] == "Phase-4 promotion gate met"

    def test_threshold_note_none_in_middle_band(self) -> None:
        rows = [self._row(warn_mode_reject=True)] + [self._row(warn_mode_reject=False) for _ in range(9)]
        telemetry = _preflight._compute_ci_rca_telemetry(rows, window_days=7, now=self.NOW)
        assert telemetry["warn_mode_reject_rate"] == pytest.approx(0.1)
        assert telemetry["warn_mode_reject_threshold_note"] is None

    def test_dispute_path_traffic_counted(self) -> None:
        rows = [self._row(source="ci_rca_evidence_dispute"), self._row(source="ci_rca_evidence_dispute"), self._row()]
        telemetry = _preflight._compute_ci_rca_telemetry(rows, window_days=7, now=self.NOW)
        assert telemetry["dispute_count"] == 2
        assert telemetry["ci_rca_total"] == 1

    def test_bundle_upload_backlog_counted(self) -> None:
        rows = [
            self._row(upload_status="ok"),
            self._row(upload_status="upload_failed"),
            self._row(upload_status=None),
        ]
        telemetry = _preflight._compute_ci_rca_telemetry(rows, window_days=7, now=self.NOW)
        assert telemetry["bundle_upload_backlog"] == 1

    def test_override_usage_counted(self) -> None:
        rows = [self._row(override=True), self._row(override=False)]
        telemetry = _preflight._compute_ci_rca_telemetry(rows, window_days=7, now=self.NOW)
        assert telemetry["why_chain_terminus_override_count"] == 1

    def test_window_filtering_excludes_old_rows(self) -> None:
        rows = [self._row(created_days_ago=1), self._row(created_days_ago=60)]
        telemetry = _preflight._compute_ci_rca_telemetry(rows, window_days=7, now=self.NOW)
        assert telemetry["ci_rca_total"] == 1

    def test_zero_total_guard(self) -> None:
        telemetry = _preflight._compute_ci_rca_telemetry([], window_days=7, now=self.NOW)
        assert telemetry["ci_rca_total"] == 0
        assert telemetry["warn_mode_reject_rate"] == 0.0

    def test_print_noop_body_when_none(self, capsys: pytest.CaptureFixture) -> None:
        _preflight.print_ci_rca_telemetry(None)
        out = capsys.readouterr().out
        assert "CI-RCA Telemetry" in out
        assert "unavailable" in out

    def test_print_renders_all_fields(self, capsys: pytest.CaptureFixture) -> None:
        telemetry = _preflight._compute_ci_rca_telemetry(
            [self._row(recurrence_class="novel", warn_mode_reject=True)], window_days=7, now=self.NOW
        )
        _preflight.print_ci_rca_telemetry(telemetry)
        out = capsys.readouterr().out
        assert "CI-RCA Telemetry (last 7d)" in out
        assert "novel=1" in out
        assert "Warn-mode reject rate: 1/1 (100%)" in out
        assert "enforcement may need tuning" in out
        assert "Dispute-path traffic: 0" in out
        assert "Bundle-upload backlog: 0" in out
        assert "why_chain_terminus_override usage: 0" in out

    def test_main_report_contains_ci_rca_telemetry_key(self) -> None:
        """ci_rca_telemetry lands in the report JSON, computed with zero new reader egress."""

        def _boom(*args, **kwargs):
            raise AssertionError("_compute_ci_rca_telemetry must not construct a DuckLake reader")

        rows = [self._row(recurrence_class="novel")]
        with patch("src.common.ducklake_reader_client.make_reader", side_effect=_boom):
            telemetry = _preflight._compute_ci_rca_telemetry(rows, window_days=7, now=self.NOW)
        assert telemetry is not None
        assert telemetry["ci_rca_total"] == 1


class TestCiRcaBackValidationSection:
    """T1.13 c12(iii): _derive_ci_rca_back_validation / print_ci_rca_back_validation / report JSON."""

    def test_returns_none_when_cache_unavailable(self) -> None:
        assert _preflight._derive_ci_rca_back_validation(None) is None

    def test_delegates_to_ci_rca_back_validation_module(self) -> None:
        flagged = [
            {
                "new_rec_id": "rec-2",
                "prior_rec_id": "rec-1",
                "file": "scripts/validate.py",
                "preventive_action_excerpt": "Fix it.",
            }
        ]
        with patch("scripts.ci_rca.back_validation.find_preventive_regressions", return_value=flagged) as mock_find:
            result = _preflight._derive_ci_rca_back_validation([{"id": "rec-1"}])
        mock_find.assert_called_once_with([{"id": "rec-1"}])
        assert result == flagged

    def test_empty_on_no_matches(self) -> None:
        with patch("scripts.ci_rca.back_validation.find_preventive_regressions", return_value=[]):
            result = _preflight._derive_ci_rca_back_validation([])
        assert result == []

    def test_print_none_body(self, capsys: pytest.CaptureFixture) -> None:
        _preflight.print_ci_rca_back_validation([])
        out = capsys.readouterr().out
        assert "CI-RCA Back-Validation" in out
        assert "none" in out

    def test_print_renders_flags(self, capsys: pytest.CaptureFixture) -> None:
        flagged = [
            {
                "new_rec_id": "rec-2",
                "prior_rec_id": "rec-1",
                "file": "scripts/validate.py",
                "preventive_action_excerpt": "Fix it.",
            }
        ]
        _preflight.print_ci_rca_back_validation(flagged)
        out = capsys.readouterr().out
        assert "rec-2" in out
        assert "rec-1" in out
        assert "scripts/validate.py" in out
        assert "CANDIDATE" in out

    def test_no_reader_call_in_derivation(self) -> None:
        """Decision-88 zero-egress guard: the back-validation derive never builds a reader."""

        def _boom(*args, **kwargs):
            raise AssertionError("_derive_ci_rca_back_validation must not construct a DuckLake reader")

        with patch("src.common.ducklake_reader_client.make_reader", side_effect=_boom):
            result = _preflight._derive_ci_rca_back_validation([])
        assert result == []

    def test_back_validation_banner_is_grade_aware(self, capsys: pytest.CaptureFixture) -> None:
        """Decision 186: the printed section carries the per-flag grade and the resolved
        artifact token, replacing the blanket '[CANDIDATE] file-only match' banner."""
        flagged = [
            {
                "new_rec_id": "rec-2",
                "prior_rec_id": "rec-1",
                "file": "scripts/validate.py",
                "preventive_action_excerpt": "Fix it.",
                "closure_artifact": "shard:some-shard",
                "artifact_status": "present",
                "grade": "VERIFIED-PRESENT",
            }
        ]
        _preflight.print_ci_rca_back_validation(flagged)
        out = capsys.readouterr().out
        assert "VERIFIED-PRESENT" in out
        assert "shard:some-shard" in out
        assert "file-only match -- treat as a candidate" not in out

    def test_open_escape_recs_are_surfaced(self, capsys: pytest.CaptureFixture) -> None:
        """Decision 186 D-B1 mitigation: any OPEN escape-classified rec is listed in this same
        section, making a refusal legible before the 30-day sweep waivers it."""
        _preflight.print_ci_rca_back_validation([], open_escape_recs=[{"id": "rec-9001", "file": "scripts/foo.py"}])
        out = capsys.readouterr().out
        assert "rec-9001" in out
        assert "scripts/foo.py" in out

    def test_open_escape_recs_omitted_renders_no_extra_section(self, capsys: pytest.CaptureFixture) -> None:
        """The existing single-arg call site (session/preflight.py) is unaffected -- the second
        parameter defaults to None and prints nothing extra."""
        _preflight.print_ci_rca_back_validation([])
        out = capsys.readouterr().out
        assert "Open escape-classified recs" not in out

    def test_derive_open_escape_recs_returns_none_when_cache_unavailable(self) -> None:
        assert ci_rca_gauges._derive_open_escape_ci_rca_recs(None) is None

    def test_derive_open_escape_recs_filters_to_open_escape_classified_ci_rca_only(self) -> None:
        rows = [
            {
                "id": "rec-1",
                "source": "ci_rca",
                "status": "open",
                "file": "a.py",
                "context_v2_json": json.dumps({"escape_class": "no-edge"}),
            },
            {  # closed -- excluded
                "id": "rec-2",
                "source": "ci_rca",
                "status": "closed",
                "file": "b.py",
                "context_v2_json": json.dumps({"escape_class": "no-edge"}),
            },
            {  # not escape-classified -- excluded
                "id": "rec-3",
                "source": "ci_rca",
                "status": "open",
                "file": "c.py",
                "context_v2_json": json.dumps({}),
            },
            {  # not source=ci_rca -- excluded
                "id": "rec-4",
                "source": "planning",
                "status": "open",
                "file": "d.py",
                "context_v2_json": json.dumps({"escape_class": "no-edge"}),
            },
            {  # no context at all -- excluded, never raises
                "id": "rec-5",
                "source": "ci_rca",
                "status": "open",
                "file": "e.py",
                "context_v2_json": None,
            },
            {  # malformed context -- excluded, never raises
                "id": "rec-6",
                "source": "ci_rca",
                "status": "open",
                "file": "f.py",
                "context_v2_json": "not-json",
            },
        ]
        result = ci_rca_gauges._derive_open_escape_ci_rca_recs(rows)
        assert [r["id"] for r in result] == ["rec-1"]

    def test_derive_open_escape_recs_no_reader_call(self) -> None:
        """Decision-88 zero-egress guard extends to the new derive: never builds a reader."""

        def _boom(*args, **kwargs):
            raise AssertionError("_derive_open_escape_ci_rca_recs must not construct a DuckLake reader")

        with patch("src.common.ducklake_reader_client.make_reader", side_effect=_boom):
            result = ci_rca_gauges._derive_open_escape_ci_rca_recs([])
        assert result == []
