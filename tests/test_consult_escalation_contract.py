"""rec-5645: the Decision, the planning skill, plan.md, the consult skill and coordinator.yaml carry the consult route."""

from __future__ import annotations

from pathlib import Path

import yaml

from tests.fixtures.decision_entry import decision_entry

REPO = Path(__file__).resolve().parents[1]
DECISION = 287


def _norm(text: str) -> str:
    return " ".join(text.split())


def _read(path: str) -> str:
    return _norm((REPO / path).read_text(encoding="utf-8"))


def test_decision_records_consult_escalation_authority() -> None:
    entry = _norm(decision_entry((REPO / "docs/DECISIONS.md").read_text(encoding="utf-8"), DECISION))
    assert "value: direction" in entry
    for phrase in (
        "compute_plane persona",
        "weakens verification (Decision 235 cl.1)",
        "choose one more round",
        "accept-with-deferral, re-scope, split and abandon stay the operator's",
        "hands such an escalation back",
        "both positions unattributed",
        "hands back unruled, with the reason",
        "escalation_decider consult",
    ):
        assert _norm(phrase) in entry, phrase


def test_planning_skill_routes_convergence_escalations_to_consult() -> None:
    planning = _read(".claude/skills/planning/SKILL.md")
    for phrase in (
        "for this gate alone (the Decision Scout and Report Critique gates escalate to the human)",
        "a convergence escalation (any trigger but the last) goes to a consult per the `consult` skill",
        "It decides once per finding",
        "any escalation after its one more round",
        "once per plan",
        "a finding that the plan weakens verification (Decision 235 cl.1)",
        "`escalation_decider`",
        "recorded on the consult's row as `overrides_consult: true` with their `override_choice`, which then counts as "
        "the row's choice",
        "An override made after a later round is also the human's answer on the latest plan-critique row",
        "after the series' final PROCEED, record it on the consult's row alone",
        "both positions unattributed",
        "the consult does not read `gate_rounds` or `context`",
        "quotes each anchor's field text",
        "where it holds: fix it",
        "hands the escalation back to the human",
        "reopens the loop under that choice",
        "(every row with `escalation_decider: consult`)",
        "(a later escalated row in that plan that the human decided)",
    ):
        assert phrase in planning, phrase
    assert "It recommends a disposition and decides nothing" not in planning
    assert "or the consult a convergence escalation" in _read(".claude/commands/plan.md")
    consult = _read(".claude/skills/consult/SKILL.md")
    for phrase in (
        "except a convergence escalation the planning skill's Escalate rule routes to it",
        "records the choice without rejecting it",
        "holds, does not hold or cannot determine",
        "No ruling: <reason>; missing: <what would settle it>",
        "guard-input (5c) or category-consistency (12p)",
        "or any convergence escalation the planning skill's Escalate rule routes to it",
    ):
        assert phrase in consult, phrase


def test_coordinator_routes_convergence_escalations_to_consult() -> None:
    contract = yaml.safe_load((REPO / "docs/contracts/coordinator.yaml").read_text(encoding="utf-8"))
    rows = [r for r in contract["routing"]["table"] if r["situation"].startswith("convergence escalation in plan critique")]
    assert len(rows) == 1
    assert "choosing only one more round" in rows[0]["route"] and "weakens verification" in rows[0]["route"]
    assert "except a convergence escalation routed to it" in _norm(contract["roles"]["advisor"])
