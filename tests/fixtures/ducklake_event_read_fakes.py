"""Shared fakes and values for the src/common/ducklake_event_reads.py concern-split tests."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.telemetry.identity import derive_entity_key

_SESSION = derive_entity_key(
    "telemetry_sessions:session_id",
    "01ARZ3NDEKTSV4RRFFQ69G5FAV",
    "01BRZ3NDEKTSV4RRFFQ69G5FBW",
    "ref",
    datetime(2026, 9, 25, 10, 30, tzinfo=timezone.utc),
)

_AS_OF = "2026-09-26T12:00:00+00:00"


class FakeCursor:
    description = [("x",)]

    def fetchall(self) -> list[tuple[Any, ...]]:
        return [(1,)]


class RecordingCon:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def execute(self, sql: str, params: Any = None) -> FakeCursor:
        self.calls.append((sql, params))
        return FakeCursor()


def NO_OPS(force_reopen: bool = False) -> tuple[Any, dict[str, Any]]:
    return None, {}
