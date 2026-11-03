"""Builders for the five parser-v4 golden cases (synthetic records only; stdlib only, no test_ prefix).

Shas are low-entropy repeats of one hex digit and session_<id> values are invented (Decision 101). Each builder returns
the files mapping of its committed golden case.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tests.fixtures.turn_capture_corpus import (
    PROJECT_REF,
    assistant,
    child,
    hook,
    make_files,
    pin_attachment,
    pin_facts_text,
    prompt,
    remote_change,
    task_notice,
    text_block,
    tool_result,
    tool_use_block,
)

SPLIT = {"ephemeral_1h_input_tokens": 15, "ephemeral_5m_input_tokens": 25}
CLASSES = {"input_tokens": 10, "output_tokens": 20, "cache_read_input_tokens": 30, "cache_creation_input_tokens": 40}
BASE_URL = "https://example.invalid/code/session_"


def _agent_use(tool_id: str, background: bool = False) -> dict[str, Any]:
    return tool_use_block(tool_id, "Agent", {"subagent_type": "Explore", "description": "d", "run_in_background": background})


def _kid(agent: str, *calls: dict[str, Any]) -> list[dict[str, Any]]:
    return [child(prompt(f"{agent}p", 3.0, "p1", "task"), agent), *[child(c, agent) for c in calls]]


def subagent_usage_final_and_spawn_correction_files() -> dict[str, str]:
    """Two sync children: one whose last call has no stop_reason and a matching spawn result (corrected), one whose spawn
    result differs in an input class (kept, not final); the first also has a mid-stream call without a stop_reason."""
    matching = {**CLASSES, "output_tokens": 77, "cache_creation": SPLIT, "output_tokens_details": {"thinking_tokens": 5}}
    differing = {**CLASSES, "input_tokens": 99, "output_tokens": 88}
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [_agent_use("tuA"), _agent_use("tuB")]),
        tool_result("r1", 6, "tuA", "one", pid="p1", tur={"agentId": "ag1", "isAsync": False, "usage": matching}),
        tool_result("r2", 6.5, "tuB", "two", pid="p1", tur={"agentId": "ag2", "isAsync": False, "usage": differing}),
        assistant("a2", 7, "m2", [text_block("both done")]),
        prompt("u2", 9, "p2", "next"),
    ]
    one = _kid(
        "ag1",
        assistant("c1", 3.5, "mc1", [text_block("working")], stop_reason=None, usage={"input_tokens": 1, "output_tokens": 1}),
        assistant("c2", 4, "mc2", [text_block("finished")], stop_reason=None, usage=CLASSES),
    )
    two = _kid("ag2", assistant("d1", 4.5, "md1", [text_block("finished")], stop_reason=None, usage=CLASSES))
    return make_files(root, {"ag1": one, "ag2": two})


STATUSES: tuple[tuple[str, dict[str, Any]], ...] = (
    ("bg1", {"status": "completed"}),
    ("bg2", {"status": "timeout"}),
    ("bg3", {"status": "failed"}),
    ("bg4", {"status": "killed"}),
    ("bg5", {"status": "surprising"}),
    ("bg6", {"raw_status": "<status>Not OK</status>\n"}),
    ("bg7", {"status": None}),
)


def background_notice_statuses_files() -> dict[str, str]:
    """Background children with notices completed, timeout, failed, killed, an unknown token, a malformed tag and no
    status tag, and one child notified twice (completed, then failed)."""
    agents = [name for name, _ in STATUSES] + ["bg8"]
    root: list[dict[str, Any]] = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [_agent_use(f"tu{n}", True) for n in range(len(agents))]),
    ]
    for n, agent in enumerate(agents):
        launched = {"agentId": agent, "isAsync": True, "status": "async_launched"}
        root.append(tool_result(f"r{n}", 3 + n / 10, f"tu{n}", "launched", pid="p1", tur=launched))
    when = 6.0
    for n, (agent, fields) in enumerate(STATUSES):
        root.append(task_notice(f"n{n}", when, agent, **fields))
        when += 0.5
    root += [task_notice("n7a", when, "bg8", "completed"), task_notice("n7b", when + 0.5, "bg8", "failed")]
    root += [assistant("a2", 12, "m2", [text_block("all notified")]), prompt("u2", 14, "p2", "next")]
    kids = {a: _kid(a, assistant(f"{a}c", 4, f"m{a}", [text_block("done")])) for a in agents}
    return make_files(root, kids)


def resume_segment_closes_files() -> dict[str, str]:
    """A root with resume boundaries: a segment ending mid-tool (close cancelled), a segment ending cleanly (success), a
    segment with no assistant turn (no close), a background child spawned before a resume with no notice (rows emitted at
    the boundary, no agent close), and a trailing resume hook group at end of file (no resume row)."""
    facts = pin_facts_text("1", "2", "false")
    launched = {"agentId": "bgk", "isAsync": True, "status": "async_launched"}
    root = [
        pin_attachment("h0", 0.5, PROJECT_REF, facts),
        prompt("u1", 1, "p1", "first"),
        assistant("a1", 2, "m1", [tool_use_block("t1", "Bash", {"command": "sleep 99"})]),
        pin_attachment("rs1", 10, PROJECT_REF, pin_facts_text("3", "2", "false"), "SessionStart:resume"),
        prompt("u2", 11, "p2", "second"),
        assistant("a2", 12, "m2", [_agent_use("tuB", True)]),
        tool_result("r2", 13, "tuB", "launched", pid="p2", tur=launched),
        assistant("a3", 14, "m3", [text_block("launched and waiting")]),
        pin_attachment("rs2", 20, PROJECT_REF, pin_facts_text("4", "2", "false"), "SessionStart:resume"),
        prompt("u3", 21, "p3", "third, never answered"),
        pin_attachment("rs3", 30, PROJECT_REF, pin_facts_text("5", "2", "false"), "SessionStart:resume"),
        prompt("u4", 31, "p4", "fourth"),
        assistant("a4", 32, "m4", [text_block("answered")]),
        hook("rs4", 40, "SessionStart:resume", exit_code=0),
    ]
    return make_files(root, {"bgk": _kid("bgk", assistant("bgc", 13.5, "mbg", [text_block("background work")]))})


def session_provenance_facts_files() -> dict[str, str]:
    """Startup and resume pin lines with different HEADs, one with base=unknown, one pin line from an old hook (no
    facts), two resumes with no prompt between (one merged group: the row takes the later pin), remote_session_change
    attachments in the root and a sub-agent file and one whose url does not match, and a trailing single-attachment
    compact group (the eof close and the compact row tie on timestamp and ordinal)."""
    ref = PROJECT_REF
    spawn = {"agentId": "ag1", "isAsync": False, "status": "completed"}
    plain = {"agentId": "ag2", "isAsync": False, "status": "completed"}
    root = [
        pin_attachment("h0", 0.5, ref, pin_facts_text("1", "2", "false")),
        remote_change("rc1", 0.6, BASE_URL + "AAAA1111bbbb2222cccc3333"),
        prompt("u1", 1, "p1", "first"),
        assistant("a1", 2, "m1", [_agent_use("tuA"), _agent_use("tuB")]),
        tool_result("r1", 5, "tuA", "one", pid="p1", tur=spawn),
        tool_result("r2", 5.5, "tuB", "two", pid="p1", tur=plain),
        remote_change("rc2", 5.8, BASE_URL + "ZZZZ9999yyyy8888xxxx7777"),
        assistant("a2", 6, "m2", [text_block("done")]),
        pin_attachment("rs1", 10, ref, pin_facts_text("3", "unknown", "true"), "SessionStart:resume"),
        prompt("u2", 11, "p2", "second"),
        assistant("a3", 12, "m3", [text_block("second done")]),
        pin_attachment("rs2", 20, ref, pin_facts_text("4", "2", "false"), "SessionStart:resume"),
        pin_attachment("rs3", 20.5, ref, pin_facts_text("5", "2", "true"), "SessionStart:resume"),
        prompt("u3", 21, "p3", "third"),
        assistant("a4", 22, "m4", [text_block("third done")]),
        pin_attachment("rs4", 30, ref, None, "SessionStart:resume"),
        prompt("u4", 31, "p4", "fourth"),
        assistant("a5", 32, "m5", [text_block("fourth done")]),
        hook("cmp", 40, "SessionStart:compact", exit_code=0),
    ]
    kids = {
        "ag1": [
            child(remote_change("kc1", 2.6, BASE_URL + "KKKK1111aaaa2222bbbb3333"), "ag1"),
            *_kid("ag1", assistant("k1", 3.5, "mk1", [text_block("child one")])),
        ],
        "ag2": [
            child(remote_change("kc2", 2.6, "https://example.invalid/code/"), "ag2"),
            *_kid("ag2", assistant("k2", 3.5, "mk2", [text_block("child two")])),
        ],
    }
    return make_files(root, kids)


def model_call_request_fields_files() -> dict[str, str]:
    """Assistant records with requestedModel, effort and a cache_creation split, one record without them, one with an
    effort value failing the pattern (column left NULL, counted), and one whose split exceeds its total (split NULL)."""
    with_split = {**CLASSES, "cache_creation": SPLIT}
    over = {**CLASSES, "cache_creation": {"ephemeral_1h_input_tokens": 41, "ephemeral_5m_input_tokens": 1}}
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [text_block("one")], usage=with_split, requestedModel="claude-test-1[1m]", effort="xhigh"),
        assistant("a2", 3, "m2", [text_block("two")]),
        assistant("a3", 4, "m3", [text_block("three")], usage=with_split, requestedModel="claude-test-2", effort="Very High"),
        assistant("a4", 5, "m4", [text_block("four")], usage=over, requestedModel="claude-test-1[1m]", effort="low"),
        prompt("u2", 7, "p2", "next"),
    ]
    return make_files(root)


V4_BUILDERS: dict[str, Callable[[], dict[str, str]]] = {
    "subagent_usage_final_and_spawn_correction.json": subagent_usage_final_and_spawn_correction_files,
    "background_notice_statuses.json": background_notice_statuses_files,
    "resume_segment_closes.json": resume_segment_closes_files,
    "session_provenance_facts.json": session_provenance_facts_files,
    "model_call_request_fields.json": model_call_request_fields_files,
}
