"""PLAN-telemetry-read-side-session-end: the sessions contract carries the turn rule (SE-1..SE-3) and the Decision citation."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

_REPO = Path(__file__).resolve().parents[2]
PLAN = "PLAN-telemetry-read-side-session-end"


def _sessions() -> dict[str, Any]:
    return yaml.safe_load((_REPO / "docs/contracts/telemetry_sessions.yaml").read_text(encoding="utf-8"))


def _flat(text: str) -> str:
    return " ".join(str(text).split())


def _resolved_number(contract: dict[str, Any]) -> int:
    semantics = _flat(contract["fields"]["state"]["semantics"])
    match = re.search(rf"Decision (\d+) \({PLAN}\)", semantics)
    assert match is not None, "state.semantics must cite 'Decision <N> (PLAN-telemetry-read-side-session-end)'"
    return int(match.group(1))


def test_sessions_state_declares_the_turn_branches() -> None:
    contract = _sessions()
    fields = contract["fields"]
    n = _resolved_number(contract)
    state = fields["state"]
    semantics, formula = _flat(state["semantics"]), _flat(state["derivation"]["formula"])
    for token in (
        "no turn opened after it",
        "stopped mid-turn",
        "blocked -> failed",
        "interrupted -> cancelled",
        "not abandoned",
    ):
        assert token in semantics, token
    for token in ("tip is a close", "latest root turn has a close with an outcome", "idle ? abandoned : running"):
        assert token in formula, token
    for field in ("observation_type", "event_kind", "observation_id", "outcome", "source_ordinal", "event_id"):
        assert f"telemetry_observations.{field}" in state["derivation"]["inputs"], field
    assert "SE-2" in _flat(fields["outcome"]["semantics"]) and "no turn opened after it" in _flat(
        fields["outcome"]["semantics"]
    )

    for text in (
        fields["event_kind"]["dq_intent_local"]["accepted_values"]["note"],
        contract["governance_notes"],
        state["semantics"],
        state["derivation"]["formula"],
    ):
        assert "running unless" not in text

    governance = _flat(contract["governance_notes"])
    assert f"since Decision {n} it instead reads from its turns" in governance
    assert "reads running then abandoned" in governance and "rec-4407 closed" in governance
    assert f"Decision {n}" in _flat(fields["event_kind"]["dq_intent_local"]["accepted_values"]["note"])

    duration = fields["duration_seconds"]
    for text in (_flat(duration["semantics"]), _flat(duration["derivation"]["formula"])):
        assert (
            "later of the latest producer close and the latest close of any root turn" in text
            or "the later of the latest producer close" in text
        )
    for field in ("observation_type", "event_kind", "event_timestamp", "source_ordinal", "event_id"):
        assert f"telemetry_observations.{field}" in duration["derivation"]["inputs"], field

    logged = [e for e in contract["amendment_log"] if PLAN in str(e.get("summary"))]
    assert len(logged) >= 1 and all(f"Decision {n}" in e["summary"] for e in logged)
    assert any(PLAN in str(e.get("summary")) for e in state["amendment_log"])
