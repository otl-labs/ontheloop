"""rec-4633: a hook run the CLI writes twice is one process_event, and a twin never opens a resume boundary."""

from __future__ import annotations

from src.turn_capture.record_turn import record_turn
from src.turn_capture.transcript import MemTree
from tests.fixtures.turn_capture_corpus import PROJECT_REF, assistant, hook, make_files, prompt, text_block

CMD = "bash ./ctx.sh"


def _run(uuid: str, t: float, name: str, use_id: str = "tu1", command: str = CMD, duration: int = 100, **extra):
    return hook(uuid, t, name, tool_use_id=use_id, command=command, durationMs=duration, stdout="x\n", **extra)


def _result(root):
    return record_turn(MemTree(make_files(root)), None, project_ref=PROJECT_REF, session_final=True)


def _hook_rows(result) -> list[dict]:
    return [
        r
        for table, rows in result.batches
        for r in rows
        if r.get("observation_type") == "process_event" and r["name"].startswith("hook:")
    ]


def _body(*mid):
    return [prompt("u1", 1, "p1", "go"), assistant("a1", 2, "m1", [text_block("x")]), *mid, prompt("u2", 9, "p2", "again")]


def test_twin_records_emit_one_process_event() -> None:
    for name in ("SessionStart:startup", "SessionStart:resume"):
        for first, second in (({"heldContent": True}, {}), ({}, {"heldContent": True})):
            rows = _hook_rows(_result(_body(_run("h1", 5, name, **first), _run("h2", 5.1, name, **second))))
            assert [r["external_ref"] for r in rows] == ["h1#0/process_event"], (name, first)


def test_a_twin_after_a_later_prompt_never_opens_a_resume_boundary() -> None:
    root = _body(_run("h1", 5, "SessionStart:resume"), _run("h2", 5.1, "SessionStart:resume", heldContent=True))
    root.append(_run("h3", 9.5, "SessionStart:resume", heldContent=True))
    root.append(assistant("a2", 10, "m2", [text_block("y")]))
    result = _result(root)
    assert [r["event_kind"] for r in result.sessions].count("resume") == 1
    assert len(_hook_rows(result)) == 1


def test_twins_are_matched_on_the_full_key_never_tool_use_id_alone() -> None:
    stops = [
        _run("s1", 5, "Stop", use_id="stop-1", command="bash ./a.sh"),
        _run("s2", 5.1, "Stop", use_id="stop-1", command="bash ./b.sh"),
    ]
    pair = [_run("p1", 5.2, "PreToolUse:Bash", use_id="tu9"), _run("p2", 5.3, "PostToolUse:Bash", use_id="tu9")]
    rows = _hook_rows(_result(_body(*stops, *pair)))
    assert sorted(r["external_ref"] for r in rows) == [
        "p1#0/process_event",
        "p2#0/process_event",
        "s1#0/process_event",
        "s2#0/process_event",
    ]
