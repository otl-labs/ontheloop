"""Mirror test for scripts/checks/contracts/_rule_grammar.py: the retired `enforced` key, the not_null spellings, the
per-class vocabularies, the exemption grammar and its class-to-kind binding, the regex subset and the date floor."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.checks.contracts import _rule_grammar as grammar
from src.row_rules.rules import EXEMPTABLE_LEGS, PRODUCER_SIDE_KINDS, WAIVABLE_KINDS

_ROOT = Path(__file__).resolve().parents[3]
_WHY = {"class": "temporary", "reason": "staged", "owner": "rec-1", "review_by": "2027-01-31"}


def _errors(
    intent: dict[str, Any], *, table: str = "ops_recommendations", cls: str = "scd2", fields: dict | None = None
) -> list[str]:
    return grammar.grammar_errors(table, cls, fields or {}, {"c": intent}, _ROOT)


def _write(directory: Path, name: str, doc: dict) -> None:
    (directory / f"{name}.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")


_LEGS = {
    "not_null": {"enforced": True},
    "accepted_values": {"values": ["a"], "enforced": False},
    "min_length": {"value": 1, "enforced": True},
    "pattern": {"value": "^a$", "enforced": True},
    "array_element_format": {"pattern": "^a$", "enforced": True},
    "array_element_reference": {"enforced": True},
    "acceptance_lint": {"require_discrimination": True, "enforced": True},
    "exactly_one_of": {"fields": ["a", "b"], "enforced": False},
}


@pytest.mark.parametrize("kind", sorted(_LEGS))
def test_enforced_key_is_rejected_on_every_leg_and_baseline(tmp_path: Path, kind: str) -> None:
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    for name in ("ops_recommendations", "session-id", "exit-criteria-ledger"):
        _write(contracts, name, {"fields": {"c": {"dq_intent": {kind: _LEGS[kind]}}}})
    _write(contracts, "local", {"fields": {"c": {"$ref": "x.yaml#/f", "dq_intent_local": {kind: _LEGS[kind]}}}})
    errors, scanned = grammar.enforced_ban_errors(contracts)
    assert scanned == 4
    assert {e.split(".yaml")[0] for e in errors} == {"ops_recommendations", "session-id", "exit-criteria-ledger", "local"}
    assert all("`enforced` key is retired" in e for e in errors)
    # the grammar also rejects the legacy spelling at HEAD
    if kind == "not_null":
        assert any("unknown sub-key" in e for e in _errors({"not_null": _LEGS[kind]}))
    # a prose or unrelated `enforced` key outside dq_intent is not a leg
    _write(contracts, "other", {"audit": {"enforced": True}, "description": "enforced at write"})
    assert not any(e.startswith("other") for e in grammar.enforced_ban_errors(contracts)[0])


def test_an_unparseable_contract_is_skipped_by_the_ban(tmp_path: Path) -> None:
    (tmp_path / "broken.yaml").write_text("a: [unclosed\n", encoding="utf-8")
    assert grammar.enforced_ban_errors(tmp_path) == ([], 0)


def test_real_tree_carries_no_enforced_key() -> None:
    errors, scanned = grammar.enforced_ban_errors(_ROOT / "docs" / "contracts")
    assert errors == [] and scanned > 40


def test_not_null_spellings() -> None:
    assert _errors({"not_null": True}) == []
    assert _errors({"not_null": {"exclude_before": "2026-10-01"}}) == []
    for bad in (False, "yes", 1, None):
        assert any("not_null leg must be true or an exclude_before mapping" in e for e in _errors({"not_null": bad})), bad
    assert any("unknown sub-key" in e for e in _errors({"not_null": {"note": "x"}}))
    assert any("not a real calendar date" in e for e in _errors({"not_null": {"exclude_before": "2026-13-45"}}))
    nullable_false = {"c": {"nullable": False}}
    assert _errors({"not_null": True}, fields=nullable_false) == []
    for intent in ({}, {"not_null": {"exclude_before": "2026-10-01"}}):
        assert any("nullable false column without `not_null: true`" in e for e in _errors(intent, fields=nullable_false))


def test_per_class_vocabularies() -> None:
    assert any("outside the scd2 vocabulary" in e for e in _errors({"required_when": {"k": ["a"]}}))
    assert _errors({"required_when": {"k": ["a"]}}, table="telemetry_sessions", cls="event") == []
    assert any("outside the event vocabulary" in e for e in _errors({"min_length": {"value": 1}}, cls="event"))
    assert any("outside the control vocabulary" in e for e in _errors({"accepted_values": {"values": ["a"]}}, cls="control"))
    assert _errors({"not_null": True}, cls="control") == []
    assert any("unknown sub-key" in e for e in _errors({"min_length": {"value": 1, "bogus": 1}}))
    assert any("min_length leg must be a mapping, got int" in e for e in _errors({"min_length": 10}))
    assert any("accepted_values leg must be a mapping, got list" in e for e in _errors({"accepted_values": ["a"]}))


def test_regex_subset_and_date_floor() -> None:
    assert grammar.regex_subset_error(r"^[^\\]*$") is None
    assert grammar.regex_subset_error(r"^a\\1$") is None
    assert "backreference" in grammar.regex_subset_error(r"^(a)\1$")
    assert "lookaround" in grammar.regex_subset_error("^(?<!a)b$")
    assert "lookaround" in grammar.regex_subset_error("^(?P<n>a)(?P=n)$")
    assert "not a valid regex" in grammar.regex_subset_error("^(a$")
    assert any("pattern" in e and "lookaround" in e for e in _errors({"pattern": {"value": "^(?=a)b$"}}))
    assert any("array_element_format pattern" in e for e in _errors({"array_element_format": {"pattern": "^(a$"}}))
    when = [{"when": {"k": ["a"]}, "pattern": "^(?!x)a$"}]
    assert any(
        "pattern_when pattern" in e for e in _errors({"pattern_when": when}, table="telemetry_observations", cls="event")
    )
    assert any(
        "earlier than the Decision 64 anchor" in e
        for e in _errors({"min_length": {"value": 1, "exclude_before": "2026-04-01"}})
    )
    assert _errors({"min_length": {"value": 1, "exclude_before": "2026-04-01"}}, table="ops_decisions") == []
    assert any("not an ISO date string" in e for e in _errors({"min_length": {"value": 1, "exclude_before": "May 2026"}}))


def test_exemption_grammar() -> None:
    base = {"min_length": {"value": 3}}
    ok = {**base, "write_time_exemptions": {"min_length": _WHY}}
    assert _errors(ok) == []
    cases: list[tuple[dict, str]] = [
        ({"class": "whenever"}, "is not one of"),
        ({"reason": " "}, "needs a reason"),
        ({"owner": ""}, "needs an owner"),
        ({"owner": "someone"}, "must be a rec id"),
        ({"review_by": None}, "needs a review_by"),
        ({"review_by": "soon"}, "not an ISO date string"),
        ({"class": "cross_row"}, "is not bound to that kind"),
        ({"class": "repository_state", "owner": "scripts/no_such_file.py"}, "is not bound to that kind"),
    ]
    for change, message in cases:
        why = {**_WHY, **change}
        if change.get("review_by") is None and "review_by" in change:
            why.pop("review_by")
        errors = _errors({**base, "write_time_exemptions": {"min_length": why}})
        assert any(message in e for e in errors), (change, errors)
    assert any(
        "review_by is only for a temporary" in e
        for e in _errors(
            {
                "array_element_reference": {},
                "write_time_exemptions": {"array_element_reference": {**_WHY, "class": "cross_row"}},
            }
        )
    )
    assert any("must be a mapping with class" in e for e in _errors({**base, "write_time_exemptions": {"min_length": "text"}}))
    assert any(
        "names a rule the field does not declare" in e for e in _errors({"write_time_exemptions": {"min_length": _WHY}})
    )
    assert any(
        "cannot be exempted" in e
        for e in _errors({"exactly_one_of": {"fields": []}, "write_time_exemptions": {"exactly_one_of": _WHY}}, cls="event")
    )
    assert any("must be a mapping of rule kind" in e for e in _errors({"write_time_exemptions": ["min_length"]}))


def test_a_producer_side_rule_needs_its_exemption() -> None:
    for kind in sorted(PRODUCER_SIDE_KINDS):
        assert any("needs a write_time_exemptions entry" in e for e in _errors({kind: {}})), kind


def test_class_is_bound_to_the_exempted_kind() -> None:
    assert set(grammar.PRODUCER_CLASS) == PRODUCER_SIDE_KINDS
    assert set(grammar.EXEMPTION_CLASSES) == {"cross_row", "repository_state", "temporary"}
    path = "scripts/checks/contracts/_rule_grammar.py"
    for kind, cls in grammar.PRODUCER_CLASS.items():
        owner = "rec-1" if cls == "cross_row" else path
        permanent = {"class": cls, "reason": "r", "owner": owner}
        assert _errors({kind: {}, "write_time_exemptions": {kind: permanent}}) == [], kind
        assert _errors({kind: {}, "write_time_exemptions": {kind: _WHY}}) == [], kind  # the mark before removal
        other = next(c for c in ("cross_row", "repository_state") if c != cls)
        wrong = {"class": other, "reason": "r", "owner": "rec-1" if other == "cross_row" else path}
        assert any("not bound to that kind" in e for e in _errors({kind: {}, "write_time_exemptions": {kind: wrong}})), kind
    for kind in sorted(WAIVABLE_KINDS - {"not_null"}):
        errors = _errors(
            {kind: {"values": ["a"]}, "write_time_exemptions": {kind: {**_WHY, "class": "cross_row"}}}, cls="event"
        )
        assert any("not bound to that kind" in e for e in errors), kind
    # a content-leg exemption counts as declared on the field that carries the threshold
    sized = {grammar.CONTENT_THRESHOLD: 10, "write_time_exemptions": {"inline_within_threshold": _WHY}}
    assert _errors(sized, cls="event") == []
    for leg in sorted(EXEMPTABLE_LEGS):
        assert any("does not declare" in e for e in _errors({"write_time_exemptions": {leg: _WHY}}, cls="event")), leg
        permanent = {**_WHY, "class": "cross_row"}
        assert any(
            "not bound" in e
            for e in _errors({grammar.CONTENT_THRESHOLD: 10, "write_time_exemptions": {leg: permanent}}, cls="event")
        ), leg


def test_table_class_and_read_table(tmp_path: Path) -> None:
    assert grammar.table_class_of({"governance": {"table_class": "event"}}) == "event"
    assert grammar.table_class_of({"governance": {"table_class": "Control"}}) == "control"
    assert grammar.table_class_of({"governance": {"table_class": "SCD2"}}) == "scd2" and grammar.table_class_of({}) == "scd2"
    _write(tmp_path, "demo", {"governance": {"table_class": "event"}, "fields": {"c": {"dq_intent": {"not_null": True}}}})
    fields, intents, cls = grammar.read_table("demo", tmp_path, tmp_path)
    assert (cls, intents, list(fields)) == ("event", {"c": {"not_null": True}}, ["c"])
