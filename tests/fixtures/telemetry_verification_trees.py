"""Shared helper (never a test_ module): the slice 4 trap trees, built at runtime with the producer corpus builders.

They live OUT of the producer golden corpus (tests/fixtures/turn_capture/golden/) so its parser_versions.yaml digest does
not move. Each tree reproduces one measured transcript shape and names the producer parser_version 4 golden case it lines
up with; the producer fixes plan may later move a builder into that corpus and delete it here (each raw shape has one home).

TREES maps a tree name to a builder returning the files dict; EXPECTED_LEDGER is what the ledger check must classify for the
cost-state tree (the hand-computed arithmetic of resume_with_cost_state's docstring).
"""

from __future__ import annotations

from typing import Any, Callable

from tests.fixtures.turn_capture_corpus import (
    SID,
    assistant,
    attachment,
    child,
    hook,
    make_files,
    prompt,
    text_block,
    thinking_block,
    tool_result,
    tool_use_block,
)

MAIN_REQUESTED = "claude-test-1[1m]"
SUB_MODEL = "claude-sub-1"
AUX_MODEL = "claude-haiku-aux"
FULL_USAGE = {"input_tokens": 10, "output_tokens": 20, "cache_read_input_tokens": 30, "cache_creation_input_tokens": 40}


def _usage(output: int, input_: int = 10, read: int = 30, create: int = 40) -> dict[str, int]:
    return {
        "input_tokens": input_,
        "output_tokens": output,
        "cache_read_input_tokens": read,
        "cache_creation_input_tokens": create,
    }


def _nonfinal(rec: dict[str, Any]) -> dict[str, Any]:
    """The builder hard-codes stop_reason end_turn; a streaming-start snapshot carries null."""
    rec["message"]["stop_reason"] = None
    return rec


def _spawn_result(
    uuid: str, t: float, tool_id: str, agent_id: str, usage: dict[str, int] | None = None, *, is_async: bool = False
) -> dict[str, Any]:
    tur: dict[str, Any] = {"agentId": agent_id, "isAsync": is_async, "status": "async_launched" if is_async else "completed"}
    if usage is not None:
        tur["usage"] = usage
    return tool_result(uuid, t, tool_id, "launched" if is_async else "child done", pid="p1", tur=tur)


def _notice(uuid: str, t: float, task_id: str, status: str) -> dict[str, Any]:
    body = f"<task-notification>\n<task-id>{task_id}</task-id>\n<status>{status}</status>\n</task-notification>"
    return attachment(uuid, t, "queued_command", prompt=body)


def _bash(tool_id: str) -> dict[str, Any]:
    return tool_use_block(tool_id, "Bash", {"command": "ls"})


def _agent_use(tool_id: str, background: bool = False) -> dict[str, Any]:
    spec: dict[str, Any] = {"description": "d", "prompt": "p", "subagent_type": "Explore"}
    if background:
        spec["run_in_background"] = True
    return tool_use_block(tool_id, "Agent", spec)


def subagent_nonfinal_midstream() -> dict[str, str]:
    """Measurement: 11 of 12 non-final sub-agent calls in one live session occurred MID-stream, not at the handback
    (rec-4354 widened 2026-10-17). A sync child makes a complete call, a two-line mid-stream non-final call (output 4) and a
    two-line final handback whose lines also carry stop_reason null (output 8) while the parent's toolUseResult.usage carries
    the true final usage (output 768). Lines up with producer case
    subagent_usage_final_and_spawn_correction (parser_version 4).
    """
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [_agent_use("tuA")]),
        _spawn_result("r1", 10, "tuA", "agentV1", _usage(768)),
        assistant("a2", 11, "m2", [text_block("wrapped up")]),
    ]
    kid = [
        child(prompt("cu1", 3, "p1", "task"), "agentV1"),
        child(assistant("ca1", 4, "mka", [tool_use_block("tc1", "Bash", {"command": "ls"})]), "agentV1"),
        child(tool_result("cr1", 5, "tc1", "out", pid="p1"), "agentV1"),
        child(_nonfinal(assistant("cb1", 6, "mkb", [thinking_block("plan")], usage=_usage(4))), "agentV1"),
        child(
            _nonfinal(assistant("cb2", 7, "mkb", [tool_use_block("tc2", "Bash", {"command": "ls"})], usage=_usage(4))),
            "agentV1",
        ),
        child(tool_result("cr2", 8, "tc2", "out", pid="p1"), "agentV1"),
        child(_nonfinal(assistant("cc1", 8.5, "mkc", [text_block("a")], usage=_usage(8))), "agentV1"),
        child(_nonfinal(assistant("cc2", 9, "mkc", [text_block("b")], usage=_usage(8))), "agentV1"),
    ]
    return make_files(root, {"agentV1": kid})


def subagent_background_failed() -> dict[str, str]:
    """Three background spawns whose completion notices read failed, killed and an unrecognised token. Status values other
    than completed are INFERRED from Claude Code's notice format, not observed. At parser_version 3 each child's close reads
    success (rec-4358). Lines up with producer case background_notice_statuses (parser_version 4).
    """
    root = [prompt("u1", 1, "p1", "go")]
    kids: dict[str, list[dict[str, Any]]] = {}
    for n, (agent, status) in enumerate((("agentF1", "failed"), ("agentF2", "killed"), ("agentF3", "unrecognised_status"))):
        base = 2 + 2 * n
        root.append(assistant(f"a{n}", base, f"m{n}", [_agent_use(f"tu{n}", background=True)]))
        root.append(_spawn_result(f"r{n}", base + 1, f"tu{n}", agent, is_async=True))
        kids[agent] = [
            child(prompt(f"cu{n}", base + 1.5, "p1", "task"), agent),
            child(assistant(f"ca{n}", base + 1.7, f"mk{n}", [text_block("work")]), agent),
        ]
        root.append(_notice(f"n{n}", 10 + n, agent, status))
    root.append(assistant("a9", 20, "m9", [text_block("wrapped up")]))
    return make_files(root, kids)


def resume_with_cost_state() -> dict[str, str]:
    """Reproduces ledger measurement 2 (planning-facts.md): turn 1 on the main model (requestedModel claude-test-1[1m],
    message.model claude-test-1); a sync sub-agent on the MAIN model with one mid-stream non-final call; a sync sub-agent on
    claude-sub-1 whose calls are all complete; then a cost-state record holding claude-test-1[1m] equal to the prefix on input
    (50), cache read (150) and cache creation (200) and 12 above it on output (84 -> 96); claude-sub-1 exact on all four
    (input 5, output 7, read 11, creation 13); claude-haiku-aux present only in the ledger. Then a SessionStart:resume group
    and turn 2 whose calls lie after the cut. Lines up with producer case resume_segment_closes (parser_version 4).
    """

    def main(rec: dict[str, Any]) -> dict[str, Any]:
        rec["requestedModel"] = MAIN_REQUESTED
        return rec

    sub_last = _usage(4, 3, 6, 7)
    root = [
        prompt("u1", 1, "p1", "go"),
        main(assistant("a1", 2, "m1", [_agent_use("tuA"), _agent_use("tuB")])),
        _spawn_result("r1", 10, "tuA", "agentM1", FULL_USAGE),
        _spawn_result("r2", 15, "tuB", "agentS1", sub_last),
        main(assistant("a3", 16, "m3", [text_block("wrapped up")])),
        {
            "type": "cost-state",
            "sessionId": SID,
            "totalCostUSD": 1.5,
            "modelUsage": {
                MAIN_REQUESTED: _ledger(50, 96, 150, 200),
                SUB_MODEL: _ledger(5, 7, 11, 13),
                AUX_MODEL: _ledger(100, 50, 0, 0),
            },
        },
        hook("rh", 100, "SessionStart:resume", command="bash ./resume.sh"),
        prompt("u2", 101, "p2", "again"),
        main(assistant("a4", 102, "m4", [text_block("welcome back")])),
    ]
    main_kid = [
        child(prompt("mu1", 3, "p1", "task"), "agentM1"),
        child(main(assistant("ma1", 4, "mm1", [tool_use_block("mt1", "Bash", {"command": "ls"})])), "agentM1"),
        child(tool_result("mr1", 5, "mt1", "out", pid="p1"), "agentM1"),
        child(main(_nonfinal(assistant("mb1", 6, "mm2", [thinking_block("plan")], usage=_usage(4)))), "agentM1"),
        child(
            main(_nonfinal(assistant("mb2", 7, "mm2", [_bash("mt2")], usage=_usage(4)))),
            "agentM1",
        ),
        child(tool_result("mr2", 8, "mt2", "out", pid="p1"), "agentM1"),
        child(main(assistant("mc1", 9, "mm3", [text_block("done")])), "agentM1"),
    ]
    sub_kid = [
        child(prompt("su1", 12, "p1", "task"), "agentS1"),
        child(
            assistant(
                "sa1", 13, "ms1", [tool_use_block("st1", "Bash", {"command": "ls"})], model=SUB_MODEL, usage=_usage(3, 2, 5, 6)
            ),
            "agentS1",
        ),
        child(tool_result("sr1", 14, "st1", "out", pid="p1"), "agentS1"),
        child(assistant("sb1", 14.5, "ms2", [text_block("done")], model=SUB_MODEL, usage=sub_last), "agentS1"),
    ]
    return make_files(root, {"agentM1": main_kid, "agentS1": sub_kid})


def _ledger(inp: int, out: int, read: int, create: int) -> dict[str, Any]:
    return {
        "inputTokens": inp,
        "outputTokens": out,
        "thinkingTokens": 0,
        "cacheReadInputTokens": read,
        "cacheCreationInputTokens": create,
        "webSearchRequests": 0,
        "costUSD": 0.5,
    }


TREES: dict[str, Callable[[], dict[str, str]]] = {
    "subagent_nonfinal_midstream": subagent_nonfinal_midstream,
    "subagent_background_failed": subagent_background_failed,
    "resume_with_cost_state": resume_with_cost_state,
}

EXPECTED_LEDGER = {
    MAIN_REQUESTED: "ledger_output_residual_nonfinal",
    SUB_MODEL: "ledger_exact",
    AUX_MODEL: "ledger_auxiliary_only",
}
