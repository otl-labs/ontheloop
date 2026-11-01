"""Parameter-binding tests for src/common/ducklake_event_reads.py (concern split of test_ducklake_event_reads.py)."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import pytest

from src.common import ducklake_event_read_verbs as verbs
from src.common import ducklake_event_reads as ev
from src.common import ducklake_runtime as rt
from tests.fixtures.ducklake_event_read_fakes import _AS_OF, _SESSION, NO_OPS, RecordingCon

_STATE = verbs.EVENT_READS["session_state_and_duration"]


_WINDOW = verbs.EVENT_READS["sessions_window"]


_CONTENT = verbs.EVENT_READS["transcript_content"]


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


_TREE = verbs.EVENT_READS["session_tree_rows"]


_ROOT = "01M3C03V80C6ZZ53H4MAGFS7Z4"


def test_session_id_must_be_a_canonical_ulid() -> None:
    for bad in (_SESSION.lower(), _SESSION[:-1], _SESSION + "0", "", 17, None, "ILOU" + _SESSION[4:]):
        with pytest.raises(ev.EventReadInputError):
            ev.bind_values(_STATE, {"session_id": bad, "as_of": _AS_OF})
    assert ev.bind_values(_STATE, {"session_id": _SESSION, "as_of": _AS_OF})["session_id"] == _SESSION


def test_as_of_must_be_tz_aware() -> None:
    for bad in ("2026-09-26T12:00:00", "2026-09-26", "yesterday", 17, None, "2026-13-40T00:00:00Z"):
        with pytest.raises(ev.EventReadInputError):
            ev.bind_values(_STATE, {"session_id": _SESSION, "as_of": bad})
    assert ev.bind_values(_STATE, {"session_id": _SESSION, "as_of": "2026-09-26T14:00:00+02:00"})["as_of"] == datetime(
        2026, 9, 26, 12, tzinfo=timezone.utc
    )


def test_window_span_is_capped_per_verb() -> None:
    base = {"as_of": _AS_OF}
    ok = ev.bind_values(_WINDOW, {"day_from": "2026-09-25", "day_to": "2026-10-01", **base})
    assert (ok["part_to"] - ok["part_from"]).days == _WINDOW.max_window_days
    for first, last in (
        ("2026-09-25", "2026-10-02"),
        ("2026-09-26", "2026-09-25"),
        ("2026-09-25", "2026-9-26"),
        ("2026-02-30", "2026-03-01"),
        ("x", "y"),
    ):
        with pytest.raises(ev.EventReadInputError):
            ev.bind_values(_WINDOW, {"day_from": first, "day_to": last, **base})
    tighter = dataclasses.replace(_WINDOW, max_window_days=2)
    with pytest.raises(ev.EventReadInputError):
        ev.bind_values(tighter, {"day_from": "2026-09-25", "day_to": "2026-09-27", **base})


def test_errors_name_the_param_never_the_value() -> None:
    bad_value = "NOT-A-ULID-" + "Z" * 9
    with pytest.raises(ev.EventReadInputError) as caught:
        ev.bind_values(_STATE, {"session_id": bad_value, "as_of": _AS_OF})
    assert "session_id" in str(caught.value) and bad_value not in str(caught.value)
    assert caught.value.verb == "session_state_and_duration" and caught.value.error_class == "EventReadInputError"
    with pytest.raises(ev.EventReadInputError) as caught:
        ev.bind_values(_STATE, {"session_id": _SESSION, "as_of": "private-value"})
    assert "as_of" in str(caught.value) and "private-value" not in str(caught.value)
    with pytest.raises(ev.EventReadInputError) as caught:
        ev.bind_values(_STATE, {"session_id": _SESSION, "as_of": _AS_OF, "extra": "leak"})
    assert "leak" not in str(caught.value)
    with pytest.raises(ev.EventReadInputError):
        ev.bind_values(_STATE, ["not", "an", "object"])


def test_partition_bounds_decode_from_the_session_id() -> None:
    values = ev.bind_values(_STATE, {"session_id": _SESSION, "as_of": _AS_OF})
    assert values["part_from"] == datetime(2026, 9, 25, tzinfo=timezone.utc)
    assert values["part_to"] == datetime(2026, 9, 26, tzinfo=timezone.utc)
    window = ev.bind_values(_WINDOW, {"day_from": "2026-09-25", "day_to": "2026-09-26", "as_of": _AS_OF})
    assert (window["part_from"], window["part_to"]) == (
        datetime(2026, 9, 25, tzinfo=timezone.utc),
        datetime(2026, 9, 27, tzinfo=timezone.utc),
    )
    sql = ev.render_sql(_STATE)
    assert "session_started_at >= $part_from AND session_started_at < $part_to AND session_id = $session_id" in sql
    assert "day(" not in sql


def test_values_are_bound_never_interpolated() -> None:
    con = RecordingCon()
    ev.execute(con, verb="session_state_and_duration", params={"session_id": _SESSION, "as_of": _AS_OF})
    sql, bound = con.calls[0]
    assert _SESSION not in sql and "2026-09-26" not in sql and "2026-09-25" not in sql
    assert set(bound) == {"session_id", "as_of", "part_from", "part_to"}
    assert bound["session_id"] == _SESSION
    con = RecordingCon()
    ev.execute(con, verb="model_call_cost_estimate", params={"session_id": _SESSION}, limit="5")
    assert con.calls[0][0].endswith(" LIMIT 5")
    with pytest.raises(rt.DuckLakeRuntimeError, match="not paginable"):
        ev.execute(con, verb="session_state_and_duration", params={"session_id": _SESSION, "as_of": _AS_OF}, limit=1)


def test_falsy_non_object_params_are_refused_before_any_acquire(declared_pin: Callable[[], tuple[str, str]]) -> None:
    params: Any
    for params in ([], "", 0):
        with pytest.raises(ev.EventReadInputError):
            ev.acquirer_for("named_read", {"verb": "telemetry_health_count", "params": params}, NO_OPS, "/e", pin=declared_pin)
    assert callable(ev.acquirer_for("named_read", {"verb": "telemetry_health_count"}, NO_OPS, "/e", pin=declared_pin))


def test_content_params_are_validated_without_echo() -> None:
    assert ev.bind_values(_CONTENT, _content_params())["parser_version"] == 3
    refusals = [
        ("producer", 7),
        ("producer", ""),
        ("producer", "x" * 201),
        ("producer", "bad\x00name"),
        ("event_id", "not-a-ulid"),
        ("event_id", 5),
        ("parser_version", "3"),
        ("parser_version", True),
        ("parser_version", 0),
        ("offset", "0"),
        ("offset", False),
        ("offset", -1),
        ("max_bytes", "9"),
        ("max_bytes", 0),
        ("max_bytes", 1.5),
    ]
    for name, value in refusals:
        with pytest.raises(ev.EventReadInputError) as caught:
            ev.bind_values(_CONTENT, _content_params(**{name: value}))
        assert f"'{name}'" in str(caught.value)
        assert str(value) not in str(caught.value) or str(value) in {"", "0", "True", "False"}
    with pytest.raises(ev.EventReadInputError):
        ev.bind_values(_CONTENT, {k: v for k, v in _content_params().items() if k != "offset"})


def test_tree_binding_validates_and_clamps_max_rows() -> None:
    values = ev.bind_values(_TREE, {"session_id": _ROOT, "offset": 0, "max_rows": 999999})
    assert values["max_rows"] == verbs.MAX_TREE_ROWS == 2000 and values["offset"] == 0
    assert values["part_to"] - values["part_from"] == timedelta(days=1) and values["session_id"] == _ROOT
    assert ev.bind_values(_TREE, {"session_id": _ROOT, "offset": 40, "max_rows": 7})["max_rows"] == 7
    for bad in (
        {"session_id": _ROOT, "offset": 0, "max_rows": 0},
        {"session_id": _ROOT, "offset": 0, "max_rows": True},
        {"session_id": _ROOT, "offset": -1, "max_rows": 5},
        {"session_id": _ROOT, "offset": "9", "max_rows": 5},
        {"session_id": "not-a-ulid-SECRET", "offset": 0, "max_rows": 5},
        {"session_id": _ROOT, "offset": 0},
    ):
        with pytest.raises(ev.EventReadInputError) as raised:
            ev.bind_values(_TREE, bad)
        assert "SECRET" not in str(raised.value)


def test_render_refuses_an_unbound_placeholder() -> None:
    broken = dataclasses.replace(_STATE, sql=_STATE.sql + " {nope}")
    with pytest.raises(rt.DuckLakeRuntimeError, match="unbound"):
        ev.render_sql(broken)
