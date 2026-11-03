"""Observation rows: one assertion per column rule, outcome precedence, hook signature and severity."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections import Counter

import pytest

from src.telemetry.append import prepare_batch
from src.telemetry.gate import GateError
from src.turn_capture import observations
from src.turn_capture.observations import build_observations, hook_name, outcome_of
from src.turn_capture.streams import ROOT, parse_tree
from src.turn_capture.transcript import MemTree
from src.turn_capture.transcripts import Sidecars
from tests.fixtures.turn_capture_corpus import (
    PROJECT,
    SID,
    TENANT,
    assistant,
    attachment,
    build_secret,
    child,
    hook,
    make_ctx,
    make_files,
    parse_records,
    prompt,
    rows_of,
    text_block,
    thinking_block,
    tool_result,
    tool_use_block,
)
from tests.fixtures.turn_capture_ducklake import load_specs


def build(root, children=None, final=True, sidecars=None, cap=None):
    diag: Counter[str] = Counter()
    tree = MemTree(make_files(root, children, sidecars))
    ctx = make_ctx() if cap is None else dataclasses.replace(make_ctx(), full_output_cap_bytes=cap)
    rows = build_observations(parse_tree(tree, None, final), ctx, diag, Sidecars(tree, None))
    return rows_of(rows), diag


def by(rows, **match):
    return [r for r in rows if all(r[k] == v for k, v in match.items())]


def test_model_call_owned_by_last_record_one_per_response() -> None:
    usage = {"input_tokens": 1, "output_tokens": 2, "cache_read_input_tokens": 3}
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [tool_use_block("t1", "Bash", {})], usage=usage, api_block=0),
        tool_result("r1", 3, "t1", "ok", pid="p1"),
        assistant("a2", 4, "m1", [text_block("done")], usage=usage, api_block=1, stop_reason=None),
        assistant("a3", 5, "m2", [text_block("second")], usage={"input_tokens": "9", "output_tokens": True}),
    ]
    rows, diag = build(root)
    calls = by(rows, observation_type="model_call")
    assert [c["external_ref"] for c in calls] == ["m1/model_call", "m2/model_call"]
    first = calls[0]
    assert first["source_ordinal"] == 3 and first["event_timestamp"].isoformat().startswith("2026-01-01T00:00:04")
    assert (first["tokens_input"], first["tokens_output"], first["tokens_cache_read"], first["tokens_cache_creation"]) == (
        1,
        2,
        3,
        0,
    )
    assert first["entity_ref"] == "m1" and first["parent_observation_ref"] == f"{SID}/p1"
    assert (
        first["billing_shape"] == "fixed_non_rollover_allowance"
        and first["provider"] is None
        and first["persona_backend"] is None
    )
    assert first["name"] == "agent_turn" and first["model"] == "claude-test-1" and first["event_kind"] == "point"
    meta = json.loads(first["metadata"])
    assert meta["block_count"] == 2 and meta["request_id"] == "req_m1" and "thinking_tokens" not in meta
    assert (first["reasoning_visibility"], first["reasoning_tokens"]) == ("none", None)
    assert (calls[1]["tokens_input"], calls[1]["tokens_output"]) == (0, 0) and diag["non_int_usage"] == 2


def test_absent_usage_keys_are_counted() -> None:
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [text_block("x")], usage={"input_tokens": 1, "output_tokens": 2}),
        assistant("a2", 3, "m2", [text_block("y")], usage={}),
        assistant("a3", 4, "m3", [text_block("z")]),
    ]
    root[3]["message"].pop("usage")
    rows, diag = build(root)
    calls = by(rows, observation_type="model_call")
    assert [c["tokens_input"] for c in calls] == [1, 0, 0]
    assert diag["absent_usage_key"] == 2 + 4 + 4 and diag["absent_usage"] == 1 and "non_int_usage" not in diag
    _, clean = build([prompt("u1", 1, "p1", "go"), assistant("a1", 2, "m1", [text_block("x")])])
    assert "absent_usage_key" not in clean and "absent_usage" not in clean


def test_thinking_tokens_and_metadata_shapes() -> None:
    usage = {"input_tokens": 1, "output_tokens": 9, "output_tokens_details": {"thinking_tokens": 7}}
    rows, _ = build(
        [
            prompt("u1", 1, "p1", "go", promptSource="sdk", turnOrigin="human"),
            assistant("a1", 2, "m1", [text_block("x")], usage=usage),
        ]
    )
    call = by(rows, observation_type="model_call")[0]
    assert call["reasoning_tokens"] == 7 and "thinking_tokens" not in json.loads(call["metadata"])
    turn_open = by(rows, observation_type="turn", event_kind="open")[0]
    assert json.loads(turn_open["metadata"]) == {"is_meta": False, "prompt_source": "sdk", "turn_origin": "human"}
    turn_close = by(rows, observation_type="turn", event_kind="close")[0]
    assert json.loads(turn_close["metadata"]) == {"response_count": 1, "tool_call_count": 0}
    assert turn_open["sequence"] == turn_close["sequence"] == 1 and turn_open["entity_ref"] == f"{SID}/p1"
    assert turn_open["external_ref"] == "u1#0/turn_open" and turn_close["external_ref"] == "a1#0/turn_close"


def _outcome_root():
    return [
        prompt("u1", 1, "p1", "go"),
        assistant(
            "a1",
            2,
            "m1",
            [
                tool_use_block("tb", "Bash", {}),
                tool_use_block("tp", "Bash", {}),
                tool_use_block("ti", "Bash", {}),
                tool_use_block("te", "Bash", {}),
                tool_use_block("ts", "Bash", {}),
                tool_use_block("tx", "Bash", {}),
            ],
        ),  # fmt: skip
        hook("h1", 2.1, "PreToolUse:Bash", tool_use_id="tb", exit_code=2),
        tool_result("rb", 2.2, "tb", "denied", pid="p1", is_error=True, tur={"interrupted": True}),
        tool_result("rp", 2.3, "tp", "fine", pid="p1", tur={"stdout": "fine", "interrupted": False}),
        hook("h2", 2.4, "PostToolUse:Bash", tool_use_id="tp", exit_code=2),
        tool_result("ri", 2.5, "ti", "", pid="p1", tur={"interrupted": True}),
        tool_result("re", 2.6, "te", "boom", pid="p1", is_error=True),
        tool_result("rs", 2.7, "ts", "ok", pid="p1", tur=["not", "a", "dict"]),
        prompt("u2", 5, "p2", "next"),
    ]


def test_tool_call_outcome_precedence() -> None:
    rows, _ = build(_outcome_root())
    closes = {r["entity_ref"]: r for r in by(rows, observation_type="tool_call", event_kind="close")}
    assert {t: c["outcome"] for t, c in closes.items()} == {
        "tb": "blocked", "tp": "success", "ti": "interrupted", "te": "error", "ts": "success", "tx": "interrupted",
    }  # fmt: skip
    assert json.loads(closes["tx"]["metadata"]) == {"synthetic": True}
    assert closes["tx"]["external_ref"] == "a1#5/tool_call_close" and closes["tx"]["source_ordinal"] == 1
    assert json.loads(closes["tb"]["metadata"])["is_error"] is True


def test_sentinels_are_used_when_pinned(monkeypatch) -> None:
    monkeypatch.setattr(observations, "HARNESS_DENIAL_PREFIXES", ("DENIED:",))
    monkeypatch.setattr(observations, "HARNESS_INTERRUPT_SENTINELS", ("STOPPED",))
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [tool_use_block("t1", "Bash", {}), tool_use_block("t2", "Bash", {})]),
        tool_result("r1", 3, "t1", "DENIED: by policy", pid="p1", is_error=True),
        tool_result("r2", 3.1, "t2", "STOPPED", pid="p1", is_error=True),
    ]
    stream = parse_records(root).streams[ROOT]
    turn = stream.turns[0]
    assert [outcome_of(use.result, stream) for use in turn.tool_uses] == ["blocked", "interrupted"]


def test_every_hook_attachment_is_one_process_event() -> None:
    root = [
        hook("h0", 0.5, "SessionStart:startup", exit_code=None, command="bash .claude/hooks/session_start.sh --x"),
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [tool_use_block("t1", "Bash", {})]),
        hook(
            "h1",
            2.1,
            "PostToolUse:Bash",
            tool_use_id="t1",
            command="python /abs/path/never_on_main.py",
            stderr="e" * 600,
            durationMs=12,
        ),
        hook("h2", 2.2, "PreToolUse:Bash", tool_use_id="unknown-tool", exit_code=1, a_type="hook_additional_context"),
        {**hook("h3", 2.3, "Stop", command="echo no script"), "attachment": {"type": "hook_success", "exitCode": "two"}},
        prompt("u2", 4, "p2", "next"),
        hook("h4", 5, "UserPromptSubmit"),
    ]
    rows, _ = build(root)
    events = {r["external_ref"]: r for r in by(rows, observation_type="process_event")}
    assert sorted(events) == [f"h{n}#0/process_event" for n in range(5)]
    first = events["h0#0/process_event"]
    assert (first["name"], first["severity"], first["parent_observation_ref"], first["entity_ref"]) == (
        "hook:session_start", "info", None, "h0#0/process_event",
    )  # fmt: skip
    post = events["h1#0/process_event"]
    assert (post["name"], post["parent_observation_ref"]) == ("hook:never_on_main", "t1")
    meta = json.loads(post["metadata"])
    assert (
        len(meta["stderr_head"]) == 512
        and meta["duration_ms"] == 12
        and meta["exit_code"] == 0
        and meta["hook_event"] == "PostToolUse"
    )
    other = events["h2#0/process_event"]
    assert (other["name"], other["severity"], other["parent_observation_ref"]) == (
        "hook:PreToolUse.Bash",
        "warning",
        f"{SID}/p1",
    )
    odd = events["h3#0/process_event"]
    assert odd["name"] == "hook:unknown" and odd["severity"] == "warning" and json.loads(odd["metadata"])["exit_code"] is None
    assert events["h4#0/process_event"]["parent_observation_ref"] == f"{SID}/p2"
    live, _ = build(root, final=False)
    assert not by(live, external_ref="h4#0/process_event") and by(live, external_ref="h0#0/process_event")


def test_hook_name_rules_h1_to_h3() -> None:
    assert hook_name({"command": 'bash "/w/.claude/hooks/quoted_hook.sh" --flag'}) == "hook:quoted_hook"
    assert hook_name({"command": "sh '/w/hooks/single.sh'", "hookName": "Stop"}) == "hook:single"
    assert hook_name({"command": "python3 /w/hooks/weird@name.py", "hookName": "PostToolUse:Bash"}) == "hook:PostToolUse.Bash"
    assert hook_name({"command": "echo done", "hookName": "Stop"}) == "hook:Stop"
    assert hook_name({"command": "python3 /w/hooks/weird@name.py", "hookName": "!!"}) == "hook:unknown"
    assert hook_name({"command": "echo", "hookName": "a b"}) == "hook:unknown"
    assert hook_name({"hookName": "x" * 201}) == "hook:unknown" and hook_name({"hookName": "x" * 200}) == "hook:" + "x" * 200
    assert hook_name({"command": 7, "hookName": 7}) == "hook:unknown"


def test_hook_stderr_scrubbed_before_truncation() -> None:
    secret = build_secret("aws_access")
    stderr = "e" * 500 + " " + secret + " tail"
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [text_block("x")]),
        hook("h1", 2.5, "Stop", command="bash .claude/hooks/stop.sh", stderr=stderr),
        prompt("u2", 4, "p2", "next"),
    ]
    rows, _ = build(root)
    head = json.loads(by(rows, observation_type="process_event")[0]["metadata"])["stderr_head"]
    assert "AKIA" not in head and len(head) <= 512 and head.startswith("e" * 500)


def test_tool_denial_kind_outcomes_and_close_metadata() -> None:
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant(
            "a1", 2, "m1",
            [tool_use_block(t, "Bash", {}) for t in ("tr", "tp", "tu", "tn", "th")],
        ),
        tool_result(
            "r1", 3, "tr", "nope", pid="p1", is_error=True, tur="User rejected tool use", toolDenialKind="user-rejected"
        ),
        tool_result(
            "r2", 3.1, "tp", "host said no", pid="p1", is_error=True, tur="Error: x", toolDenialKind="permission-rule",
            permissionDecision={"decision": "reject", "source": "user_reject"},
        ),
        tool_result("r3", 3.2, "tu", "odd", pid="p1", is_error=True, toolDenialKind="brand-new-kind"),
        tool_result("r4", 3.3, "tn", "<tool_use_error>Blocked: x", pid="p1", is_error=True),
        tool_result("r5", 3.4, "th", "ok", pid="p1", toolDenialKind="User Rejected", permissionDecision={"source": "X y"}),
        prompt("u2", 5, "p2", "next"),
    ]  # fmt: skip
    rows, diag = build(root)
    closes = {r["entity_ref"]: r for r in by(rows, observation_type="tool_call", event_kind="close")}
    assert {t: c["outcome"] for t, c in closes.items()} == {
        "tr": "interrupted", "tp": "blocked", "tu": "error", "tn": "error", "th": "success",
    }  # fmt: skip
    assert diag["unknown_tool_denial_kind"] == 2
    meta = {t: json.loads(c["metadata"]) for t, c in closes.items()}
    assert (meta["tr"]["tool_denial_kind"], meta["tr"]["permission_decision_source"]) == ("user-rejected", None)
    assert (meta["tp"]["tool_denial_kind"], meta["tp"]["permission_decision_source"]) == ("permission-rule", "user_reject")
    assert (meta["th"]["tool_denial_kind"], meta["th"]["permission_decision_source"]) == (None, None)
    assert meta["tn"]["tool_denial_kind"] is None
    synthetic = build(
        [prompt("u1", 1, "p1", "go"), assistant("a1", 2, "m1", [tool_use_block("t1", "Bash", {})]), prompt("u2", 3, "p2", "n")]
    )[0]
    assert json.loads(by(synthetic, observation_type="tool_call", event_kind="close")[0]["metadata"]) == {"synthetic": True}


def test_hook_name_fallbacks() -> None:
    assert hook_name({"command": "python -m tool.mod", "hookName": "Stop"}) == "hook:Stop"
    assert hook_name({"command": "run /x/y/gate.sh now", "hookName": "Stop"}) == "hook:gate"
    assert hook_name({"hookName": "SessionStart:resume"}) == "hook:SessionStart.resume"
    assert hook_name({}) == "hook:unknown" and hook_name({"hookName": ""}) == "hook:unknown"


def test_tool_call_open_and_close_columns() -> None:
    block = {**tool_use_block("t1", "mcp__x__y", {"a": 1}), "caller": {"type": "direct"}}
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [block], attributionMcpServer="x"),
        tool_result(
            "r1",
            3,
            "t1",
            "ok",
            pid="p1",
            tur={
                "persistedOutputPath": "C:\\tmp\\tool-results\\o.txt",
                "persistedOutputSize": 9,
                "agentId": "ag",
                "isAsync": True,
                "status": "s",
            },
        ),
    ]
    rows, _ = build(root)
    opened = by(rows, observation_type="tool_call", event_kind="open")[0]
    assert (opened["name"], opened["sequence"], opened["model"], opened["parent_observation_ref"]) == (
        "mcp__x__y",
        1,
        "claude-test-1",
        f"{SID}/p1",
    )
    assert json.loads(opened["metadata"]) == {"attribution_mcp_server": "x", "caller_type": "direct"}
    closed = by(rows, observation_type="tool_call", event_kind="close")[0]
    assert json.loads(closed["metadata"]) == {
        "agent_id": "ag", "interrupted": None, "is_async": True, "is_error": False,
        "persisted_output": "o.txt", "persisted_output_size": 9, "status": "s",
        "permission_decision_source": None, "tool_denial_kind": None,
    }  # fmt: skip


def test_open_turns_emit_nothing_and_orphans_and_late_results_are_handled() -> None:
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [tool_use_block("t1", "Bash", {})]),
        tool_result("r0", 2.5, "ghost", "orphan", pid="p1", is_error=True),
        prompt("u2", 3, "p2", "next"),
        assistant("a2", 4, "m2", [tool_use_block("t2", "Bash", {})]),
        attachment("q", 4.5, "date"),
        hook("h9", 4.6, "PostToolUse:Bash", tool_use_id="t2"),
    ]
    rows, diag = build(root, final=False)
    assert not by(rows, external_ref="a2#0/tool_call_open") and not by(rows, external_ref="h9#0/process_event")
    orphan = by(rows, entity_ref="ghost")[0]
    assert orphan["name"] is None and orphan["sequence"] is None and orphan["outcome"] == "error"
    assert diag["orphan_tool_results"] == 0  # counted at parse time, not by the builder
    closed_all, _ = build(root, final=True)
    assert by(closed_all, external_ref="a2#0/tool_call_close")[0]["outcome"] == "interrupted"
    assert by(closed_all, external_ref="h9#0/process_event")[0]["parent_observation_ref"] == "t2"


def _close(rows, tool_id):
    return by(rows, observation_type="tool_call", event_kind="close", entity_ref=tool_id)[0]


def _lines(data: bytes) -> int:
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)


def test_output_capture_on_every_tool_call_close() -> None:
    big = "L1\nL2\n"
    mcp = "Error: result too large. Output has been saved to /x/tool-results/m.txt\nFormat: json"
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [tool_use_block(t, "Bash", {}) for t in ("tn", "tc", "tu", "to", "tm", "tx", "tl")]),
        tool_result("rn", 3, "tn", "plain\nout", pid="p1"),
        tool_result("rc", 3.1, "tc", "preview", pid="p1", tur={"persistedOutputPath": "/x/tool-results/c.txt"}),
        tool_result("ru", 3.2, "tu", "preview", pid="p1", tur={"persistedOutputPath": "/x/tool-results/gone.txt"}),
        tool_result("ro", 3.3, "to", "preview", pid="p1", tur={"persistedOutputPath": "/x/tool-results/o.txt"}),
        tool_result("rm", 3.4, "tm", mcp, pid="p1", tur=mcp, is_error=True),
        tool_result("rl", 3.5, "tl", "", pid="p1"),
        prompt("u2", 5, "p2", "next"),
    ]
    sidecars = {"c.txt": big, "o.txt": "z" * 40, "m.txt": "mcp body"}
    rows, _ = build(root, sidecars=sidecars, cap=30)
    plain = b"plain\nout"
    assert (_close(rows, "tn")["output_capture"], _close(rows, "tn")["output_bytes"]) == ("not_persisted", len(plain))
    assert _close(rows, "tn")["output_sha256"] == hashlib.sha256(plain).hexdigest() and _close(rows, "tn")["output_lines"] == 2
    captured = _close(rows, "tc")
    assert (captured["output_capture"], captured["output_bytes"], captured["output_lines"]) == ("captured", len(big), 2)
    assert captured["output_sha256"] == hashlib.sha256(big.encode()).hexdigest()
    unavailable = _close(rows, "tu")
    assert unavailable["output_capture"] == "unavailable" and all(
        unavailable[k] is None for k in ("output_bytes", "output_sha256", "output_lines")
    )
    omitted = _close(rows, "to")
    assert (omitted["output_capture"], omitted["output_bytes"], omitted["output_lines"]) == ("omitted_oversize", 40, 1)
    assert (
        _close(rows, "tm")["output_capture"] == "captured"
        and json.loads(_close(rows, "tm")["metadata"])["persisted_output"] == "m.txt"
    )
    empty = _close(rows, "tl")
    assert (empty["output_capture"], empty["output_bytes"], empty["output_lines"]) == ("not_persisted", 0, 0)
    synthetic, _ = build(_outcome_root())
    assert _close(synthetic, "tx")["output_capture"] == "no_result" and _close(synthetic, "tx")["output_bytes"] is None
    orphan, _ = build([prompt("u1", 1, "p1", "go"), tool_result("r0", 2, "ghost", "text", pid="p1")], final=True)
    assert by(orphan, entity_ref="ghost")[0]["output_capture"] == "not_persisted"


def test_reasoning_visibility_and_tokens_on_model_call() -> None:
    def usage(out, thinking):
        details = {} if thinking is None else {"output_tokens_details": {"thinking_tokens": thinking}}
        return {"input_tokens": 1, "output_tokens": out, **details}

    redacted = {"type": "redacted_thinking", "data": "enc"}
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m_sum", [thinking_block("plan"), text_block("x")], usage=usage(9, 7)),
        assistant("a2", 3, "m_both", [thinking_block("plan"), redacted], usage=usage(9, None)),
        assistant("a3", 4, "m_red", [redacted, text_block("x")], usage=usage(9, 3)),
        assistant("a4", 5, "m_empty", [thinking_block(""), text_block("x")], usage=usage(9, None)),
        assistant("a5", 6, "m_tok", [text_block("x")], usage=usage(9, 5)),
        assistant("a6", 7, "m_zero", [text_block("x")], usage=usage(9, 0)),
        assistant("a7", 8, "m_none", [text_block("x")], usage=usage(9, None)),
        assistant(
            "a8", 9, "m_multi", [thinking_block("part"), tool_use_block("t", "Bash", {})], usage=usage(4, 2), api_block=0
        ),
        assistant("a9", 9.1, "m_multi", [text_block("rest")], usage=usage(4, 2), api_block=1),
        prompt("u2", 12, "p2", "next"),
    ]
    rows, _ = build(root)
    got = {
        r["entity_ref"]: (r["reasoning_visibility"], r["reasoning_tokens"]) for r in by(rows, observation_type="model_call")
    }
    assert got == {
        "m_sum": ("summarized", 7), "m_both": ("summarized", None), "m_red": ("redacted", 3), "m_empty": ("omitted", None),
        "m_tok": ("omitted", 5), "m_zero": ("none", 0), "m_none": ("none", None), "m_multi": ("summarized", 2),
    }  # fmt: skip
    spec = load_specs()["telemetry_observations"]
    row = dict(by(rows, entity_ref="m_sum", observation_type="model_call")[0])
    prepare_batch(spec, [row], tenant_id=TENANT, project_id=PROJECT)
    for bad in ({"reasoning_visibility": "none", "reasoning_tokens": 1}, {"reasoning_tokens": 10}):
        with pytest.raises(GateError, match="null_or_zero_when|at_most"):
            prepare_batch(spec, [{**row, **bad}], tenant_id=TENANT, project_id=PROJECT)


SPLIT = {"ephemeral_1h_input_tokens": 15, "ephemeral_5m_input_tokens": 25}


def _sync_run(child_usage, child_stop, result_usage):
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [tool_use_block("tuA", "Agent", {"subagent_type": "Explore"})]),
        tool_result("r1", 4, "tuA", "done", pid="p1", tur={"agentId": "ag1", "isAsync": False, "usage": result_usage}),
        assistant("a2", 5, "m2", [text_block("end")]),
        prompt("u2", 6, "p2", "next"),
    ]
    kid = [
        child(prompt("c1", 2.5, "p1", "t"), "ag1"),
        child(assistant("c2", 3, "mc1", [text_block("one")], stop_reason=None, usage={"input_tokens": 1}), "ag1"),
        child(assistant("c3", 3.5, "mc2", [text_block("two")], stop_reason=child_stop, usage=child_usage), "ag1"),
    ]
    return build(root, {"ag1": kid})


def test_subagent_final_call_without_stop_reason_takes_spawn_result_usage() -> None:
    record = {"input_tokens": 10, "output_tokens": 3, "cache_read_input_tokens": 30, "cache_creation_input_tokens": 40}
    final = {**record, "output_tokens": 77, "cache_creation": SPLIT}
    rows, diag = _sync_run(record, None, final)
    mid, last = by(rows, entity_ref="mc1")[0], by(rows, entity_ref="mc2")[0]
    assert (last["tokens_output"], last["usage_source"], last["usage_final"], last["cache_creation_1h_tokens"]) == (
        77,
        "spawn_result",
        True,
        15,
    )
    assert (mid["usage_source"], mid["usage_final"], mid["tokens_input"]) == ("record", False, 1)
    assert diag["spawn_result_corrected"] == 1 and diag["usage_not_final:subagent"] == 1
    kept, diag = _sync_run(record, None, {**final, "input_tokens": 11})
    last = by(kept, entity_ref="mc2")[0]
    assert (last["tokens_output"], last["usage_source"], last["usage_final"]) == (3, "record", False)
    assert diag["spawn_result_mismatch"] == 1 and diag["usage_not_final:subagent"] == 2
    done, diag = _sync_run(record, "end_turn", final)
    assert by(done, entity_ref="mc2")[0]["tokens_output"] == 3 and not diag["spawn_result_corrected"]


def test_non_final_usage_is_marked_and_counted() -> None:
    usage = {"input_tokens": 1, "output_tokens": 2, "cache_read_input_tokens": 3}
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [text_block("streaming")], usage=usage, stop_reason=None),
        assistant("a2", 3, "m2", [text_block("final")], usage=usage),
        prompt("u2", 4, "p2", "next"),
    ]
    rows, diag = build(root)
    flags = {r["entity_ref"]: (r["usage_final"], r["usage_source"]) for r in by(rows, observation_type="model_call")}
    assert flags == {"m1": (False, "record"), "m2": (True, "record")}
    assert diag["usage_not_final:root"] == 1
    for r in by(rows, observation_type="model_call"):
        prepare_batch(load_specs()["telemetry_observations"], [dict(r)], tenant_id=TENANT, project_id=PROJECT)
    assert all("usage_final" not in r for r in rows if r["observation_type"] != "model_call")


def test_model_call_records_requested_model_effort_and_cache_split() -> None:
    usage = {"input_tokens": 1, "output_tokens": 2, "cache_read_input_tokens": 3, "cache_creation_input_tokens": 40}
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant(
            "a1",
            2,
            "m1",
            [text_block("x")],
            usage={**usage, "cache_creation": SPLIT},
            requestedModel="claude-r[1m]",
            effort="high",
        ),
        assistant("a2", 3, "m2", [text_block("y")], usage=usage),
        assistant("a3", 4, "m3", [text_block("z")], usage={**usage, "cache_creation": SPLIT}, effort="Not Valid"),
        assistant("a4", 5, "m4", [text_block("w")], usage={**usage, "cache_creation": {"ephemeral_1h_input_tokens": 41}}),
        prompt("u2", 6, "p2", "next"),
    ]
    rows, diag = build(root)
    calls = {r["entity_ref"]: r for r in by(rows, observation_type="model_call")}
    assert (calls["m1"]["requested_model"], calls["m1"]["reasoning_effort"]) == ("claude-r[1m]", "high")
    assert (calls["m1"]["cache_creation_1h_tokens"], calls["m1"]["cache_creation_5m_tokens"]) == (15, 25)
    assert not {"requested_model", "reasoning_effort", "cache_creation_1h_tokens"} & set(calls["m2"])
    assert "reasoning_effort" not in calls["m3"] and calls["m3"]["cache_creation_5m_tokens"] == 25
    assert "cache_creation_1h_tokens" not in calls["m4"]
    assert diag["effort_unparsed"] == 1 and diag["cache_split_exceeds_total"] == 1
    spec = load_specs()["telemetry_observations"]
    for row in calls.values():
        prepare_batch(spec, [dict(row)], tenant_id=TENANT, project_id=PROJECT)
