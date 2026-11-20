"""validate_contract_drift parses contracts through the shared libyaml loader (rec-5372)."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from scripts.checks.contracts import validate_contract_drift as mod

from .conftest import validate_contract_drift

_REAL_CONTRACTS_DIR = Path(__file__).resolve().parents[4] / "docs" / "contracts"


def test_contract_parses_go_through_parse_yaml_and_live_tree_passes() -> None:
    failed: list[str] = []
    with mock.patch.object(mod, "parse_yaml", wraps=mod.parse_yaml) as spy:
        validate_contract_drift(failed, contracts_dir=_REAL_CONTRACTS_DIR)
    assert spy.call_count >= len(list(_REAL_CONTRACTS_DIR.glob("*.yaml")))
    assert failed == []
