"""Delivery-floor pins for the loop_liveness_stale sensor (rec-4931).

Evidence: PLAN-sensor-liveness-delivery-floor context (89 days of GitHub schedule delivery to 2026-11-12)
and its PLAN commit body.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import pytest

from scripts.convergence_health import sensor_liveness as sl

NOW = datetime(2026, 11, 12, 12, 0, 0, tzinfo=timezone.utc)
HOUR = 3600


def _run(run_id: int, age_seconds: float) -> dict[str, Any]:
    created = NOW - timedelta(seconds=age_seconds)
    return {"id": run_id, "created_at": created.strftime("%Y-%m-%dT%H:%M:%SZ")}


def _gh(sched_age: float, success_age: float) -> Callable[[str], Any]:
    def caller(url: str) -> Any:
        if url.endswith("/workflows?per_page=100"):
            return {"workflows": []}
        if re.search(r"/workflows/([^/]+)/runs", url):
            age = success_age if "status=success" in url else sched_age
            return {"workflow_runs": [_run(1, age)]}
        return None

    return caller


def _portal_spy() -> tuple[list[tuple[str, dict[str, Any]]], Callable[[str, dict[str, Any]], Any]]:
    calls: list[tuple[str, dict[str, Any]]] = []

    def caller(action: str, fields: dict[str, Any]) -> Any:
        calls.append((action, dict(fields)))
        return f"rec-new-{len(calls)}" if action == "file" else None

    return calls, caller


def _detect(cron: str, sched_age: float, success_age: float) -> tuple[dict[str, Any], list[tuple[str, dict[str, Any]]]]:
    calls, portal = _portal_spy()
    result = sl.detect_stale_loops(
        peers={"loop.yml": [cron]},
        now=NOW,
        gh_caller=_gh(sched_age, success_age),
        open_recs=[],
        resolved_recs=[],
        portal_caller=portal,
    )
    return result, calls


def test_every_sub_daily_shape_shares_the_floor() -> None:
    assert sl.staleness_threshold_seconds("13 */6 * * *") == 86400
    assert sl.staleness_threshold_seconds("17 */12 * * *") == 86400
    assert sl.staleness_threshold_seconds("23 */24 * * *") == 172800


@pytest.mark.parametrize(
    ("cron", "sched_hours", "success_hours"),
    [
        ("37 * * * *", 12.7, 12.7),
        ("17 * * * *", 10.8, 10.8),
        ("47 * * * *", 10.6, 10.6),
        ("0 */3 * * *", 14.6, 14.6),
        ("13 */6 * * *", 16.0, 16.0),
        ("17 */12 * * *", 15.9, 15.9),
        ("47 * * * *", 1.0, 13.0),
    ],
)
def test_measured_worst_gaps_raise_no_alarm(cron: str, sched_hours: float, success_hours: float) -> None:
    result, calls = _detect(cron, sched_hours * HOUR, success_hours * HOUR)
    assert result["filed"] == []
    assert result["updated"] == []
    assert calls == []


def test_floor_boundary_and_real_outage_still_alarm() -> None:
    cron = "17 * * * *"
    result, calls = _detect(cron, 86399, 86399)
    assert result["filed"] == [] and calls == []

    result, calls = _detect(cron, 86400, 86400)
    titles = [f["title"] for _, f in calls if "title" in f]
    assert len(titles) == 2
    assert all("Bucket: 1." in t for t in titles)

    result, calls = _detect(cron, 221.1 * HOUR, 221.1 * HOUR)
    titles = [f["title"] for _, f in calls if "title" in f]
    assert len(titles) == 2
    assert all("Bucket: 4." in t for t in titles)
