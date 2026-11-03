"""Real local DuckLake checks for the telemetry event-journal verbs (rec-4291, integration-marked).

No CI tier runs these (the full tier runs `-m "not integration"`; rec-4071 owns that residual), so the implementer runs
them before the PR and records the output. The helper FAILS, never skips, when the ducklake extension cannot load.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

duckdb = pytest.importorskip("duckdb")

from src.common import ducklake_event_reads as ev  # noqa: E402
from src.common.ducklake_event_read_verbs import EVENT_READS  # noqa: E402
from src.telemetry.identity import derive_entity_key  # noqa: E402
from tests.fixtures import telemetry_read_db as db  # noqa: E402

pytestmark = pytest.mark.integration

_TENANT = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
_PROJECT = "01BRZ3NDEKTSV4RRFFQ69G5FBW"
_DAYS = [datetime(2026, 9, 20 + i, tzinfo=timezone.utc) for i in range(4)]
_FLUSHES = 3
_SESSIONS_PER_DAY = 3
_EVENTS = 4


def _session(day: datetime, k: int) -> tuple[str, datetime]:
    started = day + timedelta(hours=3 + k)
    return derive_entity_key("telemetry_sessions:session_id", _TENANT, _PROJECT, f"{day.date()}-{k}", started), started


def _populate(con: Any) -> None:
    """Every flush writes one multi-row INSERT per day that interleaves all of that day's sessions."""
    for flush in range(_FLUSHES):
        for day in _DAYS:
            observations, sessions = [], []
            for k in range(_SESSIONS_PER_DAY):
                sid, started = _session(day, k)
                sessions.append(
                    db.base_row(
                        session_id=sid,
                        session_started_at=started,
                        event_id=f"open-{sid}-{flush}",
                        event_kind="open",
                        event_timestamp=started,
                        source_ordinal=0,
                    )
                )
                for j in range(_EVENTS):
                    observations.append(
                        db.base_row(
                            session_id=sid,
                            session_started_at=started,
                            event_id=f"{sid}-{flush}-{j}",
                            event_kind="point",
                            event_timestamp=started + timedelta(minutes=flush * 10 + j),
                            source_ordinal=flush * 10 + j,
                            observation_id=f"o{flush}{j}",
                            observation_type="process_event",
                            severity="info",
                            name="hook:x",
                        )
                    )
            db.insert_rows(con, "telemetry_observations", observations, batch=True)
            db.insert_rows(con, "telemetry_sessions", sessions, batch=True)


def _profile(con: Any, path: Path, sql: str, values: dict[str, Any]) -> dict[str, Any]:
    con.execute("PRAGMA enable_profiling='json'")
    con.execute(f"PRAGMA profiling_output='{path}'")
    try:
        con.execute(sql, values).fetchall()
    finally:
        con.execute("PRAGMA disable_profiling")
    return json.loads(path.read_text(encoding="utf-8"))


def _scans(node: dict[str, Any], acc: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if node.get("operator_type") == "TABLE_SCAN":
        acc.append(node["extra_info"])
    for child in node.get("children") or []:
        _scans(child, acc)
    return acc


def test_session_read_touches_only_its_day_files(tmp_path: Path) -> None:
    con = db.open_local_lake(tmp_path)
    _populate(con)
    total = con.execute("SELECT count(*) FROM ducklake_list_files('telemetry_catalog', 'telemetry_observations')").fetchone()[
        0
    ]
    assert total == _FLUSHES * len(_DAYS)
    sid, _ = _session(_DAYS[1], 1)
    entry = EVENT_READS["session_process_event_rollup"]
    values = ev.bind_values(entry, {"session_id": sid})
    sql = ev.render_sql(entry)
    scans = _scans(_profile(con, tmp_path / "profile.json", sql, {n: v for n, v in values.items() if f"${n}" in sql}), [])
    by_table: dict[str, list[int]] = {}
    for scan in scans:
        by_table.setdefault(scan["Table"], []).append(int(scan["Total Files Read"]))
        for name in str(scan.get("Filename(s)", "")).split(", "):
            assert not name or "day=21" in name, f"read a file outside the session's own day: {name}"
    assert max(by_table["telemetry_observations"]) == _FLUSHES, by_table
    assert max(by_table["telemetry_sessions"]) <= _FLUSHES, by_table
    rows = ev.execute(con, verb="session_process_event_rollup", params={"session_id": sid})
    assert [r["process_event_total"] for r in rows] == [_FLUSHES * _EVENTS]
    other_day = ev.execute(con, verb="session_process_event_rollup", params={"session_id": _session(_DAYS[3], 0)[0]})
    assert [r["process_event_total"] for r in other_day] == [_FLUSHES * _EVENTS]


def test_health_count_matches_real_ducklake_metadata(tmp_path: Path) -> None:
    con = db.open_local_lake(tmp_path)
    _populate(con)
    rows = ev.execute(con, verb="telemetry_health_count", params={})
    actual = {r["table_name"]: r["record_count"] for r in rows}
    expected = {t: con.execute(f"SELECT count(*) FROM telemetry_catalog.{t}").fetchone()[0] for t in db.TABLES}
    assert actual == expected and actual["telemetry_observations"] == len(_DAYS) * _SESSIONS_PER_DAY * _FLUSHES * _EVENTS
    entry = EVENT_READS["telemetry_health_count"]
    sql = ev.render_sql(entry)
    scans = _scans(_profile(con, tmp_path / "profile.json", sql, {"meta_schema": ""}), [])
    assert not [s for s in scans if str(s["Table"]).startswith("telemetry_")], "the health verb read a data table"
