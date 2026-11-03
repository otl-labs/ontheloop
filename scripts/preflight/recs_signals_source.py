"""Bounded, fail-closed recommendation inputs for session_preflight (rec-4232, Decision 215).

Preflight no longer pulls the ops_recommendations table: it makes exactly two named-verb reads
(`open_recs_tally`, `preflight_signal_rows`). Any failure yields status "unavailable" with None
rows -- never an empty list (Decision 55): an unreachable reader must not read as "no CI-RCA recs".
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Iterable

from scripts.preflight import _common

logger = logging.getLogger(__name__)

_OPEN_STATES = ("open", "in_progress")


@dataclass
class RecsSignalInputs:
    status: str  # "ok" | "unavailable"
    tally: dict[str, Any] | None = None
    signal_rows: list[dict] | None = None
    error: str | None = None
    recs_read: dict[str, Any] = field(default_factory=lambda: {"calls": 0, "bytes": {}})


def _row_bytes(rows: list[dict]) -> int:
    return len(json.dumps(rows, default=str).encode("utf-8"))


def fetch_recs_signal_inputs(reader: Any = None, followon_ids: Iterable[str] = ()) -> RecsSignalInputs:
    """Make the two bounded reads. *followon_ids* ride the single `preflight_signal_rows` call, comma-joined."""
    recs_read: dict[str, Any] = {"calls": 0, "bytes": {}}
    try:
        active: Any = reader if reader is not None else _common._make_reader()
        recs_read["calls"] += 1
        tally_rows = active.named("open_recs_tally")
        recs_read["bytes"]["open_recs_tally"] = _row_bytes(tally_rows)
        recs_read["calls"] += 1
        signal_rows = list(active.named("preflight_signal_rows", followon_ids=",".join(sorted(set(followon_ids)))))
        recs_read["bytes"]["preflight_signal_rows"] = _row_bytes(signal_rows)
        if len(tally_rows) != 1:
            raise ValueError(f"open_recs_tally returned {len(tally_rows)} rows; expected exactly one")
        return RecsSignalInputs("ok", dict(tally_rows[0]), signal_rows, None, recs_read)
    except Exception as exc:  # noqa: BLE001 -- every failure path is the same loud degraded signal
        logger.warning("recs_signals_source: recommendation signals unavailable: %s", exc)
        return RecsSignalInputs("unavailable", None, None, f"{type(exc).__name__}: {exc}"[:300], recs_read)


def ci_rca_open_total(signal_rows: list[dict] | None) -> int | None:
    """Uncapped count of open/in_progress source=ci_rca rows; None when the signal is unavailable."""
    if signal_rows is None:
        return None
    return sum(1 for r in signal_rows if r.get("source") == "ci_rca" and r.get("status") in _OPEN_STATES)
