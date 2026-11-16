"""Unit tests for scripts.convergence_health.__main__ (rec-2709 Wave 6 package-mirror).

CLI dispatch: python -m scripts.convergence_health
[--ducklake-drift|--prod-drift|--budget-ingest [--dry-run]].
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

# boto3 is imported at MODULE scope even though the tests reference it only via
# patch("boto3.Session") strings. This makes the file's heavy-dep requirement visible to the
# fast tier's cheap `--collect-only` pass so pr-validate defers it PROACTIVELY to the full
# post-merge tier, instead of catching it REACTIVELY -- which re-runs the entire changed-test set
# a second time after a runtime ModuleNotFoundError and roughly doubles the pytest cost. boto3 is
# deliberately excluded from requirements-fast.txt; the full tier runs this file. See
# scripts/checks/_scaffolding.py::partition_changed_tests_by_collectability.
import boto3  # noqa: F401
import pytest

from scripts.convergence_health import (
    HealthVerdict,
    main,
    main_budget_ingest,
    main_ducklake_drift,
    main_liveness_probe,
    main_prod_drift,
    main_sensor_liveness,
)
from scripts.convergence_health.__main__ import _dispatch


class TestMain:
    def _verdict(self, status: str = "green") -> HealthVerdict:
        return HealthVerdict(status=status, red_age_hours=0.0, unapplied_backlog=0, severity="none")

    def test_main_happy_path_returns_zero(self) -> None:
        with (
            patch("boto3.Session"),
            patch("scripts.convergence_health.__main__.read_convergence_record", return_value={}),
            patch("scripts.convergence_health.__main__.find_stuck_gated_approvals", return_value=[]),
            patch("scripts.convergence_health.__main__.assess_health", return_value=self._verdict("green")),
            patch("scripts.convergence_health.__main__.escalate", return_value={"action": "none", "rec_id": None}) as esc,
        ):
            rc = main(profile="agent_platform")
        assert rc == 0
        esc.assert_called_once()

    def test_main_red_path_escalates_and_returns_zero(self) -> None:
        with (
            patch("boto3.Session"),
            patch("scripts.convergence_health.__main__.read_convergence_record", return_value={"status": "red"}),
            patch("scripts.convergence_health.__main__.find_stuck_gated_approvals", return_value=[]),
            patch("scripts.convergence_health.__main__.find_reconcile_runs_since", return_value=[]),
            patch("scripts.convergence_health.__main__.assess_health", return_value=self._verdict("red")),
            patch(
                "scripts.convergence_health.__main__.escalate",
                return_value={"action": "file", "rec_id": "rec-1"},
            ) as esc,
        ):
            rc = main()
        assert rc == 0
        esc.assert_called_once()

    def test_main_returns_one_on_s3_init_failure(self) -> None:
        with patch("boto3.Session", side_effect=RuntimeError("no creds")):
            rc = main()
        assert rc == 1

    def test_main_red_status_checks_reconcile_in_flight_and_passes_through(self) -> None:
        # A reconcile.yml run inside the episode window -> escalate() called with
        # reconcile_in_flight=True.
        with (
            patch("boto3.Session"),
            patch(
                "scripts.convergence_health.__main__.read_convergence_record",
                return_value={"status": "red", "timestamp": "2026-06-27T06:00:00Z"},
            ),
            patch("scripts.convergence_health.__main__.find_stuck_gated_approvals", return_value=[]),
            patch(
                "scripts.convergence_health.__main__.find_reconcile_runs_since",
                return_value=[{"id": 1, "created_at": "2026-06-27T07:00:00Z"}],
            ) as find_reconcile,
            patch("scripts.convergence_health.__main__.assess_health", return_value=self._verdict("red")),
            patch(
                "scripts.convergence_health.__main__.escalate",
                return_value={"action": "skipped_reconcile_in_flight", "rec_id": None},
            ) as esc,
        ):
            rc = main()
        assert rc == 0
        find_reconcile.assert_called_once()
        assert esc.call_args.kwargs["reconcile_in_flight"] is True

    def test_main_green_status_skips_reconcile_lookup_entirely(self) -> None:
        # No red episode -> no reason to spend the extra GitHub API call.
        with (
            patch("boto3.Session"),
            patch("scripts.convergence_health.__main__.read_convergence_record", return_value={"status": "green"}),
            patch("scripts.convergence_health.__main__.find_stuck_gated_approvals", return_value=[]),
            patch("scripts.convergence_health.__main__.find_reconcile_runs_since") as find_reconcile,
            patch("scripts.convergence_health.__main__.assess_health", return_value=self._verdict("green")),
            patch("scripts.convergence_health.__main__.escalate", return_value={"action": "none", "rec_id": None}) as esc,
        ):
            rc = main()
        assert rc == 0
        find_reconcile.assert_not_called()
        assert esc.call_args.kwargs["reconcile_in_flight"] is False

    def test_main_prints_pending_gated_in_verdict_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        """DEP-11 (T2.47): the printed HealthVerdict JSON must include pending_gated so the
        convergence-health sensor surfaces a routed-pending episode."""
        marker = {"routed_at": "2026-06-27T00:00:00Z", "run_url": "https://example.com/run/1", "commit_sha": "abc123"}
        verdict = HealthVerdict(
            status="green",
            red_age_hours=0.0,
            unapplied_backlog=0,
            severity="none",
            pending_gated=marker,
        )
        with (
            patch("boto3.Session"),
            patch("scripts.convergence_health.__main__.read_convergence_record", return_value={"status": "green"}),
            patch("scripts.convergence_health.__main__.find_stuck_gated_approvals", return_value=[]),
            patch("scripts.convergence_health.__main__.assess_health", return_value=verdict),
            patch("scripts.convergence_health.__main__.escalate", return_value={"action": "none", "rec_id": None}),
        ):
            rc = main()
        assert rc == 0
        captured = capsys.readouterr()
        assert "pending_gated" in captured.out
        assert "abc123" in captured.out

    def test_main_prints_pending_gated_null_when_absent(self, capsys: pytest.CaptureFixture[str]) -> None:
        verdict = self._verdict("green")
        assert verdict.pending_gated is None
        with (
            patch("boto3.Session"),
            patch("scripts.convergence_health.__main__.read_convergence_record", return_value={"status": "green"}),
            patch("scripts.convergence_health.__main__.find_stuck_gated_approvals", return_value=[]),
            patch("scripts.convergence_health.__main__.assess_health", return_value=verdict),
            patch("scripts.convergence_health.__main__.escalate", return_value={"action": "none", "rec_id": None}),
        ):
            rc = main()
        assert rc == 0
        captured = capsys.readouterr()
        assert '"pending_gated": null' in captured.out

    def test_main_absent_record_skips_reconcile_lookup(self) -> None:
        with (
            patch("boto3.Session"),
            patch("scripts.convergence_health.__main__.read_convergence_record", return_value=None),
            patch("scripts.convergence_health.__main__.find_stuck_gated_approvals", return_value=[]),
            patch("scripts.convergence_health.__main__.find_reconcile_runs_since") as find_reconcile,
            patch("scripts.convergence_health.__main__.assess_health", return_value=self._verdict("unknown")),
            patch("scripts.convergence_health.__main__.escalate", return_value={"action": "none", "rec_id": None}),
        ):
            rc = main()
        assert rc == 0
        find_reconcile.assert_not_called()


class TestMainDiagnoseMode:
    def test_diagnose_mode_does_not_call_escalate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CONVERGENCE_HEALTH_DIAGNOSE", "1")
        with (
            patch("boto3.Session"),
            patch("scripts.convergence_health.__main__.read_convergence_record", return_value={"status": "green"}),
            patch("scripts.convergence_health.__main__.diagnose_stuck_approvals", return_value=[]),
            patch(
                "scripts.convergence_health.__main__.assess_health",
                return_value=HealthVerdict(status="green", red_age_hours=0.0, unapplied_backlog=0, severity="none"),
            ),
            patch("scripts.convergence_health.__main__.escalate") as esc,
        ):
            rc = main()
        assert rc == 0
        esc.assert_not_called()

    def test_diagnose_mode_calls_diagnose_stuck_approvals_not_find_stuck(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CONVERGENCE_HEALTH_DIAGNOSE", "1")
        with (
            patch("boto3.Session"),
            patch("scripts.convergence_health.__main__.read_convergence_record", return_value={"status": "green"}),
            patch("scripts.convergence_health.__main__.diagnose_stuck_approvals", return_value=[]) as diag,
            patch("scripts.convergence_health.__main__.find_stuck_gated_approvals") as find_stuck,
            patch(
                "scripts.convergence_health.__main__.assess_health",
                return_value=HealthVerdict(status="green", red_age_hours=0.0, unapplied_backlog=0, severity="none"),
            ),
            patch("scripts.convergence_health.__main__.escalate"),
        ):
            main()
        diag.assert_called_once()
        find_stuck.assert_not_called()

    def test_normal_mode_calls_find_stuck_not_diagnose(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CONVERGENCE_HEALTH_DIAGNOSE", raising=False)
        with (
            patch("boto3.Session"),
            patch("scripts.convergence_health.__main__.read_convergence_record", return_value={"status": "green"}),
            patch("scripts.convergence_health.__main__.find_stuck_gated_approvals", return_value=[]) as find_stuck,
            patch("scripts.convergence_health.__main__.diagnose_stuck_approvals") as diag,
            patch(
                "scripts.convergence_health.__main__.assess_health",
                return_value=HealthVerdict(status="green", red_age_hours=0.0, unapplied_backlog=0, severity="none"),
            ),
            patch("scripts.convergence_health.__main__.escalate", return_value={"action": "none", "rec_id": None}),
        ):
            rc = main()
        assert rc == 0
        find_stuck.assert_called_once()
        diag.assert_not_called()


class TestMainDucklakeDrift:
    def test_happy_path_returns_zero(self) -> None:
        with patch(
            "scripts.convergence_health.__main__.detect_ducklake_code_drift",
            return_value={"action": "none", "rec_id": None},
        ) as detect:
            rc = main_ducklake_drift(profile="agent_platform")
        assert rc == 0
        detect.assert_called_once_with(profile="agent_platform")

    def test_exception_returns_one(self) -> None:
        with patch(
            "scripts.convergence_health.__main__.detect_ducklake_code_drift",
            side_effect=RuntimeError("boom"),
        ):
            rc = main_ducklake_drift()
        assert rc == 1


class TestMainProdDrift:
    def test_happy_path_returns_zero(self) -> None:
        with patch(
            "scripts.convergence_health.__main__.detect_prod_code_drift",
            return_value={"action": "none", "rec_id": None},
        ) as detect:
            rc = main_prod_drift(profile="agent_platform")
        assert rc == 0
        detect.assert_called_once_with(profile="agent_platform")

    def test_exception_returns_one(self) -> None:
        with patch(
            "scripts.convergence_health.__main__.detect_prod_code_drift",
            side_effect=RuntimeError("boom"),
        ):
            rc = main_prod_drift()
        assert rc == 1


class TestMainBudgetIngest:
    """rec-3288: the CI budget-block warehouse ingester's CLI leg."""

    def test_happy_path_returns_zero(self) -> None:
        with patch(
            "scripts.convergence_health.__main__.ingest_budget_breaches",
            return_value={"groups": 0, "actions": []},
        ) as ingest:
            rc = main_budget_ingest(profile="agent_platform")
        assert rc == 0
        ingest.assert_called_once_with(profile="agent_platform", dry_run=False)

    def test_dry_run_is_passed_through(self) -> None:
        with patch(
            "scripts.convergence_health.__main__.ingest_budget_breaches",
            return_value={"groups": 0, "actions": [], "dry_run": True},
        ) as ingest:
            rc = main_budget_ingest(dry_run=True)
        assert rc == 0
        assert ingest.call_args.kwargs["dry_run"] is True

    def test_portal_failure_returns_one_and_says_nothing_was_buffered(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Decision 84 I-4: the failure is loud (red step) and explicitly names the no-outbox
        property, so nobody goes looking for a replay queue that does not exist."""
        with patch(
            "scripts.convergence_health.__main__.ingest_budget_breaches",
            side_effect=RuntimeError("ducklake_writer unreachable"),
        ):
            rc = main_budget_ingest()
        assert rc == 1
        out = capsys.readouterr().out
        assert "budget_ingest FAILED" in out
        assert "nothing buffered" in out
        assert "ducklake_writer unreachable" in out


_TICK = "scripts.convergence_health.dispatch_integrity.tick"


class TestMainSensorLiveness:
    """LSA-02: the scheduled-loop liveness sensor's CLI leg."""

    @pytest.fixture(autouse=True)
    def _integrity(self) -> Any:
        with patch(_TICK, return_value={"integrity_blind": "none"}) as tick:
            yield tick

    def test_happy_path_returns_zero(self) -> None:
        with patch(
            "scripts.convergence_health.__main__.detect_stale_loops",
            return_value={
                "peers": 9,
                "stale": [],
                "filed": [],
                "updated": [],
                "unchanged": [],
                "dropped": [],
                "fleet_collapse": False,
            },
        ) as detect:
            rc = main_sensor_liveness(profile="agent_platform")
        assert rc == 0
        detect.assert_called_once_with(profile="agent_platform")

    def test_failure_returns_one_and_says_nothing_was_buffered(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch(
            "scripts.convergence_health.__main__.detect_stale_loops",
            side_effect=RuntimeError("ducklake_writer unreachable"),
        ):
            rc = main_sensor_liveness()
        assert rc == 1
        out = capsys.readouterr().out
        assert "sensor_liveness FAILED" in out
        assert "nothing buffered" in out
        assert "ducklake_writer unreachable" in out

    def test_runs_dispatch_integrity_after_liveness(self, _integrity: Any) -> None:
        with patch("scripts.convergence_health.__main__.detect_stale_loops", return_value={}):
            assert main_sensor_liveness(profile="p") == 0
        _integrity.assert_called_once_with(profile="p")

    def test_integrity_raising_returns_one(self, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch("scripts.convergence_health.__main__.detect_stale_loops", return_value={}),
            patch(_TICK, side_effect=RuntimeError("portal down")),
        ):
            assert main_sensor_liveness() == 1
        assert "dispatch_integrity FAILED" in capsys.readouterr().out

    def test_liveness_raising_still_runs_integrity(self, _integrity: Any) -> None:
        with patch("scripts.convergence_health.__main__.detect_stale_loops", side_effect=RuntimeError("x")):
            assert main_sensor_liveness() == 1
        _integrity.assert_called_once()


class TestMainLivenessProbe:
    """LSA-02: the --liveness-probe acceptance oracle's CLI leg."""

    def test_fresh_returns_zero(self) -> None:
        with patch("scripts.convergence_health.__main__.stale_peers_for_probe", return_value=[]) as probe:
            rc = main_liveness_probe("convergence-health.yml")
        assert rc == 0
        probe.assert_called_once_with("convergence-health.yml", leg="cadence")

    def test_stale_returns_one(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("scripts.convergence_health.__main__.stale_peers_for_probe", return_value=["convergence-health.yml"]):
            rc = main_liveness_probe("convergence-health.yml")
        assert rc == 1
        assert "STALE" in capsys.readouterr().out

    def test_unknown_workflow_or_dead_query_returns_one(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch(
            "scripts.convergence_health.__main__.stale_peers_for_probe",
            side_effect=ValueError("'nope.yml' is not a derived scheduled peer"),
        ):
            rc = main_liveness_probe("nope.yml")
        assert rc == 1
        assert "FAILED" in capsys.readouterr().out

    def test_success_leg_is_passed_through(self) -> None:
        with patch("scripts.convergence_health.__main__.stale_peers_for_probe", return_value=[]) as probe:
            rc = main_liveness_probe("convergence-health.yml", leg="success")
        assert rc == 0
        probe.assert_called_once_with("convergence-health.yml", leg="success")

    def test_success_leg_stale_message_names_the_leg(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("scripts.convergence_health.__main__.stale_peers_for_probe", return_value=["ghas-probe.yml"]):
            rc = main_liveness_probe("ghas-probe.yml", leg="success")
        assert rc == 1
        assert "STALE (success)" in capsys.readouterr().out


class TestDispatch:
    def test_no_flags_runs_the_convergence_sensor(self) -> None:
        with patch("scripts.convergence_health.__main__.main", return_value=0) as entry:
            assert _dispatch([]) == 0
        entry.assert_called_once_with()

    def test_ducklake_flag_routes_to_the_ducklake_drift_sensor(self) -> None:
        with patch("scripts.convergence_health.__main__.main_ducklake_drift", return_value=0) as entry:
            assert _dispatch(["--ducklake-drift"]) == 0
        entry.assert_called_once_with()

    def test_prod_flag_routes_to_the_prod_drift_sensor(self) -> None:
        with patch("scripts.convergence_health.__main__.main_prod_drift", return_value=0) as entry:
            assert _dispatch(["--prod-drift"]) == 0
        entry.assert_called_once_with()

    def test_budget_ingest_flag_routes_without_dry_run_by_default(self) -> None:
        with patch("scripts.convergence_health.__main__.main_budget_ingest", return_value=0) as entry:
            assert _dispatch(["--budget-ingest"]) == 0
        entry.assert_called_once_with(dry_run=False)

    def test_budget_ingest_honours_the_dry_run_flag(self) -> None:
        with patch("scripts.convergence_health.__main__.main_budget_ingest", return_value=0) as entry:
            assert _dispatch(["--budget-ingest", "--dry-run"]) == 0
        entry.assert_called_once_with(dry_run=True)

    def test_sensor_liveness_flag_routes_to_the_liveness_sensor(self) -> None:
        with patch("scripts.convergence_health.__main__.main_sensor_liveness", return_value=0) as entry:
            assert _dispatch(["--sensor-liveness"]) == 0
        entry.assert_called_once_with()

    def test_liveness_probe_flag_routes_with_the_workflow_argument(self) -> None:
        with patch("scripts.convergence_health.__main__.main_liveness_probe", return_value=0) as entry:
            assert _dispatch(["--liveness-probe", "convergence-health.yml"]) == 0
        entry.assert_called_once_with("convergence-health.yml", leg="cadence")

    def test_liveness_probe_flag_without_a_workflow_argument(self) -> None:
        with patch("scripts.convergence_health.__main__.main_liveness_probe", return_value=0) as entry:
            assert _dispatch(["--liveness-probe"]) == 0
        entry.assert_called_once_with(None, leg="cadence")

    def test_liveness_probe_flag_with_a_workflow_and_success_leg(self) -> None:
        with patch("scripts.convergence_health.__main__.main_liveness_probe", return_value=0) as entry:
            assert _dispatch(["--liveness-probe", "ghas-probe.yml", "success"]) == 0
        entry.assert_called_once_with("ghas-probe.yml", leg="success")

    def test_liveness_probe_flag_with_bare_success_leg_and_no_workflow(self) -> None:
        # The fleet-wide probe: no workflow token, just the leg selector.
        with patch("scripts.convergence_health.__main__.main_liveness_probe", return_value=0) as entry:
            assert _dispatch(["--liveness-probe", "success"]) == 0
        entry.assert_called_once_with(None, leg="success")

    def test_liveness_probe_flag_with_bare_cadence_leg_token(self) -> None:
        with patch("scripts.convergence_health.__main__.main_liveness_probe", return_value=0) as entry:
            assert _dispatch(["--liveness-probe", "ghas-probe.yml", "cadence"]) == 0
        entry.assert_called_once_with("ghas-probe.yml", leg="cadence")

    @pytest.mark.parametrize(
        ("argv", "check"),
        [(["--dispatch-integrity-probe"], None), (["--dispatch-integrity-probe", "oidc_subject"], "oidc_subject")],
    )
    def test_dispatch_integrity_probe_routes_before_main(self, argv: list[str], check: Any) -> None:
        with (
            patch("scripts.convergence_health.dispatch_integrity.probe", return_value=0) as probe,
            patch("scripts.convergence_health.__main__.main") as fallback,
            patch("scripts.convergence_health.__main__.escalate") as esc,
        ):
            assert _dispatch(argv) == 0
        probe.assert_called_once_with(check)
        fallback.assert_not_called()
        esc.assert_not_called()

    @pytest.mark.parametrize("code", [0, 1])
    def test_dispatch_integrity_probe_returns_probe_code(self, code: int) -> None:
        with patch("scripts.convergence_health.dispatch_integrity.probe", return_value=code):
            assert _dispatch(["--dispatch-integrity-probe", "off_register_run"]) == code
