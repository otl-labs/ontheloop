"""Row-by-row comparison of a re-parse's expected rows with the stored rows of one tree (pure; one cached yaml read).

Rows are keyed by (table, producer, external_ref) and only the given producer's rows at the given parser_version are compared;
another parser_version of that producer is counted other_generation_rows and any other producer other_producer_rows, never
compared. Column rules, declared here and pinned by test_column_rules_are_declared_and_minimal, and nothing else:

* the compared value columns are compared_columns(table) (the reader's own Decision 207 set) restricted to role input in
  field_semantics.yaml; a column the producer row omits compares as NULL;
* the derived columns are checked as keys instead: event_id with derive_event_id and every KEY_PLANS column with
  derive_entity_key over the EXPECTED row's ref field for it and the stored row's session_started_at, both from the stored
  row's own tenant_id and project_id, which are not themselves compared (the writer resolves them);
* created_timestamp and producer_version are never compared;
* timestamps compare as UTC instants at millisecond precision, `metadata` (the one JSON-carrying column) as parsed values;
  transcripts content compares by content_sha256 and content_bytes, because content and content_uri are representation-only.

A key held by more than one stored row at the parser_version counts every row beyond the first: duplicate_ref_rows when their
event_ids differ (one event stored under two identities), race_duplicate_rows when they share one event_id (the Decision 210
cl.2 concurrent-append race, rec-4063's grain monitor). The compared survivor is the row whose event_id equals the expected
derivation, else the one with the smallest event_id, so no count depends on read order.
"""

from __future__ import annotations

import functools
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml

from src.common.ducklake_event_reads import compared_columns
from src.telemetry.identity import KEY_PLANS, IdentityError, derive_entity_key, derive_event_id

ALARM_CLASSES = ("missing_in_store", "unexpected_in_store", "column_mismatch", "key_mismatch", "duplicate_ref_rows")
JSON_COLUMNS = frozenset({"metadata"})
_SEMANTICS = Path(__file__).resolve().parents[2] / "config" / "lambda" / "ducklake" / "field_semantics.yaml"
_TIMESTAMPS = "TIMESTAMP WITH TIME ZONE"


@functools.lru_cache(maxsize=1)
def field_semantics() -> dict[str, Any]:
    return yaml.safe_load(_SEMANTICS.read_text(encoding="utf-8"))


@dataclass
class TableCounts:
    expected_rows: int = 0
    rows_equal: int = 0
    missing_in_store: int = 0
    unexpected_in_store: int = 0
    column_mismatch: int = 0
    key_mismatch: int = 0
    duplicate_ref_rows: int = 0
    race_duplicate_rows: int = 0
    other_generation_rows: int = 0
    other_producer_rows: int = 0
    columns: Counter[str] = field(default_factory=Counter)


@dataclass
class CompareResult:
    tables: dict[str, TableCounts] = field(default_factory=dict)
    survivors: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    defects: set[tuple[str, str]] = field(default_factory=set)

    def total(self, name: str) -> int:
        return sum(getattr(counts, name) for counts in self.tables.values())

    @property
    def columns(self) -> Counter[str]:
        out: Counter[str] = Counter()
        for counts in self.tables.values():
            out.update(counts.columns)
        return out

    def classes(self) -> dict[str, int]:
        names = (*ALARM_CLASSES, "rows_equal", "race_duplicate_rows", "other_generation_rows", "other_producer_rows")
        return {name: self.total(name) for name in names}


def compared_value_columns(table: str, semantics: Mapping[str, Any] | None = None) -> tuple[str, ...]:
    semantics = semantics if semantics is not None else field_semantics()
    roles = semantics["ops_tables"][table]["columns"]
    return tuple(name for name in compared_columns(table, dict(semantics)) if roles[name]["role"] == "input")


def key_columns(table: str) -> tuple[str, ...]:
    return ("event_id", *KEY_PLANS[table])


def _instant(value: Any) -> datetime:
    stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    stamp = stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)
    return stamp.replace(microsecond=stamp.microsecond // 1000 * 1000)


class _Unparseable:
    """A timestamp that does not parse: equal to nothing, itself included, so the column reads as a mismatch."""


def _normal(column: str, sql_type: str, value: Any) -> Any:
    if value is None:
        return None
    if sql_type == _TIMESTAMPS:
        try:
            return _instant(value)
        except (ValueError, TypeError):
            return _Unparseable()
    if column in JSON_COLUMNS and isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    if sql_type == "DOUBLE":
        return float(value)
    return value


def _same(left: Any, right: Any) -> bool:
    return type(left) is type(right) and left == right


def _expected_event_id(table: str, stored: Mapping[str, Any], expected: Mapping[str, Any]) -> str | None:
    try:
        return derive_event_id(
            table, stored["tenant_id"], stored["project_id"], expected["external_ref"], _instant(expected["event_timestamp"])
        )
    except (IdentityError, KeyError, ValueError, TypeError):
        return None


def _survivor(table: str, group: Sequence[Mapping[str, Any]], expected: Mapping[str, Any] | None) -> Mapping[str, Any]:
    ordered = sorted(group, key=lambda row: str(row.get("event_id")))
    for row in ordered:
        if expected is not None and row.get("event_id") == _expected_event_id(table, row, expected):
            return row
    return ordered[0]


def _key_mismatches(table: str, stored: Mapping[str, Any], expected: Mapping[str, Any]) -> int:
    wrong = 0
    for column in key_columns(table):
        try:
            if column == "event_id":
                want = _expected_event_id(table, stored, expected)
            else:
                plan = KEY_PLANS[table][column]
                ref = expected.get(plan.ref_field)
                want = (
                    None
                    if ref is None
                    else derive_entity_key(
                        plan.domain_tag,
                        stored["tenant_id"],
                        stored["project_id"],
                        ref,
                        _instant(stored["session_started_at"]),
                    )
                )
        except (IdentityError, KeyError, ValueError, TypeError):
            wrong += 1
            continue
        wrong += not _same(want, stored.get(column))
    return wrong


def _compare_row(
    table: str, stored: Mapping[str, Any], expected: Mapping[str, Any], columns: Sequence[str], types: Mapping[str, str]
) -> tuple[Counter[str], int]:
    mismatched: Counter[str] = Counter()
    for column in columns:
        left = _normal(column, types[column], expected.get(column))
        right = _normal(column, types[column], stored.get(column))
        if not _same(left, right):
            mismatched[column] += 1
    return mismatched, _key_mismatches(table, stored, expected)


def compare_tree(
    expected_batches: Iterable[tuple[str, Sequence[Mapping[str, Any]]]],
    stored_by_table: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    producer: str,
    parser_version: int,
    semantics: Mapping[str, Any] | None = None,
) -> CompareResult:
    semantics = semantics if semantics is not None else field_semantics()
    result = CompareResult()
    expected_by_table: dict[str, dict[str, Mapping[str, Any]]] = {}
    for table, rows in expected_batches:
        expected_by_table.setdefault(table, {}).update(
            {row["external_ref"]: row for row in rows if row["producer"] == producer}
        )
    for table in sorted(set(expected_by_table) | set(stored_by_table)):
        counts = result.tables.setdefault(table, TableCounts())
        expected = expected_by_table.get(table, {})
        counts.expected_rows = len(expected)
        types = {name: spec["sql_type"] for name, spec in semantics["ops_tables"][table]["columns"].items()}
        columns = compared_value_columns(table, semantics)
        groups: dict[str, list[Mapping[str, Any]]] = {}
        for row in stored_by_table.get(table, ()):
            if row["producer"] != producer:
                counts.other_producer_rows += 1
            elif row["parser_version"] != parser_version:
                counts.other_generation_rows += 1
            else:
                groups.setdefault(row["external_ref"], []).append(row)
        for ref in sorted(set(expected) | set(groups)):
            group, want = groups.get(ref, []), expected.get(ref)
            if not group:
                counts.missing_in_store += 1
                result.defects.add((table, ref))
                continue
            kept = _survivor(table, group, want)
            result.survivors[(table, ref)] = dict(kept)
            for row in group:
                if row is not kept:
                    if row.get("event_id") == kept.get("event_id"):
                        counts.race_duplicate_rows += 1
                    else:
                        counts.duplicate_ref_rows += 1
            if want is None:
                counts.unexpected_in_store += 1
                continue
            mismatched, keys = _compare_row(table, kept, want, columns, types)
            counts.columns.update(mismatched)
            counts.column_mismatch += sum(mismatched.values())
            counts.key_mismatch += keys
            if mismatched:
                result.defects.add((table, ref))
            if not mismatched and not keys:
                counts.rows_equal += 1
    return result
