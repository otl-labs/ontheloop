"""The parser_version 11 contract text (PLAN-persona-run-identity-producer, rec-4433): envelope line and ports SA-02 clause."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

CONTRACTS = Path(__file__).resolve().parents[2] / "docs" / "contracts"
PLAN = "PLAN-persona-run-identity-producer"


def _load(name: str) -> dict[str, Any]:
    return yaml.safe_load((CONTRACTS / name).read_text(encoding="utf-8"))


def _effective_lines(name: str) -> int:
    lines = (CONTRACTS / name).read_text(encoding="utf-8").splitlines()
    return sum(1 for line in lines if line.strip() and not line.lstrip().startswith("#"))


def test_envelope_declares_parser_version_11_after_10_and_before_dq_intents() -> None:
    text = (CONTRACTS / "telemetry-event-envelope.yaml").read_text(encoding="utf-8")
    v10, v11, dq = (text.index(token) for token in ("  PARSER_VERSION 10 (", "  PARSER_VERSION 11 (", "DQ intents"))
    assert v10 < v11 < dq
    between = text[v11:dq]
    for token in (
        PLAN,
        "rec-4433",
        "PI-1",
        "PI-2",
        "PI-3",
        "PI-4",
        "PERSONA_SKILLS",
        "Base directory for this skill: ",
        "ARGUMENTS: ",
    ):
        assert token in between, token
    log = _load("telemetry-event-envelope.yaml")["amendment_log"]
    entry = next(e for e in log if PLAN in e["summary"] and "parser_version 11" in e["summary"])
    assert entry["semantic_break"] is True


def test_ports_sa02_names_the_persona_skills_mirror_and_logs_the_amendment() -> None:
    ports = _load("ports.yaml")
    text = (CONTRACTS / "ports.yaml").read_text(encoding="utf-8")
    assert "PERSONA_SKILLS in src/turn_capture/persona_identity.py mirrors the verdict personas rows" in " ".join(text.split())
    assert any(PLAN in e["summary"] for e in ports["amendment_log"])


def test_telemetry_agents_notes_say_parser_version_11_and_name_the_monitor() -> None:
    contract = _load("telemetry_agents.yaml")
    note = " ".join(contract["fields"]["agent_type"]["governance_notes_local"].split())
    assert "parser_version 11" in note and "parser_version 10" not in note
    assert "rec-5227" in contract["governance_notes"]


def test_bound_contracts_stay_within_the_size_limit() -> None:
    assert _effective_lines("telemetry-event-envelope.yaml") <= 500
    assert _effective_lines("ports.yaml") <= 500
