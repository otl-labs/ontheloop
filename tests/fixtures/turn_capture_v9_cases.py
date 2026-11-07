"""Builders for the two parser-v9 golden cases (synthetic records only; stdlib only, no test_ prefix).

Each builder returns the files mapping of its committed golden case. Timestamps are the seconds after the corpus epoch.
The label and stderr shapes are copied from the 2026-11-06 probe of Claude Code 2.1.318 (PLAN-telemetry-capture-hook-labels).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tests.fixtures.turn_capture_corpus import (
    PROJECT_REF,
    WITNESS_STDERR,
    assistant,
    hook,
    make_files,
    pin_facts_text,
    prompt,
    text_block,
    tool_result,
    tool_use_block,
)

REF = PROJECT_REF
FACTS_LINE = f"[session_start_log_turn] capture pin {pin_facts_text()}\n"
REF_LINE = f"[session_start_log_turn] capture pin project_ref={REF}\n"


def labelled_pin(uuid: str, t: float, label: str, stderr: str, name: str = "SessionStart:startup") -> dict[str, Any]:
    return hook(uuid, t, name, command=label, stderr=stderr, stdout="")


def labelled_witness(uuid: str, t: float, label: str = "command hook") -> dict[str, Any]:
    return hook(uuid, t, "Stop", command=label, stderr=WITNESS_STDERR, stdout="", toolUseID="stop-" + uuid)


def _twin(uuid: str, t: float, held: bool) -> dict[str, Any]:
    extra: dict[str, Any] = {"durationMs": 120, "stdout": "context\n", "stderr": ""}
    if held:
        extra["heldContent"] = True
    return hook(uuid, t, "SessionStart:startup", tool_use_id="ss1", command="command hook 3", **extra)


def hook_label_shape_files() -> dict[str, str]:
    """The CC-web label shape: a gated plugin pin copy with empty stderr, the real pin, labelled witnesses and twins."""
    root = [
        labelled_pin("g1", 0.2, "command hook 1", ""),
        labelled_pin("g2", 0.3, "command hook 2", FACTS_LINE + REF_LINE),
        hook("g3", 0.4, "SessionStart:startup", command="command hook 4", stderr="", stdout=""),
        _twin("g4", 0.5, True),
        _twin("g5", 0.6, False),
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [tool_use_block("tu1", "Bash", {"command": "ls"})]),
        hook("g6", 2.1, "PreToolUse:Bash", tool_use_id="tu1", command="command hook", stderr="", stdout=""),
        tool_result("r1", 3, "tu1", "ok", pid="p1"),
        assistant("a2", 4, "m2", [text_block("first reply")]),
        labelled_witness("w1", 5),
        prompt("u2", 6, "p2", "again"),
        assistant("a3", 7, "m3", [text_block("second reply")]),
        labelled_witness("w2", 8),
    ]
    return make_files(root)


def hook_status_message_shape_files() -> dict[str, str]:
    """Today's live shape: statusMessage commands plus the ref line; rows equal the registered-command shape's."""
    root = [
        labelled_pin("g1", 0.3, "session_start_log_turn.sh --project-ref=example/project", FACTS_LINE + REF_LINE),
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [text_block("first reply")]),
        labelled_witness("w1", 5, "stop_log_turn.py"),
    ]
    return make_files(root)


V9_BUILDERS: dict[str, Callable[[], dict[str, str]]] = {
    "hook_label_shape.json": hook_label_shape_files,
    "hook_status_message_shape.json": hook_status_message_shape_files,
}
