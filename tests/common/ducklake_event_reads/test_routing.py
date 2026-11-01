"""Pin, routing and deadline tests for src/common/ducklake_event_reads.py (concern split)."""

from __future__ import annotations

import inspect
from typing import Any, Callable

import pytest

from src.common import ducklake_event_reads as ev
from src.common import ducklake_runtime as rt
from tests.fixtures.ducklake_event_read_fakes import _AS_OF, _SESSION, NO_OPS, FakeCursor, RecordingCon


def _telemetry_payload(**extra: Any) -> dict[str, Any]:
    return {"verb": "telemetry_health_count", "params": {}, **extra}


_EVENT = "01CRZ3NDEKTSV4RRFFQ69G5FAV"


def _content_params(**over: Any) -> dict[str, Any]:
    base = {
        "session_id": _SESSION,
        "producer": "claude_code",
        "event_id": _EVENT,
        "parser_version": 3,
        "offset": 0,
        "max_bytes": 1024,
    }
    return {**base, **over}


def test_acquirer_routes_only_event_verbs_to_the_telemetry_slot(declared_pin: Callable[[], tuple[str, str]]) -> None:
    def ops(force_reopen: bool = False) -> tuple[Any, dict[str, Any]]:
        return "ops-con", {}

    for action, payload in (
        ("named_read", {"verb": "open_recs_tally", "params": {}}),
        ("dq_check", {"table": "ops_recommendations", "check": "not_null", "verb": "session_state_and_duration"}),
        ("read_ops_current", {"table": "ops_recommendations"}),
        ("attach_check", {}),
    ):
        assert ev.acquirer_for(action, payload, ops, "/ext", pin=declared_pin) is ops
    event = ev.acquirer_for("named_read", {"verb": "telemetry_health_count", "params": {}}, ops, "/ext", pin=declared_pin)
    assert event is not ops


def test_a_non_string_verb_routes_to_ops(declared_pin: Callable[[], tuple[str, str]]) -> None:
    def ops() -> tuple[Any, dict[str, Any]]:
        return "ops-con", {}

    for verb in (["session_state_and_duration"], {"a": 1}, 7, None, "", "unknown_verb"):
        assert ev.acquirer_for("named_read", {"verb": verb}, ops, "/ext", pin=declared_pin) is ops
    assert ev.acquirer_for("named_read", {}, ops, "/ext", pin=declared_pin) is ops


def test_validation_and_pin_refusal_precede_any_acquire(
    monkeypatch: pytest.MonkeyPatch, declared_pin: Callable[[], tuple[str, str]], refusing_pin: Callable[[], tuple[str, str]]
) -> None:
    opened: list[str] = []

    def open_connection(**kw: Any) -> RecordingCon:
        opened.append("open")
        return RecordingCon()

    monkeypatch.setattr(rt, "open_connection", open_connection)
    monkeypatch.setattr(rt, "fetch_dsn", lambda *a, **k: {})
    with pytest.raises(ev.EventReadInputError):
        ev.acquirer_for(
            "named_read",
            {"verb": "session_state_and_duration", "params": {"session_id": "bad", "as_of": _AS_OF}},
            NO_OPS,
            "/e",
            pin=declared_pin,
        )
    with pytest.raises(rt.DuckLakeRuntimeError, match="declared pin refused"):
        ev.acquirer_for(
            "named_read",
            {"verb": "session_state_and_duration", "params": {"session_id": _SESSION, "as_of": _AS_OF}},
            NO_OPS,
            "/e",
            pin=refusing_pin,
        )
    assert opened == []
    acquire = ev.acquirer_for(
        "named_read",
        {"verb": "session_state_and_duration", "params": {"session_id": _SESSION, "as_of": _AS_OF}},
        NO_OPS,
        "/e",
        pin=declared_pin,
    )
    assert opened == []  # lazily opened: acquiring the callable pays no ATTACH
    con, meta = acquire()
    assert opened == ["open"] and meta["reused"] is False
    assert con.calls[0][0].startswith("SET memory_limit='300MB'")
    assert acquire()[1]["reused"] is True and opened == ["open"]


def test_telemetry_opener_spills_to_tmp_and_leaves_threads_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    con = RecordingCon()
    monkeypatch.setattr(rt, "open_connection", lambda **kw: con)
    monkeypatch.setattr(rt, "fetch_dsn", lambda *a, **k: {})
    ev._telemetry_opener(rt.SMOKE_META_SCHEMA, rt.SMOKE_DATA_PATH, "/e")()
    statements = [sql for sql, _ in con.calls]
    assert statements[0].startswith("SET memory_limit=") and statements[1:] == [
        "SET temp_directory='/tmp/telemetry-spill'",
        "SET max_temp_directory_size='300MB'",
    ]
    assert not any("threads" in sql or "ctid" in sql for sql in statements)


def test_deadline_ms_is_refused_before_any_attach(
    monkeypatch: pytest.MonkeyPatch, declared_pin: Callable[[], tuple[str, str]]
) -> None:
    opened: list[str] = []

    def open_connection(**kw: Any) -> RecordingCon:
        opened.append("open")
        return RecordingCon()

    monkeypatch.setattr(rt, "open_connection", open_connection)
    for bad in (0, -1, True, "5", 1.5):
        with pytest.raises(ev.EventReadInputError) as info:
            ev.acquirer_for("named_read", _telemetry_payload(deadline_ms=bad), NO_OPS, "/e", pin=declared_pin)
        assert info.value.param == "deadline_ms"
    assert opened == []
    assert callable(ev.acquirer_for("named_read", _telemetry_payload(deadline_ms=5000), NO_OPS, "/e", pin=declared_pin))


def test_deadline_ms_on_an_ops_verb_is_refused(declared_pin: Callable[[], tuple[str, str]]) -> None:
    with pytest.raises(rt.DuckLakeRuntimeError, match="deadline_ms"):
        ev.acquirer_for(
            "named_read", {"verb": "open_recs_tally", "params": {}, "deadline_ms": 5000}, NO_OPS, "/e", pin=declared_pin
        )


def test_deadline_guard_is_identity_off_event_verbs() -> None:
    for action, payload in (
        ("named_read", {"verb": "open_recs_tally", "params": {}}),
        ("dq_check", {"verb": "telemetry_health_count"}),
        ("read_ops_current", {}),
    ):
        guard = ev.deadline_guard(action, payload, lambda: 0.0)
        assert guard(object(), lambda: "ran") == "ran"
    assert ev._INTERRUPTED_SLOTS == set()


def test_deadline_guard_marks_the_slot_on_a_spent_deadline() -> None:
    guard = ev.deadline_guard("named_read", _telemetry_payload(), lambda: 5_000.0)
    ran: list[int] = []
    with pytest.raises(ev.deadline.TypedFault) as info:
        guard(object(), lambda: ran.append(1))
    assert info.value.status == 504 and ran == [] and ev.TELEMETRY_SLOT in ev._INTERRUPTED_SLOTS
    ev._INTERRUPTED_SLOTS.clear()


def test_interrupted_slot_probes_the_catalog_once(
    monkeypatch: pytest.MonkeyPatch, declared_pin: Callable[[], tuple[str, str]]
) -> None:
    probes: list[str] = []

    class ProbeCon(RecordingCon):
        def __init__(self, fail: bool = False) -> None:
            super().__init__()
            self.fail = fail

        def execute(self, sql: str, params: Any = None) -> FakeCursor:
            if sql.startswith("SELECT"):
                probes.append(sql)
                if self.fail:
                    raise RuntimeError("catalog connection is broken")
            return super().execute(sql, params)

        def close(self) -> None:
            pass

    cons = [ProbeCon(fail=False)]
    monkeypatch.setattr(rt, "open_connection", lambda **kw: cons[-1])
    monkeypatch.setattr(rt, "fetch_dsn", lambda *a, **k: {})
    acquire = ev.acquirer_for("named_read", _telemetry_payload(), NO_OPS, "/e", pin=declared_pin)
    acquire()
    assert probes == []
    ev._INTERRUPTED_SLOTS.add(ev.TELEMETRY_SLOT)
    assert acquire()[1]["reused"] is True
    assert len(probes) == 1 and "__ducklake_metadata_" in probes[0] and "ducklake_metadata" in probes[0]
    assert acquire()[1]["reused"] is True and probes[1:] == ["SELECT 1"]
    cons[0].fail = True
    cons.append(ProbeCon())
    ev._INTERRUPTED_SLOTS.add(ev.TELEMETRY_SLOT)
    con, meta = acquire()
    assert meta["reopened"] is True and con is cons[1]


def test_content_verb_hands_resolved_rows_to_the_content_module(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.common import ducklake_event_content as content

    class Cursor:
        description = [("content",), ("compared_versions",)]

        def fetchall(self) -> list[tuple[Any, ...]]:
            return [("hello", 1)]

    class Con:
        def execute(self, sql: str, params: Any = None) -> Cursor:
            self.params = params
            return Cursor()

    seen: list[Any] = []

    def serve_content(rows: Any, values: Any) -> list[dict[str, int]]:
        seen.append((rows, values))
        return [{"ok": 1}]

    monkeypatch.setattr(content, "serve_content", serve_content)
    con = Con()
    assert ev.execute(con, verb="transcript_content", params=_content_params()) == [{"ok": 1}]
    rows, values = seen[0]
    assert rows == [{"content": "hello", "compared_versions": 1}] and values["offset"] == 0
    assert sorted(con.params) == ["event_id", "parser_version", "part_from", "part_to", "producer", "session_id"]


def test_no_smoke_pin_refusal_remains() -> None:
    assert not hasattr(ev, "require_smoke_pin")
    assert not hasattr(ev, "serve_named_read") and not hasattr(ev, "is_event_verb")
    assert inspect.signature(ev.acquirer_for).parameters["pin"].default is inspect.Parameter.empty
    with pytest.raises(TypeError):
        ev.acquirer_for("named_read", _telemetry_payload(), NO_OPS, "/e")  # type: ignore[call-arg]
