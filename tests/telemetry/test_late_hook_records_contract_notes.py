"""The amended contracts carry the parser_version 7 late and twin hook record rules and their amendment_log entries."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

CONTRACTS = Path(__file__).resolve().parents[2] / "docs" / "contracts"
PLAN = "PLAN-telemetry-late-hook-records"


def load(name: str) -> dict[str, Any]:
    return yaml.safe_load((CONTRACTS / f"{name}.yaml").read_text(encoding="utf-8"))


def flat(value: Any) -> str:
    return " ".join(str(value).split())


def logged(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in entries if PLAN in str(e.get("summary")) and e.get("date") == "2026-10-26"]


def test_sessions_declares_the_late_pin_and_late_resume_rules() -> None:
    contract = load("telemetry_sessions")
    notes = flat(contract["governance_notes"])
    for token in ("parser_version 7", "HG-1..HG-4", "pin_after_first_prompt", "rec-4641", "closes no turn", "SC-1 close"):
        assert token in notes, token
    assert len(logged(contract["amendment_log"])) == 1


def test_observations_declares_one_row_per_hook_run() -> None:
    contract = load("telemetry_observations")
    parent = contract["fields"]["parent_observation_id"]
    note = flat(parent["governance_notes_local"])
    for token in ("ONE row per hook RUN", "rule HG-1", "first copy in file order", "startup, clear or resume", "rec-4439"):
        assert token in note, token
    assert len(logged(parent["amendment_log"])) == 1


def test_envelope_declares_parser_version_7_before_r8() -> None:
    contract = load("telemetry-event-envelope")
    notes = flat(contract["governance_notes"])
    for token in ("PARSER_VERSION 7", "HG-1", "HG-2", "HG-3", "HG-4", "effective boundary", "every prefix agrees (R7)"):
        assert token in notes, token
    assert notes.index("PARSER_VERSION 5") < notes.index("PARSER_VERSION 7") < notes.index("R8: non-replayable")
    assert len(logged(contract["amendment_log"])) == 1


def test_ports_sa02_names_late_and_twin_records() -> None:
    contract = load("ports")
    sa02 = next(a for a in contract["structural_assumptions"] if a["id"] == "SA-02")
    text = flat(sa02["assumption"])
    for token in ("late-loaded plugin's SessionStart hook records", "heldContent", "shares one toolUseID", "queued prompt"):
        assert token in text, token
    assert len(logged(contract["amendment_log"])) == 1
