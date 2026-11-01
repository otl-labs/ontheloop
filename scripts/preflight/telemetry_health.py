"""Telemetry-store gauge for session_preflight (T2.36 c3, Decision 215 cl.2).

One `telemetry_health_count` reader call (catalog metadata only, no data file) from the warm-up, through the reader serving
the writer's catalog. The gauge is tri-state:
pass (any of the four journals holds a committed row), unpopulated (all four are known and empty) or unavailable (the read
failed, was refused, or a table is absent). An unavailable gauge carries null counts, never 0 or [], so an unreachable reader
never reads as an empty store. Never raises.
"""

from __future__ import annotations

import logging
from typing import Any

from scripts.preflight import _common

logger = logging.getLogger(__name__)

TABLES = ("telemetry_sessions", "telemetry_observations", "telemetry_transcripts", "telemetry_agents")


def unavailable_gauge(reason: str, catalog: str | None = None) -> dict[str, Any]:
    return {"status": "unavailable", "catalog": catalog, "tables": dict.fromkeys(TABLES), "reason": reason[:300]}


def check_telemetry_health(reader: Any = None) -> dict[str, Any]:
    """Return {status, catalog, tables} (plus reason when unavailable) from one telemetry_health_count call."""
    try:
        active: Any = reader if reader is not None else _common._make_event_reader()
        rows = list(active.named("telemetry_health_count"))
    except Exception as exc:  # noqa: BLE001 -- every failure path is the same loud degraded signal
        logger.warning("telemetry_health: store gauge unavailable: %s", exc)
        return unavailable_gauge(f"{type(exc).__name__}: {exc}")
    counts: dict[str, int | None] = dict.fromkeys(TABLES)
    for row in rows:
        if row.get("table_name") in counts:
            counts[row["table_name"]] = int(row["record_count"])
    catalog = next((row.get("meta_schema") for row in rows if row.get("meta_schema")), None)
    known = [c for c in counts.values() if c is not None]
    if any(c > 0 for c in known):
        status = "pass"
    elif len(known) == len(TABLES):
        status = "unpopulated"
    else:
        return {**unavailable_gauge("telemetry table absent from the catalog", catalog), "tables": counts}
    return {"status": status, "catalog": catalog, "tables": counts}


def format_gauge(gauge: dict[str, Any]) -> str:
    """One preflight line: the status, the catalog it read and the per-table counts."""
    counts = ", ".join(f"{name.removeprefix('telemetry_')}={count}" for name, count in gauge["tables"].items())
    return f"Telemetry store: {gauge['status']} on {gauge['catalog'] or 'unknown catalog'} ({counts})"
