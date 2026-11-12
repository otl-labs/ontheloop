"""Tests for scripts/roadmap/plan_gate_rounds.py (GateRound, moved out of plan_document, plus the wording-check gate)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from scripts.roadmap.plan_gate_rounds import GateRound

_DIGEST = "a" * 64
_BASE = "b" * 40


def _row(gate: str = "plan-critique", round_: int = 1, verdict: str = "PROCEED", **extra) -> dict:
    return {"gate": gate, "round": round_, "verdict": verdict, **extra}


def _wording(verdict: str = "PASS", **extra) -> dict:
    return _row("wording-check", 1, verdict, **{"digest": _DIGEST, "base": _BASE, "selection": "complete", **extra})


@pytest.mark.parametrize(
    ("gate", "verdict"),
    [
        ("plan-critique", "BLOCK"),
        ("plan-critique", "PROCEED-WITH-NITS"),
        ("decision-scout", "PROCEED"),
        ("wording-check", "PROCEED"),
    ],
)
def test_verdict_must_belong_to_its_gate(gate: str, verdict: str) -> None:
    with pytest.raises(ValidationError, match="verdict must be one of"):
        GateRound.model_validate(_row(gate, 1, verdict, digest=_DIGEST, base=_BASE, selection="complete"))


def test_gate_is_one_of_the_known_gates() -> None:
    with pytest.raises(ValidationError):
        GateRound.model_validate(_row("report-critique", 1, "PROCEED"))


@pytest.mark.parametrize(("verdict", "blocking"), [("REVISE", 0), ("PROCEED", 1)])
def test_plan_critique_verdict_follows_blocking_count(verdict: str, blocking: int) -> None:
    with pytest.raises(ValidationError, match="REVISE carries at least one blocking finding"):
        GateRound.model_validate(_row(verdict=verdict, blocking=blocking))


def test_nits_alone_are_a_proceed() -> None:
    assert GateRound.model_validate(_row(verdict="PROCEED", nits=4)).blocking == 0


def test_escalation_choice_requires_an_escalation() -> None:
    with pytest.raises(ValidationError, match="set escalated: true"):
        GateRound.model_validate(_row(verdict="REVISE", blocking=1, escalation_choice="one more round"))


def test_escalation_choice_is_the_five_choice_menu() -> None:
    with pytest.raises(ValidationError):
        GateRound.model_validate(_row(verdict="REVISE", blocking=1, escalated=True, escalation_choice="merge"))


def test_negative_counts_are_refused() -> None:
    with pytest.raises(ValidationError):
        GateRound.model_validate(_row(nits=-1))


@pytest.mark.parametrize("verdict", ["PASS", "FAIL"])
def test_a_wording_check_row_carries_digest_base_and_selection(verdict: str) -> None:
    row = GateRound.model_validate(_wording(verdict))
    assert (row.verdict, row.digest, row.base, row.selection) == (verdict, _DIGEST, _BASE, "complete")


@pytest.mark.parametrize("missing", ["digest", "base", "selection"])
def test_a_wording_check_row_needs_every_run_fact(missing: str) -> None:
    data = _wording()
    del data[missing]
    with pytest.raises(ValidationError, match="needs digest, base and selection"):
        GateRound.model_validate(data)


@pytest.mark.parametrize("gate", ["plan-critique", "decision-scout"])
def test_other_gates_refuse_the_run_facts(gate: str) -> None:
    verdict = "PROCEED" if gate == "plan-critique" else "NO_FLAGS"
    with pytest.raises(ValidationError, match="wording-check fields only"):
        GateRound.model_validate(_row(gate, 1, verdict, digest=_DIGEST))


def test_the_digest_is_sixty_four_hex_characters() -> None:
    with pytest.raises(ValidationError, match="64 lowercase hex"):
        GateRound.model_validate(_wording(digest="abc"))


def test_the_base_is_a_forty_hex_commit_sha() -> None:
    with pytest.raises(ValidationError, match="40 hex"):
        GateRound.model_validate(_wording(base="main"))


def test_selection_is_complete_or_degraded() -> None:
    with pytest.raises(ValidationError):
        GateRound.model_validate(_wording(selection="partial"))
    assert GateRound.model_validate(_wording(selection="degraded")).selection == "degraded"


@pytest.mark.parametrize("extra", [{"blocking": 1}, {"nits": 1}])
def test_a_wording_check_row_carries_no_counts(extra: dict) -> None:
    with pytest.raises(ValidationError, match="no blocking or nits"):
        GateRound.model_validate(_wording(**extra))


def test_pending_evaluators_is_a_count_on_a_wording_check_pass_row() -> None:
    assert GateRound.model_validate(_wording("PASS", pending_evaluators=2)).pending_evaluators == 2
    assert GateRound.model_validate(_wording("PASS")).pending_evaluators is None


@pytest.mark.parametrize(
    "data",
    [
        _wording("FAIL", pending_evaluators=1),
        _row(pending_evaluators=1),
        _row("decision-scout", 1, "NO_FLAGS", pending_evaluators=1),
    ],
)
def test_pending_evaluators_is_refused_outside_a_wording_check_pass(data: dict) -> None:
    with pytest.raises(ValidationError, match="only on a wording-check PASS row"):
        GateRound.model_validate(data)


def test_pending_evaluators_is_at_least_one() -> None:
    with pytest.raises(ValidationError):
        GateRound.model_validate(_wording("PASS", pending_evaluators=0))


@pytest.mark.parametrize("fingerprint", ["v1:" + "a" * 64, "v2:" + "0" * 64, "v10:" + "f" * 64])
def test_a_decision_scout_row_accepts_a_scout_input_fingerprint(fingerprint: str) -> None:
    row = GateRound.model_validate(_row("decision-scout", 1, "FLAGS_FOUND", scout_input_fingerprint=fingerprint))
    assert row.scout_input_fingerprint == fingerprint


def test_scout_input_fingerprint_defaults_to_none() -> None:
    assert GateRound.model_validate(_row("decision-scout", 1, "NO_FLAGS")).scout_input_fingerprint is None


@pytest.mark.parametrize(
    "data", [_row(scout_input_fingerprint="v1:" + "a" * 64), _wording(scout_input_fingerprint="v1:" + "a" * 64)]
)
def test_other_gates_refuse_scout_input_fingerprint(data: dict) -> None:
    with pytest.raises(ValidationError, match="only on a decision-scout row"):
        GateRound.model_validate(data)


@pytest.mark.parametrize("fingerprint", ["a" * 64, "v0:" + "a" * 64, "v1:" + "A" * 64, "v1:" + "a" * 63, "v:" + "a" * 64, ""])
def test_a_bad_scout_input_fingerprint_is_refused(fingerprint: str) -> None:
    with pytest.raises(ValidationError, match="scout_input_fingerprint"):
        GateRound.model_validate(_row("decision-scout", 1, "NO_FLAGS", scout_input_fingerprint=fingerprint))
