"""The amended contracts carry the parser_version 5 turn-close rules and their amendment_log entries."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

CONTRACTS = Path(__file__).resolve().parents[2] / "docs" / "contracts"
PLAN = "PLAN-telemetry-stop-turn-close"


def load(name: str) -> dict[str, Any]:
    return yaml.safe_load((CONTRACTS / f"{name}.yaml").read_text(encoding="utf-8"))


def logged(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in entries if PLAN in str(e.get("summary")) and e.get("date") == "2026-10-18"]


def test_r7_declares_witness_closes_and_the_frontier() -> None:
    contract = load("telemetry-event-envelope")
    notes = " ".join(str(contract["governance_notes"]).split())
    for token in (
        "PARSER_VERSION 5",
        "up to its frontier",
        "usable Stop witness",
        "API-error record closes its own turn",
        "keyed on the record that ends its window",
        "one close per Stop by construction",
        "multi_close_turns, never a declared exception",
        "prior and current emitted sets",
        "child file after its completion evidence",
    ):
        assert token in notes, token
    assert notes.index("PARSER_VERSION 5") < notes.index("R8: non-replayable")
    assert len(logged(contract["amendment_log"])) == 1


def test_observations_declares_turn_close_outcome_per_stop() -> None:
    contract = load("telemetry_observations")
    outcome = " ".join(str(contract["fields"]["outcome"]["semantics"]).split())
    assert "From parser_version 5 claude_code turn closes carry it" in outcome and "no required_when" in outcome
    assert contract["fields"]["outcome"]["dq_intent"]["required_when"] == {
        "event_kind": ["close"],
        "observation_type": ["tool_call"],
    }
    kind = " ".join(str(contract["fields"]["observation_type"]["semantics"]).split())
    for token in (
        "TURN CLOSES",
        "one close per usable Stop witness keyed on the witness record",
        "an API-error record closes its own turn",
        "last uuid and timestamp record",
        "no usable closing event lies at or after the turn's last assistant record",
        "open-to-close durations of re-emitted history shift",
        "open row lands once its prompt is on disk",
    ):
        assert token in kind, token
    assert len(logged(contract["fields"]["outcome"]["amendment_log"])) == 1
    assert len(logged(contract["fields"]["observation_type"]["amendment_log"])) == 1


def test_sessions_declares_the_first_close_open_bound() -> None:
    contract = load("telemetry_sessions")
    field = contract["fields"]["outcome"]
    notes = " ".join(str(field["governance_notes_local"]).split())
    for token in ("A TURN IS CLOSED (parser_version 5", "usable Stop witness", "first closing event", "WT-9"):
        assert token in notes, token
    assert len(logged(field["amendment_log"])) == 1
