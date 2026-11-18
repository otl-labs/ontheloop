"""The litellm producer's envelope text (PLAN-litellm-producer-envelope, rec-4028 plan 3b-1): pin rule and LL-1..LL-5."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ENVELOPE = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "telemetry-event-envelope.yaml"
PLAN = "PLAN-litellm-producer-envelope"


def _text() -> str:
    return ENVELOPE.read_text(encoding="utf-8")


def _load() -> dict[str, Any]:
    return yaml.safe_load(_text())


def _flat(value: str) -> str:
    return " ".join(value.split())


def test_envelope_declares_litellm_line_before_dq_intents() -> None:
    text = _text()
    v11, line, dq = (text.index(token) for token in ("  PARSER_VERSION 11 (", "  LITELLM PARSER_VERSION 1 (", "DQ intents"))
    assert v11 < line < dq
    between = _flat(text[line:dq])
    for token in (
        PLAN,
        "rec-4028",
        "LL-1",
        "LL-2",
        "LL-3",
        "LL-4",
        "LL-5",
        "producer claude_code",
        "harness_key_missing",
        "outcome cancelled",
        "transport_exception",
        "no host-run sidecar",
        "litellm:<host_run_ref>",
        "<session ref>/call/<turn>/<n>",
        "'<session ref>/end#0/close'",
        "'<session ref>/end#0/run_close'",
        "outside R10",
        "rec-5338",
        "rec-5352",
    ):
        assert token in between, token


def test_session_started_at_declares_the_sole_producer_pin() -> None:
    field = _load()["fields"]["session_started_at"]
    semantics = _flat(field["semantics"])
    assert "sole producer of its tree" in semantics
    assert "started_at_ms" in semantics and "DEFERS" in semantics and "no skew allowance" in semantics
    entry = next(e for e in field["amendment_log"] if PLAN in str(e.get("summary", "")))
    assert entry["change_class"] == "governance_note_add" and entry["semantic_break"] is True


def test_r10_says_a_first_grammar_is_not_a_change() -> None:
    description = _flat(_load()["fields"]["parser_version"]["description"])
    assert "A new producer's first session entity_ref grammar is not such a change" in description
    assert "bumps EVERY producer and requires a Decision (R10)" in description
    entry = next(e for e in _load()["fields"]["parser_version"]["amendment_log"] if PLAN in str(e.get("summary", "")))
    assert entry["change_class"] == "governance_note_add" and entry["semantic_break"] is True


def test_litellm_line_states_the_decision_228_deviation() -> None:
    text = _text()
    between = _flat(text[text.index("  LITELLM PARSER_VERSION 1 (") : text.index("DQ intents")])
    assert "It emits no turn rows" in between and "Decision 228 cl.1" in between and "Decision 228 cl.3" in between


def test_top_level_amendment_entry_is_a_semantic_break() -> None:
    entry = next(e for e in _load()["amendment_log"] if PLAN in e["summary"])
    assert "LITELLM PARSER_VERSION 1" in entry["summary"] and entry["semantic_break"] is True


def test_envelope_stays_within_the_size_limit() -> None:
    lines = _text().splitlines()
    assert sum(1 for line in lines if line.strip() and not line.lstrip().startswith("#")) <= 500
