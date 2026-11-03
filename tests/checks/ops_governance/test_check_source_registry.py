"""Tests for check_source_registry() (widened scan set, PLAN-convergence-health-prod-drift-red)."""

from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.checks.ops_governance.check_source_registry import SCANNED_PATHS, check_source_registry


def _mk_entry(cid: str) -> dict:
    return {"canonical_id": cid, "description": "d", "signal_interpretation": "s", "added_date": "2026-01-01"}


class TestScannedPaths:
    """SCANNED_PATHS (the complete scan set) includes the portal surface and every enumerated
    producer module (constraints: exactly this list, never scripts/** wholesale)."""

    def test_scanned_paths_includes_portal_and_producer_modules(self) -> None:
        required = {
            "scripts/ops_data_portal.py",
            "scripts/ops_portal/*.py",
            "scripts/convergence_health/code_drift.py",
            "scripts/convergence_health/escalate.py",
            "scripts/convergence_health/sensor_liveness.py",
            "scripts/checks/_budget_recs.py",
            "scripts/ci_rca/probe_health.py",
            "scripts/preflight/ci_rca_gauges.py",
            "scripts/executor/jsonl_store.py",
            "scripts/executor/branch_lifecycle.py",
            "scripts/ducklake_smoke/lambda_ops_gates.py",
            "scripts/backlog_health/escalate.py",
            "scripts/telemetry_verify/alarms.py",
        }
        assert required <= set(SCANNED_PATHS)


class TestCheckSourceRegistry:
    """Tests for check_source_registry()."""

    def test_source_registry_ci_guard_accepts_registered(self, tmp_path: Path) -> None:
        """check_source_registry() passes when all schedule.yaml agent names are registered."""

        (tmp_path / "config" / "agent" / "data_quality").mkdir(parents=True)

        (tmp_path / "config" / "agent" / "data_quality" / "source_registry.yaml").write_text(
            yaml.dump({"entries": [_mk_entry("doc-freshness"), _mk_entry("orphan-code")]}),
            encoding="utf-8",
        )
        (tmp_path / ".github" / "agents").mkdir(parents=True)
        (tmp_path / ".github" / "agents" / "schedule.yaml").write_text(
            yaml.dump({"agents": [{"name": "doc-freshness"}, {"name": "orphan-code"}]}),
            encoding="utf-8",
        )
        (tmp_path / "scripts").mkdir(parents=True)
        (tmp_path / "scripts" / "ops_data_portal.py").write_text("", encoding="utf-8")

        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            check_source_registry(failed)
        assert failed == [], f"Expected no failures but got: {failed}"

    def test_source_registry_ci_guard_rejects_unregistered(self, tmp_path: Path) -> None:
        """check_source_registry() fails when a schedule.yaml agent name is not registered."""

        (tmp_path / "config" / "agent" / "data_quality").mkdir(parents=True)

        (tmp_path / "config" / "agent" / "data_quality" / "source_registry.yaml").write_text(
            yaml.dump({"entries": [_mk_entry("doc-freshness")]}),
            encoding="utf-8",
        )
        (tmp_path / ".github" / "agents").mkdir(parents=True)
        (tmp_path / ".github" / "agents" / "schedule.yaml").write_text(
            yaml.dump({"agents": [{"name": "doc-freshness"}, {"name": "unregistered-agent-xyz"}]}),
            encoding="utf-8",
        )
        (tmp_path / "scripts").mkdir(parents=True)
        (tmp_path / "scripts" / "ops_data_portal.py").write_text("", encoding="utf-8")

        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            check_source_registry(failed)
        assert "Source registry CI guard" in failed

    def test_rejects_unregistered_literal_in_producer_module(self, tmp_path: Path) -> None:
        """check_source_registry() fails when a SCANNED_PATHS producer module (not the portal
        surface) hardcodes an unregistered source literal -- the widened-scan defect this plan
        closes (Defect C: the old guard scanned only schedule.yaml and the portal files)."""

        (tmp_path / "config" / "agent" / "data_quality").mkdir(parents=True)
        (tmp_path / "config" / "agent" / "data_quality" / "source_registry.yaml").write_text(
            yaml.dump({"entries": [_mk_entry("ducklake_code_drift")]}),
            encoding="utf-8",
        )
        (tmp_path / "scripts" / "ops_data_portal.py").parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "scripts" / "ops_data_portal.py").write_text("", encoding="utf-8")
        (tmp_path / "scripts" / "convergence_health").mkdir(parents=True)
        (tmp_path / "scripts" / "convergence_health" / "code_drift.py").write_text(
            'FIELDS = {"source": "not-a-registered-source"}\n',
            encoding="utf-8",
        )

        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            check_source_registry(failed)
        assert "Source registry CI guard" in failed

    def test_producer_module_with_registered_literal_passes(self, tmp_path: Path) -> None:
        """The mirror-image positive case: a producer module hardcoding an ALREADY-registered
        source literal does not fail the widened scan."""

        (tmp_path / "config" / "agent" / "data_quality").mkdir(parents=True)
        (tmp_path / "config" / "agent" / "data_quality" / "source_registry.yaml").write_text(
            yaml.dump({"entries": [_mk_entry("ducklake_code_drift")]}),
            encoding="utf-8",
        )
        (tmp_path / "scripts" / "ops_data_portal.py").parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "scripts" / "ops_data_portal.py").write_text("", encoding="utf-8")
        (tmp_path / "scripts" / "convergence_health").mkdir(parents=True)
        (tmp_path / "scripts" / "convergence_health" / "code_drift.py").write_text(
            'FIELDS = {"source": "ducklake_code_drift"}\n',
            encoding="utf-8",
        )

        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            check_source_registry(failed)
        assert failed == [], f"Expected no failures but got: {failed}"

    def test_real_repo_backlog_health_literals_are_registered(self) -> None:
        """Integration check against the real repo (no tmp_path fixture): the four
        backlog_health source literals escalate.py writes are all registered in
        config/agent/data_quality/source_registry.yaml, so the widened scan set passes clean."""
        failed: list[str] = []
        check_source_registry(failed)
        assert failed == [], f"Expected no failures but got: {failed}"

    def test_declares_examined_outcome(self, tmp_path: Path) -> None:
        """Decision 170: the check declares examined() so it can shed its
        config/check_accounting_baseline.yaml grandfather entry."""

        (tmp_path / "config" / "agent" / "data_quality").mkdir(parents=True)
        (tmp_path / "config" / "agent" / "data_quality" / "source_registry.yaml").write_text(
            yaml.dump({"entries": [_mk_entry("manual")]}),
            encoding="utf-8",
        )
        (tmp_path / "scripts").mkdir(parents=True)
        (tmp_path / "scripts" / "ops_data_portal.py").write_text('X = {"source": "manual"}\n', encoding="utf-8")

        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks.ops_governance.check_source_registry.registry.examined") as mock_examined,
        ):
            failed: list[str] = []
            check_source_registry(failed)
        mock_examined.assert_called_once()
        assert mock_examined.call_args.args[0] == 1  # the one "manual" literal in ops_data_portal.py
        assert mock_examined.call_args.kwargs.get("unit") == "source_literals"

    def test_missing_source_registry_yaml_fails_and_declares_skipped(self, tmp_path: Path) -> None:
        """A missing source_registry.yaml is a precondition failure (not zero literals found) --
        declares skipped(), not examined(), and fails before any scan is attempted."""

        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks.ops_governance.check_source_registry.registry.skipped") as mock_skipped,
            patch("scripts.checks.ops_governance.check_source_registry.registry.examined") as mock_examined,
        ):
            failed: list[str] = []
            check_source_registry(failed)
        assert "Source registry CI guard" in failed
        mock_skipped.assert_called_once()
        mock_examined.assert_not_called()

    def test_placeholder_source_literal_is_skipped_not_counted(self, tmp_path: Path) -> None:
        """A matched literal that starts with '{' (a template/format placeholder, never a real
        registered value) is skipped rather than flagged as an unregistered violation or counted
        toward examined()."""

        (tmp_path / "config" / "agent" / "data_quality").mkdir(parents=True)
        (tmp_path / "config" / "agent" / "data_quality" / "source_registry.yaml").write_text(
            yaml.dump({"entries": [_mk_entry("manual")]}),
            encoding="utf-8",
        )
        (tmp_path / "scripts" / "ops_data_portal.py").parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "scripts" / "ops_data_portal.py").write_text(
            'X = {"source": "{dynamic_source}", "source": "manual"}\n', encoding="utf-8"
        )

        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks.ops_governance.check_source_registry.registry.examined") as mock_examined,
        ):
            failed: list[str] = []
            check_source_registry(failed)
        assert failed == [], f"Expected no failures but got: {failed}"
        # Only the "manual" literal counts -- the "{dynamic_source}" placeholder is skipped.
        assert mock_examined.call_args.args[0] == 1
