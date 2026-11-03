"""Turns close at their own Stop (rules WT-1..WT-9, DG-5): witness closes, the frontier, the early open row, purity."""

from __future__ import annotations

import json
from typing import Any

from src.turn_capture import sessions
from src.turn_capture.cursor import CaptureCursor
from src.turn_capture.record_turn import CaptureResult, record_turn
from src.turn_capture.streams import ROOT, parse_tree
from src.turn_capture.transcript import MemTree
from src.turn_capture.witness import is_witness_record
from tests.fixtures.turn_capture_corpus import (
    PROJECT_REF,
    SID,
    api_error,
    assistant,
    child,
    jsonl,
    make_files,
    prompt,
    remote_change,
    stop_feedback,
    stop_summary,
    text_block,
    tool_result,
    tool_use_block,
    witness,
)
from tests.fixtures.turn_capture_v3_cases import keyed
from tests.fixtures.turn_capture_v5_cases import (
    api_error_self_close_files,
    prompt_open_row_files,
    stop_blocked_feedback_first_files,
    stop_blocked_then_interrupted_files,
    stop_blocked_witness_first_files,
    stop_witness_closes_files,
    stop_witness_not_at_rest_files,
)

ROOT_FILE = f"{SID}.jsonl"


def capture(files: dict[str, str], cursor: CaptureCursor | None = None, *, final: bool = False) -> CaptureResult:
    return record_turn(MemTree(files), cursor, project_ref=PROJECT_REF, session_final=final)


def with_root(files: dict[str, str], records: list[dict[str, Any]]) -> dict[str, str]:
    return {**files, ROOT_FILE: jsonl(records)}


def closes(rows: dict[tuple[str, str], dict[str, Any]], session: str = SID) -> dict[str, dict[str, Any]]:
    return {
        ref: row
        for (table, ref), row in rows.items()
        if table == "telemetry_observations"
        and row["observation_type"] == "turn"
        and row["event_kind"] == "close"
        and row["session_ref"] == session
    }


def prefix(files: dict[str, str], lines: int) -> dict[str, str]:
    kept = files[ROOT_FILE].splitlines(keepends=True)[:lines]
    return {**files, ROOT_FILE: "".join(kept)}


def line_after(files: dict[str, str], uuid: str) -> int:
    ids = [json.loads(line)["uuid"] for line in files[ROOT_FILE].splitlines()]
    return ids.index(uuid) + 1


def test_last_turn_lands_at_its_own_stop() -> None:
    files = make_files([prompt("u1", 1, "p1", "go"), assistant("a1", 2, "m1", [text_block("k")]), witness("w1", 3)])
    result = capture(files)
    rows = keyed(result)
    assert set(closes(rows)) == {"w1#0/turn_close"} and closes(rows)["w1#0/turn_close"]["outcome"] == "success"
    assert ("telemetry_observations", "u1#0/turn_open") in rows and ("telemetry_observations", "m1/model_call") in rows
    assert ("telemetry_observations", "w1#0/process_event") in rows
    assert [r["event_kind"] for r in result.sessions] == ["open"]
    cursor = result.next_cursor
    assert cursor is not None and cursor.finalize_cuts == [] and not cursor.finalized
    assert capture(files, cursor).observations == []


def test_blocked_stop_gives_one_close_per_stop_in_either_hook_order() -> None:
    shapes = []
    for builder in (stop_blocked_witness_first_files, stop_blocked_feedback_first_files):
        rows = keyed(capture(builder()))
        found = closes(rows)
        assert set(found) == {"w1#0/turn_close", "w2#0/turn_close"}
        shapes.append([(r["outcome"], r["metadata"]) for _, r in sorted(found.items())])
    assert (
        shapes[0]
        == shapes[1]
        == [
            ("success", '{"response_count":1,"tool_call_count":0}'),
            ("success", '{"response_count":3,"tool_call_count":1}'),
        ]
    )


def test_witness_close_reads_only_records_before_it() -> None:
    files = stop_blocked_witness_first_files()
    early = keyed(capture(prefix(files, line_after(files, "w1"))))
    full = keyed(capture(files))
    assert set(closes(early)) == {"w1#0/turn_close"}
    key = ("telemetry_observations", "w1#0/turn_close")
    assert early[key] == full[key]
    assert {k for k in early if k[0] == "telemetry_observations"} <= set(full)
    assert all(early[k] == full[k] for k in early if k in full)


def test_terminal_tool_stop_closes_at_its_witness() -> None:
    files = make_files(
        [
            prompt("u1", 1, "p1", "go"),
            assistant("a1", 2, "m1", [tool_use_block("t1", "Bash", {"command": "ls"})]),
            tool_result("r1", 3, "t1", "out", pid="p1"),
            witness("w1", 4),
        ]
    )
    rows = keyed(capture(files))
    assert set(closes(rows)) == {"w1#0/turn_close"}
    assert rows[("telemetry_observations", "r1#0/tool_call_close")]["outcome"] == "success"


def test_api_error_record_closes_its_turn() -> None:
    files = api_error_self_close_files()
    first = keyed(capture(prefix(files, 2)))
    assert set(closes(first)) == {"e1#0/turn_close"} and closes(first)["e1#0/turn_close"]["outcome"] == "error"
    full = keyed(capture(files, final=True))
    assert set(closes(full)) == {"e1#0/turn_close", "e2#0/turn_close"}


def test_boundary_close_only_without_a_later_usable_witness() -> None:
    files = stop_witness_closes_files()
    assert set(closes(keyed(capture(files, final=True)))) == {"w1#0/turn_close", "w2#0/turn_close"}
    plain = make_files(
        [
            prompt("u1", 1, "p1", "go"),
            assistant("a1", 2, "m1", [text_block("k")]),
            prompt("u2", 4, "p2", "no reply yet"),
        ]
    )
    for final in (False, True):
        found = closes(keyed(capture(plain, final=final)))
        assert set(found) == {"a1#0/turn_close"}
    blocked = keyed(capture(stop_blocked_then_interrupted_files(), final=True))
    assert set(closes(blocked)) >= {"w1#0/turn_close", "a2#0/turn_close"}
    assert closes(blocked)["a2#0/turn_close"]["outcome"] == "interrupted"


def test_boundary_close_is_keyed_on_the_turns_last_record() -> None:
    files = make_files(
        [
            prompt("u1", 1, "p1", "go"),
            assistant("a1", 2, "m1", [tool_use_block("t1", "Bash", {"command": "ls"})]),
            tool_result("r1", 3, "t1", "out", pid="p1"),
            prompt("u2", 5, "p2", "next"),
        ]
    )
    cut = prefix(files, 2)
    cut_close = closes(keyed(capture(cut, final=True)))
    full_close = closes(keyed(capture(files)))
    assert set(cut_close) == {"a1#0/turn_close"} and set(full_close) == {"r1#0/turn_close"}
    assert cut_close["a1#0/turn_close"]["outcome"] == "interrupted" and full_close["r1#0/turn_close"]["outcome"] == "success"
    first = capture(cut, final=True)
    second = capture(files, first.next_cursor)
    assert second.diagnostics["reanchored_close:turn"] == 1 and "cut_content_divergence" not in second.diagnostics
    assert "prior_content_divergence" not in second.diagnostics


def test_superseded_closes_are_counted_reanchored() -> None:
    spawn = {"agentId": "ag1", "isAsync": False, "status": "completed"}
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [tool_use_block("tuA", "Agent", {"subagent_type": "Explore", "description": "d"})]),
        tool_result("r1", 4, "tuA", "done", pid="p1", tur=spawn),
        assistant("a2", 5, "m2", [text_block("k")]),
        witness("w1", 6),
    ]
    kid = [child(prompt("kp", 2.5, "kp1", "t"), "ag1"), child(assistant("k1", 3, "mk1", [text_block("work")]), "ag1")]
    files = make_files(root, {"ag1": kid})
    first = capture(files)
    grown = make_files(root, {"ag1": [*kid, child(assistant("k2", 3.5, "mk2", [text_block("more")]), "ag1")]})
    second = capture(grown, first.next_cursor)
    assert second.diagnostics["reanchored_close:turn"] == 1 and "reanchored_close:tool_call" not in second.diagnostics
    pending = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [tool_use_block("t1", "Bash", {"command": "ls"})]),
        api_error("e1", 3),
    ]
    start = capture(make_files(pending))
    synthetic = keyed(start)[("telemetry_observations", "a1#0/tool_call_close")]
    assert synthetic["outcome"] == "interrupted"
    resolved = make_files([*pending, tool_result("r1", 4, "t1", "late", pid="p1")])
    after = capture(resolved, start.next_cursor, final=True)
    assert after.diagnostics["reanchored_close:tool_call"] == 1 and "reanchored_close:turn" not in after.diagnostics
    blocked = stop_blocked_witness_first_files()
    cursor = None
    for lines in (line_after(blocked, "w1"), line_after(blocked, "w2")):
        step = capture(prefix(blocked, lines), cursor)
        cursor = step.next_cursor
        assert not any(name.startswith("reanchored_close") for name in step.diagnostics)
    assert step.diagnostics["multi_close_turns"] == 1


def test_unusable_witness_waits_for_the_boundary_and_is_counted() -> None:
    files = stop_witness_not_at_rest_files()
    open_turn = prefix(files, line_after(files, "w2"))
    waiting = capture(open_turn)
    assert waiting.observations == [] and waiting.diagnostics["witness_not_at_rest"] == 2
    assert waiting.diagnostics["frontier_withheld"] > 0
    closed = capture(files)
    assert set(closes(keyed(closed))) == {"w2#0/turn_close", "a3#0/turn_close"} or "w2#0/turn_close" in closes(keyed(closed))
    assert closed.diagnostics["witness_not_at_rest"] == 2


def test_turn_open_row_lands_with_its_prompt() -> None:
    files = prompt_open_row_files()
    first = capture(prefix(files, line_after(files, "w1")))
    second = capture(files, first.next_cursor)
    assert [r["external_ref"] for r in second.observations] == ["u2#0/turn_open"]
    grown = make_files(
        [
            *[json.loads(line) for line in files[ROOT_FILE].splitlines()],
            assistant("a2", 6, "m2", [text_block("answer")]),
            witness("w2", 7),
        ]
    )
    third = capture(grown, second.next_cursor)
    refs = {r["external_ref"] for r in third.observations}
    assert "u2#0/turn_open" not in refs and {"w2#0/turn_close", "m2/model_call", "w2#0/process_event"} <= refs


def test_session_open_bound_is_the_first_close() -> None:
    url = "https://example.invalid/code/session_AAAA1111bbbb2222cccc3333"
    records = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [text_block("first")]),
        witness("w1", 3),
        remote_change("rc", 3.5, url),
        stop_feedback("f1", 4, "p1"),
        assistant("a2", 5, "m2", [text_block("second")]),
        witness("w2", 6),
        stop_summary("s2", 6.1),
    ]
    files = make_files(records)
    early = keyed(capture(prefix(files, 3)))
    full = keyed(capture(files))
    key = ("telemetry_sessions", f"{SID}#0/open")
    assert early[key] == full[key] and "harness_session_key" not in full[key]
    stream = parse_tree(MemTree(files), None, False).streams[ROOT]
    assert sessions._open_bound(stream) == stream.turns[0].closes[0].line == 2
    plain = parse_tree(
        MemTree(
            make_files([prompt("u1", 1, "p1", "go"), assistant("a1", 2, "m1", [text_block("k")]), prompt("u2", 4, "p2", "x")])
        ),
        None,
        False,
    )
    assert sessions._open_bound(plain.streams[ROOT]) == 1


def test_every_witness_schedule_emits_each_row_once() -> None:
    builders = (
        stop_witness_closes_files,
        stop_blocked_witness_first_files,
        stop_blocked_feedback_first_files,
        stop_blocked_then_interrupted_files,
        api_error_self_close_files,
        prompt_open_row_files,
    )
    for builder in builders:
        files = builder()
        lines = files[ROOT_FILE].splitlines()
        cuts = [n for n, line in enumerate(lines, 1) if is_witness_record(json.loads(line))] + [len(lines)]
        cursor, seen, reanchored = None, {}, 0
        for cut in cuts:
            step = capture(prefix(files, cut), cursor)
            cursor = step.next_cursor
            for key, row in keyed(step).items():
                assert key not in seen, (builder.__name__, cut, key)
                seen[key] = row
            reanchored += sum(
                v for k, v in step.diagnostics.items() if k.startswith("reanchored_close") or k == "cut_content_divergence"
            )
        full = keyed(capture(files))
        assert set(seen) == set(full) and all(seen[k] == full[k] for k in seen), builder.__name__
        assert reanchored == 0, builder.__name__
        per_entity: dict[str, int] = {}
        for row in full.values():
            if row.get("observation_type") == "turn" and row["event_kind"] == "close":
                per_entity[row["entity_ref"]] = per_entity.get(row["entity_ref"], 0) + 1
        assert step.diagnostics.get("multi_close_turns", 0) == sum(1 for n in per_entity.values() if n > 1), builder.__name__


def test_continuation_hook_rows_wait_for_their_tool() -> None:
    files = stop_blocked_witness_first_files()
    total = len(files[ROOT_FILE].splitlines())
    last_witness = line_after(files, "w2")
    for lines in range(1, total + 1):
        rows = keyed(capture(prefix(files, lines)))
        for ref in ("h1#0/process_event", "h2#0/process_event"):
            row = rows.get(("telemetry_observations", ref))
            if lines < last_witness:
                assert row is None, (lines, ref)
            else:
                assert row is not None and row["parent_observation_ref"] == "tuB", (lines, ref)


def test_lines_consumed_keeps_a_stream_the_frontier_hides() -> None:
    spawn = {"agentId": "ag1", "isAsync": False, "status": "completed"}
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [text_block("first")]),
        witness("w1", 3),
        prompt("u2", 5, "p2", "spawn"),
        assistant("a2", 6, "m2", [tool_use_block("tuA", "Agent", {"subagent_type": "Explore", "description": "d"})]),
        tool_result("r2", 8, "tuA", "done", pid="p2", tur=spawn),
        assistant("a3", 9, "m3", [text_block("end")]),
    ]
    kid = [child(prompt("kp", 6.5, "kp1", "t"), "ag1"), child(assistant("k1", 7, "mk1", [text_block("work")]), "ag1")]
    files = make_files(root, {"ag1": kid})
    cut = with_root(files, root[:6])
    first = capture(cut, final=True)
    assert first.next_cursor is not None and "ag1" in first.next_cursor.lines_consumed
    second = capture(files, first.next_cursor)
    assert second.next_cursor is not None
    assert second.next_cursor.lines_consumed["ag1"] == first.next_cursor.lines_consumed["ag1"]
    assert second.diagnostics.get("orphan_subagents", 0) == 1
