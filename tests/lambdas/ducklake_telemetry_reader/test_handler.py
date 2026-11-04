"""Telemetry reader handler tests with fake slots and DSNs (no duckdb): served set, refusals, pin, deadline."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest

import src.lambdas.ducklake_telemetry_reader.handler as h
from src.common import ducklake_event_reads as ev
from src.common import ducklake_runtime as rt
from src.common.ducklake_event_read_verbs import EVENT_READS
from src.common.ducklake_scd2_schema import NAMED_READS

pytestmark = pytest.mark.unit

_SECRET = "reader-secret"  # pragma: allowlist secret
_HOSTILE = "x'; DROP TABLE telemetry_sessions; --"


class FakeCon:
    def __init__(self, fail_first: bool = False, readonly: bool = True) -> None:
        self.executed: list[tuple[str, Any]] = []
        self.description = [("table_name",), ("record_count",)]
        self._fail_first = fail_first
        self._readonly = readonly
        self._last = ""

    def execute(self, sql: str, params: Any = None) -> FakeCon:
        self.executed.append((sql, params))
        self._last = sql
        if self._fail_first and sql.lstrip().startswith(("WITH", "SELECT t.table_name")):
            self._fail_first = False
            raise Exception("server closed the connection unexpectedly")
        return self

    def fetchall(self) -> list[tuple[Any, ...]]:
        if "duckdb_databases" in self._last:
            return [(self._readonly,)]
        return [("telemetry_sessions", 3)]

    def queries(self) -> list[str]:
        return [sql for sql, _ in self.executed if not sql.startswith(("SET", "SELECT 1"))]

    def close(self) -> None:
        pass


@pytest.fixture
def wiring(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    rt.reset_warm_connection()
    ev._INTERRUPTED_SLOTS.clear()
    state: dict[str, Any] = {"opened": 0, "kwargs": [], "secrets": [], "fail_first": False, "readonly": True, "cons": []}
    monkeypatch.setenv("TELEMETRY_META_SCHEMA", rt.TELEMETRY_PROD_META_SCHEMA)
    monkeypatch.setenv("TELEMETRY_DATA_PATH", rt.TELEMETRY_PROD_DATA_PATH)
    monkeypatch.setenv("TELEMETRY_DSN_PARAMETER", _SECRET)

    def open_connection(**kwargs: Any) -> FakeCon:
        state["opened"] += 1
        state["kwargs"].append(kwargs)
        con = FakeCon(fail_first=state["fail_first"], readonly=state["readonly"])
        state["fail_first"] = False
        state["cons"].append(con)
        return con

    def fetch_dsn(parameter_name: str = "", **_: Any) -> dict[str, str]:
        state["secrets"].append(parameter_name)
        return {"username": "ducklake_telemetry_reader"}

    monkeypatch.setattr(rt, "open_connection", open_connection)
    monkeypatch.setattr(rt, "fetch_dsn", fetch_dsn)
    yield state
    rt.reset_warm_connection()
    ev._INTERRUPTED_SLOTS.clear()


class _Context:
    aws_request_id = "req-deadline"

    def __init__(self, remaining: Any) -> None:
        self._remaining = remaining

    def get_remaining_time_in_millis(self) -> float:
        return self._remaining() if callable(self._remaining) else self._remaining


def _call(payload: Any, context: Any = None) -> tuple[int, dict[str, Any]]:
    body = payload if isinstance(payload, str) else json.dumps(payload)
    response = h.handler({"body": body}, context)
    return response["statusCode"], json.loads(response["body"])


_READ = {"action": "named_read", "verb": "telemetry_health_count", "params": {}}


def test_served_verbs_exclude_transcript_content_and_every_ops_verb() -> None:
    assert h.SERVED_VERBS == frozenset(EVENT_READS) - {"transcript_content"}
    assert "transcript_content" in EVENT_READS and "transcript_content" not in h.SERVED_VERBS
    assert h.SERVED_VERBS.isdisjoint(NAMED_READS)


def test_describe_lists_only_served_verbs_and_the_binding(wiring: dict[str, Any]) -> None:
    status, body = _call({"action": "describe"})
    assert status == 200 and body["function"] == "telemetry_reader"
    assert set(body["verbs"]) == h.SERVED_VERBS
    assert body["catalog"] == {"meta_schema": "ducklake_telemetry", "data_path_is_smoke": False}
    assert wiring["opened"] == 0


def test_named_read_of_an_unserved_verb_is_400_before_any_connect(wiring: dict[str, Any]) -> None:
    for verb in ("transcript_content", "count_by_status", _HOSTILE, 17, None):
        status, body = _call({"action": "named_read", "verb": verb, "params": {}})
        assert status == 400 and body["error_type"] == "unknown_verb" and body["served"] == sorted(h.SERVED_VERBS)
        assert _HOSTILE not in json.dumps(body) and "count_by_status" not in json.dumps(body)
        assert wiring["opened"] == 0 and wiring["secrets"] == []


@pytest.mark.parametrize("action", ["query_ops", "read_ops_current", "dq_check", _HOSTILE, None, 5])
def test_query_ops_and_unknown_actions_are_400_without_echo(wiring: dict[str, Any], action: Any) -> None:
    status, body = _call({"action": action})
    assert status == 400 and body["actions"] == ["attach_check", "describe", "named_read"]
    assert _HOSTILE not in json.dumps(body) and "query_ops" not in json.dumps(body)
    assert wiring["opened"] == 0


@pytest.mark.parametrize("meta_schema", ["ducklake_smoke", "ducklake_ops", ""])
def test_production_pin_refusal_happens_before_any_connect(
    wiring: dict[str, Any], monkeypatch: pytest.MonkeyPatch, meta_schema: str
) -> None:
    monkeypatch.setenv("TELEMETRY_META_SCHEMA", meta_schema)
    for payload in (_READ, {"action": "attach_check"}):
        status, body = _call(payload)
        assert status == 500 and body["error_type"] == "runtime"
    assert wiring["opened"] == 0 and wiring["secrets"] == []


def test_acquire_uses_the_reader_secret_and_a_read_only_attach(wiring: dict[str, Any]) -> None:
    status, body = _call(_READ)
    assert status == 200 and body["rows"] == [{"table_name": "telemetry_sessions", "record_count": 3}]
    assert wiring["secrets"] == [_SECRET]
    (kwargs,) = wiring["kwargs"]
    assert kwargs["read_only"] is True and kwargs["alias"] == rt.TELEMETRY_CATALOG_ALIAS
    assert kwargs["meta_schema"] == "ducklake_telemetry" and kwargs["data_path"] == rt.TELEMETRY_PROD_DATA_PATH
    assert body["registry_version"] == rt.NAMED_READS_VERSION and len(body["read_version"]) == 16
    assert _call(_READ)[0] == 200 and wiring["opened"] == 1


def test_attach_check_reports_read_only_from_the_live_catalog(wiring: dict[str, Any]) -> None:
    status, body = _call({"action": "attach_check"})
    assert status == 200 and body["read_only"] is True and body["catalog_login"] == "ducklake_telemetry_reader"
    assert body["meta_schema"] == "ducklake_telemetry" and body["data_path_is_smoke"] is False
    rt.reset_warm_connection()
    wiring["readonly"] = False
    status, body = _call({"action": "attach_check"})
    assert status == 200 and body["read_only"] is False
    sql, params = next(call for call in wiring["cons"][-1].executed if "duckdb_databases" in call[0])
    assert params == [rt.TELEMETRY_CATALOG_ALIAS]


def test_named_read_input_is_400(wiring: dict[str, Any]) -> None:
    bad = "private-session-value"
    status, body = _call(
        {"action": "named_read", "verb": "session_state_and_duration", "params": {"session_id": bad, "as_of": "x"}}
    )
    assert status == 400 and body["error_type"] == "named_read_input" and body["error_class"] == "EventReadInputError"
    assert bad not in json.dumps(body) and wiring["opened"] == 0


def test_dead_connection_reopens_once(wiring: dict[str, Any]) -> None:
    wiring["fail_first"] = True
    status, body = _call(_READ)
    assert status == 200 and body["row_count"] == 1 and wiring["opened"] == 2


def test_unmapped_and_catalog_unavailable_are_typed(wiring: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(**_: Any) -> Any:
        raise ValueError("secret detail 123")

    monkeypatch.setattr(rt, "open_connection", boom)
    status, body = _call(_READ)
    assert status == 500 and body["error_type"] == "unmapped" and "secret detail" not in json.dumps(body)

    def dead(**_: Any) -> Any:
        raise Exception("server closed the connection unexpectedly")

    rt.reset_warm_connection()
    monkeypatch.setattr(rt, "open_connection", dead)
    status, body = _call(_READ)
    assert status == 503 and body["error_type"] == "catalog_unavailable"


def test_response_size_guard_is_413(wiring: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(h, "MAX_RESPONSE_BYTES", 10)
    status, body = _call(_READ)
    assert status == 413 and body["error_type"] == "response_too_large" and body["action"] == "named_read"


def test_one_invocation_line_per_request(wiring: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    _call(_READ)
    _call({"action": "query_ops"})
    lines = [json.loads(ln) for ln in capsys.readouterr().out.splitlines() if "ducklake_invocation" in ln]
    assert [(ln["function"], ln["action"], ln["status"]) for ln in lines] == [
        ("telemetry_reader", "named_read", 200),
        ("telemetry_reader", "unknown", 400),
    ]


def test_bad_body_version_mismatch_runtime_and_limit(wiring: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    assert _call("[1, 2]")[0] == 400 and _call("{not json")[0] == 400
    assert _call({**_READ, "params": [1]})[1]["error_type"] == "named_read_input"
    assert _call({**_READ, "limit": 5})[1]["error_type"] == "runtime"
    window = {
        "action": "named_read",
        "verb": "sessions_window",
        "params": {"day_from": "2026-09-25", "day_to": "2026-09-26", "as_of": "2026-09-26T12:00:00+00:00"},
    }
    status, body = _call({**window, "limit": 7})
    assert status == 200 and any("LIMIT 7" in q for q in wiring["cons"][-1].queries())

    def mismatch(*_a: Any, **_k: Any) -> Any:
        raise rt.VersionMismatchError("stale")

    monkeypatch.setattr(ev, "execute", mismatch)
    status, body = _call(_READ)
    assert status == 500 and body["error_type"] == "version_mismatch"


def test_event_read_past_its_deadline_answers_504(wiring: dict[str, Any]) -> None:
    status, body = _call(_READ, _Context(5_000))
    assert (status, body) == (
        504,
        {
            "ok": False,
            "error_type": "read_deadline_exceeded",
            "reason": "reader_deadline",
            "request_id": "req-deadline",
            "action": "named_read",
        },
    )
    assert all(con.queries() == [] for con in wiring["cons"])
    assert ev.TELEMETRY_SLOT in ev._INTERRUPTED_SLOTS
    assert _call(_READ, _Context(600_000))[0] == 200


def test_reopen_once_runs_under_the_deadline(wiring: dict[str, Any]) -> None:
    remaining = iter([600_000, 5_000])
    wiring["fail_first"] = True
    status, body = _call(_READ, _Context(lambda: next(remaining)))
    assert status == 504 and body["error_type"] == "read_deadline_exceeded" and wiring["opened"] == 2


def test_describe_and_attach_check_run_without_a_deadline(wiring: dict[str, Any]) -> None:
    assert _call({"action": "describe"}, _Context(5_000))[0] == 200
    assert _call({"action": "attach_check"}, _Context(5_000))[0] == 200


def test_duckdb_caller_input_errors_are_400_and_other_engine_errors_are_unmapped(
    wiring: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    refused = type("InvalidInputException", (Exception,), {"__module__": "_duckdb"})
    other = type("BinderException", (Exception,), {"__module__": "_duckdb"})

    def raiser(error: type[Exception]) -> Any:
        def execute(*_a: Any, **_k: Any) -> Any:
            raise error("bad value")

        return execute

    monkeypatch.setattr(ev, "execute", raiser(refused))
    status, body = _call(_READ)
    assert (status, body["error_type"], body["error_class"]) == (400, "named_read_input", "InvalidInputException")
    monkeypatch.setattr(ev, "execute", raiser(other))
    assert _call(_READ)[1]["error_type"] == "unmapped"


def test_datetime_values_are_serialised_as_iso(wiring: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import datetime, timezone

    stamp = datetime(2026, 9, 26, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(ev, "execute", lambda *_a, **_k: [{"at": stamp, "n": 1}])
    status, body = _call(_READ)
    assert status == 200 and body["rows"] == [{"at": stamp.isoformat(), "n": 1}]


def test_there_is_no_ops_slot() -> None:
    with pytest.raises(rt.DuckLakeRuntimeError):
        h._no_ops_slot()


def test_event_shapes_are_parsed(wiring: dict[str, Any]) -> None:
    assert h.handler({"body": {"action": "describe"}})["statusCode"] == 200
    assert h.handler({"action": "describe"})["statusCode"] == 200
    assert h.handler({"body": None, "action": "describe"})["statusCode"] == 200
    assert h.handler({"body": 7, "action": "describe"})["statusCode"] == 200
    assert h.handler([1, 2])["statusCode"] == 400  # type: ignore[arg-type]


def test_unset_parameter_fails_closed_before_any_connect(wiring: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEMETRY_DSN_PARAMETER")
    for payload in (_READ, {"action": "attach_check"}):
        status, body = _call(payload)
        assert status == 500 and body["error_type"] == "runtime"
    assert wiring["opened"] == 0 and wiring["secrets"] == []
