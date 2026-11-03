"""Mirror tests for scripts/telemetry_verify/shape.py: source-shape counts over the verification trees and variants."""

from __future__ import annotations

from typing import Any

from scripts.telemetry_verify import shape
from tests.fixtures import telemetry_verification_trees as trees
from tests.fixtures.turn_capture_corpus import (
    assistant,
    attachment,
    child,
    make_files,
    mcp_overflow_files,
    mem_tree,
    prompt,
    text_block,
    tool_result,
    tool_use_block,
)

_USAGE = {"input_tokens": 10, "output_tokens": 20, "cache_read_input_tokens": 30, "cache_creation_input_tokens": 40}


def _names(result: shape.Shape) -> list[str]:
    return [f.name for f in result.findings]


def _nonfinal(rec: dict[str, Any]) -> dict[str, Any]:
    rec["message"]["stop_reason"] = None
    return rec


def _spawn_tree(spawn_usage: dict[str, int] | None, child_usage: dict[str, int], child_final: bool = True) -> dict[str, str]:
    result: dict[str, Any] = {"agentId": "k1", "isAsync": False, "status": "completed"}
    if spawn_usage is not None:
        result["usage"] = spawn_usage
    kid = assistant("c1", 3, "mk", [text_block("work")], usage=child_usage)
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [tool_use_block("tuA", "Agent", {"description": "d", "prompt": "p"})]),
        tool_result("r1", 5, "tuA", "done", pid="p1", tur=result),
    ]
    return make_files(root, {"k1": [child(kid if child_final else _nonfinal(kid), "k1")]})


def test_nonfinal_calls_are_counted_at_every_position() -> None:
    mid = shape.check_shape(mem_tree(trees.subagent_nonfinal_midstream()))
    assert mid.nonfinal == {("sub", "mid"): 1, ("sub", "final"): 1}
    assert [(f.name, f.kind, f.position) for f in mid.named("nonfinal_call")] == [
        ("nonfinal_call", "sub", "mid"),
        ("nonfinal_call", "sub", "final"),
    ]
    assert shape.check_shape(mem_tree(trees.resume_with_cost_state())).nonfinal == {("sub", "mid"): 1}
    assert shape.check_shape(mem_tree(trees.subagent_background_failed())).nonfinal == {}

    root = [
        prompt("u1", 1, "p1", "go"),
        _nonfinal(assistant("a1", 2, "m1", [text_block("streaming")])),
        dict(_nonfinal(assistant("a2", 3, "m2", [text_block("aborted")])), isAbortedMidStream=True),
    ]
    loose = shape.check_shape(mem_tree(make_files(root)))
    assert loose.nonfinal == {("root", "mid"): 1}, "an aborted stream's last record is not a non-final call"
    assert [f.alarm for f in loose.findings] == [True] and loose.findings[0].name == "nonfinal_root_call"


def test_spawn_usage_is_compared_with_the_childs_last_call() -> None:
    corrected = shape.check_shape(mem_tree(trees.subagent_nonfinal_midstream()))
    (finding,) = corrected.named("spawn_corrects_nonfinal")
    assert finding.agent_id == "agentV1" and finding.detail == "760" and not finding.alarm
    assert _names(shape.check_shape(mem_tree(trees.resume_with_cost_state()))).count("spawn_equal") == 2

    assert _names(shape.check_shape(mem_tree(_spawn_tree(_USAGE, _USAGE)))) == ["spawn_equal"]
    skewed = dict(_USAGE, input_tokens=11)
    mismatch = shape.check_shape(mem_tree(_spawn_tree(skewed, _USAGE)))
    assert _names(mismatch) == ["spawn_usage_mismatch"] and mismatch.findings[0].alarm
    higher = dict(_USAGE, output_tokens=99)
    assert _names(shape.check_shape(mem_tree(_spawn_tree(higher, _USAGE, child_final=True)))) == ["spawn_usage_mismatch"]
    assert _names(shape.check_shape(mem_tree(_spawn_tree(None, _USAGE)))) == []
    assert _names(shape.check_shape(mem_tree(_spawn_tree(_USAGE, _USAGE, child_final=False)))) == [
        "nonfinal_call",
        "spawn_equal",
    ]


def test_background_notice_status_is_read_and_compared() -> None:
    tree = mem_tree(trees.subagent_background_failed())
    closes = {"agentF1": "success", "agentF2": "success", "agentF3": "success"}
    result = shape.check_shape(tree, closes=closes)
    assert result.statuses == {"agentF1": "failed", "agentF2": "killed", "agentF3": "unrecognised_status"}
    assert sorted(f.agent_id for f in result.named("background_status_mismatch")) == ["agentF1", "agentF2", "agentF3"]
    assert shape.check_shape(tree, closes={"agentF1": "failed"}).named("background_status_mismatch") == []
    assert shape.check_shape(tree).named("background_status_mismatch") == [], "no close outcome given means nothing to compare"
    noisy = make_files(
        [
            prompt("u1", 1, "p1", "go"),
            attachment(
                "n1", 2, "queued_command", prompt="<task-notification>\n<status>completed</status>\n</task-notification>"
            ),
            attachment(
                "n2",
                3,
                "queued_command",
                prompt="<task-notification>\n<task-id>bg</task-id>\n<status>completed</status>\n</task-notification>",
            ),
            attachment("n3", 4, "queued_command", prompt="plain prompt"),
        ]
    )
    quiet = shape.check_shape(mem_tree(noisy), closes={"bg": "success"})
    assert quiet.statuses == {"bg": "completed"} and quiet.findings == []


def test_pinned_absent_sidecars_now_present_are_found() -> None:
    tree = mem_tree(mcp_overflow_files())
    (found,) = shape.check_shape(tree, pinned_absent=["mcp-overflow.txt", "never-written.txt"]).findings
    assert (found.name, found.detail, found.alarm) == ("sidecar_pinned_absent_present", "mcp-overflow.txt", True)
    assert shape.check_shape(tree, pinned_absent=["never-written.txt"]).findings == []


def test_stop_hook_summaries_are_counted() -> None:
    summary = {"type": "system", "subtype": "stop_hook_summary", "hookCount": 3, "timestamp": "2026-01-01T00:00:01.000Z"}
    root = [
        prompt("u1", 1, "p1", "go"),
        summary,
        dict(summary, hookCount=2),
        {"type": "system", "subtype": "stop_hook_summary"},
    ]
    result = shape.check_shape(mem_tree(make_files(root)))
    assert (result.stop_hook_summaries, result.stop_hook_count) == (3, 5)
    assert shape.check_shape(mem_tree(make_files([prompt("u1", 1, "p1", "go")]))).stop_hook_summaries == 0
