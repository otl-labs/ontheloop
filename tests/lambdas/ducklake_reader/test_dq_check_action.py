"""The reader's dq_check action: dispatch, response shape, typed 400 before any connection (query-ops-retirement PR A)."""

from __future__ import annotations

import json
from typing import Any

import pytest

import src.lambdas.ducklake_reader.handler as h
from src.common import ducklake_dq_checks as dq
from src.common import ducklake_runtime as rt
from tests.fixtures.ducklake_reader_fakes import CountCon

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_warm_connection():
    rt.reset_warm_connection()
    yield
    rt.reset_warm_connection()


def _call(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    response = h.handler({"body": json.dumps(payload)})
    return response["statusCode"], json.loads(response["body"])


def test_dq_check_answers_count_and_check_version(monkeypatch: pytest.MonkeyPatch) -> None:
    con = CountCon(3)
    monkeypatch.setattr(h, "_open_reader_connection", lambda: con)
    status, body = _call(
        {"action": "dq_check", "check": "not_null", "table": "ops_recommendations", "params": {"column": "title"}}
    )
    assert status == 200
    assert body == {
        "ok": True,
        "check": "not_null",
        "table": "ops_recommendations",
        "violation_count": 3,
        "check_version": dq.check_version("not_null"),
    }
    assert len(con.executed) == 1
    assert "title" in con.executed[0][0] and "rows" not in body


def test_refused_dq_check_is_400_before_any_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[int] = []
    monkeypatch.setattr(h, "_open_reader_connection", lambda: opened.append(1) or CountCon())
    refused_marker = "REFUSED-VALUE-9f2"
    status, body = _call(
        {
            "action": "dq_check",
            "check": "pattern",
            "table": "ops_recommendations",
            "params": {"column": refused_marker, "value": "x"},
        }
    )
    assert status == 400 and body["error_type"] == "dq_check_input"
    assert body["check"] == "pattern" and body["param"] == "column"
    assert refused_marker not in json.dumps(body)
    status, body = _call({"action": "dq_check", "check": "not_null", "table": "telemetry_sessions", "params": {"column": "x"}})
    assert status == 400 and body["param"] == "table"
    status, body = _call({"action": "dq_check", "check": "drop_table", "table": "ops_recommendations"})
    assert status == 400 and body["param"] == "check" and body["check"] == "drop_table"
    assert opened == []


def test_no_reader_action_accepts_caller_sql() -> None:
    import ast
    import inspect

    assert "query_ops" not in h._ACTIONS
    for name, fn in h._ACTIONS.items():
        for node in ast.walk(ast.parse(inspect.getsource(fn).lstrip())):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "get" and node.args:
                first = node.args[0]
                assert not (isinstance(first, ast.Constant) and first.value == "sql"), name
            if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
                assert node.slice.value != "sql", name
