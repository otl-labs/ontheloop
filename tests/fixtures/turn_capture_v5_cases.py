"""Builders for the seven parser-v5 golden cases (synthetic records only; stdlib only, no test_ prefix).

Each builder returns the files mapping of its committed golden case. Timestamps are the seconds after the corpus epoch.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tests.fixtures.turn_capture_corpus import (
    api_error,
    assistant,
    blocking_error,
    child,
    hook,
    make_files,
    other_stop_hook,
    prompt,
    stop_feedback,
    stop_summary,
    text_block,
    tool_result,
    tool_use_block,
    witness,
)

INTERRUPT_TOOL = "[Request interrupted by user for tool use]"


def stop_witness_closes_files() -> dict[str, str]:
    """Two turns closed at their witnesses (one ends on assistant text with a stop_hook_summary and another Stop hook's
    attachment, one on a terminal tool's result with no summary), a trailing prompt (a boundary adding no close) and a
    sync sub-agent whose file carries a SubagentStop attachment (a process_event only)."""
    spawn = {"agentId": "ag1", "isAsync": False, "status": "completed"}
    root = [
        prompt("u1", 1, "p1", "first"),
        assistant("a1", 2, "m1", [tool_use_block("tuA", "Agent", {"subagent_type": "Explore", "description": "d"})]),
        tool_result("r1", 4, "tuA", "child done", pid="p1", tur=spawn),
        assistant("a2", 5, "m2", [text_block("first answer")]),
        witness("w1", 6),
        other_stop_hook("o1", 6.1),
        stop_summary("s1", 6.2),
        prompt("u2", 8, "p2", "second"),
        assistant("a3", 9, "m3", [tool_use_block("tuB", "Bash", {"command": "ls"})]),
        tool_result("r2", 10, "tuB", "listing", pid="p2"),
        witness("w2", 11),
        prompt("u3", 13, "p3", "third"),
    ]
    kid = [
        child(prompt("kp", 2.5, "kp1", "task"), "ag1"),
        child(assistant("k1", 3, "mk1", [text_block("child answer")]), "ag1"),
        child(witness("kw", 3.5, "SubagentStop"), "ag1"),
    ]
    return make_files(root, {"ag1": kid})


def _gate(uuid: str, t: float, kind: str, tool: str) -> dict[str, Any]:
    return hook(uuid, t, f"{kind}:Bash", tool_use_id=tool, command="bash ./hook.sh")


def stop_blocked_witness_first_files() -> dict[str, str]:
    """A blocked Stop in the witness-first order (witness, the exit-2 feedback record, the summary), then a continuation
    that runs a tool under a PreToolUse and a PostToolUse hook and ends at its own witness: two closes."""
    root = [
        prompt("u1", 1, "p1", "do it"),
        assistant("a1", 2, "m1", [text_block("first try")]),
        witness("w1", 3),
        stop_feedback("f1", 6, "p1"),
        stop_summary("s1", 6.1),
        assistant("a2", 7, "m2", [tool_use_block("tuB", "Bash", {"command": "make"})]),
        _gate("h1", 7.5, "PreToolUse", "tuB"),
        tool_result("r2", 8, "tuB", "built", pid="p1"),
        _gate("h2", 8.5, "PostToolUse", "tuB"),
        assistant("a3", 9, "m3", [text_block("second try")]),
        witness("w2", 10),
        stop_summary("s2", 10.1),
    ]
    return make_files(root)


def stop_blocked_feedback_first_files() -> dict[str, str]:
    """The same blocked turn in the feedback-first order of a JSON block (feedback, hook_blocking_error, then witness)."""
    root = [
        prompt("u1", 1, "p1", "do it"),
        assistant("a1", 2, "m1", [text_block("first try")]),
        stop_feedback("f1", 3, "p1"),
        blocking_error("e1", 3.1),
        witness("w1", 6),
        stop_summary("s1", 6.1),
        assistant("a2", 7, "m2", [tool_use_block("tuB", "Bash", {"command": "make"})]),
        _gate("h1", 7.5, "PreToolUse", "tuB"),
        tool_result("r2", 8, "tuB", "built", pid="p1"),
        _gate("h2", 8.5, "PostToolUse", "tuB"),
        assistant("a3", 9, "m3", [text_block("second try")]),
        witness("w2", 10),
        stop_summary("s2", 10.1),
    ]
    return make_files(root)


def stop_blocked_then_interrupted_files() -> dict[str, str]:
    """A blocked Stop whose continuation runs a tool and is interrupted before its Stop, then an interrupt turn: the
    witness close (success) and a boundary close on the continuation's last record (interrupted), with a synthetic tool
    close."""
    root = [
        prompt("u1", 1, "p1", "do it"),
        assistant("a1", 2, "m1", [text_block("first try")]),
        witness("w1", 3),
        stop_feedback("f1", 3.5, "p1"),
        stop_summary("s1", 3.6),
        assistant("a2", 4, "m2", [tool_use_block("tuB", "Bash", {"command": "sleep 99"})]),
        prompt("u2", 6, "p2", [text_block(INTERRUPT_TOOL)]),
        assistant("a3", 7, "m3", [text_block("what next")]),
        witness("w2", 8),
    ]
    return make_files(root)


def api_error_self_close_files() -> dict[str, str]:
    """A turn ending on an API-error record followed by a later prompt, and a final turn ending on one."""
    root = [
        prompt("u1", 1, "p1", "hello"),
        api_error("e1", 2),
        prompt("u2", 4, "p2", "again"),
        api_error("e2", 5, "rate_limit"),
    ]
    return make_files(root)


def stop_witness_not_at_rest_files() -> dict[str, str]:
    """A witness written before its turn's tool result and one after a response with a null stop_reason: both unusable and
    counted, so the turn closes at its boundary."""
    partial = assistant("a2", 4, "m2", [text_block("still going")], stop_reason=None)
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [tool_use_block("tuA", "Bash", {"command": "ls"})]),
        witness("w1", 2.5),
        tool_result("r1", 3, "tuA", "out", pid="p1"),
        partial,
        witness("w2", 5),
        prompt("u2", 7, "p2", "next"),
        assistant("a3", 8, "m3", [text_block("fine")]),
    ]
    return make_files(root)


def prompt_open_row_files() -> dict[str, str]:
    """A tree whose last record is a new prompt with no Stop: a non-final pass emits only that turn's open row."""
    root = [
        prompt("u1", 1, "p1", "first"),
        assistant("a1", 2, "m1", [text_block("answer")]),
        witness("w1", 3),
        prompt("u2", 5, "p2", "second"),
    ]
    return make_files(root)


V5_BUILDERS: dict[str, Callable[[], dict[str, str]]] = {
    "stop_witness_closes.json": stop_witness_closes_files,
    "stop_blocked_witness_first.json": stop_blocked_witness_first_files,
    "stop_blocked_feedback_first.json": stop_blocked_feedback_first_files,
    "stop_blocked_then_interrupted.json": stop_blocked_then_interrupted_files,
    "api_error_self_close.json": api_error_self_close_files,
    "stop_witness_not_at_rest.json": stop_witness_not_at_rest_files,
    "prompt_open_row.json": prompt_open_row_files,
}
