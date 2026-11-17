"""rec-5321: a malformed output_schema yields a verdict-enum failure, never an exception or a whole-tree abort."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from scripts.checks.contracts.validate_persona_contracts import validate_persona_contracts
from tests.persona_contracts.fixture_tree import PERSONA, build_tree, edit_contract


def _list_properties(c: dict[str, Any]) -> None:
    c["output_schema"]["properties"] = []


def _verdict_not_mapping(c: dict[str, Any]) -> None:
    c["output_schema"]["properties"]["verdict"] = "PROCEED"


def _mixed_enum(c: dict[str, Any]) -> None:
    c["output_schema"]["properties"]["verdict"]["enum"] = ["PROCEED", 5]


@pytest.mark.parametrize("change", [_list_properties, _verdict_not_mapping, _mixed_enum])
def test_malformed_output_schema_yields_failure_not_exception(change: Any, tmp_path: Path) -> None:
    tree = build_tree(tmp_path)
    edit_contract(tree, change, rerender=True)
    failed: list[str] = []
    validate_persona_contracts(failed, tree)
    assert any("verdict" in f and "enum" in f and PERSONA in f for f in failed), failed
    assert not any("missing or unreadable" in f for f in failed), failed
