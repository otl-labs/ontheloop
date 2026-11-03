"""witness.py: the Stop witness shape, the at-rest test, closing events, the frontier, close windows and outcomes."""

from __future__ import annotations

from typing import Any

from src.turn_capture import witness
from src.turn_capture.streams import ROOT, ParsedTree, Turn, parse_tree
from tests.fixtures.turn_capture_corpus import (
    STOP_COMMAND,
    WITNESS_STDERR,
    api_error,
    assistant,
    child,
    hook,
    make_files,
    mem_tree,
    other_stop_hook,
    prompt,
    stop_feedback,
    text_block,
    tool_result,
    tool_use_block,
)
from tests.fixtures.turn_capture_corpus import witness as witness_rec

INTERRUPT = "[Request interrupted by user]"


def parse(
    root: list[dict[str, Any]],
    children: dict[str, list[dict[str, Any]]] | None = None,
    *,
    final: bool = False,
    frontier: bool | None = None,
) -> ParsedTree:
    return parse_tree(mem_tree(make_files(root, children)), None, final, frontier=not final if frontier is None else frontier)


def turns(parsed: ParsedTree) -> list[Turn]:
    return parsed.streams[ROOT].turns


def test_witness_matches_only_the_capture_shim_line_at_stop() -> None:
    good = witness_rec("w", 1)
    assert witness.is_witness_record(good)
    att = good["attachment"]
    variants = [
        {**good, "type": "user"},
        {**good, "attachment": "text"},
        {**good, "attachment": {**att, "type": "hook_blocking_error"}},
        {**good, "attachment": {**att, "hookEvent": "SubagentStop"}},
        {**good, "attachment": {**att, "command": "bash ./other_stop.sh"}},
        {**good, "attachment": {**att, "command": None}},
        {**good, "attachment": {**att, "stderr": "ok\n" + WITNESS_STDERR}},
        {**good, "attachment": {**att, "stderr": ""}},
        {**good, "attachment": {**att, "stderr": None}},
    ]
    assert not any(witness.is_witness_record(v) for v in variants)
    assert witness.is_witness_record({**good, "attachment": {**att, "stderr": WITNESS_STDERR + "more\n"}})
    root = [prompt("u1", 1, "p1", "go"), assistant("a1", 2, "m1", [text_block("k")]), witness_rec("w1", 3)]
    kid = [
        child(prompt("kp", 2, "kp1", "t"), "ag1"),
        child(witness_rec("kw", 3, "SubagentStop"), "ag1"),
        child(witness_rec("kx", 4), "ag1"),
    ]
    parsed = parse(root, {"ag1": kid})
    hooks = [r for s in parsed.streams.values() for r in s.hooks]
    assert {r.uuid for r in hooks if witness.is_witness(r)} == {"w1"}
    assert STOP_COMMAND in good["attachment"]["command"]


def test_witness_is_usable_only_when_the_turn_is_at_rest() -> None:
    ok = parse(
        [prompt("u1", 1, "p1", "go"), assistant("a1", 2, "m1", [text_block("k")]), witness_rec("w1", 3)], frontier=False
    )
    assert [r.uuid for r in turns(ok)[0].closes] == ["w1"] and turns(ok)[0].closed and not turns(ok)[0].bounded
    pending = parse(
        [
            prompt("u1", 1, "p1", "go"),
            assistant("a1", 2, "m1", [tool_use_block("t1", "Bash", {"command": "ls"})]),
            witness_rec("w1", 3),
            tool_result("r1", 4, "t1", "out", pid="p1"),
        ],
        frontier=False,
    )
    null_reason = parse(
        [
            prompt("u1", 1, "p1", "go"),
            assistant("a1", 2, "m1", [text_block("k")], stop_reason=None),
            witness_rec("w1", 3),
        ],
        frontier=False,
    )
    no_reply = parse([prompt("u1", 1, "p1", "go"), witness_rec("w1", 3)], frontier=False)
    for parsed in (pending, null_reason, no_reply):
        assert turns(parsed)[0].closes == [] and not turns(parsed)[0].closed
        assert parsed.diag["witness_not_at_rest"] == 1
    early = parse([witness_rec("w0", 0.5), prompt("u1", 1, "p1", "go")], frontier=False)
    assert early.diag["witness_outside_turn"] == 1 and early.diag["witness_not_at_rest"] == 0
    turn = turns(ok)[0]
    assert witness.at_rest(turn, 2) and not witness.at_rest(turn, 1)


def test_closing_events_frontier_and_windows() -> None:
    root = [
        prompt("u1", 1, "p1", "do it"),
        assistant("a1", 2, "m1", [text_block("first")]),
        witness_rec("w1", 3),
        stop_feedback("f1", 4, "p1"),
        assistant("a2", 5, "m2", [tool_use_block("t1", "Bash", {"command": "make"})]),
        tool_result("r1", 6, "t1", "built", pid="p1"),
        assistant("a3", 7, "m3", [text_block("second")]),
        other_stop_hook("o1", 8),
        witness_rec("w2", 9),
        hook("late", 10, "SessionEnd", command="bash ./x.sh"),
    ]
    parsed = parse(root)
    turn = turns(parsed)[0]
    assert [r.uuid for r in turn.closes] == ["w1", "w2"] and witness.frontier(turn) == turn.closes[-1].line
    assert parsed.diag["frontier_withheld"] == 1 and turn.recs[-1].uuid == "w2"
    first = turn.closes[0]
    assert [r.uuid for r in witness.close_window(turn, first.line)] == ["u1", "a1", "w1"]
    assert witness.close_window(turn, None) is turn.recs
    assert witness.counts(turn, first.line) == (1, 0) and witness.counts(turn, None) == (3, 1)
    assert witness.counts(turn, turn.closes[1].line) == (3, 1)
    errors = parse([prompt("u1", 1, "p1", "hi"), api_error("e1", 2), prompt("u2", 4, "p2", "again")])
    assert [r.uuid for r in turns(errors)[0].closes] == ["e1"] and witness.frontier(turns(errors)[1]) == turns(errors)[
        1
    ].first.line


def test_close_outcome_branches() -> None:
    def outcome(root: list[dict[str, Any]], bound: int | None = None) -> str:
        return witness.turn_outcome(turns(parse(root, final=True))[0], bound)

    base = [prompt("u1", 1, "p1", "go"), assistant("a1", 2, "m1", [text_block("k")])]
    assert outcome(base) == "success"
    assert outcome([prompt("u1", 1, "p1", "hi"), api_error("e1", 2)]) == "error"
    assert outcome([*base, prompt("u2", 3, "p1", [text_block(INTERRUPT)])]) == "interrupted"
    assert outcome([*base, prompt("u2", 3, "p1", [text_block(INTERRUPT)])], bound=1) == "success"
    use = assistant("a1", 2, "m1", [tool_use_block("t1", "Bash", {"command": "ls"})])
    assert outcome([prompt("u1", 1, "p1", "go"), use]) == "interrupted"
    rejected = tool_result("r1", 3, "t1", "no", pid="p1", is_error=True, toolDenialKind="user-rejected")
    assert outcome([prompt("u1", 1, "p1", "go"), use, rejected]) == "interrupted"
    flagged = tool_result("r1", 3, "t1", "x", pid="p1", tur={"interrupted": True})
    assert outcome([prompt("u1", 1, "p1", "go"), use, flagged]) == "interrupted"
    done = tool_result("r1", 3, "t1", "x", pid="p1")
    assert outcome([prompt("u1", 1, "p1", "go"), use, done]) == "success"
    assert outcome([prompt("u1", 1, "p1", "go"), use, done], bound=1) == "interrupted"
    aborted = assistant("a2", 4, "m2", [text_block("part")], stop_reason=None, isAbortedMidStream=True)
    assert outcome([prompt("u1", 1, "p1", "go"), use, done, aborted]) == "interrupted"
