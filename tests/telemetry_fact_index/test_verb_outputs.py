"""Declared verb outputs: every verb has them and the content declarations match what masked.py returns."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from scripts.telemetry_fact_index import project
from scripts.telemetry_fact_index.verb_outputs import DECLARED_CONTENT_VERBS, declared_outputs
from src.common.ducklake_content_read_verbs import CONTENT_READS
from src.common.ducklake_event_read_verbs import EVENT_READS
from src.lambdas.ducklake_telemetry_content_reader import masked

REPO = Path(__file__).resolve().parents[2]


def _stub_row(**extra: Any) -> dict[str, Any]:
    row = {
        "session_id": "s",
        "producer": "claude_code",
        "event_id": "e",
        "parser_version": 1,
        "event_timestamp": "2026-01-01T00:00:00Z",
        "purpose": "tool_input",
        "origin": "tool",
        "content_bytes": 2,
        "content_sha256": "0" * 64,
        "content_omitted_reason": None,
        "content": "hi",
        "content_uri": None,
        "total_rows": 1,
        "conflicted_grain_keys": [],
        "compared_versions": 1,
    }
    row.update(extra)
    return row


def test_every_registered_verb_has_declared_outputs() -> None:
    outputs = declared_outputs()
    assert set(outputs) == set(EVENT_READS) | set(CONTENT_READS)
    assert all(o.columns for o in outputs.values())


def test_content_declarations_equal_what_masked_returns() -> None:
    values = {"offset": 0, "max_bytes": 100}
    rows, _ = masked.serve_row([_stub_row()], values)
    assert set(rows[0]) == set(DECLARED_CONTENT_VERBS["transcript_row_masked"])
    envelope, _ = masked.serve_session([_stub_row()], {"offset": 0}, True, None)
    declared = set(DECLARED_CONTENT_VERBS["session_transcript_masked"])
    assert set(envelope[0]) | set(envelope[0]["rows"][0]) == declared


def test_no_content_verb_serves_content_uri() -> None:
    for verb, columns in DECLARED_CONTENT_VERBS.items():
        assert "content_uri" not in columns, verb
    rows = project(REPO)
    uri = [r for r in rows if r.address == "telemetry_transcripts.content_uri"]
    assert all(not any(s.masked for s in r.serving) for r in uri)


@pytest.fixture(scope="module")
def real_rows() -> dict[str, Any]:
    return {r.address: r for r in project(REPO)}


def test_session_transcript_masked_serves_digest_and_skill_facts(real_rows: dict[str, Any]) -> None:
    for address in (
        "telemetry_transcripts.content_sha256[purpose=tool_input]",
        "telemetry_transcripts.content#skill[purpose=tool_input]",
    ):
        verbs = {s.verb: s for s in real_rows[address].serving}
        assert verbs["session_transcript_masked"].masked is True


def test_next_offset_is_a_verb_only_row(real_rows: dict[str, Any]) -> None:
    assert real_rows["verb:session_transcript_masked.next_offset"].kind == "verb_only"
    assert "verb:session_transcript_masked.text" not in real_rows
