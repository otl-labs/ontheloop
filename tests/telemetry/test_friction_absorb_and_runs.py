"""rec-5097 acceptance (PLAN-friction-rules-v2): absorb and repeat-run rules of the friction oracle (fast tier)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.telemetry_verify import derive

_STAMP = "2026-09-25T10:00:00+00:00"
_GATE = "gate:validate_scope_boundary"
_SESSIONS_CONTRACT = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "telemetry_sessions.yaml"


def _row(
    obs_id: str, obs_type: str, kind: str, name: str | None, severity: str | None, outcome: str | None, parent: str, t: int
) -> dict[str, Any]:
    return {
        "producer": "claude_code",
        "parser_version": 1,
        "created_timestamp": _STAMP,
        "session_id": "s",
        "session_started_at": _STAMP,
        "event_id": f"ev-{obs_id}-{kind}",
        "event_kind": kind,
        "event_timestamp": datetime(2026, 9, 25, 10, 0, t, tzinfo=timezone.utc),
        "source_ordinal": t,
        "observation_id": obs_id,
        "observation_type": obs_type,
        "name": name,
        "severity": severity,
        "outcome": outcome,
        "parent_observation_id": parent,
    }


def _rollup(rows: list[dict[str, Any]]) -> dict[str, Any]:
    (row,) = derive.session_friction_rollup({"telemetry_observations": rows}, "s")
    return row


def _labels(rows: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return sorted((ref, label) for ref, label, _ in derive.friction_facts(rows))


def test_friction_gate_under_error_close_counts_once() -> None:
    rows = [
        _row("g1", "process_event", "point", _GATE, "error", None, "t1", 1),
        _row("t1", "tool_call", "close", "Bash", None, "error", "A", 2),
    ]
    assert _labels(rows) == [("g1", "scope_creep")]
    assert _rollup(rows)["absorbed_count"] == 1
    assert _rollup(rows)["exception_total"] == 1


def test_status_failure_absorbs_its_error_close() -> None:
    rows = [
        _row("s1", "process_event", "point", "status:ops_portal.file_rec.failed", "error", None, "t1", 1),
        _row("t1", "tool_call", "close", "Bash", None, "error", "A", 2),
    ]
    assert _labels(rows) == [("s1", "reported_failure")]
    assert _rollup(rows)["absorbed_count"] == 1


def test_null_name_closes_form_no_run() -> None:
    rows = [_row(f"t{i}", "tool_call", "close", None, None, "error", "A", i) for i in (1, 2, 3)]
    assert {label for _, label in _labels(rows)} == {"tool_error"}
    assert _rollup(rows)["rework_total"] == 0


def test_absorbed_closes_still_form_a_run() -> None:
    rows = []
    for i in (1, 2, 3):
        rows.append(_row(f"g{i}", "process_event", "point", _GATE, "error", None, f"t{i}", 2 * i - 1))
        rows.append(_row(f"t{i}", "tool_call", "close", "Bash", None, "error", "A", 2 * i))
    roll = _rollup(rows)
    assert roll["rework_total"] == 1
    assert roll["absorbed_count"] == 3
    assert roll["exception_total"] == 3


def test_blocked_absorb_is_counted() -> None:
    for hook in ("hook:never_on_main", "hook:edit_scope_guard"):
        rows = [
            _row("h1", "process_event", "point", hook, "error", None, "t1", 1),
            _row("t1", "tool_call", "close", "Edit", None, "blocked", "A", 2),
        ]
        assert _rollup(rows)["absorbed_count"] == 1


def test_absorbed_count_counts_closes_not_children() -> None:
    rows = [
        _row("g1", "process_event", "point", _GATE, "error", None, "t1", 1),
        _row("g2", "process_event", "point", _GATE, "error", None, "t1", 2),
        _row("t1", "tool_call", "close", "Bash", None, "error", "A", 3),
    ]
    roll = _rollup(rows)
    assert roll["absorbed_count"] == 1
    assert roll["exception_total"] == 2


def test_contract_declares_v2_friction_rules() -> None:
    text = " ".join(_SESSIONS_CONTRACT.read_text(encoding="utf-8").split())
    for phrase in (
        "absorbed_count",
        "notice-class",
        "a signature prefix ranks between the two",
        "a close with no name (an orphan close) joins no run",
        "PLAN-friction-rules-v2",
    ):
        assert phrase in text, phrase
