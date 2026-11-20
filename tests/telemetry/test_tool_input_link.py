"""Pins _joins.yaml ref_links.tool_call_open_to_tool_input on the producer's golden corpus (rec-5440)."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from scripts.telemetry_verify.joins import load_telemetry_joins
from src.turn_capture import refs
from tests.fixtures import turn_capture_corpus as corpus

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "docs" / "contracts"
LINK_ID = "tool_call_open_to_tool_input"
OPEN_SUFFIX = "/tool_call_open"
_ENTRY_FIELDS = {
    "from", "to", "key", "cardinality", "grammar_owner", "producers", "holds_from_parser_version", "pinned_by",
    "description",
}  # fmt: skip


def _load(name: str) -> dict[str, Any]:
    return yaml.safe_load((CONTRACTS / name).read_text(encoding="utf-8"))


def _golden_rows() -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(Path(corpus.GOLDEN_DIR).glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "expected_rows" in data:
            out.extend((path.name, row) for row in data["expected_rows"])
    return out


def _scope(row: dict[str, Any]) -> tuple[Any, ...]:
    return (row.get("tenant_id"), row.get("project_id"), row.get("producer"), row.get("session_id"), row.get("parser_version"))


def _is_open(row: dict[str, Any]) -> bool:
    return (
        row.get("table") == "telemetry_observations"
        and row.get("observation_type") == "tool_call"
        and row.get("event_kind") == "open"
    )


def _is_input(row: dict[str, Any]) -> bool:
    return row.get("table") == "telemetry_transcripts" and row.get("purpose") == "tool_input"


class TestToolInputLink:
    def test_entry_declares_every_field(self) -> None:
        entry = _load("_joins.yaml")["ref_links"][LINK_ID]
        assert set(entry) == _ENTRY_FIELDS
        assert entry["pinned_by"] == "tests/telemetry/test_tool_input_link.py"
        assert entry["producers"] == ["claude_code"]
        assert (ROOT / entry["grammar_owner"].split(" ")[0]).is_file()

    def test_open_and_transcript_refs_share_the_prefix(self) -> None:
        for record_id, index in (("a1", 0), ("u9", 7)):
            assert refs.role_ref(record_id, index, "tool_call_open") == refs.transcript_ref(record_id, index) + OPEN_SUFFIX

    def test_every_golden_open_reaches_exactly_one_tool_input(self) -> None:
        rows = _golden_rows()
        inputs = Counter((case, _scope(r), r["external_ref"]) for case, r in rows if _is_input(r))
        opens = [(case, r) for case, r in rows if _is_open(r)]
        assert opens
        for case, op in opens:
            assert op["external_ref"].endswith(OPEN_SUFFIX), op["external_ref"]
            key = (case, _scope(op), op["external_ref"][: -len(OPEN_SUFFIX)])
            assert inputs[key] == 1, key

    def test_corpus_keeps_the_edge_cases(self) -> None:
        rows = [row for _, row in _golden_rows()]
        assert any(
            _is_open(r) and not r["external_ref"].startswith(("a1#0/", "u1#0/")) and "#0/" not in r["external_ref"]
            for r in rows
        )
        assert any(_is_input(r) and r.get("content_omitted_reason") == "withheld_by_policy" for r in rows)

    def test_joins_consumers_exclude_the_ref_link(self) -> None:
        assert all(join_id != LINK_ID for join_id, *_ in load_telemetry_joins())
        fs = (ROOT / "config" / "lambda" / "ducklake" / "field_semantics.yaml").read_text(encoding="utf-8")
        assert LINK_ID not in fs

    def test_pointers_resolve(self) -> None:
        ref_links = _load("_joins.yaml")["ref_links"]
        transcripts = (CONTRACTS / "telemetry_transcripts.yaml").read_text(encoding="utf-8")
        assert f"ref_links.{LINK_ID}" in transcripts
        envelope = _load("telemetry-event-envelope.yaml")
        text = json.dumps(envelope)
        assert "only through _joins.yaml ref_links" in text
        for path in CONTRACTS.glob("*.yaml"):
            for link_id in re.findall(r"ref_links\.([a-z_]+)", path.read_text(encoding="utf-8")):
                assert link_id in ref_links, f"{path.name} names unknown ref link {link_id}"
