"""append_events' pre-SQL row gate (Decision 199): unknown/derived column rejection, a strict
per-sql_type Python type gate, NOT NULL checks, and intra-batch duplicate collapse/conflict.

Split out from append.py to keep it well under the 500-SLOC budget (Decision 128). Pure;
stdlib only (Decision 184 cl.2 plane-neutral rule) -- duckdb is never imported here.

DuckDB's own casts coerce silently (1.5 -> 2, True -> 1, a naive datetime assumed UTC), which is
exactly the failure mode this gate exists to close: every check below is a REJECTION, never a
lossy coercion.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any


class AppendError(ValueError):
    """Raised by every rejection this module (and src/telemetry/append.py) performs."""


GATE_REASONS = (
    "unknown_column",
    "derived_column",
    "type",
    "range",
    "not_null",
    "required_ref",
    "invalid_ref",
    "time_range",
    "grain_key_null",
)


class GateError(AppendError):
    """A row is rejected before any SQL: unknown or derived column, type, NOT NULL or a contract row rule.

    table, row (0-based index as sent), column and reason (one of GATE_REASONS) are value-free locators a writer can
    return; the message may name a caller's undeclared key for in-process callers, the attributes never do.
    """

    def __init__(
        self,
        message: str,
        *,
        table: str | None = None,
        row: int | None = None,
        column: str | None = None,
        reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.table = table
        self.row = row
        self.column = column
        self.reason = reason


class RowRuleGateError(GateError):
    """A contract row rule broke (built from a RowRuleError): carries the rule name beside the locators."""

    def __init__(
        self, message: str, *, rule: str, table: str | None = None, row: int | None = None, column: str | None = None
    ) -> None:
        super().__init__(message, table=table, row=row, column=column, reason="row_rule")
        self.rule = rule


class GrainConflictError(AppendError):
    """Two different rows under one grain key: within the batch or against a stored row."""

    def __init__(self, message: str, *, table: str | None = None, event_id: str | None = None, row: int | None = None) -> None:
        super().__init__(message)
        self.table = table
        self.event_id = event_id
        self.row = row


def reject_unknown_or_derived_columns(
    row: dict[str, Any],
    known_columns: frozenset[str],
    derived_columns: frozenset[str],
) -> None:
    """Raise if *row* carries a key outside known_columns, or a caller-supplied derived one.

    Derived columns (event_id, created_timestamp, tenant_id, project_id, and every table's own
    KEY_PLANS entity/FK columns) are computed by the write boundary and must never be supplied
    directly by the caller.
    """
    for key in row:
        if key in derived_columns:
            raise GateError(
                f"column {key!r} is derived by the write boundary and must not be caller-supplied",
                column=key,
                reason="derived_column",
            )
        if key not in known_columns:
            raise GateError(f"unknown column {key!r} is not declared on this table's spec", reason="unknown_column")


def check_not_null(row: dict[str, Any], not_null_columns: frozenset[str]) -> None:
    """Raise on the first NOT NULL column that is missing or explicitly None in *row*."""
    for col in not_null_columns:
        if row.get(col) is None:
            raise GateError(f"column {col!r} is NOT NULL but missing or None", column=col, reason="not_null")


def check_and_normalize_value(column: str, value: Any, sql_type: str) -> Any:
    """Strictly validate *value* against *sql_type* and return its normalised bind form.

    Rejections DuckDB's own CAST would otherwise silently coerce: bool is never accepted as
    BIGINT/DOUBLE; a float VARCHAR/BOOLEAN is rejected outright; NaN/inf DOUBLE is rejected; a
    naive datetime is rejected (never assumed UTC); an out-of-int64-range BIGINT is rejected.
    """
    if sql_type == "VARCHAR":
        if not isinstance(value, str):
            raise GateError(
                f"column {column!r}: VARCHAR requires str, got {type(value).__name__}", column=column, reason="type"
            )
        return value
    if sql_type == "BIGINT":
        if isinstance(value, bool) or not isinstance(value, int):
            raise GateError(
                f"column {column!r}: BIGINT requires int (not bool), got {type(value).__name__}", column=column, reason="type"
            )
        if not (-(2**63) <= value <= 2**63 - 1):
            raise GateError(f"column {column!r}: BIGINT value is out of int64 range", column=column, reason="range")
        return value
    if sql_type == "DOUBLE":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise GateError(
                f"column {column!r}: DOUBLE requires int or float (not bool), got {type(value).__name__}",
                column=column,
                reason="type",
            )
        try:
            as_float = float(value)
        except OverflowError as exc:
            raise GateError(f"column {column!r}: DOUBLE value is out of range", column=column, reason="range") from exc
        if not math.isfinite(as_float):
            raise GateError(f"column {column!r}: DOUBLE requires a finite value", column=column, reason="range")
        return as_float
    if sql_type == "BOOLEAN":
        if not isinstance(value, bool):
            raise GateError(
                f"column {column!r}: BOOLEAN requires bool, got {type(value).__name__}", column=column, reason="type"
            )
        return value
    if sql_type == "VARCHAR[]":
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise GateError(f"column {column!r}: VARCHAR[] requires list[str]", column=column, reason="type")
        return value
    if sql_type == "TIMESTAMP WITH TIME ZONE":
        if not isinstance(value, datetime):
            raise GateError(
                f"column {column!r}: TIMESTAMP WITH TIME ZONE requires a datetime, got {type(value).__name__}",
                column=column,
                reason="type",
            )
        offset = value.utcoffset()
        if offset is None:
            raise GateError(
                f"column {column!r}: naive datetime is not accepted (a timezone is required)", column=column, reason="type"
            )
        utc_value = value.astimezone(timezone.utc)
        floored_micro = (utc_value.microsecond // 1000) * 1000
        return utc_value.replace(microsecond=floored_micro)
    raise GateError(f"column {column!r}: unsupported sql_type {sql_type!r}", column=column, reason="type")


def differing_columns(a: dict[str, Any], b: dict[str, Any], *, ignore: frozenset[str]) -> list[str]:
    """Return the sorted column names where *a* and *b* differ, excluding *ignore*.

    *ignore* is the table spec's compare_excluded set: the write-time stamp and provenance-only columns plus every
    declared representation-only column -- the grain-enforced-at-write compared-content definition (Decision 207,
    data-modeling-standard.yaml).
    """
    keys = (set(a) | set(b)) - ignore
    return sorted(k for k in keys if a.get(k) != b.get(k))


def collapse_or_reject_duplicates(
    rows: list[dict[str, Any]],
    dedupe_key: tuple[str, ...],
    compare_excluded: frozenset[str],
) -> tuple[list[dict[str, Any]], int]:
    """Collapse intra-batch rows sharing *dedupe_key* whose content is identical outside *compare_excluded*;
    reject conflicting ones. Rejects a NULL in any grain-key column.

    Returns (deduped_rows, collapsed_count). Preserves first-seen order. Raises GrainConflictError
    naming the event_id (never first-wins) when two rows share the dedupe key but differ in any
    other stored column.
    """
    seen: dict[tuple[Any, ...], dict[str, Any]] = {}
    deduped: list[dict[str, Any]] = []
    collapsed = 0
    for index, row in enumerate(rows):
        for col in dedupe_key:
            if row.get(col) is None:
                raise GateError(
                    f"grain-key column {col!r} is NULL (grain key={dedupe_key})",
                    row=index,
                    column=col,
                    reason="grain_key_null",
                )
        key = tuple(row[k] for k in dedupe_key)
        prior = seen.get(key)
        if prior is None:
            seen[key] = row
            deduped.append(row)
            continue
        diff = differing_columns(prior, row, ignore=compare_excluded)
        if not diff:
            collapsed += 1
            continue
        raise GrainConflictError(
            f"conflicting duplicate rows for event_id={row.get('event_id')!r} "
            f"(dedupe_key={dedupe_key}): differing columns {diff}",
            event_id=row.get("event_id"),
            row=index,
        )
    return deduped, collapsed
