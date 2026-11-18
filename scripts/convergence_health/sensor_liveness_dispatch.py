"""Cadence credit for the scheduled workflow dispatcher's bot runs (Decision 267 clause 6, rec-4233).

A sibling of sensor_liveness.py (which sits near its SLOC budget): for a workflow the dispatcher's register names,
the newest workflow_dispatch run on main whose actor is the dispatcher's bot counts as cadence alongside the newest
event=schedule run. Imports nothing from sensor_liveness.py.
"""

from __future__ import annotations

import urllib.parse
from typing import Any, Callable, Optional

from scripts.convergence_health.record import _parse_utc
from src.data.handlers.workflow_dispatcher_handler import load_register

_DEAD_QUERY = (
    "scripts.convergence_health.sensor_liveness_dispatch: dispatch-run query for {loop} returned nothing (Decision 55)."
)


def _bot_run(loop: str, caller: Callable[[str], Any], api_base: str, query_suffix: str, bot: str) -> Optional[dict]:
    actor = urllib.parse.quote(bot, safe="")
    data = caller(f"{api_base}/actions/workflows/{loop}/runs?event=workflow_dispatch&actor={actor}{query_suffix}&per_page=5")
    if not data:
        raise RuntimeError(_DEAD_QUERY.format(loop=loop))
    for run in data.get("workflow_runs") or []:
        login = (run.get("actor") or {}).get("login")
        if login != bot or run.get("head_branch") != "main":
            print(f"[sensor_liveness_dispatch] {loop}: run {run.get('id')} earns no cadence credit")
            continue
        return dict(run)
    return None


def newer_cadence_run(
    schedule_run: Optional[dict],
    loop: str,
    caller: Callable[[str], Any],
    api_base: str,
    query_suffix: str = "",
    register: Optional[dict] = None,
) -> Optional[dict]:
    register = register or load_register()
    workflows = {row["workflow"] for row in register["schedules"].values()}
    if loop not in workflows:
        return schedule_run
    bot_run = _bot_run(loop, caller, api_base, query_suffix, register["app"]["bot_login"])
    if bot_run is None:
        return schedule_run
    if schedule_run is None:
        return bot_run
    bot_ts = _parse_utc(str(bot_run.get("created_at") or ""))
    schedule_ts = _parse_utc(str(schedule_run.get("created_at") or ""))
    return bot_run if bot_ts > schedule_ts else schedule_run
