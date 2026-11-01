"""Unit tests for scripts.cost_reconciliation.

All portal calls (file_rec / update_rec) and the source-scoped reader are mocked -- no live
warehouse access.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

import scripts.cost_reconciliation as cr
from scripts.cost_reconciliation import (
    CostSnapshot,
    TelemetryCost,
    TriggerResult,
    _previous_month,
    _price_change_factor,
    _validate_rec_payload,
    build_rec_fields,
    build_report,
    compute_discrepancy,
    evaluate_triggers,
    find_open_cost_rec_for,
    load_config,
    load_snapshot,
    load_telemetry_cost,
    main,
    reconcile,
)

FIXTURES = Path("tests/fixtures/cost_reconciliation")
TRIPPED = FIXTURES / "snapshot_tripped.json"
CLEAN = FIXTURES / "snapshot_clean.json"


def _load(path: Path) -> CostSnapshot:
    return load_snapshot(str(path))


# ---------------------------------------------------------------------------
# TestTriggerEvaluation
# ---------------------------------------------------------------------------


class TestTriggerEvaluation:
    def test_tripped_fixture_trips_pool_and_s3(self) -> None:
        snapshot = _load(TRIPPED)
        config = load_config()
        results = {t.key: t for t in evaluate_triggers(snapshot, config)}

        assert results["anthropic_pool_utilisation"].tripped is True
        assert results["s3_share_of_bill"].tripped is True
        assert results["deepseek_price_change"].tripped is False
        assert results["runner_cost_vs_scheduled"].tripped is False
        assert results["step_functions_share_of_bill"].tripped is False

    def test_clean_fixture_trips_nothing(self) -> None:
        snapshot = _load(CLEAN)
        config = load_config()
        results = evaluate_triggers(snapshot, config)
        assert all(not r.tripped for r in results)

    def test_kg7_maps_to_anthropic_pool_trigger(self) -> None:
        snapshot = _load(TRIPPED)
        config = load_config()
        results = {t.key: t for t in evaluate_triggers(snapshot, config)}
        assert "KG.7" in results["anthropic_pool_utilisation"].label

    def test_deepseek_price_change_trips_on_direction_increase(self) -> None:
        snapshot = _load(CLEAN)
        snapshot.providers.deepseek.unit_price_input_per_mtok = 5.0
        config = load_config()
        results = {t.key: t for t in evaluate_triggers(snapshot, config)}
        assert results["deepseek_price_change"].tripped is True

    def test_runner_trips_when_scheduled_alternative_present(self) -> None:
        snapshot = _load(CLEAN)
        snapshot.aws.line_items_usd["self_hosted_runner"] = 50.0
        snapshot.runner_scheduled_alternative_usd = 10.0
        config = load_config()
        results = {t.key: t for t in evaluate_triggers(snapshot, config)}
        assert results["runner_cost_vs_scheduled"].tripped is True

    def test_runner_vestigial_guard_avoids_divide_by_zero(self) -> None:
        snapshot = _load(CLEAN)
        snapshot.aws.line_items_usd["self_hosted_runner"] = 0.0
        snapshot.runner_scheduled_alternative_usd = 0.0
        config = load_config()
        results = {t.key: t for t in evaluate_triggers(snapshot, config)}
        assert results["runner_cost_vs_scheduled"].tripped is False
        assert results["runner_cost_vs_scheduled"].measured == 0.0

    def test_step_functions_trips_over_threshold(self) -> None:
        snapshot = _load(CLEAN)
        snapshot.aws.line_items_usd["step_functions"] = 100.0
        config = load_config()
        results = {t.key: t for t in evaluate_triggers(snapshot, config)}
        assert results["step_functions_share_of_bill"].tripped is True

    def test_price_change_factor_guards_zero_current_or_baseline(self) -> None:
        assert _price_change_factor(0.0, 1.0) == 0.0
        assert _price_change_factor(1.0, 0.0) == 0.0
        assert _price_change_factor(2.0, 1.0) == 2.0

    def test_s3_share_guarded_when_total_bill_zero(self) -> None:
        snapshot = _load(CLEAN)
        snapshot.aws.total_bill_usd = 0.0
        config = load_config()
        results = {t.key: t for t in evaluate_triggers(snapshot, config)}
        assert results["s3_share_of_bill"].measured == 0.0
        assert results["s3_share_of_bill"].tripped is False


# ---------------------------------------------------------------------------
# TestGracefulDegradation
# ---------------------------------------------------------------------------


class TestGracefulDegradation:
    def test_load_telemetry_cost_returns_none(self) -> None:
        assert load_telemetry_cost("2026-06") is None

    def test_compute_discrepancy_none_when_telemetry_unavailable(self) -> None:
        snapshot = _load(TRIPPED)
        config = load_config()
        assert compute_discrepancy(snapshot, None, config) is None

    def test_compute_discrepancy_computed_when_telemetry_present(self) -> None:
        snapshot = _load(TRIPPED)
        config = load_config()
        telemetry = TelemetryCost(month="2026-06", est_cost_usd=100.0)
        discrepancy = compute_discrepancy(snapshot, telemetry, config)
        assert discrepancy is not None
        assert discrepancy.invoice_total_usd == 140.0
        assert discrepancy.tripped is True

    def test_compute_discrepancy_not_tripped_within_threshold(self) -> None:
        snapshot = _load(TRIPPED)
        config = load_config()
        telemetry = TelemetryCost(month="2026-06", est_cost_usd=139.0)
        discrepancy = compute_discrepancy(snapshot, telemetry, config)
        assert discrepancy is not None
        assert discrepancy.tripped is False

    def test_report_notes_telemetry_pending_when_none(self) -> None:
        snapshot = _load(TRIPPED)
        config = load_config()
        triggers = evaluate_triggers(snapshot, config)
        full_report, public_summary = build_report(triggers, None, "2026-06")
        assert "t2.36" in full_report.lower()
        assert "pending (t2.36)" in public_summary.lower()

    def test_five_invoice_triggers_still_evaluate_without_telemetry(self) -> None:
        snapshot = _load(TRIPPED)
        config = load_config()
        results = evaluate_triggers(snapshot, config)
        assert len(results) == 5

    def test_reconcile_dry_run_with_no_telemetry_does_not_raise(self) -> None:
        result = reconcile(str(TRIPPED), "2026-06", dry_run=True)
        assert result["dry_run"] is True


# ---------------------------------------------------------------------------
# TestReconcileDedupe
# ---------------------------------------------------------------------------


class TestReconcileDedupe:
    def test_dry_run_builds_valid_payload_and_calls_no_portal_functions(self) -> None:
        with (
            patch("scripts.ops_data_portal.file_rec") as mock_file,
            patch("scripts.ops_data_portal.update_rec") as mock_update,
            patch("scripts.ops_data_portal.sync") as mock_sync,
        ):
            result = reconcile(str(TRIPPED), "2026-06", dry_run=True)

        assert result["dry_run"] is True
        assert len(result["would_file"]) == 2
        mock_file.assert_not_called()
        mock_update.assert_not_called()
        mock_sync.assert_not_called()

    def test_live_mode_looks_up_each_trigger_without_syncing(self) -> None:
        looked_up: list[str] = []

        def _find(trigger_key):
            looked_up.append(trigger_key)

        with (
            patch("scripts.ops_data_portal.sync") as mock_sync,
            patch("scripts.ops_data_portal.file_rec", return_value="rec-9001") as mock_file,
            patch("scripts.ops_data_portal.update_rec") as mock_update,
            patch("scripts.cost_reconciliation.find_open_cost_rec_for", side_effect=_find),
        ):
            reconcile(str(TRIPPED), "2026-06", dry_run=False)

        mock_sync.assert_not_called()
        assert len(looked_up) == len(set(looked_up)) >= 2
        assert mock_file.call_count == 2
        mock_update.assert_not_called()

    def test_live_mode_updates_existing_open_rec(self) -> None:
        with (
            patch("scripts.ops_data_portal.file_rec") as mock_file,
            patch("scripts.ops_data_portal.update_rec") as mock_update,
            patch("scripts.cost_reconciliation.find_open_cost_rec_for", return_value="rec-100"),
        ):
            result = reconcile(str(TRIPPED), "2026-06", dry_run=False)

        mock_file.assert_not_called()
        # Every trigger has an existing open rec: 2 tripped -> update (refresh context),
        # 3 cleared -> close.
        assert mock_update.call_count == 5
        actions = {a["trigger"]: a["action"] for a in result["actions"]}
        assert actions["anthropic_pool_utilisation"] == "update"
        assert actions["s3_share_of_bill"] == "update"
        assert actions["deepseek_price_change"] == "close"

    def test_dry_run_includes_discrepancy_when_telemetry_available_and_tripped(self) -> None:
        with patch(
            "scripts.cost_reconciliation.load_telemetry_cost",
            return_value=TelemetryCost(month="2026-06", est_cost_usd=100.0),
        ):
            result = reconcile(str(TRIPPED), "2026-06", dry_run=True)

        keys = {fields["title"] for fields in result["would_file"]}
        assert any("invoice_vs_telemetry_discrepancy" in k for k in keys)
        assert len(result["would_file"]) == 3

    def test_live_mode_files_rec_for_tripped_discrepancy(self) -> None:
        with (
            patch(
                "scripts.cost_reconciliation.load_telemetry_cost",
                return_value=TelemetryCost(month="2026-06", est_cost_usd=100.0),
            ),
            patch("scripts.ops_data_portal.file_rec", return_value="rec-9003") as mock_file,
            patch("scripts.ops_data_portal.update_rec"),
            patch("scripts.cost_reconciliation.find_open_cost_rec_for", return_value=None),
        ):
            result = reconcile(str(TRIPPED), "2026-06", dry_run=False)

        actions = {a["trigger"]: a["action"] for a in result["actions"]}
        assert actions["invoice_vs_telemetry_discrepancy"] == "file"
        filed_keys = [c.args[0]["source"] for c in mock_file.call_args_list]
        assert all(k == "cost_reconciliation" for k in filed_keys)

    def test_remedy_text_is_trigger_specific(self) -> None:
        s3_fields = build_rec_fields(
            TriggerResult(key="s3_share_of_bill", label="S3 share", tripped=True, measured=60.0, threshold=50.0, unit="%"),
            "2026-06",
            str(TRIPPED),
        )
        pool_fields = build_rec_fields(
            TriggerResult(
                key="anthropic_pool_utilisation", label="Pool util", tripped=True, measured=85.0, threshold=70.0, unit="%"
            ),
            "2026-06",
            str(TRIPPED),
        )
        assert "Anthropic API key" not in s3_fields["context"]
        assert "S3 storage growth" in s3_fields["context"]
        assert "Anthropic API key" in pool_fields["context"]

    def test_live_mode_closes_rec_on_cleared_trigger(self) -> None:
        with (
            patch("scripts.ops_data_portal.file_rec") as mock_file,
            patch("scripts.ops_data_portal.update_rec") as mock_update,
            patch("scripts.cost_reconciliation.find_open_cost_rec_for", return_value="rec-200"),
        ):
            result = reconcile(str(CLEAN), "2026-06", dry_run=False)

        mock_file.assert_not_called()
        assert mock_update.call_count == 5
        for _call in mock_update.call_args_list:
            _, kwargs_or_updates = _call.args[0], _call.args[1]
            assert kwargs_or_updates["status"] == "closed"
        actions = {a["trigger"]: a["action"] for a in result["actions"]}
        assert all(v == "close" for v in actions.values())

    def test_validate_rec_payload_raises_on_bad_acceptance(self) -> None:
        fields = build_rec_fields(
            TriggerResult(key="s3_share_of_bill", label="S3 share", tripped=True, measured=60.0, threshold=50.0, unit="%"),
            "2026-06",
            str(TRIPPED),
        )
        fields["acceptance"] = 'python -c "import os"'
        with pytest.raises(ValueError):
            _validate_rec_payload(fields)

    def test_validate_rec_payload_passes_for_well_formed_fields(self) -> None:
        fields = build_rec_fields(
            TriggerResult(key="s3_share_of_bill", label="S3 share", tripped=True, measured=60.0, threshold=50.0, unit="%"),
            "2026-06",
            str(TRIPPED),
        )
        _validate_rec_payload(fields)  # must not raise

    def test_validate_rec_payload_runs_the_portal_gate(self) -> None:
        """The dry-run validator is file_rec's own pre-check path: a too-short context and an absolute file path are
        refused naming the contract rule."""
        fields = build_rec_fields(
            TriggerResult(key="s3_share_of_bill", label="S3 share", tripped=True, measured=60.0, threshold=50.0, unit="%"),
            "2026-06",
            str(TRIPPED),
        )
        with pytest.raises(ValueError, match="at least 80 stripped characters"):
            _validate_rec_payload({**fields, "context": "too short"})
        with pytest.raises(ValueError, match=r"ops_recommendations\.file: must match"):
            _validate_rec_payload({**fields, "file": "/abs/ROADMAP.yaml"})


# ---------------------------------------------------------------------------
# TestPublicBoundary
# ---------------------------------------------------------------------------


class TestPublicBoundary:
    def test_public_summary_has_no_dollar_figures(self) -> None:
        snapshot = _load(TRIPPED)
        config = load_config()
        triggers = evaluate_triggers(snapshot, config)
        telemetry = TelemetryCost(month="2026-06", est_cost_usd=100.0)
        discrepancy = compute_discrepancy(snapshot, telemetry, config)
        _, public_summary = build_report(triggers, discrepancy, "2026-06")
        assert "$" not in public_summary

    def test_public_summary_has_no_measured_value_substrings(self) -> None:
        snapshot = _load(TRIPPED)
        config = load_config()
        triggers = evaluate_triggers(snapshot, config)
        full_report, public_summary = build_report(triggers, None, "2026-06")
        for trig in triggers:
            measured_str = f"measured={trig.measured:.2f}"
            assert measured_str in full_report
            assert measured_str not in public_summary

    def test_public_summary_carries_names_and_verdicts(self) -> None:
        snapshot = _load(TRIPPED)
        config = load_config()
        triggers = evaluate_triggers(snapshot, config)
        _, public_summary = build_report(triggers, None, "2026-06")
        for trig in triggers:
            assert trig.label in public_summary
            assert ("PASS" in public_summary) or ("FAIL" in public_summary)

    def test_full_report_carries_absolute_figures(self) -> None:
        snapshot = _load(TRIPPED)
        config = load_config()
        triggers = evaluate_triggers(snapshot, config)
        telemetry = TelemetryCost(month="2026-06", est_cost_usd=100.0)
        discrepancy = compute_discrepancy(snapshot, telemetry, config)
        full_report, _ = build_report(triggers, discrepancy, "2026-06")
        assert "$" in full_report


# ---------------------------------------------------------------------------
# Loaders / schema
# ---------------------------------------------------------------------------


class TestLoaders:
    def test_load_snapshot_rejects_unknown_fields(self, tmp_path: Path) -> None:
        bad = json.loads(TRIPPED.read_text())
        bad["unexpected_field"] = "nope"
        bad_path = tmp_path / "bad.json"
        bad_path.write_text(json.dumps(bad), encoding="utf-8")
        with pytest.raises(ValidationError):
            load_snapshot(str(bad_path))

    def test_load_config_default_path(self) -> None:
        config = load_config()
        assert config.thresholds.s3_share_of_bill_pct == 50.0
        assert config.report.private_s3_prefix.startswith("s3://")

    def test_load_config_explicit_path(self) -> None:
        config = load_config(str(cr.DEFAULT_CONFIG_PATH))
        assert config.baseline.deepseek_input_per_mtok == 0.252


# ---------------------------------------------------------------------------
# find_open_cost_rec_for
# ---------------------------------------------------------------------------


class TestFindOpenCostRecViaReader:
    @staticmethod
    def _reader(rows: list[dict] | None = None, error: BaseException | None = None) -> MagicMock:
        reader = MagicMock()
        reader.current_state.side_effect = error or (lambda table, row_filter=None: rows)
        return reader

    @staticmethod
    def _rec(rec_id: str, key: str, status: str = "open", source: str = "cost_reconciliation") -> dict:
        return {"id": rec_id, "status": status, "source": source, "title": f"[cost:{key}] exceeded threshold (2026-06)"}

    def test_source_scoped_read_finds_open_tagged_rec(self) -> None:
        reader = self._reader([self._rec("rec-320", "step_functions_share_of_bill"), self._rec("rec-321", "s3_share_of_bill")])
        with patch("scripts.rec_episode._make_reader", return_value=reader):
            assert find_open_cost_rec_for("s3_share_of_bill") == "rec-321"
        reader.current_state.assert_called_once_with("ops_recommendations", row_filter="source = 'cost_reconciliation'")

    def test_returns_none_when_status_not_open_or_tag_differs(self) -> None:
        reader = self._reader([self._rec("rec-1", "s3_share_of_bill", status="closed"), self._rec("rec-2", "other")])
        with patch("scripts.rec_episode._make_reader", return_value=reader):
            assert find_open_cost_rec_for("s3_share_of_bill") is None

    def test_reader_failure_raises_instead_of_returning_none(self) -> None:
        with patch("scripts.rec_episode._make_reader", return_value=self._reader(error=RuntimeError("reader down"))):
            with pytest.raises(RuntimeError, match="reader down"):
                find_open_cost_rec_for("s3_share_of_bill")

    def test_never_reads_cache_file(self) -> None:
        assert not hasattr(cr, "RECS_JSONL_PATH")


# ---------------------------------------------------------------------------
# reconcile() report_out + previous month
# ---------------------------------------------------------------------------


class TestReconcileReportOut:
    def test_report_out_writes_full_report(self, tmp_path: Path) -> None:
        out_path = tmp_path / "report.md"
        reconcile(str(TRIPPED), "2026-06", dry_run=True, report_out=str(out_path))
        assert out_path.exists()
        assert "Cost reconciliation report" in out_path.read_text(encoding="utf-8")

    def test_previous_month_rolls_over_year_boundary(self) -> None:
        import datetime as dt

        assert _previous_month(dt.date(2026, 1, 15)) == "2025-12"
        assert _previous_month(dt.date(2026, 7, 1)) == "2026-06"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCLI:
    def test_main_dry_run_exits_zero(self, capsys: pytest.CaptureFixture) -> None:
        exit_code = main(["--invoice", str(TRIPPED), "--month", "2026-06", "--dry-run"])
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "DRY RUN" in captured.out

    def test_main_defaults_month_when_omitted(self) -> None:
        exit_code = main(["--invoice", str(TRIPPED), "--dry-run"])
        assert exit_code == 0

    def test_main_live_mode_reports_actions(self, capsys: pytest.CaptureFixture) -> None:
        with (
            patch("scripts.ops_data_portal.file_rec", return_value="rec-9002"),
            patch("scripts.cost_reconciliation.find_open_cost_rec_for", return_value=None),
        ):
            exit_code = main(["--invoice", str(TRIPPED), "--month", "2026-06"])
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "cost_reconciliation" in captured.out or "->" in captured.out

    def test_main_returns_nonzero_on_error(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist.json"
        exit_code = main(["--invoice", str(missing), "--month", "2026-06", "--dry-run"])
        assert exit_code == 1

    def test_module_entrypoint_invocable(self) -> None:
        assert callable(main)
