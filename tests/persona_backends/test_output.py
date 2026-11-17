import json

from src.persona_backends.contract import load
from src.persona_backends.output import check, failure, repair_message
from tests.persona_backends.fakes import FIELD_SCHEMA, FIXTURE, RULE, VALID, rule_contract

LOADED = load(FIXTURE)


def test_valid_success():
    assert check(VALID, "stop", LOADED).value["verdict"] == "PROCEED"


def test_listed_failure():
    value = failure("budget_exhausted", "x")
    assert check(json.dumps(value), "stop", LOADED).value == value


def test_unlisted_failure_id_and_extra_keys():
    assert check(json.dumps(failure("made_up", "x")), "stop", LOADED).errors
    assert check(json.dumps({**failure("budget_exhausted", "x"), "extra": 1}), "stop", LOADED).errors


def test_blank_unparseable_nonobject_and_length():
    for content, finish in [("  ", "stop"), ("nope", "stop"), ("[1]", "stop"), (VALID, "length")]:
        assert check(content, finish, LOADED).value is None
        assert check(content, finish, LOADED).errors


def test_schema_violation_lists_paths():
    bad = json.dumps({"outcome": "success", "verdict": "MAYBE", "findings": []})
    errors = check(bad, "stop", LOADED).errors
    assert errors and errors[0].startswith("$['verdict']")


def test_repair_message_lists_errors():
    text = repair_message(["$: a", "$: b"])
    assert text.startswith("Your last reply is not a valid result:\n$: a\n$: b\n")
    assert text.endswith("a failure the contract lists.")


def test_failure_detail_must_be_a_string():
    value = {"outcome": "failure", "failure": "budget_exhausted", "detail": 5}
    assert check(json.dumps(value), "stop", LOADED).errors == ["$['detail']: must be a string"]


def _ruled(tmp_path):
    return load(rule_contract(tmp_path, RULE, FIELD_SCHEMA))


def _success(verdict, *severities):
    return json.dumps({"outcome": "success", "verdict": verdict, "findings": [{"severity": s} for s in severities]})


def test_contradictory_verdict_is_an_error(tmp_path):
    result = check(_success("PROCEED", "blocking"), "stop", _ruled(tmp_path))
    assert result.value is None and "the verdict rule gives REVISE" in result.errors[0]


def test_agreeing_success_and_unchecked_failure(tmp_path):
    loaded = _ruled(tmp_path)
    assert check(_success("REVISE", "blocking"), "stop", loaded).errors == []
    assert check(_success("PROCEED", "nit"), "stop", loaded).errors == []
    value = failure("budget_exhausted", "x")
    assert check(json.dumps(value), "stop", loaded).value == value


def test_schema_invalid_value_reports_schema_errors_only(tmp_path):
    bad = json.dumps({"outcome": "success", "verdict": "MAYBE", "findings": [{"severity": "blocking"}]})
    errors = check(bad, "stop", _ruled(tmp_path)).errors
    assert errors and all("verdict rule" not in e for e in errors)
