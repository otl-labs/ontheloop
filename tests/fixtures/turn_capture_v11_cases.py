"""Builders for the two parser-v11 golden cases and the PI-4 cut trees (synthetic records only; stdlib only, no test_ prefix).

Each builder returns the files mapping of its committed golden case. Timestamps are the seconds after the corpus epoch.
The definition record is the shape of the injected skill record in sub-agent transcripts of Claude Code 2.1.x
(2026-11-16): a user record with isMeta true, sourceToolUseID the Skill call's tool_use id and one text block.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tests.fixtures.turn_capture_corpus import (
    assistant,
    child,
    make_files,
    prompt,
    text_block,
    tool_result,
    tool_use_block,
)

SYNC_BODY = "## Intent\n\nSynthetic critique definition."
SYNC_ARGS = "docs/plans/PLAN-x.yaml"
SYNC_RECORD = f"Base directory for this skill: /w/.claude/skills/plan-critique\n\n{SYNC_BODY}\n\nARGUMENTS: {SYNC_ARGS}"
SCOUT_BODY = "## Intent\n\nSynthetic scout definition."
SCOUT_RECORD = f"Base directory for this skill: /w/.claude/skills/decision-scout\n\n{SCOUT_BODY}"
CHART_RECORD = "Base directory for this skill: /w/.claude/skills/dataviz\n\nSynthetic chart text."


def definition_record(uuid: str, t: float, pid: str, tool_use_id: str, text: str) -> dict[str, Any]:
    return prompt(uuid, t, pid, [text_block(text)], isMeta=True, sourceToolUseID=tool_use_id, turnCompanion=True)


def persona_subagent_persona_sync_files() -> dict[str, str]:
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant(
            "a1",
            2,
            "m1",
            [tool_use_block("tuA", "Agent", {"description": "d", "prompt": "p", "subagent_type": "general-purpose"})],
        ),
        tool_result("r1", 8, "tuA", "child done", pid="p1", tur={"agentId": "agP1", "isAsync": False, "status": "completed"}),
        assistant("a2", 9, "m2", [text_block("wrapped up")]),
    ]
    kid = [
        prompt("kp1", 2.5, "kq1", "task"),
        assistant(
            "k1",
            3,
            "mk1",
            [
                tool_use_block("tuB", "Bash", {"command": "ls"}),
                tool_use_block("tuK", "Skill", {"skill": "ontheloop:plan-critique", "args": SYNC_ARGS}),
            ],
        ),
        tool_result("kr1", 3.2, "tuB", "files"),
        tool_result("kr2", 3.4, "tuK", "Launching skill: plan-critique"),
        definition_record("kd1", 3.5, "kq1", "tuK", SYNC_RECORD),
        assistant("k2", 4, "mk2", [text_block("critique done")]),
    ]
    return make_files(root, {"agP1": [child(r, "agP1") for r in kid]})


def _background_root(*, notice: bool) -> list[dict[str, Any]]:
    from tests.fixtures.turn_capture_corpus import task_notice  # noqa: PLC0415

    root = [
        prompt("u1", 1, "p1", "go"),
        assistant(
            "a1",
            2,
            "m1",
            [
                tool_use_block(
                    "tuA", "Agent", {"description": "d", "prompt": "p", "run_in_background": True, "subagent_type": "Explore"}
                )
            ],
        ),
        tool_result(
            "r1", 3, "tuA", "launched", pid="p1", tur={"agentId": "{AGENT}", "isAsync": True, "status": "async_launched"}
        ),
    ]
    if notice:
        root += [task_notice("n1", 8, "{AGENT}"), assistant("a2", 9, "m2", [text_block("wrapped up")])]
    return root


def _with_agent(records: list[dict[str, Any]], agent_id: str) -> list[dict[str, Any]]:
    import json  # noqa: PLC0415

    return json.loads(json.dumps(records).replace("{AGENT}", agent_id))


def persona_subagent_persona_background_files() -> dict[str, str]:
    kid = [
        prompt("kp1", 2.5, "kq1", "task"),
        assistant("k1", 3, "mk1", [tool_use_block("tuS", "Skill", {"skill": "dataviz"})]),
        tool_result("kr1", 3.2, "tuS", "Launching skill: dataviz"),
        definition_record("kd0", 3.3, "kq1", "tuS", CHART_RECORD),
        assistant("k2", 4, "mk2", [tool_use_block("tuD", "Skill", {"skill": "decision-scout"})]),
        tool_result("kr2", 4.2, "tuD", "Launching skill: decision-scout"),
        definition_record("kd1", 4.4, "kq1", "tuD", SCOUT_RECORD),
        assistant("k3", 5, "mk3", [text_block("scout done")]),
    ]
    root = _with_agent(_background_root(notice=True), "agP2")
    return make_files(root, {"agP2": [child(r, "agP2") for r in kid]})


def close_identity_trees() -> tuple[dict[str, str], dict[str, str]]:
    """(cut, full): the cut ends at the background child's first assistant record, which holds no Skill call; the full tree
    adds the decision-scout call and its definition record in that same turn, then the completion notice."""
    head = [prompt("kp1", 2.5, "kq1", "task"), assistant("k1", 3, "mk1", [text_block("looking")])]
    tail = [
        assistant("k2", 3.5, "mk2", [tool_use_block("tuD", "Skill", {"skill": "decision-scout"})]),
        tool_result("kr2", 3.6, "tuD", "Launching skill: decision-scout"),
        definition_record("kd1", 3.7, "kq1", "tuD", SCOUT_RECORD),
        assistant("k3", 4, "mk3", [text_block("scout done")]),
    ]
    cut_root = _with_agent(_background_root(notice=False), "agP3")
    full_root = _with_agent(_background_root(notice=True), "agP3")
    cut = make_files(cut_root, {"agP3": [child(r, "agP3") for r in head]})
    full = make_files(full_root, {"agP3": [child(r, "agP3") for r in head + tail]})
    return cut, full


V11_BUILDERS: dict[str, Callable[[], dict[str, str]]] = {
    "subagent_persona_sync.json": persona_subagent_persona_sync_files,
    "subagent_persona_background.json": persona_subagent_persona_background_files,
}
