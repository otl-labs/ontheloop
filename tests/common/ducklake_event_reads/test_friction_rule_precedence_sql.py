"""The reader SQL's friction rule precedence and literal prefix match over synthetic rules (PLAN-friction-rules-v2)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

duckdb = pytest.importorskip("duckdb", reason="env: duckdb is not installed in the fast tier")

from src.common import ducklake_event_read_sql as sql  # noqa: E402
from src.common import ducklake_event_reads as ev  # noqa: E402
from src.common.ducklake_event_read_verbs import FRICTION_RULES  # noqa: E402
from src.telemetry.identity import derive_entity_key  # noqa: E402
from tests.fixtures import telemetry_read_db as db  # noqa: E402

_TENANT = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
_PROJECT = "01BRZ3NDEKTSV4RRFFQ69G5FBW"
_AT = datetime.fromisoformat("2026-09-25T10:00:00+00:00")
_RULES = (
    ("a1", "process_event", None, None, "error", "generic_label", "exception"),
    ("b1", "process_event", None, "status:", "error", "prefix_label", "exception"),
    ("c1", "process_event", "status:x.failed", None, "error", "exact_label", "exception"),
    ("d1", "process_event", None, "gate:validate_", "error", "gate_prefix_label", "exception"),
)


def _labels(name: str) -> list[str]:
    session = derive_entity_key("telemetry_sessions:session_id", _TENANT, _PROJECT, name, _AT)
    con = db.open_memory_catalog()
    row: dict[str, Any] = db.base_row(
        session_id=session,
        session_started_at=_AT,
        event_id="ev-e1",
        event_kind="point",
        event_timestamp=_AT.replace(second=1),
        source_ordinal=1,
        observation_id="e1",
        observation_type="process_event",
        name=name,
        severity="error",
        outcome=None,
        parent_observation_id="A",
    )
    db.insert_rows(con, "telemetry_observations", [row])
    entry = ev.EventRead(
        verb="friction_facts",
        tables=(),
        sql=sql.FRICTION_FACTS_SQL,
        params=("session_id",),
        binding="session",
        data=dict(FRICTION_RULES, rules=_RULES),
    )
    return [r["label"] for r in ev.execute_entry(con, entry, {"session_id": session})]


def test_exact_rule_beats_prefix_rule() -> None:
    assert _labels("status:x.failed") == ["exact_label"]


def test_prefix_rule_beats_generic_rule() -> None:
    assert _labels("status:y.failed") == ["prefix_label"]


def test_prefix_is_literal_not_a_like_pattern() -> None:
    assert _labels("gate:validateXy") == ["generic_label"]
    assert _labels("gate:validate_x") == ["gate_prefix_label"]


def test_non_matching_prefix_reads_generic() -> None:
    assert _labels("other:thing") == ["generic_label"]
