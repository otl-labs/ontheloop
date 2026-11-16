"""Mirror test for the references leg of src/row_rules/rules.py (Decision 268): RowRules.references is built from a
projected array_element_reference: true; any other value, or the leg beside its exemption, is refused. check_row never
evaluates references (the engine stays pure)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.row_rules.rules import WRITER_REFERENCE_KINDS, RowRules, RuleProjectionError, check_row

_WHY = {"class": "cross_row", "reason": "r", "owner": "rec-1"}


def _columns(**leg: object) -> dict:
    return {"id": {"nullable": False}, "deps": {"nullable": True, "array_element_reference": True, **leg}}


def test_references_built_from_projected_leg() -> None:
    rules = RowRules.from_projection(_columns())
    assert rules.references == frozenset({"deps"})
    assert rules.producer_exemptions == {}
    assert RowRules.from_projection({"id": {"nullable": False}}).references == frozenset()
    assert WRITER_REFERENCE_KINDS == frozenset({"array_element_reference"})
    now = datetime(2026, 11, 16, tzinfo=timezone.utc)
    check_row("t", {"id": "x", "deps": ["rec-99999"]}, rules, now)  # the engine never looks anything up


@pytest.mark.parametrize("value", [False, None, {}, "yes", 1])
def test_reference_leg_value_must_be_true(value: object) -> None:
    columns = {"deps": {"nullable": True, "array_element_reference": value}}
    with pytest.raises(RuleProjectionError, match="must be true"):
        RowRules.from_projection(columns)


def test_reference_leg_with_its_exemption_is_refused() -> None:
    columns = _columns(write_time_exemptions={"array_element_reference": _WHY})
    with pytest.raises(RuleProjectionError, match="exempt and must not also ship"):
        RowRules.from_projection(columns)
    exempt_only = {"deps": {"nullable": True, "write_time_exemptions": {"array_element_reference": _WHY}}}
    rules = RowRules.from_projection(exempt_only)
    assert rules.references == frozenset() and ("deps", "array_element_reference") in rules.producer_exemptions
