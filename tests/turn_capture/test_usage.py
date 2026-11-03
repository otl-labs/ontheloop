"""Rules US-1..US-4: usage and request columns of a model_call, the spawn-result correction, and their counters."""

from __future__ import annotations

from collections import Counter
from typing import Any

from src.turn_capture.streams import ROOT
from src.turn_capture.usage import EFFORT_PATTERN, usage_columns
from tests.fixtures.turn_capture_corpus import assistant, parse_records, prompt, text_block

SPLIT = {"ephemeral_1h_input_tokens": 15, "ephemeral_5m_input_tokens": 25}
FULL = {
    "input_tokens": 10,
    "output_tokens": 20,
    "cache_read_input_tokens": 30,
    "cache_creation_input_tokens": 40,
    "cache_creation": SPLIT,
}


def columns(usage: dict[str, Any] | None = None, spawn: dict[str, Any] | None = None, **extra: Any):
    root = [prompt("u1", 1, "p1", "go"), assistant("a1", 2, "m1", [text_block("x")], usage=usage, **extra)]
    tree = parse_records([*root, prompt("u2", 3, "p2", "next")], None, False)
    diag: Counter[str] = Counter()
    resp = tree.streams[ROOT].turns[0].responses[0]
    return usage_columns(resp, spawn, diag), diag


def test_final_record_carries_the_classes_the_split_and_the_request_fields() -> None:
    cols, diag = columns(FULL, requestedModel="claude-x[1m]", effort="xhigh")
    assert cols == {
        "tokens_input": 10,
        "tokens_output": 20,
        "tokens_cache_read": 30,
        "tokens_cache_creation": 40,
        "reasoning_tokens": None,
        "cache_creation_1h_tokens": 15,
        "cache_creation_5m_tokens": 25,
        "requested_model": "claude-x[1m]",
        "reasoning_effort": "xhigh",
        "usage_final": True,
        "usage_source": "record",
    }
    assert not diag
    cols, _ = columns({**FULL, "output_tokens_details": {"thinking_tokens": 7}})
    assert cols["reasoning_tokens"] == 7 and cols["requested_model"] is None and cols["reasoning_effort"] is None


def test_split_above_its_total_and_bad_efforts_are_null_and_counted() -> None:
    cols, diag = columns({**FULL, "cache_creation": {"ephemeral_1h_input_tokens": 41, "ephemeral_5m_input_tokens": 5}})
    assert (cols["cache_creation_1h_tokens"], cols["cache_creation_5m_tokens"]) == (None, 5)
    assert diag["cache_split_exceeds_total"] == 1
    cols, diag = columns({**FULL, "cache_creation": {"ephemeral_1h_input_tokens": "9", "ephemeral_5m_input_tokens": True}})
    assert (cols["cache_creation_1h_tokens"], cols["cache_creation_5m_tokens"]) == (None, None) and not diag
    cols, _ = columns({k: v for k, v in FULL.items() if k != "cache_creation"})
    assert cols["cache_creation_1h_tokens"] is None
    for bad in ("Xhigh", "", 5, "a" * 33):
        cols, diag = columns(FULL, effort=bad)
        assert cols["reasoning_effort"] is None and diag["effort_unparsed"] == 1, bad
    assert EFFORT_PATTERN == r"^[a-z][a-z0-9_-]{0,31}$"
    cols, _ = columns(FULL, requestedModel="")
    assert cols["requested_model"] is None


def test_a_record_without_a_stop_reason_is_not_final_and_counted() -> None:
    cols, diag = columns(FULL, stop_reason=None)
    assert (cols["usage_final"], cols["usage_source"]) == (False, "record") and diag["usage_not_final:root"] == 1
    tree = parse_records(
        [prompt("u1", 1, "p1", "go"), assistant("a1", 2, "m1", [text_block("x")], stop_reason=None)], None, True
    )
    diag = Counter()
    usage_columns(tree.streams[ROOT].turns[0].responses[0], None, diag, subagent=True)
    assert diag["usage_not_final:subagent"] == 1


def test_spawn_result_usage_replaces_a_non_final_record_only_on_an_exact_input_match() -> None:
    spawn = {**FULL, "output_tokens": 99, "output_tokens_details": {"thinking_tokens": 3}}
    cols, diag = columns(FULL, spawn, stop_reason=None)
    assert (cols["tokens_output"], cols["reasoning_tokens"], cols["usage_source"], cols["usage_final"]) == (
        99,
        3,
        "spawn_result",
        True,
    )
    assert diag == Counter({"spawn_result_corrected": 1})
    cols, diag = columns(FULL, {**spawn, "input_tokens": 11}, stop_reason=None)
    assert (cols["tokens_output"], cols["usage_source"], cols["usage_final"]) == (20, "record", False)
    assert diag["spawn_result_mismatch"] == 1 and diag["usage_not_final:root"] == 1
    cols, diag = columns(FULL, {**spawn, "input_tokens": "10"}, stop_reason=None)
    assert cols["usage_source"] == "record" and diag["spawn_result_mismatch"] == 1
    cols, diag = columns(FULL, spawn, stop_reason=None, isAbortedMidStream=True)
    assert cols["usage_source"] == "record" and not diag["spawn_result_corrected"] and not diag["spawn_result_mismatch"]
    cols, diag = columns(FULL, spawn)
    assert cols["tokens_output"] == 20 and cols["usage_final"] is True and not diag
    over = {**spawn, "cache_creation": {"ephemeral_1h_input_tokens": 41, "ephemeral_5m_input_tokens": 1}}
    cols, diag = columns(FULL, over, stop_reason=None)
    assert cols["cache_creation_1h_tokens"] is None and cols["cache_creation_5m_tokens"] == 1
    assert diag["cache_split_exceeds_total"] == 1


def test_absent_and_non_integer_usage_are_counted_as_before() -> None:
    tree = parse_records([prompt("u1", 1, "p1", "go"), assistant("a1", 2, "m1", [text_block("x")])], None, True)
    resp = tree.streams[ROOT].turns[0].responses[0]
    resp.owner.data["message"]["usage"] = None
    diag: Counter[str] = Counter()
    cols = usage_columns(resp, None, diag)
    assert diag["absent_usage"] == 1 and diag["absent_usage_key"] == 4 and cols["tokens_input"] == 0
    resp.owner.data["message"]["usage"] = {"input_tokens": "9", "output_tokens": True}
    diag = Counter()
    usage_columns(resp, None, diag)
    assert diag["non_int_usage"] == 2
