"""Rules SC-1..SC-3 end to end: a resume boundary derives the previous segment's close from the tree, no cut recorded."""

from __future__ import annotations

import dataclasses

from src.turn_capture.record_turn import PARSER_VERSION, record_turn
from src.turn_capture.transcript import MemTree
from tests.fixtures.turn_capture_corpus import PROJECT_REF, assistant, hook, make_files, prompt, text_block


def _resumed() -> dict[str, str]:
    return make_files(
        [
            prompt("u1", 1, "p1", "first"),
            assistant("a1", 2, "m1", [text_block("one")]),
            hook("rs1", 10, "SessionStart:resume"),
            prompt("u2", 11, "p2", "second"),
            assistant("a2", 12, "m2", [text_block("two")]),
            prompt("u3", 13, "p3", "third"),
        ]
    )


def _pass(files, cursor=None, final=False):
    return record_turn(MemTree(files), cursor, project_ref=PROJECT_REF, session_final=final)


def _closes(result) -> list[str]:
    return [r["external_ref"] for r in result.sessions if r["event_kind"] == "close"]


def test_segment_closes_need_no_finalization_cut() -> None:
    first = _pass(_resumed())
    assert _closes(first) == ["a1#0/close"] and first.next_cursor.finalize_cuts == []
    assert [r["event_kind"] for r in first.sessions].count("resume") == 1
    again = _pass(_resumed(), first.next_cursor)
    assert again.sessions == [] and again.next_cursor.finalize_cuts == []
    stale = dataclasses.replace(first.next_cursor, parser_version=PARSER_VERSION - 1)
    reset = _pass(_resumed(), stale)
    assert _closes(reset) == ["a1#0/close"] and reset.next_cursor.finalize_cuts == []
    final = _pass(_resumed(), first.next_cursor, final=True)
    assert _closes(final) == ["u3#0/close"]
    assert len(final.next_cursor.finalize_cuts) == 1


def _late(*tail):
    return [
        prompt("u1", 1, "p1", "first"),
        assistant("a1", 2, "m1", [text_block("one")]),
        prompt("u2", 10, "p2", "second"),
        assistant("a2", 11, "m2", [text_block("two")]),
        hook("rs1", 12, "SessionStart:resume", durationMs=5000),
        *tail,
    ]


def test_late_resume_segment_close_is_anchored_before_the_effective_boundary() -> None:
    files = make_files(_late(prompt("u3", 20, "p3", "third"), assistant("a3", 21, "m3", [text_block("three")])))
    result = _pass(files)
    assert _closes(result) == ["a1#0/close"]
    assert [r["event_kind"] for r in result.sessions].count("resume") == 1 and result.next_cursor.finalize_cuts == []


def test_turn_after_a_late_resume_belongs_to_the_next_segment() -> None:
    tail = [
        assistant("a3", 13, "m3", [text_block("three")]),
        hook("rs2", 30, "SessionStart:resume"),
        prompt("u3", 31, "p3", "third"),
        assistant("a4", 32, "m4", [text_block("four")]),
    ]
    assert _closes(_pass(make_files(_late(*tail)))) == ["a1#0/close", "a3#0/close"]
