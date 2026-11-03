"""Builders for the ten parser-v3 golden cases (spike-shaped records; stdlib only, no test_ prefix).

Field names, value types and the pinned harness literals mirror the redacted skeletons of the 2026-10-12 observation
spike; no real transcript content is copied. Each builder returns the files mapping of its committed golden case.
"""

from __future__ import annotations

import dataclasses
from collections import Counter
from typing import Any, Callable

from src.turn_capture.record_turn import PARSER_VERSION, CaptureResult, record_turn
from src.turn_capture.transcript import MemTree
from tests.fixtures.turn_capture_corpus import (
    PROJECT_REF,
    SID,
    _base,
    assistant,
    case_cap,
    hook,
    jsonl,
    make_files,
    prompt,
    queue_op,
    resume_tail,
    root_lines,
    text_block,
    tool_result,
    tool_use_block,
    with_root,
)

INTERRUPT = "[Request interrupted by user]"
INTERRUPT_TOOL = "[Request interrupted by user for tool use]"
REJECTED_TEXT = (
    "The user doesn't want to proceed with this tool use. The tool use was rejected (eg. if it was a file edit, the "
    "new_string was NOT written to the file). STOP what you are doing and wait for the user to tell you how to proceed."
)
SOURCE_SID = "bbbbbbbb-0000-4000-8000-000000000002"
ZERO_USAGE = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
GATE_LOG = "GATE-FAILURE v1 source=gate check=validate_alpha"
PRECOMMIT_BLOCK = "ruff.....Failed\n- hook id: ruff\n- exit code: 1\n\nsrc/x.py:1:1: F401 unused import\n"


def _interrupt_prompt(uuid: str, t: float, pid: str, text: str) -> dict[str, Any]:
    return prompt(uuid, t, pid, [text_block(text)])


def _api_error(uuid: str, t: float, kind: str = "server_error") -> dict[str, Any]:
    rec = assistant(
        uuid,
        t,
        "00000000-0000-4000-8000-0000000000e1",
        [text_block("API Error: Connection refused")],
        usage=ZERO_USAGE,
        model="<synthetic>",
        isApiErrorMessage=True,
        error=kind,
    )
    rec["message"]["stop_reason"] = "stop_sequence"
    return rec


def _copied(rec: dict[str, Any], branch: bool) -> dict[str, Any]:
    rec = {**rec, "sessionId": SID}
    return {**rec, "forkedFrom": {"sessionId": SOURCE_SID, "messageUuid": rec["uuid"]}} if branch else rec


def _head(branch: bool) -> list[dict[str, Any]]:
    head = [
        prompt("c1", 1, "cp1", "copied question"),
        assistant("c2", 2, "cm1", [text_block("copied answer")]),
        prompt("c3", 3, "cp2", "copied follow up"),
        assistant("c4", 4, "cm2", [text_block("copied reply")]),
    ]
    return [_copied(rec, branch) for rec in head]


def _own_turns(t: float, tag: str) -> list[dict[str, Any]]:
    return [
        prompt(f"{tag}1", t, f"{tag}p1", "own question"),
        assistant(f"{tag}2", t + 1, f"{tag}m1", [text_block("own answer")]),
        prompt(f"{tag}3", t + 2, f"{tag}p2", "own follow up"),
    ]


def gate_and_precommit_markers_files() -> dict[str, str]:
    long_log = GATE_LOG.replace("alpha", "far_back") + "\n" + "\n".join(f"filler line {i}" for i in range(250)) + "\n"
    sidecar_marker = GATE_LOG.replace("alpha", "from_sidecar")
    persisted_log = f"starting\n{sidecar_marker}\n{sidecar_marker}\n"
    visible = "checks ran\n" + GATE_LOG + "\nmore\n" + GATE_LOG.replace("alpha", "beta") + "\n"
    persisted = {"persistedOutputPath": "/elsewhere/tool-results/gate.txt", "persistedOutputSize": len(persisted_log)}
    records = [
        prompt("u1", 1, "p1", "run the gates"),
        assistant(
            "a1", 2, "m1",
            [
                tool_use_block("tu1", "Bash", {"command": "bin/venv-python -m scripts.validate --pre"}),
                tool_use_block("tu2", "Bash", {"command": "bin/venv-python -m scripts.validate"}),
                tool_use_block("tu3", "Bash", {"command": "git commit -m wip"}),
                tool_use_block("tu4", "Bash", {"command": "bin/venv-python -m scripts.validate --full"}),
                tool_use_block("tu5", "Bash", {"command": "ls"}),
            ],
        ),
        tool_result("r1", 3, "tu1", visible, pid="p1", is_error=True),
        tool_result("r2", 4, "tu2", "preview", pid="p1", tur=persisted),
        tool_result("r3", 5, "tu3", "[main abc] blocked\n" + PRECOMMIT_BLOCK + "passing....Passed\n", pid="p1", is_error=True),
        tool_result("r4", 6, "tu4", long_log, pid="p1", is_error=True),
        tool_result("r5", 7, "tu5", GATE_LOG, pid="p1"),
        prompt("u2", 9, "p2", "thanks"),
    ]  # fmt: skip
    return make_files(records, sidecars={"gate.txt": persisted_log})


def close_cancelled_mid_stream_files() -> dict[str, str]:
    partial = assistant("a2", 5, "m2", [text_block("starting to")], isAbortedMidStream=True)
    partial["message"]["stop_reason"] = None
    records = [
        prompt("u1", 1, "p1", "first question"),
        assistant("a1", 2, "m1", [text_block("first answer")]),
        prompt("u2", 4, "p2", "second question"),
        partial,
        _interrupt_prompt("u3", 6, "p3", INTERRUPT),
    ]
    return make_files(records)


def close_cancelled_running_tool_files() -> dict[str, str]:
    records = [
        prompt("u1", 1, "p1", "run it"),
        assistant("a1", 2, "m1", [tool_use_block("tu1", "Bash", {"command": "sleep 30"})]),
        tool_result(
            "r1", 3, "tu1", REJECTED_TEXT, pid="p1", is_error=True,
            tur="User rejected tool use", toolDenialKind="user-rejected",
        ),
        _interrupt_prompt("u2", 4, "p1", INTERRUPT_TOOL),
    ]  # fmt: skip
    return make_files(records)


def close_failed_api_error_files() -> dict[str, str]:
    records = [
        prompt("u1", 1, "p1", "touch a file"),
        assistant("a1", 2, "m1", [tool_use_block("tu1", "Bash", {"command": "touch denied.txt"})]),
        tool_result(
            "r1", 3, "tu1", "denied by host", pid="p1", is_error=True,
            tur="Error: denied by host", toolDenialKind="permission-rule",
            permissionDecision={"decision": "reject", "source": "user_reject"},
        ),
        assistant("a2", 4, "m2", [text_block("understood")]),
        prompt("u2", 6, "p2", "say hi"),
        _api_error("a3", 7),
    ]  # fmt: skip
    return make_files(records)


def resume_after_interrupt_files() -> dict[str, str]:
    synthetic = assistant(
        "a2", 8, "00000000-0000-4000-8000-0000000000e2", [text_block("No response requested.")],
        usage=ZERO_USAGE, model="<synthetic>", isApiErrorMessage=False,
    )  # fmt: skip
    records = [
        prompt("u1", 1, "p1", "run it"),
        assistant("a1", 2, "m1", [tool_use_block("tu1", "Bash", {"command": "sleep 30"})]),
        tool_result(
            "r1", 3, "tu1", REJECTED_TEXT, pid="p1", is_error=True,
            tur="User rejected tool use", toolDenialKind="user-rejected",
        ),
        _interrupt_prompt("u2", 4, "p1", INTERRUPT_TOOL),
        hook("rs1", 6, "SessionStart:resume", command="bash ./resume.sh"),
        hook("rs2", 7, "SessionStart:resume", command="bash ./other.sh"),
        synthetic,
        prompt("u3", 10, "p2", "continue please"),
        assistant("a3", 11, "m3", [text_block("continuing")]),
    ]  # fmt: skip
    return make_files(records)


def fork_branch_copied_head_files() -> dict[str, str]:
    own_head = _base("sys1", 19, type="system", subtype="local_command", content="<command-name>/branch</command-name>")
    return make_files(_head(True) + [own_head] + _own_turns(20, "o"))


def fork_session_copied_head_files() -> dict[str, str]:
    group = [
        hook("hf1", 19, "SessionStart:fork", command="bash ./start.sh"),
        hook("hf2", 19.5, "SessionStart:fork", command="bash ./x.sh"),
    ]
    return make_files(_head(False) + group + _own_turns(20, "o"))


def fork_session_nested_files() -> dict[str, str]:
    parent_group = [hook("hp1", 9, "SessionStart:fork", command="bash ./start.sh")]
    parent_own = [prompt("pp1", 10, "pp1", "parent own"), assistant("pa1", 11, "pm1", [text_block("parent answer")])]
    child_group = [hook("hf1", 29, "SessionStart:fork", command="bash ./start.sh")]
    return make_files(_head(False) + parent_group + parent_own + child_group + _own_turns(30, "o"))


def hook_command_names_files() -> dict[str, str]:
    records = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [tool_use_block("tu1", "Bash", {"command": "ls"})]),
        hook("h1", 3, "PostToolUse:Bash", tool_use_id="tu1", command='bash "/w/.claude/hooks/quoted_hook.sh" --flag'),
        hook("h2", 4, "PostToolUse:Bash", tool_use_id="tu1", command="python3 /w/hooks/weird@name.py"),
        hook("h3", 5, "PostToolUse:Bash", tool_use_id="tu1", command="echo done"),
        hook("h4", 6, "", tool_use_id="tu1", command="echo !!"),
        tool_result("r1", 7, "tu1", "ok", pid="p1"),
        assistant("a2", 8, "m2", [text_block("done")]),
        prompt("u2", 9, "p2", "next"),
    ]
    return make_files(records)


def no_reply_session_files() -> dict[str, str]:
    records = [
        queue_op(3, uuid="q0"),
        hook("hk1", 1, "SessionStart:startup", exit_code=0),
        prompt("u1", 4, "p1", "anyone there"),
    ]
    return make_files(records)


V3_BUILDERS: dict[str, Callable[[], dict[str, str]]] = {
    "gate_and_precommit_markers.json": gate_and_precommit_markers_files,
    "close_cancelled_mid_stream.json": close_cancelled_mid_stream_files,
    "close_cancelled_running_tool.json": close_cancelled_running_tool_files,
    "close_failed_api_error.json": close_failed_api_error_files,
    "resume_after_interrupt.json": resume_after_interrupt_files,
    "fork_branch_copied_head.json": fork_branch_copied_head_files,
    "fork_session_copied_head.json": fork_session_copied_head_files,
    "fork_session_nested.json": fork_session_nested_files,
    "hook_command_names.json": hook_command_names_files,
    "no_reply_session.json": no_reply_session_files,
}


def keyed(result: CaptureResult) -> dict[tuple[str, str], dict[str, Any]]:
    return {(table, row["external_ref"]): row for table, rows in result.batches for row in rows}


def _closes(rows: dict[tuple[str, str], dict[str, Any]]) -> dict[tuple[str, str], tuple[Any, dict[str, Any]]]:
    """The latest close per (type, entity) by (event_timestamp, source_ordinal, event ref) among observation closes."""
    out: dict[tuple[str, str], tuple[Any, dict[str, Any]]] = {}
    for (table, ref), row in rows.items():
        if (
            table != "telemetry_observations"
            or row["event_kind"] != "close"
            or row["observation_type"] not in ("turn", "tool_call")
        ):
            continue
        key, order = (row["observation_type"], row["entity_ref"]), (row["event_timestamp"], row["source_ordinal"], ref)
        if key not in out or order > out[key][0]:
            out[key] = (order, row)
    return out


def run_schedule(files: dict[str, str], cut: int, kind: str, name: str = "") -> tuple[list[CaptureResult], dict[str, str]]:
    """Finalize at root line *cut*; then RESUME (a resume tail on the prefix) or CONTINUED (the original root grows);
    Stop; finalize. Sub-agent files are at full length throughout."""
    lines = [line for line in root_lines(files) if line.endswith("\n")]
    prefix = "".join(lines[:cut])
    final_files = with_root(files, prefix + jsonl(resume_tail())) if kind == "resume" else files
    results = []
    with case_cap(name):
        results.append(record_turn(MemTree(with_root(files, prefix)), None, project_ref=PROJECT_REF, session_final=True))
        for final in (False, True):
            cursor = results[-1].next_cursor
            results.append(record_turn(MemTree(final_files), cursor, project_ref=PROJECT_REF, session_final=final))
    return results, final_files


def check_schedule(files: dict[str, str], cut: int, kind: str, name: str = "") -> Counter[str]:
    """The VP 1 property for one (case, cut, kind); returns the schedule's summed diagnostics."""
    results, final_files = run_schedule(files, cut, kind, name)
    label = (name, kind, cut)
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    diag: Counter[str] = Counter()
    for result in results:
        for key, row in keyed(result).items():
            assert key not in seen, (label, "emitted twice", key)
            seen[key] = row
        diag.update(result.diagnostics)
    cursor = results[-1].next_cursor
    assert cursor is not None and diag["prior_content_divergence"] == 0, label
    with case_cap(name):
        stale = dataclasses.replace(cursor, parser_version=PARSER_VERSION - 1)
        scratch = keyed(record_turn(MemTree(final_files), stale, project_ref=PROJECT_REF, session_final=True))
        full = keyed(record_turn(MemTree(final_files), None, project_ref=PROJECT_REF, session_final=True))
    assert seen == scratch, (label, "union differs from the emission set recomputed from the cuts")
    assert not set(full) - set(seen), (label, "rows of the full final parse are missing")
    extra = set(seen) - set(full)
    is_close = all(table == "telemetry_sessions" and ref.endswith("/close") for table, ref in extra)
    changed = [key for key in full if seen[key] != full[key]]
    if kind == "resume":
        assert is_close and not changed, (label, extra, changed)
        assert not any(diag[k] for k in ("cut_content_divergence", "reanchored_close:turn", "reanchored_close:tool_call")), (
            label
        )
    else:
        assert all(table == "telemetry_sessions" or ref.endswith("_close") for table, ref in extra), (label, extra)
        assert all(key[1].endswith("/model_call") for key in changed) and len(changed) <= diag["cut_content_divergence"], label
    expected = _closes(full)
    for key, (_, row) in _closes(seen).items():
        assert key not in expected or expected[key][1] == row, (label, "latest close differs", key)
    return diag
