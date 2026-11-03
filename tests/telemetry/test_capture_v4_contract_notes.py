"""PLAN-telemetry-capture-producer-fixes: the amended contract notes carry their rule tokens and amendment_log entries."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

CONTRACTS = Path(__file__).resolve().parents[2] / "docs" / "contracts"
PLAN = "PLAN-telemetry-capture-producer-fixes"


def _contract(name: str) -> dict[str, Any]:
    return yaml.safe_load((CONTRACTS / name).read_text(encoding="utf-8"))


def _flat(text: str) -> str:
    return " ".join(text.split())


def _logged(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in entries if PLAN in str(e.get("summary"))]


def test_r7_declares_usage_final_and_resume_segment_closes() -> None:
    envelope = _contract("telemetry-event-envelope.yaml")
    r7 = _flat(envelope["governance_notes"])
    for token in (
        "declares in usage_final whether that usage is final",
        "usage_source",
        "a non-final row stays non-final until a parser_version bump re-parses it",
        "one session close per root resume boundary",
        "(SC-1)",
        "background work does not survive a resume",
        "notice_after_boundary",
        "prior_content_divergence",
        "the checkout reaches the tree only through the pin hook's recorded line",
    ):
        assert token in r7, token
    assert "only once its source record carries final usage" not in r7
    entries = _logged(envelope["amendment_log"])
    assert len(entries) == 1 and entries[0]["semantic_break"] is True and entries[0]["date"] == "2026-10-17"

    sessions = _contract("telemetry_sessions.yaml")
    notes = _flat(sessions["governance_notes"])
    for token in (
        "SC-1",
        "SC-2",
        "SC-3",
        "PV-1..PV-4",
        "rec-4407",
        "reads running then abandoned",
        "never from a live git call",
    ):
        assert token in notes, token
    for field in ("event_kind", "event_timestamp", "source_ordinal", "external_ref", "outcome"):
        assert "parser_version 4" in _flat(sessions["fields"][field]["governance_notes_local"]) or "SC-" in _flat(
            sessions["fields"][field]["governance_notes_local"]
        ), field
    assert len(_logged(sessions["amendment_log"])) == 1


def test_agents_background_outcome_follows_the_notice() -> None:
    agents = _contract("telemetry_agents.yaml")
    note = _flat(agents["fields"]["outcome"]["governance_notes_local"])
    for token in (
        "completed -> success",
        "timeout -> timeout",
        "notice_status:<token>",
        "notice_status:malformed",
        "notice_status:absent",
        "one per completion notice",
        "reader takes the latest",
    ):
        assert token in note, token
    assert agents["fields"]["outcome"]["dq_intent"]["accepted_values"]["values"] == [
        "success",
        "failed",
        "timeout",
        "throttled",
    ]
    assert len(_logged(agents["amendment_log"])) == 1
    assert any(PLAN in str(e.get("summary")) for e in agents["fields"]["outcome"]["amendment_log"])
    ports = _contract("ports.yaml")
    sa02 = next(a for a in ports["structural_assumptions"] if a["id"] == "SA-02")
    assert "<task-notification> <status> token" in _flat(sa02["assumption"]) and "default branch main" in sa02["assumption"]
    assert any(PLAN in str(e.get("summary")) for e in ports["amendment_log"])
