"""Independent stdlib implementation of the telemetry envelope's generations and read-side dedupe rules (Decision 207).

Written from docs/contracts/telemetry-event-envelope.yaml (R4, R5a-R5c), never from the reader's SQL. Rows are plain dicts
keyed by stored column name; timestamps may be datetimes or ISO strings and are normalised once by normalise_row. A table's
rows are those of one scope (a session, or a window of sessions): generations are per (producer, session_id).

The compared-column set behind the Decision 207 conflict report is an argument per call; by default it is
src.common.ducklake_event_reads.compared_columns(table), governed data declared shared with the reader.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

from src.common.ducklake_event_reads import compared_columns as governed_compared_columns

GrainKey = tuple[str, str, int]
Row = dict[str, Any]

_MODEL_CALL_PRECEDENCE = {"claude_code": 0, "litellm": 1}
_OTHER_PRECEDENCE = 2
_TIMESTAMP_COLUMNS = frozenset({"event_timestamp", "session_started_at", "created_timestamp"})


def parse_timestamp(value: Any) -> datetime:
    """A timezone-aware UTC datetime from a datetime or an ISO-8601 string (naive values are taken as UTC)."""
    stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)


def normalise_row(row: Mapping[str, Any]) -> Row:
    """A copy of *row* whose timestamp columns are aware datetimes."""
    out = dict(row)
    for column in _TIMESTAMP_COLUMNS:
        if out.get(column) is not None:
            out[column] = parse_timestamp(out[column])
    return out


def compute_generations(session_rows: Iterable[Row]) -> dict[tuple[str, str], int]:
    """R4: V*(producer, session) = max parser_version of that producer's telemetry_sessions open rows for that session.

    A pair with no open row is absent (R5 alone applies to it, R8).
    """
    generations: dict[tuple[str, str], int] = {}
    for row in session_rows:
        if row.get("event_kind") != "open":
            continue
        key = (row["producer"], row["session_id"])
        generations[key] = max(generations.get(key, row["parser_version"]), row["parser_version"])
    return generations


def _grain_key(row: Row) -> GrainKey:
    return (row["producer"], row["event_id"], row["parser_version"])


def conflicted_grain_keys(rows: Iterable[Row], columns: Iterable[str]) -> set[GrainKey]:
    """Decision 207: a grain key whose rows differ on any compared column (NULL against a value counts), before retirement."""
    names = tuple(columns)
    seen: dict[GrainKey, tuple[Any, ...]] = {}
    conflicted: set[GrainKey] = set()
    for row in rows:
        key = _grain_key(row)
        content = tuple(row.get(name) for name in names)
        if key in seen and seen[key] != content:
            conflicted.add(key)
        seen.setdefault(key, content)
    return conflicted


def retire(rows: Iterable[Row], generations: Mapping[tuple[str, str], int]) -> list[Row]:
    """R5a: drop a producer's rows for a session whose parser_version is not that producer's V* (when V* is defined)."""
    kept = []
    for row in rows:
        v_star = generations.get((row["producer"], row["session_id"]))
        if v_star is None or row["parser_version"] == v_star:
            kept.append(row)
    return kept


def collapse_events(rows: Iterable[Row]) -> list[Row]:
    """R5b: per (producer, event_id) keep the highest parser_version, then the earliest created_timestamp."""
    groups: dict[tuple[str, str], list[Row]] = {}
    for row in rows:
        groups.setdefault((row["producer"], row["event_id"]), []).append(row)
    out = []
    for group in groups.values():
        top = max(row["parser_version"] for row in group)
        out.append(
            min((r for r in group if r["parser_version"] == top), key=lambda r: parse_timestamp(r["created_timestamp"]))
        )
    return out


def _model_call_order(row: Row) -> tuple[Any, ...]:
    precedence = _MODEL_CALL_PRECEDENCE.get(row["producer"], _OTHER_PRECEDENCE)
    return (
        precedence,
        -parse_timestamp(row["event_timestamp"]).timestamp(),
        parse_timestamp(row["created_timestamp"]),
        row["producer"],
        row["event_id"],
    )


def collapse_model_calls(rows: Iterable[Row]) -> list[Row]:
    """R5c: per observation_id keep the first row of the total order claude_code, litellm, others; event_timestamp
    latest first (final usage wins, rec-4062); created_timestamp, producer, event_id ascending."""
    groups: dict[Any, list[Row]] = {}
    for row in rows:
        groups.setdefault(row["observation_id"], []).append(row)
    return [min(group, key=_model_call_order) for group in groups.values()]


def resolve_table(
    table: str,
    rows: Iterable[Row],
    generations: Mapping[tuple[str, str], int],
    *,
    compared: Iterable[str] | None = None,
    entity_collapse: Callable[[Row], bool] | bool = False,
) -> tuple[list[Row], set[GrainKey]]:
    """Survivors and conflicted grain keys of one table's rows.

    entity_collapse applies R5c to the rows it selects (True for all rows, or a predicate such as model_call rows only).
    """
    rows = [normalise_row(row) for row in rows]
    columns = tuple(compared) if compared is not None else governed_compared_columns(table)
    conflicts = conflicted_grain_keys(rows, columns)
    survivors = collapse_events(retire(rows, generations))
    if entity_collapse:
        selects = (lambda _row: True) if entity_collapse is True else entity_collapse
        collapsing = [r for r in survivors if selects(r)]
        survivors = [r for r in survivors if not selects(r)] + collapse_model_calls(collapsing)
    return survivors, conflicts
