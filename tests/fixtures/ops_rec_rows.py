"""Shared existing-row factory and writer stand-in for the portal row-rule gate tests (rec-4158).

reader_rec_row builds a complete, rule-clean ops_recommendations row in the shape the rec_by_id reader verb returns
(string created_timestamp, coerced arrays), so update_rec's gate has every required input. writer_stand_in replaces
`_ducklake_write`: it runs the writer's own schema_gate and check_write with the stored row as prior and maps a refusal
to ValueError naming rule and column, as writer_transport maps the writer's 422. Lives in tests/fixtures/ (Decision 131).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from scripts.ops_portal.writer_transport import _project_ops_record
from src.common.ducklake_scd2_schema import SchemaGateError, resolve_table_spec, schema_gate
from src.common.ducklake_write_rules import RowRuleViolationError, check_write, rules_for

NEWER_THAN_EVERY_RULE_DATE = "2026-10-01T00:00:00+00:00"
OLDER_THAN_EVERY_RULE_DATE = "2026-01-01T00:00:00+00:00"


def reader_rec_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": "rec-4001",
        "title": "Existing recommendation under test for the update_rec content boundary",
        "file": "scripts/ops_data_portal.py",
        "context": "A sufficiently long context string so the write-time content validators are satisfied here.",
        "acceptance": "grep -q ops_data_portal scripts/ops_data_portal.py && grep -q update_rec scripts/ops_data_portal.py",
        "effort": "XS",
        "priority": "Low",
        "source": "planning",
        "risk": "low",
        "status": "open",
        "automatable": True,
        "dependencies": None,
        "tags": None,
        "resolution": None,
        "created_timestamp": NEWER_THAN_EVERY_RULE_DATE,
        "date": "2026-10-01",
    }
    row.update(overrides)
    return row


def writer_stand_in(stored: list[dict[str, Any]] | None = None) -> Callable[..., dict[str, Any]]:
    """A `_ducklake_write` stand-in; `.writes` records each accepted (table, action, record)."""
    rows = {str(r["id"]): r for r in (stored or [])}
    writes: list[tuple[str, str, dict[str, Any]]] = []

    def _write(table: str, record: dict, *, action: str, profile: str | None = None, **_: Any) -> dict[str, Any]:
        spec = resolve_table_spec(table)
        projected = _project_ops_record(table, record)
        key = projected.get(spec.merge_key)
        prior = rows.get(str(key)) if action in ("update_ops", "write_ops") else None
        if action == "file_ops":
            projected = {**projected, spec.merge_key: f"{spec.entity_id_prefix}0"}
        try:
            schema_gate(projected, table=table)
            created = datetime.fromisoformat(prior["created_timestamp"]) if prior else datetime.now(timezone.utc)
            check_write(spec, rules_for(spec), projected, created, datetime.now(timezone.utc), "0" * 26, prior)
        except RowRuleViolationError as exc:
            raise ValueError(f"{exc.table}.{exc.column}: row rule {exc.rule} violated") from exc
        except SchemaGateError as exc:
            raise ValueError(str(exc)) from exc
        writes.append((table, action, record))
        return {"key": key or "rec-1", "ok": True}

    _write.writes = writes  # type: ignore[attr-defined]
    return _write
