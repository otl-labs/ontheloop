"""TestBuildReport, moved unchanged out of tests/test_plan_obligations.py (PLAN-plan-lint relief)."""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts.roadmap import plan_obligations


def _base_scope() -> dict:
    return {
        "schema_version": 4,
        "plan_type": "IMPLEMENTATION",
        "slug": "fixture-probe",
        "plan_path": "docs/plans/PLAN-fixture-probe.yaml",
        "scope": [
            {"file": "scripts/checks/roadmap/validate_x.py", "action": "Create", "purpose": "p"},
            {"file": "scripts/checks/roadmap/_manifest.py", "action": "Modify", "purpose": "p"},
            {"file": "config/ci_rca_taxonomy.yaml", "action": "Modify", "purpose": "p"},
            {"file": "tests/checks/roadmap/test_validate_x.py", "action": "Create", "purpose": "p"},
        ],
    }


def _write(tmp_path: Path, data: dict, name: str = "PLAN-fixture-probe.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


class TestBuildReport:
    def test_complete_plan_reports_no_findings(self, tmp_path: Path) -> None:
        path = _write(tmp_path, _base_scope())
        report = plan_obligations.build_report(path)
        assert "no unmet registration obligations" in report

    def test_incomplete_plan_reports_each_finding(self, tmp_path: Path) -> None:
        data = _base_scope()
        data["scope"] = [r for r in data["scope"] if r["file"] != "config/ci_rca_taxonomy.yaml"]
        path = _write(tmp_path, data)
        report = plan_obligations.build_report(path)
        assert "config/ci_rca_taxonomy.yaml" in report
