"""Mirror test for scripts/checks/roadmap/_plan_critique_end.py."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts.checks.roadmap import _plan_critique_end as module
from scripts.roadmap.plan_gate_rounds import GateRound

pytestmark = pytest.mark.unit


def _row(verdict: str = "PROCEED", nits: int = 0, choice: str | None = None, dispositions: tuple[str, ...] | None = None):
    findings = None if dispositions is None else [SimpleNamespace(severity="nit", disposition=d) for d in dispositions]
    return SimpleNamespace(verdict=verdict, nits=nits, escalation_choice=choice, effective_choice=choice, findings=findings)


@pytest.mark.parametrize("version", [6, 7])
def test_clean_proceed_confirming_round_and_deferral_end_at_both_versions(version: int) -> None:
    assert module.ended([_row()], version)
    assert module.ended([_row(nits=1), _row()], version)
    assert module.ended([_row("REVISE", choice="accept-with-deferral")], version)
    assert not module.ended([_row("REVISE")], version)
    assert not module.ended([_row("REVISE", nits=1), _row("REVISE")], version)


def test_proceed_with_nits_needs_a_round_at_6_but_disposed_nits_end_at_7() -> None:
    disposed = _row(nits=2, dispositions=("applied-wording", "build-note"))
    assert not module.ended([disposed], 6)
    assert module.ended([disposed], 7)
    assert not module.ended([_row(nits=2, dispositions=("applied-wording", "fixed"))], 7)
    assert not module.ended([_row(nits=1, dispositions=("disputed",))], 7)
    assert not module.ended([_row(nits=1)], 7)


def test_gap_message_names_the_plan_and_keeps_the_pinned_substrings() -> None:
    msg = module.gap_message("PLAN-x.yaml")
    assert msg.startswith("PLAN-x.yaml:")
    assert "must end with a plan-critique PROCEED that ends Step 9" in msg
    assert "or the confirming round after a PROCEED listing nits" in msg


def _consult_row(round_: int = 1, **extra) -> GateRound:
    data = {
        "gate": "plan-critique",
        "round": round_,
        "verdict": "REVISE",
        "blocking": 1,
        "escalated": True,
        "escalation_choice": "one more round",
        "escalation_decider": "consult",
        "findings": [],
        **extra,
    }
    data["findings"] = [
        {
            "id": f"B{round_}",
            "severity": "blocking",
            "origin": "judgement",
            "anchor": "scope",
            "subject": "s",
            "disposition": "fixed",
        }
    ]
    return GateRound(**data)


def test_an_override_to_deferral_on_a_consult_row_ends_the_series() -> None:
    plain = _consult_row()
    overridden = _consult_row(overrides_consult=True, override_choice="accept-with-deferral")
    assert not module.ended([plain], 7)
    assert module.ended([overridden], 7)


def _late_row(answered: bool) -> GateRound:
    data = {
        "gate": "plan-critique",
        "round": 2,
        "verdict": "REVISE",
        "blocking": 1,
        "escalated": True,
        "findings": [
            {
                "id": "B9",
                "severity": "blocking",
                "origin": "judgement",
                "anchor": "scope",
                "subject": "s",
                "disposition": "deferred" if answered else "fixed",
            }
        ],
    }
    if answered:
        data.update(escalation_choice="accept-with-deferral", escalation_decider="operator")
    return GateRound(**data)


def test_a_late_override_ends_the_series_on_the_latest_row() -> None:
    overridden = _consult_row(overrides_consult=True, override_choice="accept-with-deferral")
    assert module.ended([overridden, _late_row(True)], 7)
    assert not module.ended([overridden, _late_row(False)], 7)


def test_a_late_override_after_the_final_proceed_ends_the_series() -> None:
    overridden = _consult_row(overrides_consult=True, override_choice="accept-with-deferral")
    proceed = GateRound(gate="plan-critique", round=2, verdict="PROCEED", findings=[])
    assert module.ended([overridden, proceed], 7)
