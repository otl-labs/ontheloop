import json
from pathlib import Path

import pytest
import yaml

from src.persona_backends import contract as backend_contract
from src.persona_backends import verdict
from tests.persona_backends.fakes import FIELD_SCHEMA, RULE, ScriptedTransport, drive, final, rule_contract

CONTRADICTION = json.dumps({"outcome": "success", "verdict": "PROCEED", "findings": [{"severity": "blocking"}]})
AGREEING = json.dumps({"outcome": "success", "verdict": "REVISE", "findings": [{"severity": "blocking"}]})


def _contract(tmp_path):
    return rule_contract(tmp_path, RULE, FIELD_SCHEMA)


def test_contradictory_verdict_returns_schema_invalid(tmp_path):
    transport = ScriptedTransport([final(CONTRADICTION), final(CONTRADICTION)])
    result, _ = drive(transport, contract=_contract(tmp_path))
    assert result["failure"] == "schema_invalid"
    repair = transport.calls[1]["messages"][-1]["content"]
    assert "the verdict rule gives REVISE" in repair


def test_repaired_verdict_returns_success(tmp_path):
    result, events = drive(ScriptedTransport([final(CONTRADICTION), final(AGREEING)]), contract=_contract(tmp_path))
    assert result["verdict"] == "REVISE" and events[-1]["revisions_used"] == 1


def test_failure_value_passes_unchecked(tmp_path):
    value = {"outcome": "failure", "failure": "budget_exhausted", "detail": "x"}
    result, _ = drive(ScriptedTransport([final(json.dumps(value))]), contract=_contract(tmp_path))
    assert result == value


def test_contract_without_rule_is_unchanged():
    lenient = json.dumps({"outcome": "success", "verdict": "PROCEED", "findings": ["x"]})
    result, _ = drive(ScriptedTransport([final(lenient)]))
    assert result["verdict"] == "PROCEED"


_REAL = Path(__file__).resolve().parents[2] / "docs" / "contracts"


def _real(persona: str) -> Path:
    return _REAL / "personas" / f"{persona}.yaml"


def _f(severity: str) -> dict:
    return {"severity": severity}


_REAL_CASES = [
    ("code_reviewer", {"findings": [_f("Medium")]}, "PROCEED"),
    ("code_reviewer", {"findings": [_f("High")]}, "REVISE"),
    ("code_reviewer", {"findings": [_f("Critical")]}, "REVISE"),
    ("plan_critic", {"findings": [_f("nit")]}, "PROCEED"),
    ("plan_critic", {"findings": [_f("blocking")]}, "REVISE"),
    ("decision_scout", {"contradictions": [_f("WARN")], "spirit": [], "roadmap": {"rows": []}}, "FLAGS_FOUND"),
    ("decision_scout", {"contradictions": [_f("NOTE")], "spirit": [], "roadmap": {"rows": []}}, "FLAGS_FOUND"),
    ("decision_scout", {"contradictions": [], "spirit": [{"x": 1}], "roadmap": {"rows": []}}, "FLAGS_FOUND"),
    ("decision_scout", {"contradictions": [], "spirit": [], "roadmap": {"rows": [{"lane": "CONFLICT"}]}}, "FLAGS_FOUND"),
    (
        "decision_scout",
        {"contradictions": [_f("BLOCK")], "spirit": [], "roadmap": {"rows": [{"lane": "CONFLICT"}]}},
        "BLOCK",
    ),
    (
        "decision_scout",
        {"contradictions": [], "spirit": [], "roadmap": {"rows": [{"lane": "CITE"}, {"lane": "RELATED"}]}},
        "NO_FLAGS",
    ),
    ("decision_scout", {"contradictions": [], "spirit": [], "roadmap": {"rows": []}}, "NO_FLAGS"),
]


@pytest.mark.parametrize(("persona", "value", "expected"), _REAL_CASES)
def test_real_contract_rules(persona, value, expected):
    loaded = backend_contract.load(_real(persona))
    assert verdict.expected(loaded.verdict_consistency, value)[0] == expected


def _review_input() -> dict:
    data = yaml.safe_load(_real("code_reviewer").read_text(encoding="utf-8"))
    out = {}
    for key, spec in data["input_schema"]["properties"].items():
        out[key] = "a" * 40 if key == "head_sha" else "x"
    return out


def _review_turn() -> str:
    finding = {
        "id": "a.py:1:testing",
        "severity": "High",
        "origin": "judgement",
        "anchor": "a.py:1",
        "dimension": "testing",
        "file": "a.py",
        "line": 1,
        "title": "A finding with a long title",
        "detail": "d",
        "acceptance": "a",
        "effort": "S",
        "risk": "low",
    }
    value = {
        "outcome": "success",
        "verdict": "PROCEED",
        "summary": "s",
        "plan_context": "absent",
        "acceptance_check": [],
        "findings": [finding],
        "test_completeness": "t",
        "weakening_dispositions": [],
        "exemptions": {"status": "unavailable", "matched": 0},
    }
    return json.dumps(value)


def test_real_code_reviewer_contradiction_is_schema_invalid():
    loaded = backend_contract.load(_real("code_reviewer"))
    turns = [final(_review_turn()) for _ in range(loaded.max_revisions + 1)]
    transport = ScriptedTransport(turns)
    result, _ = drive(transport, _review_input(), contract=_real("code_reviewer"))
    assert result["failure"] == "schema_invalid"
    assert "REVISE" in transport.calls[1]["messages"][-1]["content"]


def test_index_declares_and_requires_rule():
    index = yaml.safe_load((_REAL / "executor-personas.yaml").read_text(encoding="utf-8"))
    pc = index["persona_contract"]
    assert "verdict_consistency" in pc["fields"]
    assert "verdict_consistency" in pc["required_for_verdict"]
