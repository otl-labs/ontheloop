"""Tests for the byte leg of the structural-size engine (Decision 275, rec-5262)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.checks.structural import _byte_budget, _classify
from scripts.checks.structural._classify import effective_lines
from scripts.checks.structural.size_limits import escape_violations, validate_structural_size_limits

_REPO = Path(__file__).resolve().parents[3]
_ROW = {"slug": "contracts", "max_bytes": 1000, "relief_valve": "compact the log; split a subject"}


def _reg(**roster: int) -> dict:
    return {"byte_budgets": dict(roster)} if roster else {}


class TestByteLimit:
    def test_row_without_max_bytes_has_no_limit(self) -> None:
        assert _byte_budget.byte_limit("docs/contracts/a.yaml", {"slug": "x"}, {}) is None

    def test_non_integer_max_bytes_has_no_limit(self) -> None:
        assert _byte_budget.byte_limit("docs/contracts/a.yaml", {"max_bytes": True}, {}) is None

    def test_row_limit_applies_without_roster_entry(self) -> None:
        assert _byte_budget.byte_limit("docs/contracts/a.yaml", _ROW, _reg(b=5)) == 1000

    def test_roster_entry_overrides_for_the_exact_depth_one_path(self) -> None:
        assert _byte_budget.byte_limit("docs/contracts/a.yaml", _ROW, {"byte_budgets": {"a.yaml": 2000}}) == 2000

    def test_nested_file_never_inherits_a_depth_one_entry(self) -> None:
        reg = {"byte_budgets": {"a.yaml": 2000}}
        assert _byte_budget.byte_limit("docs/contracts/personas/a.yaml", _ROW, reg) == 1000

    def test_non_integer_roster_entry_falls_back_to_the_row(self) -> None:
        assert _byte_budget.byte_limit("docs/contracts/a.yaml", _ROW, {"byte_budgets": {"a.yaml": "big"}}) == 1000


class TestFileViolations:
    def test_at_the_limit_passes_and_one_over_fails_naming_valves(self) -> None:
        assert _byte_budget.file_violations("docs/contracts/a.yaml", 1000, _ROW, {}) == []
        out = _byte_budget.file_violations("docs/contracts/a.yaml", 1001, _ROW, {})
        assert len(out) == 1 and "split a subject" in out[0] and "1001 bytes" in out[0]

    def test_row_without_a_byte_limit_never_fails(self) -> None:
        assert _byte_budget.file_violations("a.yaml", 10**9, {"slug": "config"}, {}) == []


class TestE2:
    def test_clean_row_and_rows_without_max_bytes_pass(self) -> None:
        assert _byte_budget.e2_violations([{"slug": "contracts", "max_bytes": 49152}, {"slug": "config"}]) == []

    def test_non_integer_unpinned_and_over_maximum_each_fail_leg_labelled(self) -> None:
        out = _byte_budget.e2_violations(
            [
                {"slug": "contracts", "max_bytes": "x"},
                {"slug": "config", "max_bytes": 100},
                {"slug": "contracts", "max_bytes": 49153},
            ]
        )
        assert len(out) == 3 and all(msg.startswith("E2 (max_bytes)") for msg in out)

    def test_escape_violations_include_the_byte_leg(self) -> None:
        reg = {
            "classes": [
                {"slug": "contracts", "governed": True, "limit": 500, "max_line_chars": 2000, "max_bytes": 99999},
                {"slug": "residual", "governed": True, "limit": 500, "max_line_chars": 2000},
            ]
        }
        assert any("E2 (max_bytes)" in msg for msg in escape_violations(reg))


class TestRoster:
    def test_stale_slashed_and_non_integer_keys_fail(self, tmp_path: Path) -> None:
        (tmp_path / "docs" / "contracts" / "nested").mkdir(parents=True)
        (tmp_path / "docs" / "contracts" / "real.yaml").write_text("a: 1\n", encoding="utf-8")
        (tmp_path / "docs" / "contracts" / "nested" / "deep.yaml").write_text("a: 1\n", encoding="utf-8")
        reg = {"byte_budgets": {"real.yaml": 5, "gone.yaml": 5, "nested/deep.yaml": 5, "bad.yaml": "x"}}
        (tmp_path / "docs" / "contracts" / "bad.yaml").write_text("a: 1\n", encoding="utf-8")
        out = _byte_budget.roster_violations(reg, tmp_path)
        assert len(out) == 3
        assert any("gone.yaml" in m for m in out) and any("nested/deep.yaml" in m for m in out)
        assert any("non-integer" in m for m in out)

    def test_clean_and_absent_roster_pass_and_non_mapping_fails(self, tmp_path: Path) -> None:
        (tmp_path / "docs" / "contracts").mkdir(parents=True)
        (tmp_path / "docs" / "contracts" / "real.yaml").write_text("a: 1\n", encoding="utf-8")
        assert _byte_budget.roster_violations({"byte_budgets": {"real.yaml": 5}}, tmp_path) == []
        assert _byte_budget.roster_violations({}, tmp_path) == []
        assert len(_byte_budget.roster_violations({"byte_budgets": ["real.yaml"]}, tmp_path)) == 1


_REGISTRY = """\
schema_version: 1
exclusions:
  suffixes: [".py", ".md"]
  dirs: []
generated_pinned: []
classes:
  - slug: contracts
    include: ["docs/contracts/*.yaml"]
    governed: true
    unit: effective-lines
    limit: 500
    max_line_chars: 2000
    max_bytes: 49152
    relief_valve: "compact amendment_log rows; split a second subject"
  - slug: residual
    include: []
    governed: true
    unit: effective-lines
    limit: 500
    max_line_chars: 2000
budgets: {}
byte_budgets: {}
long_line_budgets: {}
"""


def _run_validator(root: Path, tracked: list[str]) -> list[str]:
    from unittest.mock import MagicMock

    def _ls(cmd, **kwargs):
        result = MagicMock()
        result.returncode = 0
        result.stdout = "".join(f"{p}\n" for p in tracked)
        return result

    _classify._registry_cache.clear()
    failed: list[str] = []
    with patch("scripts.checks._common.ROOT", root), patch("scripts.checks._common.run", _ls):
        validate_structural_size_limits(failed)
    return failed


def test_contract_byte_companion_fails_growth_at_line_limit(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "structural_size_budgets.yaml").write_text(_REGISTRY, encoding="utf-8")
    contracts = tmp_path / "docs" / "contracts"
    contracts.mkdir(parents=True)
    path = contracts / "fixture.yaml"
    path.write_text("key: value\n" * 500, encoding="utf-8")
    assert _run_validator(tmp_path, ["docs/contracts/fixture.yaml"]) == []

    path.write_text("key: value\n" * 499 + "k: " + "y" * 1900 + "\n" + "#" + "z" * 47000 + "\n", encoding="utf-8")
    assert effective_lines(path.read_text(encoding="utf-8")) == 500
    assert path.stat().st_size > 49152
    assert len(_run_validator(tmp_path, ["docs/contracts/fixture.yaml"])) == 1


def test_real_registry_contracts_byte_companion() -> None:
    reg = yaml.safe_load((_REPO / "config" / "structural_size_budgets.yaml").read_text(encoding="utf-8"))
    row = next(r for r in reg["classes"] if r["slug"] == "contracts")
    assert isinstance(row["max_bytes"], int) and row["max_bytes"] <= _byte_budget._PERMITTED_MAX_BYTES["contracts"]
    roster = reg.get("byte_budgets") or {}
    assert _byte_budget.roster_violations(reg, _REPO) == []
    text = (_REPO / "config" / "structural_size_budgets.yaml").read_text(encoding="utf-8")
    for key in roster:
        line = next(ln for ln in text.splitlines() if ln.strip().startswith(f"{key}:"))
        assert "# raise-approved: dec-" in line
