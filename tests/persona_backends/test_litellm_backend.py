import asyncio
import dataclasses
import json

import pytest

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
