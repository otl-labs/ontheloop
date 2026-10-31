"""Tests for PlanDocument.gate_rounds (PLAN-critique-severity-convergence); GateRound's own rules live in
tests/test_plan_gate_rounds.py."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from scripts.roadmap import plan_document, plan_gate_rounds
from scripts.roadmap.plan_document import PlanDocument
from tests.fixtures.plan_document_helpers import _mutate

_HANDOFF = {"full_validation_required_before_commit": True, "timeout_disposition": "blocked"}


def _v5(**overrides) -> dict:
    return _mutate(schema_version=5, handoff_policy=dict(_HANDOFF), **overrides)


def _row(gate: str = "plan-critique", round_: int = 1, verdict: str = "PROCEED", **extra) -> dict:
    return {"gate": gate, "round": round_, "verdict": verdict, **extra}


def _wording(round_: int, verdict: str = "PASS") -> dict:
    return _row("wording-check", round_, verdict, digest="a" * 64, base="b" * 40, selection="complete")


def test_gate_round_stays_importable_from_plan_document() -> None:
    assert plan_document.GateRound is plan_gate_rounds.GateRound


def test_gate_rounds_is_optional_and_defaults_empty() -> None:
    assert not PlanDocument.model_fields["gate_rounds"].is_required()
    assert PlanDocument.model_validate(_v5()).gate_rounds == []


def test_a_full_record_validates() -> None:
    rows = [
        _row("decision-scout", 1, "FLAGS_FOUND"),
        _row(round_=1, verdict="REVISE", blocking=2, nits=3),
        _row(round_=2, verdict="PROCEED", nits=1),
        _row(round_=3, verdict="PROCEED", gate_run_id="run-3"),
        _wording(1, "FAIL"),
        _wording(2),
    ]
    doc = PlanDocument.model_validate(_v5(gate_rounds=rows))
    critique = [(r.gate, r.round, r.verdict) for r in doc.gate_rounds if r.gate == "plan-critique"]
    assert critique[-1] == ("plan-critique", 3, "PROCEED")
    assert doc.gate_rounds[-1].gate == "wording-check"


@pytest.mark.parametrize("rounds", [[2], [1, 3], [2, 1], [1, 1]])
def test_rounds_number_one_to_n_without_gaps(rounds: list[int]) -> None:
    rows = [_row(round_=n, verdict="PROCEED") for n in rounds]
    with pytest.raises(ValidationError, match="must number 1..N"):
        PlanDocument.model_validate(_v5(gate_rounds=rows))


@pytest.mark.parametrize("rounds", [[2], [1, 3], [1, 1]])
def test_wording_check_rows_number_one_to_n_like_every_gate(rounds: list[int]) -> None:
    with pytest.raises(ValidationError, match="gate_rounds for wording-check must number 1..N"):
        PlanDocument.model_validate(_v5(gate_rounds=[_wording(n) for n in rounds]))


def test_gate_rounds_require_schema_version_five() -> None:
    with pytest.raises(ValidationError, match="gate_rounds requires schema_version 5"):
        PlanDocument.model_validate(_mutate(gate_rounds=[_row()]))
