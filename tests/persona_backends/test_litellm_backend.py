import asyncio
import dataclasses
import json
import os
import shutil
import sys
from pathlib import Path

import pytest
import yaml
from mcp.client.stdio import StdioServerParameters

from src.persona_backends import RunAborted, RunRefused
from tests.persona_backends import fakes
from tests.persona_backends.fakes import ScriptedTransport as T
from tests.persona_backends.fakes import attempt, drive, error, final, tool

FB = "warm_fetched_escape_hatch"
READ = {"blob": "notes", "mode": "lines"}


def test_tool_then_final_succeeds_with_two_turn_events():
    t = T([tool(READ), final()])
    result, events = drive(t)
    assert result["outcome"] == "success"
    assert [e["kind"] for e in events if "kind" in e] == ["tool", "terminal_valid"]
    assert t.calls[1]["messages"][-2]["role"] == "assistant"
    assert t.calls[1]["messages"][-2]["reasoning_content"] == "thinking"
    assert t.calls[1]["messages"][-1]["role"] == "tool" and "1: line one" in t.calls[1]["messages"][-1]["content"]


def test_every_call_passes_json_object_and_read_input_tool():
    t = T([tool(READ), final()])
    drive(t)
    for call in t.calls:
        assert call["response_format"] == {"type": "json_object"}
        assert call["tools"][0]["function"]["name"] == "read_input"
        assert call["persona"] == "fixture_persona"


def test_invalid_then_valid_within_max_revisions():
    result, events = drive(T([final("nope"), final()]))
    assert result["outcome"] == "success" and events[-1]["revisions_used"] == 1


def test_revisions_exhausted_returns_schema_invalid():
    result, _ = drive(T([final("nope"), final("nope")]))
    assert result["failure"] == "schema_invalid"


def test_empty_output_is_a_repair_turn():
    t = T([error("empty_output", (attempt(outcome="empty_output"),)), final()])
    result, events = drive(t)
    assert result["outcome"] == "success"
    assert t.calls[1]["messages"][-1]["role"] == "user" and t.calls[1]["messages"][-2]["role"] == "user"
    assert events[1]["kind"] == "terminal_invalid"


def test_malformed_output_is_a_repair_turn():
    result, _ = drive(T([error("malformed_output", (attempt(outcome="malformed_output"),)), final()]))
    assert result["outcome"] == "success"


def test_max_turns_counts_retried_attempts():
    three = (attempt(outcome="outage", n=1), attempt(outcome="outage", n=2), attempt(n=3))
    result, events = drive(T([tool(READ, attempts=three), tool(READ, attempts=three), final()]))
    assert result["failure"] == "budget_exhausted" and events[-1]["turns_used"] == 6


def test_fail_over_attempts_stay_within_max_turns(tmp_path):
    import shutil

    import yaml

    for max_turns, over, expect_attempts in [(3, True, 1), (5, True, 2), (2, False, 2)]:
        root = tmp_path / str(max_turns)
        (root / "rendered").mkdir(parents=True)
        data = yaml.safe_load(fakes.FIXTURE.read_text(encoding="utf-8"))
        data["budget"]["max_turns"] = max_turns
        (root / "fixture_persona.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
        shutil.copy(fakes.FIXTURE.parent / "rendered" / "fixture_persona.txt", root / "rendered" / "fixture_persona.txt")
        t = T([final()])
        drive(t, contract=root / "fixture_persona.yaml")
        assert t.calls[0]["fail_over"] is over and t.calls[0]["max_attempts"] == expect_attempts


def test_fail_over_keeps_fallback_role():
    failed = dataclasses.replace(
        tool(READ), attempts=(attempt(outcome="outage"), attempt(outcome="outage", n=2), attempt(FB)), failed_over=True
    )
    t = T([failed, tool(READ), final()])
    result, _ = drive(t)
    assert result["outcome"] == "success"
    assert [c["role"] for c in t.calls] == ["primary", FB, FB]
    assert [c["fail_over"] for c in t.calls] == [True, False, False]


def test_fail_over_then_fallback_empty_output_counts_probe(tmp_path):
    import shutil

    import yaml

    root = tmp_path / "c"
    (root / "rendered").mkdir(parents=True)
    data = yaml.safe_load(fakes.FIXTURE.read_text(encoding="utf-8"))
    (root / "fixture_persona.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    shutil.copy(fakes.FIXTURE.parent / "rendered" / "fixture_persona.txt", root / "rendered" / "fixture_persona.txt")
    attempts = (attempt(outcome="outage"), attempt(outcome="outage", n=2), attempt(FB, "empty_output"))
    t = T([error("empty_output", attempts), final()])
    result, events = drive(t, contract=root / "fixture_persona.yaml")
    assert result["outcome"] == "success"
    assert events[1]["probe_turn"] == 1 and events[-1]["turns_used"] == 5
    assert t.calls[1]["role"] == FB and t.calls[1]["max_attempts"] == 2 and t.calls[1]["fail_over"] is False


def test_deadline_returns_budget_exhausted():
    clock = fakes.FakeClock(step=100.0)
    t = T([tool(READ)])
    result, _ = drive(t, clock=clock)
    assert result["failure"] == "budget_exhausted" and "wall" in result["detail"] and not t.calls


def test_wait_for_timeout_returns_budget_exhausted(monkeypatch):
    async def slow(*_a, **_k):
        await asyncio.sleep(10)

    clock = fakes.FakeClock()
    real = asyncio.wait_for

    async def short(coro, _limit):
        return await real(coro, 0.01)

    monkeypatch.setattr(asyncio, "wait_for", short)
    result, events = drive(slow, clock=clock)
    assert result["failure"] == "budget_exhausted"
    assert events[1]["attempts_known"] is False and events[-1]["turns_used"] == 5


def test_input_incomplete_makes_no_call():
    t = T([])
    result, events = drive(t, {"change_id": "c1", "head_sha": fakes.SHA})
    assert result["failure"] == "input_incomplete" and not t.calls and len(events) == 2


def test_type_error_returns_input_unusable_when_listed(tmp_path):
    import shutil

    import yaml

    root = tmp_path / "c"
    (root / "rendered").mkdir(parents=True)
    data = yaml.safe_load(fakes.FIXTURE.read_text(encoding="utf-8"))
    data["failure_outputs"].append({"id": "input_unusable", "meaning": "x"})
    (root / "fixture_persona.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    shutil.copy(fakes.FIXTURE.parent / "rendered" / "fixture_persona.txt", root / "rendered" / "fixture_persona.txt")
    t = T([])
    result, _ = drive(t, {**fakes.good_input(), "notes": 5}, contract=root / "fixture_persona.yaml")
    assert result["failure"] == "input_unusable" and not t.calls
    result, _ = drive(t, {**fakes.good_input(), "notes": 5})
    assert result["failure"] == "input_incomplete"


def test_fallback_unavailable_counts_upper_bound():
    t = T([error("fallback_unavailable")])
    with pytest.raises(RunAborted) as info:
        drive(t)
    events = info.value.events
    assert events[1]["attempts_known"] is False and events[-1]["turns_used"] == 5


def test_fallback_setup_refusal_counts_probe():
    attempts = (attempt(outcome="outage"), attempt(outcome="outage", n=2))
    t = T([error("refused", attempts)])
    with pytest.raises(RunAborted) as info:
        drive(t)
    assert info.value.kind == "refused"
    assert info.value.events[1]["probe_turn"] == 1 and info.value.events[-1]["turns_used"] == 3


def test_outage_raises_run_aborted_with_events():
    seen = []
    t = T([error("outage", (attempt(outcome="outage"),))])
    with pytest.raises(RunAborted) as info:
        drive(t, on_event=seen.append)
    assert info.value.kind == "outage"
    kinds = [(e.get("kind"), e.get("outcome")) for e in seen]
    assert ("transport_error", None) in kinds and (None, "aborted") in kinds
    assert seen[-1]["failure"] is None and seen[-1]["error"] == "outage"


def test_non_transport_exception_aborts():
    with pytest.raises(RunAborted) as info:
        drive(T([ValueError("boom")]))
    assert info.value.kind == "transport_exception" and "ValueError" in info.value.detail


def test_unknown_tool_gets_error_text():
    t = T([tool({}, name="other"), final()])
    drive(t)
    assert t.calls[1]["messages"][-1]["content"] == "error: unknown tool"


def test_facade_refuses_other_backends_and_workspace(tmp_path):
    with pytest.raises(RunRefused):
        drive(T([]), backend="claude_cli")
    with pytest.raises(RunRefused):
        drive(T([]), backend="codex_cli")
    with pytest.raises(RunRefused):
        drive(T([]), workspace=tmp_path)


def test_opening_message_lists_blobs():
    t = T([final()])
    drive(t)
    user = t.calls[0]["messages"][1]["content"]
    assert "notes: 3 lines" in user and "change_id: c1" in user
    assert json.dumps(t.calls[0]["messages"][0]["content"])


ROOT = Path(__file__).resolve().parents[2]
VERB = {"verb": "fake_rows"}


def _contract(tmp_path, capabilities=("read_input:notes", "warehouse_read:fake_rows"), seconds=None):
    root = tmp_path / "w"
    (root / "rendered").mkdir(parents=True, exist_ok=True)
    data = yaml.safe_load(fakes.FIXTURE.read_text(encoding="utf-8"))
    data["capabilities"] = list(capabilities)
    if seconds is not None:
        data["budget"]["max_wall_clock_seconds"] = seconds
    (root / "fixture_persona.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    shutil.copy(fakes.FIXTURE.parent / "rendered" / "fixture_persona.txt", root / "rendered" / "fixture_persona.txt")
    return root / "fixture_persona.yaml"


def _server(mode="ok", env=None):
    def factory():
        return StdioServerParameters(
            command=sys.executable, args=["-m", "tests.persona_backends.fake_mcp_server", mode], cwd=ROOT, env=env
        )

    return factory


def _warehouse(tmp_path, script, mode="ok", contract=None, **kw):
    return drive(
        T(script) if isinstance(script, list) else script,
        contract=contract or _contract(tmp_path),
        mcp_server=_server(mode),
        **kw,
    )


def test_warehouse_tool_turn_returns_server_text(tmp_path):
    t = T([tool({"limit": 1}, name="fake_rows"), final()])
    result, events = _warehouse(tmp_path, t)
    assert result["outcome"] == "success"
    message = t.calls[1]["messages"][-1]
    assert message["role"] == "tool" and "synthetic row" in message["content"]
    turn = events[1]
    assert turn["tool_calls"] == ["fake_rows"] and turn["tool_result_bytes"] == [len(message["content"].encode())]
    assert events[-1]["turns_used"] == 2


def test_offered_tools_are_read_input_then_named_verbs(tmp_path):
    t = T([final()])
    _warehouse(tmp_path, t)
    assert [spec["function"]["name"] for spec in t.calls[0]["tools"]] == ["read_input", "fake_rows"]


def test_contract_without_read_input_blob_offers_no_read_input_tool(tmp_path):
    t = T([final()])
    _warehouse(tmp_path, t, contract=_contract(tmp_path, ("warehouse_read:fake_rows",)))
    assert [spec["function"]["name"] for spec in t.calls[0]["tools"]] == ["fake_rows"]
    assert "read_input" not in t.calls[0]["messages"][1]["content"] and "notes:" not in t.calls[0]["messages"][1]["content"]


def test_contract_naming_no_tools_sends_none(tmp_path):
    t = T([final()])
    drive(t, contract=_contract(tmp_path, ()))
    assert t.calls[0]["tools"] is None


def test_non_warehouse_run_starts_no_server():
    def boom():
        raise AssertionError("factory called")

    result, _ = drive(T([final()]), mcp_server=boom)
    assert result["outcome"] == "success"


def test_warehouse_without_server_is_refused(tmp_path):
    with pytest.raises(RunRefused, match="MCP server"):
        drive(T([final()]), contract=_contract(tmp_path))


def test_input_incomplete_starts_no_server(tmp_path):
    def boom():
        raise AssertionError("factory called")

    result, _ = drive(T([]), {"change_id": "c1", "head_sha": fakes.SHA}, contract=_contract(tmp_path), mcp_server=boom)
    assert result["failure"] == "input_incomplete"


def _recording_devnull(monkeypatch):
    opened = []
    real = os.devnull

    def helper():
        handle = open(real, "w", encoding="utf-8")
        opened.append(handle)
        return handle

    monkeypatch.setattr("src.persona_backends.litellm_backend._open_devnull", helper)
    return opened


def test_unavailable_server_aborts_before_any_call(tmp_path, monkeypatch):
    opened = _recording_devnull(monkeypatch)
    t = T([])
    with pytest.raises(RunAborted) as info:
        _warehouse(tmp_path, t, mode="crash")
    assert info.value.kind == "tool_server_unavailable" and not t.calls
    assert info.value.events[-1]["outcome"] == "aborted" and len(opened) == 1 and opened[0].closed


def test_factory_failure_aborts_unavailable(tmp_path, monkeypatch):
    opened = _recording_devnull(monkeypatch)

    def factory():
        raise ImportError("no mcp")

    with pytest.raises(RunAborted) as info:
        drive(T([]), contract=_contract(tmp_path), mcp_server=factory)
    assert info.value.kind == "tool_server_unavailable" and info.value.detail == "ImportError"
    assert info.value.events[-1]["error"] == "tool_server_unavailable" and not opened


def test_errlog_devnull_closed_after_run(tmp_path, monkeypatch):
    opened = _recording_devnull(monkeypatch)
    _warehouse(tmp_path, [final()])
    assert len(opened) == 1 and opened[0].closed


def test_supplied_errlog_is_left_open(tmp_path):
    with (tmp_path / "err.txt").open("w", encoding="utf-8") as log:
        _warehouse(tmp_path, [final()], mcp_errlog=log)
        assert not log.closed


def test_server_failure_mid_run_aborts(tmp_path):
    t = T([tool({}, name="fake_rows"), final()])
    with pytest.raises(RunAborted) as info:
        _warehouse(tmp_path, t, mode="die")
    assert info.value.kind == "tool_server_error" and len(t.calls) == 1
    events = info.value.events
    assert events[1]["tool_calls"] == ["fake_rows"] and events[1]["tool_result_bytes"] == []
    assert events[-1]["outcome"] == "aborted" and events[-1]["turns_used"] == 1


def test_tool_turn_ended_at_is_transport_return(tmp_path, monkeypatch):
    now = [1000.0]

    def wall():
        return now[0]

    from src.persona_backends import mcp_bridge

    real = mcp_bridge.Bridge.call

    async def advancing(self, name, arguments_json):
        now[0] += 50.0
        return await real(self, name, arguments_json)

    monkeypatch.setattr(mcp_bridge.Bridge, "call", advancing)
    _, events = _warehouse(tmp_path, [tool({}, name="fake_rows"), final()], wall=wall)
    assert events[1]["ended_at_ms"] == 1000000 and events[-1]["ended_at_ms"] == 1050000


def test_startup_hang_returns_budget_exhausted(tmp_path):
    t = T([final()])
    result, events = _warehouse(tmp_path, t, mode="hang", contract=_contract(tmp_path, seconds=1))
    assert result["failure"] == "budget_exhausted" and "started" in result["detail"] and not t.calls
    assert events[-1]["outcome"] == "failure"


def test_warehouse_call_past_deadline_returns_budget_exhausted(tmp_path, monkeypatch):
    real = asyncio.wait_for

    async def short(coro, limit):
        return await real(coro, 0.01 if limit < 3000 else limit)

    monkeypatch.setattr(asyncio, "wait_for", short)
    t = T([tool({}, name="fake_rows"), final()])
    result, events = _warehouse(tmp_path, t, mode="slow", contract=_contract(tmp_path, seconds=2000))
    assert result["failure"] == "budget_exhausted" and "started" not in result["detail"] and len(t.calls) == 1
    assert events[1]["tool_calls"] == ["fake_rows"] and events[-1]["outcome"] == "failure"


def test_transport_outage_with_bridge_open_aborts(tmp_path):
    t = T([error("outage", (attempt(outcome="outage"),))])
    with pytest.raises(RunAborted) as info:
        _warehouse(tmp_path, t)
    assert type(info.value) is RunAborted and info.value.kind == "outage"


def test_unlisted_tool_name_with_bridge_open_gets_error_text(tmp_path):
    t = T([tool({}, name="fake_by_ids"), final()])
    _warehouse(tmp_path, t)
    assert t.calls[1]["messages"][-1]["content"] == "error: unknown tool"


def test_fail_over_turn_carries_probe_record():
    probe = fakes.record(FB)
    failed = dataclasses.replace(
        final(),
        attempts=(attempt(outcome="outage"), attempt(outcome="outage", n=2), attempt(FB)),
        failed_over=True,
        probe_record=probe,
    )
    _, events = drive(T([failed]))
    assert events[1]["probe_record"] == dataclasses.asdict(probe) and events[1]["failed_over"] is True
    _, events = drive(T([error("empty_output", (attempt(FB, "empty_output"),), probe), final()]))
    assert events[1]["probe_record"] == dataclasses.asdict(probe)


def test_header_tools_sha256_matches_tools_sent():
    from src.persona_backends import events as ev

    t = T([final()])
    _, events = drive(t)
    assert events[0]["tools_sha256"] == ev.tools_sha256(t.calls[0]["tools"])


def test_header_tools_sha256_covers_bridge_specs(tmp_path):
    from src.persona_backends import events as ev

    seen = []
    t = T([final()])
    _, events = _warehouse(tmp_path, t, on_event=seen.append)
    assert events[0]["tools_sha256"] == ev.tools_sha256(t.calls[0]["tools"])
    heads = [e for e in seen if "schema" in e]
    assert len(heads) == 2 and heads[-1]["tools_sha256"] == events[0]["tools_sha256"]
