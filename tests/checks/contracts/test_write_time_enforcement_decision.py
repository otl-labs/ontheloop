"""Guard for the write-time enforcement rule and its Decision (PLAN-write-time-enforcement-decision).

The Decision number is resolved from the rule statement's opening citation, never hard-coded.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_STANDARD = _REPO_ROOT / "docs" / "contracts" / "data-modeling-standard.yaml"
_DECISIONS = _REPO_ROOT / "docs" / "DECISIONS.md"


def _norm(text: str) -> str:
    return " ".join(text.split()).lower()


def _section(text: str, number: str) -> str:
    marker = f"## Decision {number}:"
    assert marker in text, f"DECISIONS.md must carry a section for Decision {number}"
    start = text.index(marker)
    end = text.find("\n## Decision ", start + 1)
    return text[start : end if end != -1 else len(text)]


def _load() -> tuple[dict, dict, str]:
    standard = yaml.safe_load(_STANDARD.read_text(encoding="utf-8"))
    rules = {rule["id"]: rule for rule in standard["rules"]}
    assert "write-time-enforcement" in rules
    return standard, rules["write-time-enforcement"], _DECISIONS.read_text(encoding="utf-8")


def _decision_number(statement: str) -> str:
    match = re.match(r"Decision (\d+):", statement.strip())
    assert match, "write-time-enforcement statement must OPEN with 'Decision <N>:'"
    return match.group(1)


def test_write_time_enforcement_rule_declared() -> None:
    standard, rule, _ = _load()
    number = _decision_number(rule["statement"])
    statement = _norm(rule["statement"])
    for phrase in (
        "row-local",
        "required_when",
        "exactly-one-of",
        "own transaction",
        "arrival order",
        "clock domain",
        "defence in depth",
    ):
        assert phrase in statement, f"rule statement must carry {phrase!r}"
    walk = standard["design_time_walk"]
    assert "write-time-enforcement" in walk and f"Decision {number}" in walk


def test_decision_entry_and_victim_annotation() -> None:
    _, rule, decisions_text = _load()
    number = _decision_number(rule["statement"])
    section = _norm(_section(decisions_text, number))
    for anchor in ("amends: [81]", "numbered_decision", "reversal-conditions"):
        assert anchor in section, f"Decision {number} must carry {anchor!r}"
    for owner in ("rec-4024", "rec-4158", "rec-4063", "rec-4101", "rec-4121"):
        assert owner in section, f"Decision {number} must name coverage owner {owner}"
    for clause in ("monotone", "arrival order", "single source", "fix at the source", "defence in depth"):
        assert clause in section, f"Decision {number} must keep its {clause!r} clause"

    victim = _section(decisions_text, "81")
    assert re.search(rf"^\[Amendment \d{{4}}-\d{{2}}-\d{{2}}, Decision {number}:", victim, re.MULTILINE), (
        f"Decision 81 must carry a dated annotation naming Decision {number}"
    )


def test_coverage_sentence_and_decision_annotation_name_the_conformance_check() -> None:
    _, rule, decisions_text = _load()
    statement = _norm(rule["statement"])
    for phrase in (
        "validate_write_time_enforcement_conformance",
        "declared equals enforced",
        "every lambda manifest's handlers",
        "--lambda-row-rules smoke gate",
        "contract-less tables -> dormant, refused by the writer until their class a contract lands",
        "(owners t4.3 c6 and t4.5 c2)",
        "no enforced key exists",
        "the class is bound to the exempted kind",
        "ratchet (validate_ops_rule_source",
        "census: 0 violators",
        "a date never moves later or is removed",
        "rec-4121",
        "rec-4166",
        "rec-4167",
        "the portal -> write_validators.py gate_write, the writer's own gate run as a derived pre-check",
        "ops.yaml -> dq backstops only",
    ):
        assert phrase in statement, phrase
    section = _norm(_section(decisions_text, _decision_number(rule["statement"])))
    assert "[amendment 2026-10-15, rec-4158: the writer-conformance residual is closed for row-local rules" in section
    assert (
        "(validate_write_time_enforcement_conformance); a declared rule is enforced, so the enforced key is retired" in section
    )
