import json

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
