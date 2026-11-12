#!/usr/bin/env python3
"""Episode filing for the Actions capacity gauge (rec-5022): alarm, never a gate (Decision 83).

One rec per contract alarm class through scripts.rec_episode.run_episode, distinguished by a sub_key over context
(the telemetry_verify alarms precedent). A write failure propagates (Decision 84 I-4: no outbox).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable, Optional

from scripts.ci.actions_capacity_contract import Contract
from scripts.ci.actions_capacity_gauge import HourRow, Reading, breaching_hours, class_clear, trailing
from scripts.rec_episode import find_recs, run_episode

ALARM_SOURCE = "actions_capacity"
GAUGE_FILE = "scripts/ci/actions_capacity_gauge.py"
_ROWS_CACHE: Optional[list[dict[str, Any]]] = None
_HOURS_RE = re.compile(r"breaching_hours=(\S*)")


def acceptance_for(cls: str) -> str:
    return f"bin/venv-python -m scripts.ci.actions_capacity_gauge --assert-clear {cls} --reading actions-capacity-reading.json"


def read_open_recs(reader: Any = None) -> Optional[list[dict[str, Any]]]:
    """The module's one find_recs read (source actions_capacity, open); None when the reader fails."""
    global _ROWS_CACHE
    try:
        _ROWS_CACHE = find_recs(ALARM_SOURCE, status="open", reader=reader)
    except Exception as exc:  # noqa: BLE001
        print(f"[actions_capacity] open-rec read failed: {type(exc).__name__}")
        _ROWS_CACHE = None
    return _ROWS_CACHE


def open_rec_exists(rows: Optional[list[dict[str, Any]]] = None, reader: Any = None) -> Optional[bool]:
    """True when an open actions_capacity rec exists, False when none, None (read_failed) when the read failed."""
    found = rows if rows is not None else read_open_recs(reader)
    if rows is not None:
        global _ROWS_CACHE
        _ROWS_CACHE = rows
    return None if found is None else bool(found)


def _class_matches(cls: str) -> Callable[[dict[str, Any]], bool]:
    token = re.compile(r"(?:^|\s)class=" + re.escape(cls) + r"(?:\s|$)")
    return lambda rec: token.search(rec.get("context") or "") is not None


def _context(cls: str, hours: list[str], table: list[HourRow], contract: Contract, run_url: str) -> str:
    by_hour = {r.hour: r for r in table}
    detail = "; ".join(
        f"{h} slot_demand={by_hour[h].slot_demand} queue_p90_s={by_hour[h].queue_p90_s}" for h in hours if h in by_hour
    )
    return (
        f"class={cls} breaching_hours={','.join(sorted(hours))} values=[{detail}] run={run_url} "
        f"cap={contract.cap.concurrent_jobs} trigger_response: {' '.join(contract.trigger_response.split())}"
    )


def _fields(cls: str, context: str) -> dict[str, Any]:
    title = {
        "capacity_breach": "Actions capacity trigger fired: slot demand or main-validate queue over the contract threshold",
        "gauge_blind": "Actions capacity gauge cannot see enough hours: too few evaluated hours in the trailing 24",
    }[cls]
    return {
        "title": title,
        "file": GAUGE_FILE,
        "status": "open",
        "source": "actions_capacity",
        "priority": "High",
        "effort": "S",
        "risk": "low",
        "automatable": False,
        "verification_tier": "V2",
        "context": context,
        "acceptance": acceptance_for(cls),
    }


def _hour_set(context: str) -> set[str]:
    m = _HOURS_RE.search(context or "")
    return {h for h in (m.group(1).split(",") if m else []) if h}


def _episode(
    cls: str,
    over: bool,
    hours: list[str],
    table: list[HourRow],
    contract: Contract,
    run_url: str,
    portal: Optional[Callable[[str, dict[str, Any]], Any]],
    rows: Optional[list[dict[str, Any]]],
) -> str:
    context = _context(cls, hours, table, contract, run_url)

    def build_update(existing: dict[str, Any]) -> Optional[dict[str, Any]]:
        return None if _hour_set(existing.get("context") or "") == set(hours) else {"context": context}

    def build_close(existing: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "closed",
            "resolution": f"Cleared: the trailing 24 hours no longer hold the {cls} condition ({run_url}).",
        }

    result = run_episode(
        source=ALARM_SOURCE,
        over_threshold=over,
        build_fields=lambda: _fields(cls, context),
        build_update=build_update,
        build_close=build_close,
        sub_key=_class_matches(cls),
        sub_key_fields=("context",),
        portal_caller=portal,
        rows=rows if rows is not None else [],
    )
    return str(result["action"])


def act(
    reading: Reading,
    table: list[HourRow],
    contract: Contract,
    now: datetime,
    run_url: str,
    diagnose: bool = False,
    portal: Optional[Callable[[str, dict[str, Any]], Any]] = None,
    rows: Optional[list[dict[str, Any]]] = None,
) -> str:
    """Run both alarm classes; returns a compact action label. CONVERGENCE_HEALTH_DIAGNOSE: print, write nothing."""
    rows = rows if rows is not None else _ROWS_CACHE
    breach_hours = breaching_hours(table, now, contract)
    blind = len(trailing(table, now)) < contract.validity.min_evaluated_hours_24h
    breach_action = "none"
    if reading.verdict == "red_measured":
        breach_over: Optional[bool] = True
    elif class_clear("capacity_breach", table, now, contract):
        breach_over = False
    else:
        breach_over = None
    if diagnose:
        print(f"[actions_capacity] diagnose: capacity_breach over={breach_over} hours={breach_hours} gauge_blind over={blind}")
        return "diagnose"
    if breach_over is not None:
        breach_action = _episode("capacity_breach", breach_over, breach_hours, table, contract, run_url, portal, rows)
    blind_action = _episode("gauge_blind", blind, [], table, contract, run_url, portal, rows)
    return f"{breach_action}/{blind_action}"
