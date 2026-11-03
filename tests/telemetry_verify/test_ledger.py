"""Mirror tests for scripts/telemetry_verify/ledger.py: the cut-bounded Claude Code ledger reconciliation."""

from __future__ import annotations

import json
from typing import Any

from scripts.telemetry_verify import ledger
from tests.fixtures import telemetry_verification_trees as trees
from tests.fixtures.turn_capture_corpus import (
    SID,
    assistant,
    attachment,
    child,
    hook,
    make_files,
    mem_tree,
    prompt,
    text_block,
    tool_result,
    tool_use_block,
)

_OUT = {"input_tokens": 10, "output_tokens": 20, "cache_read_input_tokens": 30, "cache_creation_input_tokens": 40}
_LED = {"inputTokens": 10, "outputTokens": 20, "cacheReadInputTokens": 30, "cacheCreationInputTokens": 40}


def _cost_state(**models: dict[str, int] | None) -> dict[str, Any]:
    return {"type": "cost-state", "sessionId": SID, "modelUsage": models}


def _classes(files: dict[str, str], cut: int = 0) -> dict[str | None, str]:
    return {f.model: f.cls for f in ledger.reconcile_ledger(mem_tree(files)) if f.cut_index == cut}


def _one_call(led: dict[str, int] | None, **extra: Any) -> dict[str, str]:
    state = _cost_state() if led is None else _cost_state(**{"claude-test-1": led})
    root = [prompt("u1", 1, "p1", "go"), assistant("a1", 2, "m1", [text_block("hi")]), state]
    return _classes(make_files(root + extra.get("tail", [])))


def test_cut_bounded_reconciliation_classifies_every_model() -> None:
    files = trees.resume_with_cost_state()
    found = {f.model: f for f in ledger.reconcile_ledger(mem_tree(files))}
    assert {m: f.cls for m, f in found.items()} == trees.EXPECTED_LEDGER
    main = found[trees.MAIN_REQUESTED]
    assert (main.shortfall, main.nonfinal_calls) == (12, 1) and not main.alarm
    assert main.transcript == {"input": 50, "output": 84, "cache_read": 150, "cache_creation": 200}, (
        "the turn after the cut is excluded"
    )
    assert found[trees.SUB_MODEL].transcript == {"input": 5, "output": 7, "cache_read": 11, "cache_creation": 13}
    assert found[trees.AUX_MODEL].transcript == {} and found[trees.AUX_MODEL].ledger["input"] == 100

    assert _one_call(_LED) == {"claude-test-1": "ledger_exact"}
    assert _one_call(dict(_LED, inputTokens=11)) == {"claude-test-1": "ledger_input_class_mismatch"}
    assert _one_call(dict(_LED, cacheCreationInputTokens=0)) == {"claude-test-1": "ledger_input_class_mismatch"}
    assert _one_call(dict(_LED, outputTokens=25)) == {"claude-test-1": "ledger_output_residual_unexplained"}
    assert _one_call(dict(_LED, outputTokens=19)) == {"claude-test-1": "ledger_output_excess"}
    assert _one_call(None) == {"claude-test-1": "ledger_model_unexplained"}, "a transcript model absent from the ledger"
    assert _classes(
        make_files([prompt("u", 1, "p", "x"), assistant("a", 2, "m", [text_block("h")]), _cost_state(**{"claude-x": _LED})])
    ) == {
        "claude-x": "ledger_model_unexplained",
        "claude-test-1": "ledger_model_unexplained",
    }
    declared = {f.cls for f in ledger.reconcile_ledger(mem_tree(files))}
    assert declared <= set(ledger.DECLARED) and not (declared & set(ledger.ALARMS))


def _auxiliary(led: dict[str, int]) -> dict[str, str]:
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [text_block("hi")], model="claude-haiku-9"),
        _cost_state(**{"claude-haiku-9": led}),
    ]
    return _classes(make_files(root))


def test_an_auxiliary_model_with_records_is_compared_as_a_bound() -> None:
    assert _auxiliary(dict(_LED, outputTokens=99)) == {"claude-haiku-9": "ledger_bounded_auxiliary"}
    assert _auxiliary(dict(_LED, inputTokens=1)) == {"claude-haiku-9": "ledger_input_class_mismatch"}
    assert _auxiliary(dict(_LED, outputTokens=1)) == {"claude-haiku-9": "ledger_output_excess"}


def test_a_compaction_before_the_cut_makes_every_model_a_bound() -> None:
    compact = hook("h1", 3, "PreCompact")
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [text_block("hi")]),
        compact,
        _cost_state(**{"claude-test-1": dict(_LED, outputTokens=60)}),
    ]
    assert _classes(make_files(root)) == {"claude-test-1": "ledger_bounded_compaction"}
    after = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [text_block("hi")]),
        _cost_state(**{"claude-test-1": _LED}),
        compact,
    ]
    assert _classes(make_files(after)) == {"claude-test-1": "ledger_exact"}, "a compaction after the cut does not count"


def _background(notice_before_cut: bool) -> dict[str, str]:
    launch = tool_use_block("tuA", "Agent", {"description": "d", "prompt": "p", "run_in_background": True})
    notice = attachment(
        "n1",
        5,
        "queued_command",
        prompt="<task-notification>\n<task-id>bg</task-id>\n<status>completed</status>\n</task-notification>",
    )
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [launch]),
        tool_result("r1", 3, "tuA", "launched", pid="p1", tur={"agentId": "bg", "isAsync": True, "status": "async_launched"}),
        *([notice] if notice_before_cut else []),
        _cost_state(
            **{
                "claude-test-1": dict(
                    _LED,
                    inputTokens=20,
                    outputTokens=40,
                    cacheReadInputTokens=60,
                    cacheCreationInputTokens=80,
                )
            }
        ),
    ]
    kid = [child(prompt("c1", 4, "p1", "task"), "bg"), child(assistant("c2", 6, "mc", [text_block("work")]), "bg")]
    return _classes(make_files(root, {"bg": kid}))


def test_a_background_child_without_a_notice_before_the_cut_is_a_bound() -> None:
    assert _background(False) == {"claude-test-1": "ledger_bounded_background"}
    assert _background(True) == {"claude-test-1": "ledger_exact"}


def test_a_second_cost_state_record_reconciles_against_its_own_prefix() -> None:
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [text_block("hi")]),
        _cost_state(**{"claude-test-1": _LED}),
        assistant("a2", 3, "m2", [text_block("again")]),
        _cost_state(
            **{
                "claude-test-1": dict(
                    _LED, inputTokens=20, outputTokens=40, cacheReadInputTokens=60, cacheCreationInputTokens=80
                )
            }
        ),
        {"type": "cost-state", "sessionId": SID},
    ]
    files = make_files(root)
    assert _classes(files, 0) == {"claude-test-1": "ledger_exact"}
    assert _classes(files, 1) == {"claude-test-1": "ledger_exact"}
    last = [f for f in ledger.reconcile_ledger(mem_tree(files)) if f.cut_index == 2]
    assert [(f.model, f.cls, f.alarm) for f in last] == [(None, "ledger_unavailable", False)]
    assert [f.cut_line for f in ledger.reconcile_ledger(mem_tree(files)) if f.cut_index == 1][0] == 4


def test_synthetic_error_and_usageless_records_never_count_and_a_nested_child_is_followed() -> None:
    tool = tool_use_block("tuA", "Agent", {"description": "d", "prompt": "p"})
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [tool]),
        assistant("s1", 2.5, "ms", [text_block("x")], model="<synthetic>"),
        dict(assistant("e1", 2.6, "me", [text_block("x")]), isApiErrorMessage=True),
        {
            **assistant("n1", 2.7, "mn", [text_block("x")]),
            "message": {"id": "mn", "model": "claude-test-1", "role": "assistant"},
        },
        {"type": "assistant", "message": "not a mapping"},
        tool_result("r1", 5, "tuA", "done", pid="p1", tur={"agentId": "k1", "isAsync": False, "status": "completed"}),
        {
            "type": "cost-state",
            "modelUsage": {
                "claude-test-1": dict(
                    _LED, inputTokens=30, outputTokens=60, cacheReadInputTokens=90, cacheCreationInputTokens=120
                )
            },
        },
    ]
    nested = tool_use_block("tuB", "Task", {"description": "d", "prompt": "p"})
    first = [
        child(assistant("k1a", 3, "mk1", [nested]), "k1"),
        child(tool_result("k1r", 4, "tuB", "ok", pid="p1", tur={"agentId": "k2", "isAsync": False}), "k1"),
    ]
    second = [child(assistant("k2a", 3.5, "mk2", [text_block("deep")], requestedModel=None), "k2")]
    files = make_files(root, {"k1": first, "k2": second})
    found = ledger.reconcile_ledger(mem_tree(files))
    assert [(f.model, f.cls) for f in found] == [("claude-test-1", "ledger_exact")]
    assert found[0].transcript["input"] == 30, "root + child + nested child; synthetic, error and usage-less records excluded"


def test_a_record_without_a_message_has_no_model_key_and_a_repeated_spawn_counts_once() -> None:
    assert ledger.model_key({"type": "assistant", "message": "text"}) is None
    tool = tool_use_block("tuA", "Agent", {"description": "d", "prompt": "p"})
    spawn = {"agentId": "k1", "isAsync": False}
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [tool]),
        tool_result("r1", 3, "tuA", "done", pid="p1", tur=spawn),
        tool_result("r2", 4, "tuA", "done again", pid="p1", tur=spawn),
        _cost_state(
            **{
                "claude-test-1": dict(
                    _LED, inputTokens=20, outputTokens=40, cacheReadInputTokens=60, cacheCreationInputTokens=80
                )
            }
        ),
    ]
    files = make_files(root, {"k1": [child(assistant("k1a", 2.5, "mk", [text_block("work")]), "k1")]})
    assert _classes(files) == {"claude-test-1": "ledger_exact"}


def test_prefix_calls_are_the_counted_responses() -> None:
    tool = tool_use_block("tuA", "Agent", {"description": "d", "prompt": "p"})
    nested = tool_use_block("tuB", "Task", {"description": "d", "prompt": "p"})
    root = [
        prompt("u1", 1, "p1", "go"),
        assistant("a1", 2, "m1", [tool]),
        assistant("s1", 2.5, "ms", [text_block("x")], model="<synthetic>"),
        tool_result("r1", 5, "tuA", "done", pid="p1", tur={"agentId": "k1", "isAsync": False, "status": "completed"}),
        assistant("a2", 6, "m2", [text_block("open last turn")]),
        {
            "type": "cost-state",
            "modelUsage": {
                "claude-test-1": dict(
                    _LED, inputTokens=40, outputTokens=80, cacheReadInputTokens=120, cacheCreationInputTokens=160
                )
            },
        },
        assistant("a3", 7, "m3", [text_block("after the cut")]),
    ]
    first = [
        child(assistant("k1a", 3, "mk1", [nested]), "k1"),
        child(tool_result("k1r", 4, "tuB", "ok", pid="p1", tur={"agentId": "k2", "isAsync": False}), "k1"),
    ]
    second = [child(assistant("k2a", 3.5, "mk2", [text_block("deep")]), "k2")]
    files = make_files(root, {"k1": first, "k2": second})
    files[f"{SID}.jsonl"] = "not json\n" + files[f"{SID}.jsonl"]
    tree = mem_tree(files)
    (finding,) = ledger.reconcile_ledger(tree)
    calls = ledger.prefix_calls(tree, finding.cut_line)
    assert sorted(calls) == ["claude-test-1"]
    assert sorted(message for _, _, message in calls["claude-test-1"]) == ["m1", "m2", "mk1", "mk2"]
    streams = {message: stream for stream, _, message in calls["claude-test-1"]}
    assert streams == {"m1": "root", "m2": "root", "mk1": "k1", "mk2": "k2"}
    summed = {name: len(calls["claude-test-1"]) * _OUT[ledger.USAGE_KEYS[name]] for name in ledger.CLASSES}
    assert summed == finding.transcript, "one counted response per listed call"
    lines = {message: line for stream, line, message in calls["claude-test-1"] if stream == "root"}
    raw = files[f"{SID}.jsonl"].split("\n")
    assert [json.loads(raw[lines[m]])["message"]["id"] for m in ("m1", "m2")] == ["m1", "m2"], "FILE line numbers"
    assert ledger.file_line(tree, finding.cut_line) == raw.index(next(r for r in raw if "cost-state" in r))
