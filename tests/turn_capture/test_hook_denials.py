"""Mirror test for src/turn_capture/hook_denials.py (rules HD-1..HD-4, rec-4959)."""

from __future__ import annotations

import json
from typing import Any

from src.turn_capture.record_turn import record_turn
from tests.fixtures.turn_capture_corpus import (
    PROJECT_REF,
    assistant,
    make_files,
    mem_tree,
    prompt,
    text_block,
    tool_result,
    tool_use_block,
    witness,
)
from tests.fixtures.turn_capture_v10_cases import (
    MARKER_NEVER_ON_MAIN,
    PERMISSION_HOOK,
    denial_text,
    denied_result,
    hook_denial_attachment_twin_files,
    hook_denial_cloud_shape_files,
)


def _capture(files: dict[str, str], final: bool = True) -> Any:
    return record_turn(mem_tree(files), None, project_ref=PROJECT_REF, session_final=final)


def _obs(result: Any) -> list[dict[str, Any]]:
    return [r for t, rows in result.batches if t == "telemetry_observations" for r in rows]


def _denials(result: Any) -> list[dict[str, Any]]:
    return [r for r in _obs(result) if r["observation_type"] == "process_event" and r.get("severity") == "error"]


def _one_call(text: Any, **fields: Any) -> dict[str, str]:
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [tool_use_block("tu1", "Write", {"file_path": "x.txt"})]),
        denied_result("r1", 3, "tu1", text, **fields),
        assistant("a2", 4, "m2", [text_block("done")]),
        witness("w1", 5),
    ]
    return make_files(root)


def _closes(result: Any) -> list[str | None]:
    return [r.get("outcome") for r in _obs(result) if r["observation_type"] == "tool_call" and r["event_kind"] == "close"]


def test_marker_names_the_denied_tool_call() -> None:
    result = _capture(_one_call(denial_text("Write", "edit_scope_guard", "HOOK-BLOCK v1 hook=edit_scope_guard")))
    rows = _denials(result)
    assert [r["name"] for r in rows] == ["hook:edit_scope_guard"]
    meta = json.loads(rows[0]["metadata"])
    assert meta["marker_version"] == 1 and meta["tool_use_id"] == "tu1" and meta["hook_event"] == "PreToolUse"
    call = next(r for r in _obs(result) if r["observation_type"] == "tool_call" and r["event_kind"] == "open")
    assert rows[0]["parent_observation_ref"] == call["entity_ref"]
    assert _closes(result) == ["blocked"]
    assert result.diagnostics["hook_denial_marker"] == 1
    assert "denied" not in rows[0]["metadata"] and "synthetic" not in rows[0]["metadata"]


def test_legacy_text_names_a_pre_marker_transcript() -> None:
    result = _capture(_one_call(denial_text("Write", "never_on_main", None)))
    (row,) = _denials(result)
    assert row["name"] == "hook:never_on_main" and json.loads(row["metadata"])["marker_version"] is None
    assert result.diagnostics["hook_denial_legacy"] == 1


def test_marker_wins_over_legacy_text() -> None:
    result = _capture(_one_call(denial_text("Write", "never_on_main", "HOOK-BLOCK v1 hook=edit_scope_guard")))
    assert [r["name"] for r in _denials(result)] == ["hook:edit_scope_guard"]


def test_fallback_names_event_and_tool() -> None:
    result = _capture(_one_call("PreToolUse:Write hook error: [command hook 4]: no evidence."))
    assert [r["name"] for r in _denials(result)] == ["hook:PreToolUse.Write"]
    assert result.diagnostics["hook_denial_fallback"] == 1
    for decision in (
        {"decision": "reject", "source": "hook"},
        {"decision": "reject", "source": "hook", "reasonCode": "other"},
    ):
        result = _capture(_one_call("no names here", permissionDecision=decision))
        assert [r["name"] for r in _denials(result)] == ["hook:unknown"]
        assert result.diagnostics["hook_denial_unknown"] == 1


def test_attachment_twin_yields_no_second_row() -> None:
    result = _capture(hook_denial_attachment_twin_files())
    blocks = _denials(result)
    assert [r["name"] for r in blocks] == ["hook:never_on_main"]
    assert json.loads(blocks[0]["metadata"])["exit_code"] == 2


def test_non_hook_denials_emit_nothing() -> None:
    cases = [
        {"permissionDecision": {"decision": "reject", "source": "user", "reasonType": "user"}},
        {"permissionDecision": None},
        {"permissionDecision": {"decision": "reject", "source": "X y"}},
        {"permissionDecision": "hook"},
    ]
    for fields in cases:
        assert _denials(_capture(_one_call("blocked text", **fields))) == [], fields
    ok = make_files(
        [
            prompt("u1", 1, "p1", "go"),
            assistant("a1", 2, "m1", [tool_use_block("tu1", "Write", {"file_path": "x.txt"})]),
            tool_result("r1", 3, "tu1", "fine", pid="p1", permissionDecision=dict(PERMISSION_HOOK)),
            assistant("a2", 4, "m2", [text_block("done")]),
            witness("w1", 5),
        ]
    )
    result = _capture(ok)
    assert _closes(result) == ["success"] and _denials(result) == []


def test_marker_inside_a_line_is_not_read() -> None:
    text = "PreToolUse:Write hook error: [command hook 3]: echoed HOOK-BLOCK v1 hook=edit_scope_guard inline"
    result = _capture(_one_call(text))
    assert [r["name"] for r in _denials(result)] == ["hook:PreToolUse.Write"]
    assert result.diagnostics.get("hook_denial_marker", 0) == 0


def test_bad_stem_falls_through() -> None:
    for stem in ("bad stem", "a" * 201):
        text = denial_text("Write", "edit_scope_guard", f"HOOK-BLOCK v1 hook={stem}")
        assert [r["name"] for r in _denials(_capture(_one_call(text)))] == ["hook:edit_scope_guard"]
    fallback = "PreToolUse:Write hook error: HOOK-BLOCK v1 hook=bad stem"
    assert [r["name"] for r in _denials(_capture(_one_call(fallback)))] == ["hook:PreToolUse.Write"]


def test_disagreeing_markers_take_the_last_and_count() -> None:
    text = "first\nHOOK-BLOCK v1 hook=never_on_main\nsecond\nHOOK-BLOCK v1 hook=edit_scope_guard\n"
    result = _capture(_one_call(text))
    assert [r["name"] for r in _denials(result)] == ["hook:edit_scope_guard"]
    assert result.diagnostics["hook_denial_markers_disagree"] == 1


def test_open_turn_emits_no_denial_row() -> None:
    files = hook_denial_cloud_shape_files()
    root = next(k for k in files if k.endswith(".jsonl") and "/" not in k)
    lines = files[root].splitlines(keepends=True)
    assert json.loads(lines[-1])["uuid"] == "w1"
    open_files = {**files, root: "".join(lines[:-1])}
    assert [r for r in _denials(_capture(open_files, final=False)) if r["name"] != "hook:stop_log_turn"] == []
    closed = _denials(_capture(files, final=False))
    assert sorted(r["name"] for r in closed if r["name"] != "hook:stop_log_turn") == [
        "hook:PreToolUse.Bash",
        "hook:edit_scope_guard",
        "hook:never_on_main",
    ]


def test_denial_text_constants_are_the_probe_shape() -> None:
    assert PERMISSION_HOOK["source"] == "hook" and MARKER_NEVER_ON_MAIN.startswith("HOOK-BLOCK v1 hook=")
