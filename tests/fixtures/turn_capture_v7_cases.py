"""Builders for the three parser-v7 golden cases (synthetic records only; stdlib only, no test_ prefix).

Each builder returns the files mapping of its committed golden case. Timestamps are the seconds after the corpus epoch.
The shapes are the fresh project-thread shapes (late SessionStart group, heldContent copies with plain twins) with every
id, sha and path replaced.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tests.fixtures.turn_capture_corpus import (
    assistant,
    hook,
    make_files,
    pin_attachment,
    pin_facts_text,
    prompt,
    text_block,
    witness,
)

CMD_A = 'bash "$CLAUDE_PROJECT_DIR/.claude/hooks/context_a.sh"'
CMD_B = 'bash "$CLAUDE_PROJECT_DIR/.claude/hooks/context_b.sh"'
REF = "synthetic"


def _run(uuid: str, t: float, name: str, use_id: str, command: str, duration: int, held: bool = False) -> dict[str, Any]:
    """One stdout-printing hook record: the heldContent copy (held) or the plain copy of the run."""
    extra: dict[str, Any] = {"durationMs": duration, "stdout": "context\n", "stderr": ""}
    if held:
        extra["heldContent"] = True
    return hook(uuid, t, name, tool_use_id=use_id, command=command, **extra)


def _pin(uuid: str, t: float, name: str, use_id: str) -> dict[str, Any]:
    rec = pin_attachment(uuid, t, REF, pin_facts_text(), name)
    rec["attachment"]["toolUseID"] = use_id
    return rec


def late_startup_group_files() -> dict[str, str]:
    """A fresh thread: the first turn closes at its Stop witness before any SessionStart record is written; the startup
    group (pin plain, held copies at the release time, plain twins) lands after it and across the next prompt."""
    s = "SessionStart:startup"
    root = [
        prompt("u0", 0.5, "p0", "<system-reminder>session context</system-reminder>", isMeta=True),
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 3, "m1", [text_block("first reply")]),
        witness("w1", 4),
        _pin("s1", 5, s, "ss1"),
        _run("s2", 5.5, s, "ss1", CMD_A, 120, held=True),
        _run("s3", 5.5, s, "ss1", CMD_B, 340, held=True),
        _run("s4", 5.6, s, "ss1", CMD_A, 120),
        prompt("u2", 7, "p2", "second request"),
        _run("s5", 7.2, s, "ss1", CMD_B, 340),
        assistant("a2", 8, "m2", [text_block("second reply")]),
        witness("w2", 9),
        prompt("u3", 11, "p3", "third request"),
    ]
    return make_files(root)


def late_resume_group_files() -> dict[str, str]:
    """A synthetic late resume: the group's first hook started before the reply of the turn it was written into."""
    s = "SessionStart:resume"
    root = [
        prompt("u1", 1, "p1", "first request"),
        assistant("a1", 2, "m1", [text_block("first reply")]),
        witness("w1", 3),
        prompt("u2", 10, "p2", "second request"),
        assistant("a2", 11, "m2", [text_block("second reply")]),
        _run("r1", 12, s, "rs1", CMD_A, 5000),
        _pin("r2", 12.1, s, "rs1"),
        witness("w2", 13),
        prompt("u3", 20, "p3", "third request"),
        assistant("a3", 21, "m3", [text_block("third reply")]),
        witness("w3", 22),
    ]
    return make_files(root)


def resume_hook_twins_files() -> dict[str, str]:
    """rec-4633's on-time resume: plain-then-held twins before the next prompt, one held twin after it."""
    s = "SessionStart:resume"
    root = [
        prompt("u1", 1, "p1", "first request"),
        assistant("a1", 2, "m1", [text_block("first reply")]),
        witness("w1", 3),
        _run("r1", 5, s, "rs1", CMD_A, 100),
        _run("r2", 5.1, s, "rs1", CMD_A, 100, held=True),
        _pin("r3", 5.2, s, "rs1"),
        prompt("u2", 8, "p2", "second request"),
        _run("r4", 8.5, s, "rs1", CMD_A, 100, held=True),
        assistant("a2", 9, "m2", [text_block("second reply")]),
        witness("w2", 10),
    ]
    return make_files(root)


V7_BUILDERS: dict[str, Callable[[], dict[str, str]]] = {
    "late_startup_group.json": late_startup_group_files,
    "late_resume_group.json": late_resume_group_files,
    "resume_hook_twins.json": resume_hook_twins_files,
}
