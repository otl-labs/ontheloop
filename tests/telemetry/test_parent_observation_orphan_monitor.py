"""rec-4101's orphan monitor: orphans are flagged only in quiescent trees (PLAN-telemetry-verification-monitor, VP step 6)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest

from scripts.telemetry_verify import monitor, orphans
from scripts.telemetry_verify.joins import load_telemetry_joins
from src.common import ducklake_event_reads as ev
from src.common.ducklake_event_read_verbs import IDLE_THRESHOLD_SECONDS
from tests.fixtures import telemetry_golden_chain as g

duckdb = pytest.importorskip("duckdb", reason="env: duckdb is not installed in the fast tier")

TREES = ["corpus:subagent_sync", "corpus:resume_after_interrupt"]
DAY = {"day_from": "2026-01-01", "day_to": "2026-01-01"}
OBS_JOIN = "parent_observation_id_join_observations_self_tree"
SESSION_JOIN = "parent_session_id_join_sessions_self_spawn"
FK_JOIN = "session_id_join_telemetry_sessions_to_observations"
PLANTS = {
    OBS_JOIN: ("telemetry_observations", {"event_id": "orphan-o", "parent_observation_id": "missing-observation"}),
    SESSION_JOIN: (
        "telemetry_sessions",
        {"event_id": "orphan-s", "session_id": "orphan-child", "parent_session_id": "missing-session"},
    ),
    FK_JOIN: ("telemetry_observations", {"event_id": "orphan-f", "session_id": "unknown-session"}),
}


def _world() -> tuple[Any, dict[str, tuple[str, str]], datetime]:
    con = g.open_catalog()
    projects = g.load_world(con, TREES)
    rows = g.stored_rows(con)
    last = max(r["event_timestamp"] for t in g.TABLES for r in rows[t] if r["project_id"] == projects[TREES[0]][0])
    return con, projects, last


def _named(con: Any):
    return lambda verb, **params: g.run_verb(con, verb, **params)


def _counts(con: Any, as_of: datetime) -> dict[str, dict[str, Any]]:
    verb = g.run_verb(con, "telemetry_orphan_counts", as_of=as_of.isoformat(), **DAY)
    independent = orphans.orphan_counts(
        g.stored_rows(con), load_telemetry_joins(), as_of, IDLE_THRESHOLD_SECONDS, {p[0]: "real" for p in _projects(con)}
    )
    return {"verb": {r["join_id"]: r for r in verb}, "independent": {r["join_id"]: r for r in independent}}


def _projects(con: Any) -> list[tuple[str]]:
    return con.execute(f"SELECT DISTINCT project_id FROM {g.db.ALIAS}.telemetry_sessions").fetchall()


def test_orphan_parent_flagged_only_in_quiescent_trees() -> None:
    con, projects, last = _world()
    pid = projects[TREES[0]][0]
    clean = _counts(con, last + timedelta(days=2))
    assert all(r["orphans_quiescent"] == r["orphans_pending"] == 0 for r in clean["verb"].values())
    for join_id, (table, replace) in PLANTS.items():
        g.clone_row(con, table, pid, replace)
    exact = last + timedelta(seconds=IDLE_THRESHOLD_SECONDS)
    for name, counts in _counts(con, exact).items():
        for join_id in PLANTS:
            assert (counts[join_id]["orphans_quiescent"], counts[join_id]["orphans_pending"]) == (0, 1), (name, join_id)
    for name, counts in _counts(con, exact + timedelta(seconds=1)).items():
        for join_id in PLANTS:
            assert (counts[join_id]["orphans_quiescent"], counts[join_id]["orphans_pending"]) == (1, 0), (name, join_id)
        assert counts[OBS_JOIN]["trees_quiescent"] >= 1


def _run(con: Any, as_of: datetime) -> monitor.MonitorReport:
    return monitor.run_monitor(
        _named(con),
        lambda: ev.describe_all(),
        quiet_day="2026-01-02",
        as_of=as_of,
        budget=monitor.Budget(2000, lambda: 0.0),
    )


def test_the_monitor_alarms_a_quiescent_orphan_and_counts_a_pending_one() -> None:
    con, projects, last = _world()
    pid = projects[TREES[0]][0]
    for table, replace in PLANTS.values():
        g.clone_row(con, table, pid, replace)
    pending = _run(con, last + timedelta(seconds=IDLE_THRESHOLD_SECONDS))
    assert pending.orphan_pending == len(PLANTS) and not [c for c in pending.findings() if c.startswith("orphan_")]
    quiescent = _run(con, last + timedelta(days=2))
    found = quiescent.findings()
    assert all(found[f"orphan_quiescent:{join}"] == 1 for join in PLANTS)
    assert not [c for c in found if c.startswith("orphan_undercount")] and quiescent.orphan_pending == 0


def test_an_orphan_the_verb_misses_is_an_undercount() -> None:
    con, projects, last = _world()
    for table, replace in PLANTS.values():
        g.clone_row(con, table, projects[TREES[0]][0], replace)

    def blind(verb: str, **params: Any) -> list[dict[str, Any]]:
        rows = g.run_verb(con, verb, **params)
        if verb != "telemetry_orphan_counts":
            return rows
        return [{**r, "orphans_quiescent": 0} for r in rows]

    report = monitor.run_monitor(
        blind,
        lambda: ev.describe_all(),
        quiet_day="2026-01-02",
        as_of=last + timedelta(days=2),
        budget=monitor.Budget(2000, lambda: 0.0),
    )
    assert report.findings()[f"orphan_undercount:{OBS_JOIN}"] == 1


def test_a_rootless_tree_is_reported_never_alarmed() -> None:
    con, projects, last = _world()
    con.execute(f"DELETE FROM {g.db.ALIAS}.telemetry_sessions WHERE project_id = ?", [projects[TREES[0]][0]])
    report = _run(con, last + timedelta(days=2))
    assert report.rootless_trees == 1 and report.quiet == 1
    assert not [c for c in report.findings() if c.startswith(("orphan_undercount", "rederivation"))]
