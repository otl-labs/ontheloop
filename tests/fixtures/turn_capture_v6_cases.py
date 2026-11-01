"""Builders for the three parser-v6 golden cases (synthetic records only; stdlib only, no test_ prefix).

Each builder returns the files mapping of its committed golden case. STATUS lines are built at run time so no tracked file
holds a whole-line literal. Timestamps are the seconds after the corpus epoch.
"""

from __future__ import annotations

from collections.abc import Callable

from src.turn_capture.markers import format_status_marker
from tests.fixtures.turn_capture_corpus import (
    assistant,
    child,
    make_files,
    prompt,
    text_block,
    tool_result,
    tool_use_block,
)


def _skill(tool_id: str, skill: str) -> dict:
    return tool_use_block(tool_id, "Skill", {"skill": skill})


def workflow_from_skill_files() -> dict[str, str]:
    """A relay-shaped root (plain first prompt): one response calls a non-workflow Skill and then a planning Skill whose
    result is an error, then a second turn calls implement. The open row reads plan: the first workflow Skill call within
    the open bound, errored or not. Both calls sit in the first assistant record, so every finalize cut sees them (R3)."""
    root = [
        prompt("u1", 1, "p1", "relay request"),
        assistant("a1", 2, "m1", [_skill("tuA", "dataviz"), _skill("tuB", "planning")]),
        tool_result("r1", 3, "tuA", "Launching skill", pid="p1"),
        tool_result("r2", 5, "tuB", "Unknown skill: planning", pid="p1", is_error=True),
        assistant("a3", 6, "m3", [text_block("reply")]),
        prompt("u2", 8, "p2", "second request"),
        assistant("a4", 9, "m4", [_skill("tuC", "implement")]),
        tool_result("r3", 10, "tuC", "Launching skill", pid="p2"),
        assistant("a5", 11, "m5", [text_block("done")]),
    ]
    return make_files(root)


def subagent_workflow_skills_files() -> dict[str, str]:
    """A root opened by an /implement command tag with three sync sub-agents: one calls Skill code-review, one calls no
    skill, one whose dispatch prompt carries a /plan command-name tag and calls no skill."""
    spec = {"subagent_type": "general-purpose", "description": "d"}
    done = {"isAsync": False, "status": "completed"}
    root = [
        prompt("u1", 1, "p1", "<command-name>/implement</command-name>"),
        assistant(
            "a1", 2, "m1",
            [
                tool_use_block("tuA", "Agent", spec),
                tool_use_block("tuB", "Agent", spec),
                tool_use_block("tuC", "Agent", spec),
            ],
        ),
        tool_result("r1", 8, "tuA", "review done", pid="p1", tur={**done, "agentId": "ag1"}),
        tool_result("r2", 9, "tuB", "plain done", pid="p1", tur={**done, "agentId": "ag2"}),
        tool_result("r3", 10, "tuC", "tagged done", pid="p1", tur={**done, "agentId": "ag3"}),
        assistant("a2", 11, "m2", [text_block("all done")]),
    ]  # fmt: skip
    kids = {
        "ag1": [
            child(prompt("kp1", 2.5, "kq1", "review the diff"), "ag1"),
            child(assistant("k1", 3, "mk1", [_skill("tuK", "code-review")]), "ag1"),
            child(assistant("k2", 4, "mk2", [text_block("reviewed")]), "ag1"),
        ],
        "ag2": [
            child(prompt("kp2", 2.6, "kq2", "list the files"), "ag2"),
            child(assistant("k3", 5, "mk3", [text_block("listed")]), "ag2"),
        ],
        "ag3": [
            child(prompt("kp3", 2.7, "kq3", "<command-name>/plan</command-name> write the plan"), "ag3"),
            child(assistant("k4", 6, "mk4", [text_block("planned")]), "ag3"),
        ],
    }
    return make_files(root, kids)


def status_markers_files() -> dict[str, str]:
    """Bash calls to repo CLIs printing STATUS lines: done (no row), skipped with a reason (a row), failed (a row), a
    malformed line (none), the skipped line echoed by a non-CLI command (none), and one skipped and one failed line of
    one source in a single output (two rows). Exactly four status rows."""
    done = format_status_marker("tool_a", "done")
    skipped = format_status_marker("tool_b", "skipped", "no_creds")
    failed = format_status_marker("tool_c", "failed")
    malformed = "STATUS v1 source=tool_d result=bogus"
    both = "\n".join((format_status_marker("tool_e", "skipped"), "between", format_status_marker("tool_e", "failed")))
    root = [
        prompt("u1", 1, "p1", "run the tools"),
        assistant(
            "a1", 2, "m1",
            [
                tool_use_block("tu1", "Bash", {"command": "bin/venv-python -m scripts.tool_a"}),
                tool_use_block("tu2", "Bash", {"command": "bin/venv-python -m scripts.tool_b"}),
                tool_use_block("tu3", "Bash", {"command": "bin/venv-python scripts/tool_c.py"}),
                tool_use_block("tu4", "Bash", {"command": "bin/venv-python -m scripts.tool_d"}),
                tool_use_block("tu5", "Bash", {"command": "echo replay"}),
                tool_use_block("tu6", "Bash", {"command": "bin/venv-python -m scripts.tool_e"}),
            ],
        ),
        tool_result("r1", 3, "tu1", f"working\n{done}\n", pid="p1"),
        tool_result("r2", 4, "tu2", f"working\n{skipped}\n", pid="p1"),
        tool_result("r3", 5, "tu3", f"{failed}\n", pid="p1", is_error=True),
        tool_result("r4", 6, "tu4", f"{malformed}\n", pid="p1"),
        tool_result("r5", 7, "tu5", f"{skipped}\n", pid="p1"),
        tool_result("r6", 8, "tu6", f"{both}\n", pid="p1"),
        prompt("u2", 10, "p2", "thanks"),
    ]  # fmt: skip
    return make_files(root)


V6_BUILDERS: dict[str, Callable[[], dict[str, str]]] = {
    "workflow_from_skill.json": workflow_from_skill_files,
    "subagent_workflow_skills.json": subagent_workflow_skills_files,
    "status_markers.json": status_markers_files,
}
