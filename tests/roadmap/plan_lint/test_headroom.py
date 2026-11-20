"""headroom._load parses through the shared fast loader (rec-5372)."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from scripts.contract_rules import parse_yaml
from scripts.roadmap.plan_lint import headroom


class TestHeadroomLoader:
    def test_unbuilt_scan_parses_through_fast_loader(self, tmp_path: Path) -> None:
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "sloc_budgets.yaml").write_text("budgets:\n  a.py: 10\n", encoding="utf-8")
        with mock.patch.object(headroom, "parse_yaml", wraps=parse_yaml) as spy:
            data = headroom._load(tmp_path / "config" / "sloc_budgets.yaml")
        assert data == {"budgets": {"a.py": 10}}
        assert spy.call_count == 1

    def test_malformed_file_reads_as_none(self, tmp_path: Path) -> None:
        bad = tmp_path / "x.yaml"
        bad.write_text("a: [unclosed\n", encoding="utf-8")
        assert headroom._load(bad) is None
