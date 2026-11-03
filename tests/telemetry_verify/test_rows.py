"""Mirror tests for scripts/telemetry_verify/rows.py over a fake named callable."""

from __future__ import annotations

import json
from datetime import timezone
from typing import Any

import pytest

from scripts.telemetry_verify import TreeMovedError, decode_rows, read_tree
from src.common.ducklake_reader_client import ResponseTooLargeError


class FakeTree:
    def __init__(self, total: int, roots: int = 1, limit: int | None = None) -> None:
        self.total, self.roots, self.limit, self.calls = total, roots, limit, []

    def __call__(self, verb: str, **params: Any) -> list[dict[str, Any]]:
        assert verb == "session_tree_rows"
        self.calls.append((params["offset"], params["max_rows"]))
        if self.limit is not None and params["max_rows"] > self.limit:
            raise ResponseTooLargeError("named_read", "too large", None)
        start, stop = params["offset"], min(self.total, params["offset"] + params["max_rows"])
        return [
            {
                "table_name": "telemetry_sessions",
                "row_json": json.dumps(
                    {"event_id": f"E{i}", "event_timestamp": "2026-01-01 00:00:01+00", "parser_version": 3}
                ),
                "total_rows": self.total,
                "roots_in_tree": self.roots,
            }
            for i in range(start, stop)
        ]


def test_pages_reassemble_to_every_row_once() -> None:
    pages = read_tree(FakeTree(23), "S", 5)
    rows = decode_rows(pages)["telemetry_sessions"]
    assert [r["event_id"] for r in rows] == [f"E{i}" for i in range(23)]
    assert [len(p) for p in pages] == [5, 5, 5, 5, 3]
    stamp = rows[0]["event_timestamp"]
    assert stamp.tzinfo is not None and stamp.astimezone(timezone.utc).hour == 0 and rows[0]["parser_version"] == 3
    assert read_tree(FakeTree(0), "S") == []


def test_a_moving_total_raises() -> None:
    tree = FakeTree(10)
    original = tree.__call__

    def moving(verb: str, **params: Any) -> list[dict[str, Any]]:
        page = original(verb, **params)
        if params["offset"] > 0:
            tree.total = 11
            page = [dict(r, total_rows=11) for r in page]
        return page

    with pytest.raises(TreeMovedError):
        read_tree(moving, "S", 4)
    shrunk = FakeTree(10)
    first = shrunk.__call__
    calls = {"n": 0}

    def vanishing(verb: str, **params: Any) -> list[dict[str, Any]]:
        calls["n"] += 1
        return first(verb, **params) if calls["n"] == 1 else []

    with pytest.raises(TreeMovedError):
        read_tree(vanishing, "S", 4)


def test_a_413_halves_the_page_until_it_fits() -> None:
    tree = FakeTree(9, limit=2)
    pages = read_tree(tree, "S", 8)
    assert [r["event_id"] for r in decode_rows(pages)["telemetry_sessions"]] == [f"E{i}" for i in range(9)]
    assert tree.calls[:4] == [(0, 8), (0, 4), (0, 2), (2, 2)]
    with pytest.raises(ResponseTooLargeError):
        read_tree(FakeTree(3, limit=0), "S", 4)
