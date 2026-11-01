"""The shared reader serves no event verb, with fake connections (Decision 236 cl.2).

Every action, dq_check included, binds the ops slot and never opens or reaches a telemetry connection, even with the
telemetry env still set; describe lists NAMED_READS only and an event verb name answers as an unregistered verb. The event
verbs are served by agent-platform-ducklake-telemetry-reader (tests/lambdas/ducklake_telemetry_reader/).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import src.lambdas.ducklake_reader.handler as h
from src.common import ducklake_dq_checks as dq
from src.common import ducklake_runtime as rt
from src.common.ducklake_event_read_verbs import EVENT_READS

pytestmark = pytest.mark.unit


class FakeCon:
    def __init__(self, label: str) -> None:
        self.label = label
        self.executed: list[tuple[str, Any]] = []
        self.description = [("table_name",), ("record_count",)]

    def execute(self, sql: str, params: Any = None) -> FakeCon:
        self.executed.append((sql, params))
        return self

    def fetchall(self) -> list[tuple[Any, ...]]:
        return [("telemetry_sessions", 3)]

    def close(self) -> None:
        pass


@pytest.fixture
def wiring(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    rt.reset_warm_connection()
    state: dict[str, Any] = {"ops_opened": 0, "telemetry_opened": 0}
    ops_con = FakeCon("ops")
    monkeypatch.setenv("TELEMETRY_META_SCHEMA", rt.SMOKE_META_SCHEMA)
    monkeypatch.setenv("TELEMETRY_DATA_PATH", rt.SMOKE_DATA_PATH)

    def open_ops() -> FakeCon:
        state["ops_opened"] += 1
        return ops_con

    def open_connection(**kwargs: Any) -> FakeCon:
        assert kwargs["alias"] == rt.TELEMETRY_CATALOG_ALIAS
        state["telemetry_opened"] += 1
        return FakeCon("telemetry")

    monkeypatch.setattr(h, "_open_reader_connection", open_ops)
    monkeypatch.setattr(rt, "open_connection", open_connection)
    monkeypatch.setattr(rt, "fetch_dsn", lambda *a, **k: {})
    state["ops_con"] = ops_con
    yield state
    rt.reset_warm_connection()


def _call(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    response = h.handler({"body": json.dumps(payload)})
    return response["statusCode"], json.loads(response["body"])


def test_ops_actions_never_open_the_telemetry_slot(wiring: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rt, "named_read", lambda con, **kw: [{"n": 1}])
    monkeypatch.setattr(rt, "read_current", lambda con, **kw: [])
    assert _call({"action": "named_read", "verb": "count_by_status", "params": {}})[0] == 200
    assert _call({"action": "read_current"})[0] == 200
    assert wiring["telemetry_opened"] == 0 and wiring["ops_opened"] == 1
    assert rt._named_slots.get("telemetry", {}) == {}


def test_dq_check_runs_on_the_ops_slot_only(wiring: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []
    monkeypatch.setattr(dq, "run_check", lambda con, check: seen.append(con) or 0)
    status, body = _call(
        {"action": "dq_check", "check": "row_count_min", "table": "ops_recommendations", "params": {"min": 1}}
    )
    assert status == 200 and body["violation_count"] == 0
    assert seen == [wiring["ops_con"]] and wiring["telemetry_opened"] == 0


class _Context:
    aws_request_id = "req-deadline"

    def __init__(self, remaining: Any) -> None:
        self._remaining = remaining

    def get_remaining_time_in_millis(self) -> float:
        return self._remaining() if callable(self._remaining) else self._remaining


def _call_with(payload: dict[str, Any], context: Any) -> tuple[int, dict[str, Any]]:
    response = h.handler({"body": json.dumps(payload)}, context)
    return response["statusCode"], json.loads(response["body"])


def test_ops_verbs_run_without_a_deadline(wiring: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rt, "named_read", lambda con, **kw: [{"n": 1}])
    status, body = _call_with({"action": "named_read", "verb": "count_by_status", "params": {}}, _Context(5_000))
    assert status == 200 and body["row_count"] == 1
    payload = {"action": "named_read", "verb": "count_by_status", "params": {}, "deadline_ms": 1}
    status, body = _call_with(payload, _Context(5_000))
    assert status == 200 and body["row_count"] == 1


def test_shared_reader_describe_lists_no_event_row(wiring: dict[str, Any]) -> None:
    status, body = _call({"action": "describe"})
    assert status == 200 and set(body["verbs"]) == set(rt.NAMED_READS)
    assert not set(body["verbs"]) & set(EVENT_READS)
    assert not [v for v in body["verbs"].values() if v.get("kind") == "event_journal"]
    assert wiring["telemetry_opened"] == 0 and wiring["ops_opened"] == 0


@pytest.mark.parametrize("verb", sorted(EVENT_READS))
def test_shared_reader_answers_an_event_verb_as_an_unregistered_verb(wiring: dict[str, Any], verb: str) -> None:
    payload = {"action": "named_read", "verb": verb, "params": {}, "deadline_ms": 1}
    status, body = _call_with(payload, _Context(5_000))
    assert wiring["ops_opened"] == 1 and wiring["ops_con"].executed == []
    unregistered = _call_with({"action": "named_read", "verb": "no_such_verb", "params": {}}, _Context(5_000))
    assert status == unregistered[0] == 500 and body["error_type"] == unregistered[1]["error_type"] == "runtime"
    assert wiring["telemetry_opened"] == 0 and rt._named_slots.get("telemetry", {}) == {}
