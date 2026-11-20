"""headroom's YAML reads go through the shared libyaml loader (rec-5372)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.roadmap.plan_lint import headroom


class TestHeadroomLoader:
    def test_unbuilt_scan_parses_through_fast_loader(self, tmp_path: Path) -> None:
        plans = tmp_path / "docs" / "plans"
        plans.mkdir(parents=True)
        (plans / "PLAN-other.yaml").write_text("schema_version: 6\nscope:\n  - file: scripts/a.py\n", encoding="utf-8")
        ctx = SimpleNamespace(root=tmp_path, plan={"slug": "mine"}, scope=[{"file": "scripts/a.py"}])
        with (
            patch.object(headroom, "_recent_plan_paths", return_value=["docs/plans/PLAN-other.yaml"]),
            patch.object(headroom, "parse_yaml", wraps=headroom.parse_yaml) as spy,
        ):
            lines = headroom.unbuilt_same_file(ctx)
        assert spy.call_count == 1
        assert lines and "also scopes scripts/a.py" in lines[0]

    def test_malformed_yaml_still_reads_as_none(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("{bad: [unclosed", encoding="utf-8")
        assert headroom._load(bad) is None
