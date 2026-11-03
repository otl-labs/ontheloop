"""CaptureCursor: JSON round-trip, pins, reset, sidecar pins."""

from __future__ import annotations

import json

import pytest

from src.turn_capture.cursor import CaptureCursor, CursorError, SourceMutated


def make(**over: object) -> CaptureCursor:
    base = {
        "producer": "claude_code",
        "parser_version": 1,
        "root_session_ref": "sess",
        "project_ref": "proj",
        "billing_shape": "fixed_non_rollover_allowance",
        "session_started_at_ms": 1_700_000_000_000,
        "lines_consumed": {"root": 4, "agent1": 2},
        "sidecars": {"a.txt": "ab" * 32, "b.txt": None},
        "finalized": True,
        "finalize_cuts": [{"root": 2}, {"root": 4, "agent1": 2}],
    }
    base.update(over)
    return CaptureCursor(**base)  # type: ignore[arg-type]


def test_cursor_json_round_trip() -> None:
    cursor = make()
    assert CaptureCursor.from_json(cursor.to_json()) == cursor


def test_format_two_round_trip_and_format_one_refused() -> None:
    cursor = make()
    assert cursor.cursor_format == 2 and json.loads(cursor.to_json())["finalize_cuts"] == [
        {"root": 2},
        {"root": 4, "agent1": 2},
    ]
    old = json.loads(cursor.to_json())
    old.pop("finalize_cuts")
    old["cursor_format"] = 1
    with pytest.raises(CursorError, match="cursor_format"):
        CaptureCursor.from_json(json.dumps(old))


def test_reset_keeps_the_four_pins_and_the_finalization_cuts() -> None:
    reset = make().reset(2)
    assert reset.parser_version == 2
    assert (reset.root_session_ref, reset.project_ref, reset.billing_shape, reset.session_started_at_ms) == (
        "sess", "proj", "fixed_non_rollover_allowance", 1_700_000_000_000,
    )  # fmt: skip
    assert reset.lines_consumed == {} and reset.sidecars == {} and reset.finalized is False
    assert reset.finalize_cuts == [{"root": 2}, {"root": 4, "agent1": 2}]


def test_pin_mismatches_raise() -> None:
    cursor = make()
    ok = {
        "root_session_ref": "sess",
        "project_ref": "proj",
        "billing_shape": "fixed_non_rollover_allowance",
        "session_started_at_ms": 1_700_000_000_000,
    }
    cursor.check_pins(**ok)
    for key, bad in (
        ("root_session_ref", "other"),
        ("project_ref", "other"),
        ("billing_shape", "metered_marginal"),
        ("session_started_at_ms", 1),
    ):
        with pytest.raises(CursorError, match=key):
            cursor.check_pins(**{**ok, key: bad})


def test_sidecar_contradiction_raises_and_absent_pin_is_honoured() -> None:
    cursor = make()
    cursor.check_sidecar("a.txt", "ab" * 32)
    cursor.check_sidecar("b.txt", "anything")
    cursor.check_sidecar("unpinned.txt", None)
    for sha in (None, "cd" * 32):
        with pytest.raises(SourceMutated):
            cursor.check_sidecar("a.txt", sha)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(cursor_format=9),
        lambda d: d.update(parser_version=-1),
        lambda d: d.update(session_started_at_ms=True),
        lambda d: d.update(project_ref=""),
        lambda d: d.update(producer=3),
        lambda d: d.update(lines_consumed=[]),
        lambda d: d.update(lines_consumed={"root": -1}),
        lambda d: d.update(sidecars=[]),
        lambda d: d.update(sidecars={"a": 5}),
        lambda d: d.update(finalized="yes"),
        lambda d: d.update(finalize_cuts={}),
        lambda d: d.update(finalize_cuts=[3]),
        lambda d: d.update(finalize_cuts=[{"root": -1}]),
        lambda d: d.update(finalize_cuts=[{"root": True}]),
        lambda d: d.pop("finalize_cuts"),
        lambda d: d.pop("finalized"),
        lambda d: d.update(extra=1),
    ],
)
def test_malformed_cursor_raises(mutate) -> None:
    data = json.loads(make().to_json())
    mutate(data)
    with pytest.raises(CursorError):
        CaptureCursor.from_json(json.dumps(data))


def test_non_json_and_non_object_raise() -> None:
    for text in ("{not json", "[1]"):
        with pytest.raises(CursorError):
            CaptureCursor.from_json(text)
