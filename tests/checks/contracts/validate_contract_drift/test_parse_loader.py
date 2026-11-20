"""validate_contract_drift parses its contracts through scripts.contract_rules.parse_yaml (rec-5372)."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from scripts.checks.contracts import validate_contract_drift as vcd_module
from scripts.contract_rules import parse_yaml

from .conftest import validate_contract_drift

_REAL_CONTRACTS_DIR = Path(__file__).resolve().parents[4] / "docs" / "contracts"


def test_contract_parses_go_through_parse_yaml_and_live_tree_passes() -> None:
    failed: list[str] = []
    with mock.patch.object(vcd_module, "parse_yaml", wraps=parse_yaml) as spy:
        validate_contract_drift(failed, contracts_dir=_REAL_CONTRACTS_DIR)
    assert failed == [], failed
    assert spy.call_count >= len(list(_REAL_CONTRACTS_DIR.glob("*.yaml")))
