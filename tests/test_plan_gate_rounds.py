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


def test_kind_blind_rows() -> None:
    for verdict in ("mechanism", "direction"):
        assert GateRound.model_validate(_row("kind-blind", 1, verdict)).verdict == verdict
    with pytest.raises(ValidationError, match="verdict must be one of"):
        GateRound.model_validate(_row("kind-blind", 1, "PASS"))
    with pytest.raises(ValidationError, match="wording-check fields only"):
        GateRound.model_validate(_row("kind-blind", 1, "mechanism", digest=_DIGEST))
    with pytest.raises(ValidationError, match="no blocking or nits"):
        GateRound.model_validate(_row("kind-blind", 1, "mechanism", blocking=1))
    with pytest.raises(ValidationError, match="never escalated"):
        GateRound.model_validate(_row("kind-blind", 1, "mechanism", escalated=True))


def _draft(title: str) -> str:
    env = "significance:\n  value: numbered_decision\n  justification: r.\nkind:\n  value: direction\n  justification: j.\n"
    return f"## Decision {{{{N}}}}: {title}\n\n```yaml\nnumber: {{{{N}}}}\n{env}```\n\n**Status:** Decided\n"


_TITLES = ("First, with a comma", 'Second "quoted"')


def _critique(*items: tuple[str, str], verdict: str = "PROCEED", blocking: int = 0, **extra) -> dict:
    entries = [{"entry": title, "verdict": v} for title, v in items]
    return _row(verdict=verdict, blocking=blocking, category_consistency=entries, **extra)


def _category_plan(tmp_path, named: list[str] | None, blocking_row: bool = False):
    from types import SimpleNamespace

    import yaml

    from scripts.checks.roadmap import _plan_category_verdicts as cat

    text = "\n".join(_draft(title=t) for t in _TITLES)
    path = tmp_path / "PLAN-demo.yaml"
    path.write_text(yaml.safe_dump({"slug": "demo", "intent": "x", "context": [text]}), encoding="utf-8")
    row = _row() if named is None else _critique(*[(t, "PASS") for t in named])
    doc = SimpleNamespace(slug="demo", schema_version=5, gate_rounds=[GateRound.model_validate(row)])
    return cat.category_gaps(path, doc, tmp_path)[0]


def test_step_12p_verdict_recorded_per_drafted_entry(tmp_path) -> None:
    assert _category_plan(tmp_path, list(_TITLES)) == []
    dropped = _category_plan(tmp_path, [_TITLES[0]])
    assert len(dropped) == 1 and _TITLES[1] in dropped[0]
    misspelt = _category_plan(tmp_path, [_TITLES[0], "Secnod"])
    assert len(misspelt) == 2 and any("Secnod" in g for g in misspelt) and any(_TITLES[1] in g for g in misspelt)
    absent = _category_plan(tmp_path, None)
    assert len(absent) == 1 and "category_consistency" in absent[0] and _TITLES[0] in absent[0]
    assert GateRound.model_validate(_critique(("a", "PASS"), ("b", "PASS"))).category_consistency is not None
    assert GateRound.model_validate(_critique()).category_consistency == []


def test_category_consistency_only_on_a_plan_critique_row() -> None:
    with pytest.raises(ValidationError, match="only on a plan-critique row"):
        GateRound.model_validate(_row("decision-scout", 1, "NO_FLAGS", category_consistency=[]))


def test_category_consistency_refuses_a_duplicate_or_blank_entry() -> None:
    with pytest.raises(ValidationError, match="more than once"):
        GateRound.model_validate(_critique(("a", "PASS"), ("a ", "PASS")))
    with pytest.raises(ValidationError, match="empty after strip"):
        GateRound.model_validate(_critique(("  ", "PASS")))


def test_a_revise_entry_needs_a_revise_row_with_enough_blocking() -> None:
    with pytest.raises(ValidationError, match="12p is blocking"):
        GateRound.model_validate(_critique(("a", "REVISE")))
    with pytest.raises(ValidationError, match="12p is blocking"):
        GateRound.model_validate(_critique(("a", "REVISE"), ("b", "REVISE"), verdict="REVISE", blocking=1))
    ok = GateRound.model_validate(_critique(("a", "REVISE"), ("b", "REVISE"), verdict="REVISE", blocking=2))
    assert ok.blocking == 2


def _finding(id_: str = "N1", severity: str = "nit", disposition: str = "build-note", **extra) -> dict:
    return {
        "id": id_,
        "severity": severity,
        "origin": "judgement",
        "anchor": "scope",
        "subject": "s",
        "disposition": disposition,
        **extra,
    }


def test_findings_accepts_matching_counts_and_dispositions() -> None:
    row = _row(findings=[_finding("N1", disposition="applied-wording"), _finding("N2", disposition="disputed")], nits=2)
    assert len(GateRound(**row).findings or []) == 2
    assert GateRound(**_row(findings=[])).findings == []
    revise = _row(verdict="REVISE", blocking=1, findings=[_finding("B1", "blocking", "fixed")])
    assert GateRound(**revise).findings


@pytest.mark.parametrize(
    "row",
    [
        {"gate": "decision-scout", "round": 1, "verdict": "NO_FLAGS", "findings": []},
        _row(nits=2, findings=[_finding("N1"), _finding("N1")]),
        _row(nits=1, findings=[]),
        _row(nits=0, findings=[_finding("N1")]),
        _row(verdict="REVISE", blocking=1, findings=[_finding("B1", "blocking", "build-note")]),
        _row(verdict="REVISE", blocking=1, findings=[_finding("B1", "blocking", "deferred")]),
        _row(nits=1, findings=[_finding("N1", disposition="deferred")]),
        _row(nits=1, findings=[_finding("N1", subject="a\nb")]),
        _row(nits=1, findings=[_finding("N1", subject="x" * 201)]),
        _row(nits=1, findings=[_finding("N1", anchor=" ")]),
    ],
)
def test_findings_refuses_bad_rows(row: dict) -> None:
    with pytest.raises(ValidationError):
        GateRound(**row)


def test_deferred_blocking_accepted_only_under_accept_with_deferral() -> None:
    row = _row(
        verdict="REVISE",
        blocking=1,
        escalated=True,
        escalation_choice="accept-with-deferral",
        findings=[_finding("B1", "blocking", "deferred")],
    )
    assert GateRound(**row).findings


def test_version_errors_at_6_and_7() -> None:
    from scripts.roadmap.plan_gate_rounds import version_errors

    bare = [GateRound(**_row())]
    with_findings = [GateRound(**_row(findings=[]))]
    deferral = [GateRound(**_row(escalated=True, escalation_choice="accept-with-deferral", findings=[]))]
    assert version_errors(6, bare) == [] and version_errors(7, with_findings) == []
    assert "findings requires schema_version 7" in version_errors(6, with_findings)[0]
    assert "findings: [{id" in version_errors(7, bare)[0]
    assert "accept-with-deferral on a PROCEED" in version_errors(7, deferral)[0]
    assert version_errors(7, [GateRound(gate="decision-scout", round=1, verdict="NO_FLAGS")]) == []


def _consult(**extra) -> dict:
    row = {"escalated": True, "escalation_choice": "one more round", "escalation_decider": "consult"}
    return _row(verdict="REVISE", blocking=1, **{**row, **extra})


def test_consult_decides_only_one_more_round() -> None:
    assert GateRound(**_consult()).escalation_decider == "consult"
    for choice in ("re-scope", "split", "abandon", "accept-with-deferral"):
        with pytest.raises(ValidationError, match="only one more round"):
            GateRound(**_consult(escalation_choice=choice))


def test_override_is_written_on_the_consults_row() -> None:
    row = GateRound(**_consult(overrides_consult=True, override_choice="re-scope"))
    assert row.escalation_decider == "consult" and row.effective_choice == "re-scope"
    assert GateRound(**_consult()).effective_choice == "one more round"
    refused = [
        _consult(escalation_decider="operator", overrides_consult=True, override_choice="re-scope"),
        _consult(escalation_decider=None, overrides_consult=True, override_choice="re-scope"),
        _consult(overrides_consult=True),
        _consult(override_choice="re-scope"),
        _consult(overrides_consult="yes", override_choice="re-scope"),
        _consult(overrides_consult=1, override_choice="re-scope"),
        _consult(overrides_consult=True, override_choice="one more round"),
    ]
    for bad in refused:
        with pytest.raises(ValidationError):
            GateRound(**bad)
    with pytest.raises(ValidationError, match="names a different choice"):
        GateRound(**_consult(overrides_consult=True, override_choice="one more round"))


def test_an_override_to_deferral_allows_deferred_blocking_findings() -> None:
    from scripts.roadmap.plan_gate_rounds import version_errors

    deferred = [_finding("B1", "blocking", "deferred")]
    assert GateRound(**_consult(overrides_consult=True, override_choice="accept-with-deferral", findings=deferred))
    with pytest.raises(ValidationError):
        GateRound(**_consult(findings=deferred))
    proceed = _row(
        escalated=True,
        escalation_choice="one more round",
        escalation_decider="consult",
        overrides_consult=True,
        override_choice="accept-with-deferral",
        findings=[],
    )
    assert "accept-with-deferral on a PROCEED" in version_errors(7, [GateRound(**proceed)])[0]


def test_consult_route_is_plan_critique_only() -> None:
    scout = {
        "gate": "decision-scout",
        "round": 1,
        "verdict": "BLOCK",
        "escalated": True,
        "escalation_choice": "one more round",
    }
    with pytest.raises(ValidationError, match="plan-critique gate's alone"):
        GateRound(**{**scout, "escalation_decider": "consult"})
    with pytest.raises(ValidationError):
        GateRound(**{**_wording(), "overrides_consult": True, "override_choice": "re-scope"})
    assert GateRound(**{**scout, "escalation_decider": "operator"}).escalation_decider == "operator"


def test_decider_needs_an_escalation_choice() -> None:
    with pytest.raises(ValidationError, match="needs an escalation_choice"):
        GateRound(**_row(verdict="REVISE", blocking=1, escalated=True, escalation_decider="operator"))


def test_consult_decides_once_per_plan() -> None:
    from scripts.roadmap.plan_gate_rounds import version_errors

    one = GateRound(**_consult(findings=[_finding("B1", "blocking", "fixed")]))
    two = GateRound(**_consult(round=2, findings=[_finding("B2", "blocking", "fixed")]))
    assert version_errors(7, [one]) == []
    assert version_errors(7, [one, two]) == ["a consult decides once per plan (Decision 287 cl.3)"]
