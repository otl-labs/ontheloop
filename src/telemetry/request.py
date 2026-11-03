"""The plane-neutral append request grammar (T2.36, slice 2a-2, plan 3 part 2; Decision 199, Decision 210 cl.5).

parse_append_request validates the wire shape of one append_telemetry body BEFORE any database work, so every plane that
accepts telemetry refuses the same requests the same way. Stdlib only, and the only kernel import is
src.telemetry.timestamps: this module must stay reachable from a producer-side lint without opening the turn_capture
import fence (Decision 184 cl.2, Decision 212 cl.2). A table's shape is read through a local Protocol, never an import
of src.telemetry.append, not even under TYPE_CHECKING (import-linter counts those edges).

Rules, in this order: encoding validity (a lone surrogate in any string, U+0000 in a key, the project_ref, the component
or a row's producer); the body key allowlist; project_ref; component; batch shapes (a closed table set, each table once,
rows a list of objects, the generation-marker table last when present, at least one row); one producer and one
parser_version per request; a caller content_uri (the writer derives it after verifying the object, writer_derived);
then wire timestamp decoding. Inline content above the threshold is the kernel's row rule (422), not this grammar's.

RequestError carries only error_type, table, row, column, rule, reason, threshold and cap. An undeclared name the caller sent
(an unknown body key, table or column) and every row value are never carried.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from src.telemetry.timestamps import TimestampError, parse_wire_timestamp

GENERATION_MARKER_TABLE = "telemetry_sessions"
DEFAULT_COMPONENT = "unknown"
COMPONENT_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
ALLOWED_BODY_KEYS = frozenset({"action", "project_ref", "component", "batches"})
_BATCH_KEYS = frozenset({"table", "rows"})
_TIMESTAMP_TYPE = "TIMESTAMP WITH TIME ZONE"


class RequestError(ValueError):
    """A request-grammar refusal. error_type is one of bad_request, write_order, mixed_producer, payload_too_large,
    schema_gate; the other fields locate it without carrying a caller value."""

    def __init__(
        self,
        error_type: str,
        *,
        table: str | None = None,
        row: int | None = None,
        column: str | None = None,
        rule: str | None = None,
        reason: str | None = None,
        threshold: int | None = None,
        cap: int | None = None,
    ) -> None:
        self.error_type = error_type
        self.table = table
        self.row = row
        self.column = column
        self.rule = rule
        self.reason = reason
        self.threshold = threshold
        self.cap = cap
        located = {
            "reason": reason,
            "table": table,
            "row": row,
            "column": column,
            "rule": rule,
            "threshold": threshold,
            "cap": cap,
        }
        super().__init__(error_type + "".join(f" {k}={v}" for k, v in located.items() if v is not None))


class _SpecLike(Protocol):
    @property
    def table(self) -> str: ...

    @property
    def columns(self) -> Mapping[str, str]: ...

    @property
    def rules(self) -> Any: ...


@dataclass(frozen=True)
class TableShape:
    """What the grammar needs to know about one event table."""

    table: str
    timestamp_columns: frozenset[str]
    uri_column: str | None = None

    @classmethod
    def from_spec(cls, spec: _SpecLike) -> TableShape:
        timestamps = frozenset(name for name, sql_type in spec.columns.items() if sql_type == _TIMESTAMP_TYPE)
        content = spec.rules.content
        return cls(spec.table, timestamps, None if content is None else content.uri)


@dataclass(frozen=True)
class AppendRequest:
    project_ref: str
    component: str
    batches: tuple[tuple[str, list[dict[str, Any]]], ...]
    producer: Any
    parser_version: Any


def _lone_surrogate(text: str) -> bool:
    return any(0xD800 <= ord(ch) <= 0xDFFF for ch in text)


def _bad(reason: str, **where: Any) -> RequestError:
    return RequestError("bad_request", reason=reason, **where)


def _walk_unicode(body: Mapping[str, Any], shapes: Mapping[str, TableShape]) -> None:
    """Refuse a lone surrogate in any string or a U+0000 in any key, iteratively (no recursion on caller nesting)."""
    stack: list[tuple[Any, tuple[Any, ...]]] = [(body, ())]
    while stack:
        node, path = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                if not isinstance(key, str) or _lone_surrogate(key) or "\x00" in key:
                    raise _invalid_unicode(body, path, shapes)
                stack.append((value, (*path, key)))
        elif isinstance(node, list):
            stack.extend((item, (*path, index)) for index, item in enumerate(node))
        elif isinstance(node, str) and _lone_surrogate(node):
            raise _invalid_unicode(body, path, shapes)


def _invalid_unicode(body: Mapping[str, Any], path: tuple[Any, ...], shapes: Mapping[str, TableShape]) -> RequestError:
    """Position only: the declared table name and the row index, never a column name or a value."""
    table: str | None = None
    row: int | None = None
    batches = body.get("batches")
    if len(path) >= 2 and path[0] == "batches" and isinstance(batches, list) and isinstance(path[1], int):
        batch = batches[path[1]] if path[1] < len(batches) else None
        name = batch.get("table") if isinstance(batch, dict) else None
        table = name if isinstance(name, str) and name in shapes else None
        if len(path) >= 4 and path[2] == "rows" and isinstance(path[3], int):
            row = path[3]
    return _bad("invalid_unicode", table=table, row=row)


def _iter_rows(batches: Any) -> Iterator[dict[str, Any]]:
    if isinstance(batches, list):
        for batch in batches:
            rows = batch.get("rows") if isinstance(batch, dict) else None
            if isinstance(rows, list):
                yield from (row for row in rows if isinstance(row, dict))


def _refuse_nul(body: Mapping[str, Any]) -> None:
    for name in ("project_ref", "component"):
        value = body.get(name)
        if isinstance(value, str) and "\x00" in value:
            raise _bad("invalid_unicode")
    for row in _iter_rows(body.get("batches")):
        producer = row.get("producer")
        if isinstance(producer, str) and "\x00" in producer:
            raise _bad("invalid_unicode")


def _same(a: Any, b: Any) -> bool:
    return type(a) is type(b) and a == b


def _check_one_value(batches: list[tuple[str, list[dict[str, Any]]]], column: str) -> Any:
    first: Any = None
    seen = False
    for _table, rows in batches:
        for row in rows:
            if column not in row or row[column] is None:
                continue
            if seen and not _same(first, row[column]):
                raise RequestError("mixed_producer", reason=column)
            first, seen = row[column], True
    return first


def _parse_batches(raw: Any, shapes: Mapping[str, TableShape]) -> list[tuple[str, list[dict[str, Any]]]]:
    if not isinstance(raw, list) or not raw:
        raise _bad("batches")
    batches: list[tuple[str, list[dict[str, Any]]]] = []
    for batch in raw:
        if not isinstance(batch, dict) or set(batch) != _BATCH_KEYS:
            raise _bad("batch_shape")
        table = batch["table"]
        if not isinstance(table, str) or table not in shapes:
            raise _bad("unknown_table")
        if any(table == seen for seen, _ in batches):
            raise _bad("duplicate_table", table=table)
        rows = batch["rows"]
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise _bad("rows", table=table)
        batches.append((table, rows))
    if not any(rows for _, rows in batches):
        raise _bad("empty_request")
    names = [table for table, _ in batches]
    if GENERATION_MARKER_TABLE in names and names[-1] != GENERATION_MARKER_TABLE:
        raise RequestError("write_order", table=GENERATION_MARKER_TABLE, reason="sessions_batch_last")
    return batches


def _refuse_caller_uri(batches: list[tuple[str, list[dict[str, Any]]]], shapes: Mapping[str, TableShape]) -> None:
    for table, rows in batches:
        column = shapes[table].uri_column
        if column is None:
            continue
        for index, row in enumerate(rows):
            if column in row:
                raise _bad("writer_derived", table=table, row=index, column=column)


def _decode_timestamps(
    batches: list[tuple[str, list[dict[str, Any]]]], shapes: Mapping[str, TableShape]
) -> tuple[tuple[str, list[dict[str, Any]]], ...]:
    decoded: list[tuple[str, list[dict[str, Any]]]] = []
    for table, rows in batches:
        columns = shapes[table].timestamp_columns
        out: list[dict[str, Any]] = []
        for index, row in enumerate(rows):
            fresh = dict(row)
            for column in columns:
                if fresh.get(column) is None:
                    continue
                try:
                    fresh[column] = parse_wire_timestamp(fresh[column])
                except (TimestampError, OverflowError) as exc:
                    raise RequestError("schema_gate", table=table, row=index, column=column, reason="wire_timestamp") from exc
            out.append(fresh)
        decoded.append((table, out))
    return tuple(decoded)


def parse_append_request(body: Mapping[str, Any], shapes: Mapping[str, TableShape]) -> AppendRequest:
    """Validate and decode one append body; raises RequestError. Returns new row dicts (the input is untouched)."""
    _walk_unicode(body, shapes)
    _refuse_nul(body)
    if not set(body) <= ALLOWED_BODY_KEYS:
        raise _bad("unknown_key")
    project_ref = body.get("project_ref")
    if not isinstance(project_ref, str) or not project_ref:
        raise _bad("project_ref")
    component = body.get("component", DEFAULT_COMPONENT)
    if not isinstance(component, str) or COMPONENT_RE.match(component) is None:
        raise _bad("component")
    batches = _parse_batches(body.get("batches"), shapes)
    producer = _check_one_value(batches, "producer")
    parser_version = _check_one_value(batches, "parser_version")
    _refuse_caller_uri(batches, shapes)
    return AppendRequest(project_ref, component, _decode_timestamps(batches, shapes), producer, parser_version)


__all__ = [
    "ALLOWED_BODY_KEYS",
    "AppendRequest",
    "COMPONENT_RE",
    "DEFAULT_COMPONENT",
    "GENERATION_MARKER_TABLE",
    "RequestError",
    "TableShape",
    "parse_append_request",
]
