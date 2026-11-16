"""The DuckLake writer's adapter onto the shared row-rule engine (rec-4158 plan B, Decision 210 cl.1/cl.3).

The rules reach the writer only through the generated field_semantics.yaml projection of the Class A contract; the
engine is src/row_rules/rules.py. This module is pure and I/O-free: it builds the RowRules for a table spec, shapes the
single existing-row statement write_scd2 already issues (Decision 88: still one statement) and evaluates the bound row.
A rejection raises RowRuleViolationError carrying the table, rule, column and the rule's exclude_before, never a value.
Kept out of ducklake_scd2_schema.py, which is being split (telemetry slice 2a-2 plan 1).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from src.common.ducklake_scd2_schema import CATALOG_ALIAS, ScdTableSpec, SchemaGateError
from src.row_rules.rules import RowRuleError, RowRules, RuleProjectionError, check_row


class RowRuleViolationError(SchemaGateError):
    """A contract-declared row rule rejected the record. Terminal, never retried; carries no row value."""

    def __init__(self, table: str | None, rule: str, column: str, exclude_before: str | None = None) -> None:
        self.table = table
        self.rule = rule
        self.column = column
        self.exclude_before = exclude_before
        super().__init__(f"{table}.{column}: row rule {rule} violated")


WRITER_REFERENCE_KINDS = frozenset({"array_element_reference"})


def reference_columns(fields: Mapping[str, Mapping[str, Any]]) -> frozenset[str]:
    """The columns whose elements must name an existing merge key of the same table (Decision 268), from the projection.

    A projected array_element_reference must be true and must not share a column with its write_time_exemptions entry.
    The pure engine never evaluates it: the writer does, inside its own transaction (check_references).
    """
    columns: set[str] = set()
    for name, spec in fields.items():
        for kind in sorted(WRITER_REFERENCE_KINDS & set(spec)):
            if spec[kind] is not True:
                raise RuleProjectionError(f"{name}: {kind} must be true")
            if kind in (spec.get("write_time_exemptions") or {}):
                raise RuleProjectionError(f"{name}: {kind} is exempt and must not also ship as a rule")
            columns.add(name)
    return frozenset(columns)


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
    column a dated rule reads, plus every reference column: the only prior values the changed-column tests need.
    """
    columns = ["created_timestamp"]
    if include_status:
        columns.append("status")
    wanted = {*_dated_columns(rules), *(reference_columns(spec.fields) if rules is not None else ())}
    columns.extend(c for c in sorted(wanted) if c not in columns)
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


def check_references(con: Any, spec: ScdTableSpec, record: dict[str, Any], prior: dict[str, Any] | None) -> None:
    """Reject an element of a reference column that names no existing merge key of this table (Decision 268).

    Runs on the caller's open transaction: one SELECT of the merge key from the current table, one placeholder per distinct
    element. It binds on an insert (prior None) or when the write changes the column, so an unchanged stored value is never
    re-read. The error names the table, rule and column and carries no value.
    """
    for column in sorted(reference_columns(spec.fields)):
        value = record.get(column)
        if not value or (prior is not None and value == prior.get(column)):
            continue
        wanted = sorted({str(e) for e in value})
        marks = ", ".join("?" for _ in wanted)
        sql = f"SELECT {spec.merge_key} FROM {CATALOG_ALIAS}.{spec.current_table} WHERE {spec.merge_key} IN ({marks})"
        found = {row[0] for row in con.execute(sql, wanted).fetchall()}
        if not set(wanted) <= found:
            raise RowRuleViolationError(spec.table, "array_element_reference", column)
