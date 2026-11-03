"""Paged reads of one tree's stored rows through session_tree_rows, and their decode to plain rows (counts only).

*named* is any callable with the reader client's named() contract: named(verb, **params) returns the list of row dicts, and a
response above the reader's envelope guard raises ResponseTooLargeError (the typed 413). Rows are held in memory by the caller
for the comparison and never persisted or printed.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from scripts.telemetry_verify.dedupe import parse_timestamp
from src.common.ducklake_reader_client import ResponseTooLargeError

VERB = "session_tree_rows"
_TIMESTAMPS = frozenset({"event_timestamp", "session_started_at", "created_timestamp"})


class TreeMovedError(RuntimeError):
    """A later page disagrees with the first page's totals: the tree changed while it was read; the caller re-reads."""


def read_tree(
    named: Callable[..., list[dict[str, Any]]], session_id: str, page_rows: int = 2000
) -> list[list[dict[str, Any]]]:
    """Every page of the tree holding *session_id* (offset 0 up to total_rows), halving the page on a 413 down to one row."""
    pages: list[list[dict[str, Any]]] = []
    offset, size, first = 0, max(1, page_rows), None
    while True:
        try:
            page = named(VERB, session_id=session_id, offset=offset, max_rows=size)
        except ResponseTooLargeError:
            if size == 1:
                raise
            size = max(1, size // 2)
            continue
        if not page:
            if first is not None and offset < first[0]:
                raise TreeMovedError("session_tree_rows answered no rows before total_rows was reached")
            return pages
        totals = (page[0]["total_rows"], page[0]["roots_in_tree"])
        if any((r["total_rows"], r["roots_in_tree"]) != totals for r in page) or (first is not None and totals != first):
            raise TreeMovedError("session_tree_rows totals changed while the tree was being read")
        first = totals
        pages.append(page)
        offset += len(page)
        if offset >= totals[0]:
            return pages


def _decode(value: Any, column: str) -> Any:
    if column in _TIMESTAMPS and value is not None:
        return parse_timestamp(value)
    return value


def decode_rows(pages: list[list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    """rows_by_table as plain dicts (aware datetimes, integers) in the shape dedupe and derive take."""
    out: dict[str, list[dict[str, Any]]] = {}
    for page in pages:
        for entry in page:
            row = json.loads(entry["row_json"])
            out.setdefault(entry["table_name"], []).append({k: _decode(v, k) for k, v in row.items()})
    return out
