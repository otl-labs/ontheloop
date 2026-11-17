import json

from src.persona_backends.contract import load
from src.persona_backends.output import check, failure, repair_message
from tests.persona_backends.fakes import FIXTURE, VALID

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
