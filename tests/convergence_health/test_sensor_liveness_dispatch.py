"""Mirror test for scripts/convergence_health/sensor_liveness_dispatch.py (PLAN-scheduled-workflow-dispatch PR 2)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

import pytest

from scripts.convergence_health import sensor_liveness as sl
from scripts.convergence_health import sensor_liveness_dispatch as sld

API = "https://api.github.com/repos/x/y"
BOT = "otl-workflow-dispatcher[bot]"
LOOP = "terraform-drift.yml"
NOW = datetime(2026, 11, 16, 12, 0, 0, tzinfo=timezone.utc)


def _run(run_id: int, created: str, login: str = BOT, branch: str = "main") -> dict[str, Any]:
    return {"id": run_id, "created_at": created, "actor": {"login": login}, "head_branch": branch}


def _caller(schedule: Any, dispatch: Any) -> tuple[Callable[[str], Any], list[str]]:
    seen: list[str] = []

    def caller(url: str) -> Any:
        seen.append(url)
        value = dispatch if "event=workflow_dispatch" in url else schedule
        if value is None:
            return None
        return {"workflow_runs": value}

    return caller, seen


SCHEDULE = {"id": 1, "created_at": "2026-11-16T10:00:00Z"}


def test_newer_bot_run_wins() -> None:
    caller, _ = _caller([], [_run(2, "2026-11-16T11:00:00Z")])
    assert sld.newer_cadence_run(SCHEDULE, LOOP, caller, API)["id"] == 2


def test_newer_schedule_run_wins() -> None:
    caller, _ = _caller([], [_run(2, "2026-11-16T09:00:00Z")])
    assert sld.newer_cadence_run(SCHEDULE, LOOP, caller, API) is SCHEDULE


def test_non_register_loop_makes_no_call() -> None:
    caller, seen = _caller([], [])
    assert sld.newer_cadence_run(SCHEDULE, "ghas-probe.yml", caller, API) is SCHEDULE
    assert seen == []


def test_dead_dispatch_query_raises() -> None:
    caller, _ = _caller([], None)
    with pytest.raises(RuntimeError, match="Decision 55"):
        sld.newer_cadence_run(SCHEDULE, LOOP, caller, API)


@pytest.mark.parametrize(
    "run", [_run(3, "2026-11-16T11:00:00Z", login="someone"), _run(3, "2026-11-16T11:00:00Z", branch="dev")]
)
def test_other_actor_or_branch_earns_no_credit(run: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    caller, _ = _caller([], [run])
    assert sld.newer_cadence_run(SCHEDULE, LOOP, caller, API) is SCHEDULE
    assert "earns no cadence credit" in capsys.readouterr().out


def test_no_schedule_run_takes_bot_run_or_none() -> None:
    caller, _ = _caller([], [_run(4, "2026-11-16T11:00:00Z")])
    assert sld.newer_cadence_run(None, LOOP, caller, API)["id"] == 4
    caller, _ = _caller([], [])
    assert sld.newer_cadence_run(None, LOOP, caller, API) is None


def test_query_quotes_bot_and_carries_suffix() -> None:
    caller, seen = _caller([], [])
    sld.newer_cadence_run(SCHEDULE, LOOP, caller, API, query_suffix="&status=success")
    quoted = "otl-workflow-dispatcher%5Bbot%5D"
    assert seen == [f"{API}/actions/workflows/{LOOP}/runs?event=workflow_dispatch&actor={quoted}&status=success&per_page=5"]


def test_explicit_register_is_used() -> None:
    register = {"app": {"bot_login": "x[bot]"}, "schedules": {"a": {"workflow": "a.yml"}}}
    caller, seen = _caller([], [])
    assert sld.newer_cadence_run(SCHEDULE, LOOP, caller, API, register=register) is SCHEDULE
    assert seen == []


def test_assess_peer_anchors_on_the_bot_run() -> None:
    caller, _ = _caller([SCHEDULE], [_run(9, "2026-11-16T11:50:00Z")])
    runner = lambda cmd: ""  # noqa: E731
    info = sl._assess_peer(LOOP, ["17 * * * *"], caller, runner, NOW, API)
    assert info["anchor"] == "run:9"
