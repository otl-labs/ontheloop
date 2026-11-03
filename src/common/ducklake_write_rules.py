"""The DuckLake writer's adapter onto the shared row-rule engine (rec-4158 plan B, Decision 210 cl.1/cl.3).

The rules reach the writer only through the generated field_semantics.yaml projection of the Class A contract; the
engine is src/row_rules/rules.py. This module is pure and I/O-free: it builds the RowRules for a table spec, shapes the
single existing-row statement write_scd2 already issues (Decision 88: still one statement) and evaluates the bound row.
A rejection raises RowRuleViolationError carrying the table, rule, column and the rule's exclude_before, never a value.
Kept out of ducklake_scd2_schema.py, which is being split (telemetry slice 2a-2 plan 1).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.common.ducklake_scd2_schema import CATALOG_ALIAS, ScdTableSpec, SchemaGateError
from src.row_rules.rules import RowRuleError, RowRules, check_row


class RowRuleViolationError(SchemaGateError):
    """A contract-declared row rule rejected the record. Terminal, never retried; carries no row value."""

    def __init__(self, table: str | None, rule: str, column: str, exclude_before: str | None = None) -> None:
        self.table = table
        self.rule = rule
        self.column = column
        self.exclude_before = exclude_before
        super().__init__(f"{table}.{column}: row rule {rule} violated")


def rules_for(spec: ScdTableSpec) -> RowRules | None:
    """The engine rules for an ops table's projected columns; None for the smoke pair (no contract, no rules)."""
    if spec.table is None:
        return None
    return RowRules.from_projection(spec.fields)


def _dated_columns(rules: RowRules | None) -> list[str]:
    if rules is None:
        return []
    columns: set[str] = set()
    for column, kinds in rules.exclude_before.items():
        columns.add(column)
        for kind in kinds:
            if kind == "not_before":
                columns.add(rules.not_before[column])
            elif kind == "at_most":
                columns.add(rules.at_most[column])
    return sorted(columns)


def existing_row_select(spec: ScdTableSpec, rules: RowRules | None, include_status: bool) -> tuple[str, tuple[str, ...]]:
    """The one existing-row SELECT and the column names it returns, in order.

    created_timestamp, status when the table has a status DAG, and -- only when the rules carry a dated leg -- every
    column a dated rule reads: the only prior values the changed-column test needs.
    """
    columns = ["created_timestamp"]
    if include_status:
        columns.append("status")
    columns.extend(c for c in _dated_columns(rules) if c not in columns)
    sql = f"SELECT {', '.join(columns)} FROM {CATALOG_ALIAS}.{spec.current_table} WHERE {spec.merge_key} = ?"
    return sql, tuple(columns)


def check_write(
    spec: ScdTableSpec,
    rules: RowRules | None,
    record: dict[str, Any],
    created_timestamp: datetime,
    last_updated_timestamp: datetime,
    ulid: str,
    prior: dict[str, Any] | None,
) -> None:
    """Evaluate the row the writer is about to MERGE: every column from the record (absent = None) plus the minted
    ulid, created_timestamp and last_updated_timestamp. *prior* is the existing row, None on an insert."""
    if rules is None:
        return
    row: dict[str, Any] = {}
    for name, _ in spec.ordered_columns:
        if name == "ulid":
            row[name] = ulid
        elif name == "created_timestamp":
            row[name] = created_timestamp
        elif name == "last_updated_timestamp":
            row[name] = last_updated_timestamp
        else:
            row[name] = record.get(name)
    try:
        check_row(str(spec.table), row, rules, created_timestamp, prior=prior)
    except RowRuleError as exc:
        since = rules.exclude_before.get(exc.column, {}).get(exc.rule)
        raise RowRuleViolationError(spec.table, exc.rule, exc.column, since.date().isoformat() if since else None) from exc
