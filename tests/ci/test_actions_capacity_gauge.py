"""The Actions capacity gauge computation and CLI (rec-5022). Fixtures are built in code; nothing touches the network."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import pytest

from scripts.ci import actions_capacity_alarm as alarm
from scripts.ci import actions_capacity_gauge as g
from scripts.ci import actions_capacity_github as gh
from scripts.ci.actions_capacity_contract import JobRef, load_contract

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = load_contract(ROOT)
NOW = datetime(2026, 11, 11, 12, 30, tzinfo=timezone.utc)
CI = ".github/workflows/ci.yml"
H0 = datetime(2026, 11, 11, 11, 0, tzinfo=timezone.utc)


def at(hours_ago: float, base: datetime = NOW) -> datetime:
    return base - timedelta(hours=hours_ago)


def job(
    start: datetime,
    minutes: float,
    name: str = "work",
    workflow: str = ".github/workflows/x.yml",
    event: str = "push",
    wait_s: float = 5,
    status: str = "completed",
    conclusion: Optional[str] = "success",
    run_id: str = "1",
    run_created: Optional[datetime] = None,
) -> g.Job:
    return g.Job(
        workflow,
        name,
        event,
        run_id,
        start - timedelta(seconds=wait_s),
        start,
        start + timedelta(minutes=minutes) if status == "completed" else None,
        status,
        conclusion,
        run_created or start - timedelta(seconds=wait_s),
    )


def table_for(
    jobs: list[g.Job],
    runs: Optional[list[g.Run]] = None,
    coverage: Optional[datetime] = None,
    read_from: Optional[datetime] = None,
    now: datetime = NOW,
) -> list[g.HourRow]:
    last = g._floor(now)
    rows = g.hour_table(jobs, runs or [], coverage or last, read_from or last - timedelta(hours=26), now, CONTRACT)
    return g.pool_table(rows, CONTRACT)


def full_rows(
    count: int, slot: float = 1.0, merges: int = 0, waits: Optional[list[int]] = None, end: datetime = NOW
) -> list[g.HourRow]:
    last = g._floor(end)
    out = []
    for i in range(count, 0, -1):
        hour = last - timedelta(hours=i)
        out.append(
            g.HourRow(
                gh.iso(hour),
                True,
                slot_demand=slot,
                merges=merges,
                queue_waits_s=list(waits or []),
                queue_samples=len(waits or []),
                job_minutes={".github/workflows/x.yml|work|push": slot * 60},
            )
        )
    return g.pool_table(out, CONTRACT)


def read(table: list[g.HourRow], open_rec_clear: bool = True) -> g.Reading:
    return g.verdict(table, g.projection(table, CONTRACT), CONTRACT, NOW, no_open_rec=open_rec_clear)


def test_trigger_fires_above_16_slots_or_p90_queue_300s() -> None:
    hour = H0
    slots17 = [job(hour, 60, run_id=str(i)) for i in range(17)]
    assert read(table_for(slots17)).verdict == "red_measured"

    def with_queue(wait: int) -> g.Reading:
        base = [job(hour, 60, run_id=str(i)) for i in range(15)]
        mv = [
            job(hour + timedelta(minutes=i), 1, name="main-validate", workflow=CI, wait_s=wait, run_id=f"m{i}")
            for i in range(10)
        ]
        return read(table_for(base + mv))

    assert with_queue(301).verdict == "red_measured"
    assert with_queue(299).verdict != "red_measured"
    exact = [job(hour, 60, run_id=str(i)) for i in range(16)]
    assert read(table_for(exact)).verdict != "red_measured"


def test_open_hour_is_not_evaluated() -> None:
    rows = table_for([job(H0, 0, status="in_progress", conclusion=None)])
    assert next(r for r in rows if r.hour == gh.iso(H0)).evaluated is False
    queued = job(H0, 0, status="queued", conclusion=None)
    queued.started_at = None
    assert next(r for r in table_for([queued]) if r.hour == gh.iso(H0)).reason == "job_open"


def test_waiting_job_does_not_block_an_hour() -> None:
    for status in ("waiting", "pending"):
        waiting = job(H0, 0, status=status, conclusion=None)
        waiting.started_at = None
        assert next(r for r in table_for([waiting]) if r.hour == gh.iso(H0)).evaluated is True
    own = job(H0, 0, status="in_progress", conclusion=None, run_id="99")
    rows = g.hour_table([own], [], g._floor(NOW), g._floor(NOW) - timedelta(hours=26), NOW, CONTRACT, own_run_id="99")
    assert next(r for r in rows if r.hour == gh.iso(H0)).evaluated is True


def test_hour_past_coverage_is_not_evaluated() -> None:
    rows = table_for([], coverage=datetime(2026, 11, 11, 10, 30, tzinfo=timezone.utc))
    by = {r.hour: r.evaluated for r in rows}
    assert by[gh.iso(datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc))] is True
    assert by[gh.iso(datetime(2026, 11, 11, 10, 0, tzinfo=timezone.utc))] is False
    assert by[gh.iso(H0)] is False


def test_job_spanning_two_hours_splits_its_minutes() -> None:
    start = datetime(2026, 11, 11, 9, 30, tzinfo=timezone.utc)
    rows = {r.hour: r for r in table_for([job(start, 60)])}
    assert rows[gh.iso(datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc))].slot_demand == pytest.approx(0.5)
    assert rows[gh.iso(datetime(2026, 11, 11, 10, 0, tzinfo=timezone.utc))].slot_demand == pytest.approx(0.5)


def test_skipped_jobs_count_nothing() -> None:
    skipped = job(H0, 60, conclusion="skipped")
    assert next(r for r in table_for([skipped]) if r.hour == gh.iso(H0)).slot_demand == 0


def test_thin_hour_pools_24h_then_168h_or_is_insufficient() -> None:
    thin = full_rows(30, waits=[100])
    assert all(r.queue_pool == "24h" and r.queue_p90_s == 100 for r in thin[-5:])
    assert all(r.queue_p90_s is None for r in full_rows(30, waits=[]))
    sparse = full_rows(100)
    for i in range(0, 100, 30):
        sparse[i].queue_waits_s = [200] * 8
    pooled = g.pool_table(sparse, CONTRACT)
    assert pooled[-1].queue_pool == "168h" and pooled[-1].queue_p90_s == 200 and pooled[-1].queue_samples == 32


def test_insufficient_data_is_never_green() -> None:
    reading = read(full_rows(5))
    assert reading.verdict == "insufficient_data" and not all(reading.green_predicate.values())


def test_planned_row_leaves_projection_once_live() -> None:
    contract = CONTRACT.model_copy(deep=True)
    row = contract.ledger.rows[0]
    row.live_signal = JobRef(workflow=".github/workflows/persona.yml", job="verdict")
    table = full_rows(24, merges=3)
    assert row.id in g.projection(table, contract)[table[0].hour]
    table[5].job_minutes[".github/workflows/persona.yml|verdict|push"] = 12.0
    assert row.id not in g.projection(table, contract)[table[0].hour]


def test_analog_row_uses_measured_minutes_per_merge() -> None:
    table = full_rows(24, merges=2)
    for r in table:
        r.job_minutes[f"{CI}|main-validate|push"] = 10.0
    proj = g.projection(table, CONTRACT)
    assert proj[table[0].hour]["github-full-tier-per-ready-pr"] == pytest.approx(10.0)
    assert proj[table[0].hour]["sit-deploy-per-merge"] == 0


def test_projected_breach_is_red_projected_not_red_measured() -> None:
    table = full_rows(30, slot=12.0, merges=10, waits=[9])
    reading = read(table)
    assert reading.projected_peak > 16 and reading.verdict == "red_projected"


def test_merge_tables_never_replaces_evaluated_with_not_evaluated() -> None:
    old = g.HourRow(gh.iso(H0), True, slot_demand=3)
    bad = g.HourRow(gh.iso(H0), False, reason="job_open")
    assert g.merge_tables([old], [bad], CONTRACT)[0].evaluated is True
    good = g.HourRow(gh.iso(H0), True, slot_demand=5)
    assert g.merge_tables([old], [good], CONTRACT)[0].slot_demand == 5
    far = g.HourRow(gh.iso(H0 - timedelta(hours=200)), True)
    assert [r.hour for r in g.merge_tables([far, old], [], CONTRACT)] == [old.hour]


def test_start_hour_rereads_oldest_hole() -> None:
    rows = [g.HourRow(gh.iso(at(h)), h != 5 and h != 9) for h in (10, 9, 8, 7, 6, 5, 4)]
    rows = [g.HourRow(gh.iso(g._floor(NOW) - timedelta(hours=h)), h not in (9, 5)) for h in (10, 9, 8, 7, 6, 5, 4)]
    want = g._floor(NOW) - timedelta(hours=9) - timedelta(hours=2)
    assert g.start_hour(rows, NOW, CONTRACT) == want
    for r in rows:
        r.evaluated = True
    assert g.start_hour(rows, NOW, CONTRACT) == g._floor(NOW) - timedelta(hours=4) + timedelta(hours=1) - timedelta(hours=2)
    assert g.start_hour(None, NOW, CONTRACT) == g._floor(NOW) - timedelta(hours=26)


def test_rerun_attempt_queue_uses_job_created_at() -> None:
    rerun = job(H0 + timedelta(minutes=5), 1, name="main-validate", workflow=CI, wait_s=4, run_created=H0 - timedelta(hours=1))
    table = table_for([rerun] * 10)
    row = next(r for r in table if r.hour == gh.iso(H0))
    assert row.queue_p90_s == 4 and row.queue_waits_s == [4] * 10


def test_lead_in_hours_emit_no_rows() -> None:
    now = datetime(2026, 11, 11, 15, 30, tzinfo=timezone.utc)
    rf = datetime(2026, 11, 11, 10, 0, tzinfo=timezone.utc)
    rows = g.hour_table([], [], g._floor(now), rf, now, CONTRACT)
    assert [r.hour for r in rows][0] == gh.iso(rf + timedelta(hours=2))
    assert len(rows) == 3


class _EmptyCaller:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def __call__(self, url: str) -> Any:
        self.urls.append(url)
        return {"workflow_runs": [], "jobs": []}


def test_chained_ticks_never_move_start_backward() -> None:
    prev: Optional[list[g.HourRow]] = None
    starts = []
    for k in range(6):
        now = NOW + timedelta(hours=k * 3)
        start = g.start_hour(prev, now, CONTRACT)
        starts.append(start)
        caller = _EmptyCaller()
        budget = gh.Budget(CONTRACT.window.request_budget_per_tick, CONTRACT.window.time_budget_seconds)
        fetched = gh.fetch_runs_and_jobs(caller, start, budget, now, "o", "r")
        assert fetched.calls <= CONTRACT.window.request_budget_per_tick
        fresh = g.hour_table(*g.normalize(fetched)[::-1], fetched.coverage, start, now, CONTRACT)
        prev = g.pool_table(g.merge_tables(prev or [], fresh, CONTRACT), CONTRACT)
    assert starts[1:] == sorted(starts[1:]) and starts[1] > starts[0]
    assert all(b > a for a, b in zip(starts[1:], starts[2:]))


def test_previous_table_hours_feed_pooling_analog_and_live_signal() -> None:
    contract = CONTRACT.model_copy(deep=True)
    contract.ledger.rows[0].live_signal = JobRef(workflow=".github/workflows/persona.yml", job="verdict")
    old = full_rows(40, merges=2, waits=[7] * 3)
    for r in old[:20]:
        r.job_minutes[f"{CI}|main-validate|push"] = 6.0
    old[3].job_minutes[".github/workflows/persona.yml|verdict|push"] = 9.0
    fresh = [g.HourRow(gh.iso(g._floor(NOW) - timedelta(hours=0, minutes=60)), True, merges=2)]
    table = g.pool_table(g.merge_tables(old, fresh, contract), contract)
    assert table[-1].queue_p90_s == 7 and table[-1].queue_pool in ("24h", "168h")
    proj = g.projection(table, contract)
    assert contract.ledger.rows[0].id not in proj[table[-1].hour]
    assert proj[table[-1].hour]["github-full-tier-per-ready-pr"] > 0


def test_breach_outranks_insufficient_data() -> None:
    table = full_rows(3, slot=17.0)
    assert read(table).verdict == "red_measured"


def _filled(**kw: Any) -> list[g.HourRow]:
    return full_rows(168, **kw)


def test_every_reading_has_exactly_one_verdict() -> None:
    known = {"green", "red_measured", "red_projected", "insufficient_data", "amber"}
    cases: dict[str, tuple[list[g.HourRow], bool, str]] = {}
    cases["green"] = (_filled(waits=[9]), True, "green")
    cases["open_rec"] = (_filled(waits=[9]), False, "amber")
    cases["filling"] = (full_rows(30, waits=[9]), True, "amber")
    cases["few"] = (full_rows(5, waits=[9]), True, "insufficient_data")
    breach = _filled(waits=[9])
    breach[-1].slot_demand = 17
    cases["breach_now"] = (breach, True, "red_measured")
    older_q = _filled(waits=[9])
    older_q[10].queue_p90_s = 400.0
    cases["older_queue_breach"] = (older_q, True, "amber")
    older_s = _filled(waits=[9])
    older_s[10].slot_demand = 17
    cases["older_slot_breach"] = (older_s, True, "red_projected")
    for name, (table, clear, want) in cases.items():
        got = read(table, clear).verdict
        assert got in known and got == want, name


def test_late_start_minutes_reported() -> None:
    late = job(H0, 30, run_created=H0 - timedelta(hours=3))
    ok = job(H0, 30)
    assert g.late_start_minutes([late, ok], NOW, CONTRACT) == pytest.approx(30)


def test_green_needs_every_predicate_leg() -> None:
    table = _filled(waits=[9])
    assert read(table, True).verdict == "green"
    assert read(table, False).verdict != "green"
    assert read(table[30:], True).verdict != "green"
    table2 = _filled(waits=[9], merges=40)
    assert read(table2, True).verdict != "green"


@pytest.fixture
def cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    state: dict[str, Any] = {"open": False, "act": "none/none", "act_calls": 0, "prev": None, "raise_fetch": False}

    class FakeCaller:
        rate_remaining = 4321

        def __init__(self, token: str) -> None:
            pass

    def fetch(caller: Any, since: datetime, budget: Any, now: datetime, owner: Any = None, repo: Any = None) -> gh.Fetched:
        if state["raise_fetch"]:
            return gh.Fetched([], [], since, 0, 0.0, "HTTPError")
        return gh.Fetched([], [], g._floor(now), 3, 0.2)

    def act(*a: Any, **k: Any) -> str:
        state["act_calls"] += 1
        if state.get("act_raises"):
            raise RuntimeError("portal down")
        return state["act"]

    monkeypatch.setattr(gh, "JsonCaller", FakeCaller)
    monkeypatch.setattr(gh, "make_artifact_fetcher", lambda token: lambda url: b"")
    monkeypatch.setattr(gh, "job_seconds_left", lambda *a, **k: None)
    monkeypatch.setattr(gh, "previous_table", lambda *a, **k: state["prev"])
    monkeypatch.setattr(gh, "fetch_runs_and_jobs", fetch)
    monkeypatch.setattr(alarm, "open_rec_exists", lambda *a, **k: state["open"])
    monkeypatch.setattr(alarm, "act", act)
    monkeypatch.setenv("GITHUB_RUN_ID", "77")
    monkeypatch.setenv("GH_TOKEN", "t")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    state["out"] = tmp_path / "actions-capacity-reading.json"
    return state


def test_warehouse_read_failure_exits_zero_and_is_not_green(cli: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    cli["open"] = None
    assert g.main(["--reading-out", str(cli["out"])]) == 0
    payload = json.loads(cli["out"].read_text())
    assert payload["verdict"] != "green" and cli["act_calls"] == 0 and payload["read_failed_ticks"] == 1
    assert "alarm=skipped_read_failed" in capsys.readouterr().out


def test_main_exits_zero_on_read_failure_and_one_on_write_failure(cli: dict[str, Any]) -> None:
    cli["raise_fetch"] = True
    assert g.main(["--reading-out", str(cli["out"])]) == 0
    assert cli["out"].exists()
    cli["act_raises"] = True
    cli["out"].unlink()
    assert g.main(["--reading-out", str(cli["out"])]) == 1
    assert cli["out"].exists()


def test_assert_clear_mode(tmp_path: Path) -> None:
    rows = [asdict_row(r) for r in full_rows(24, waits=[9])]
    path = tmp_path / "r.json"
    path.write_text(json.dumps({"generated_at": gh.iso(NOW), "table": rows}))
    assert g.main(["--assert-clear", "capacity_breach", "--reading", str(path)]) == 0
    assert g.main(["--assert-clear", "gauge_blind", "--reading", str(path)]) == 0
    rows[-1]["slot_demand"] = 18
    path.write_text(json.dumps({"generated_at": gh.iso(NOW), "table": rows}))
    assert g.main(["--assert-clear", "capacity_breach", "--reading", str(path)]) == 1
    assert g.main(["--assert-clear", "gauge_blind", "--reading", str(path)]) == 0
    path.write_text(json.dumps({"generated_at": gh.iso(NOW), "table": rows[-3:]}))
    assert g.main(["--assert-clear", "gauge_blind", "--reading", str(path)]) == 1


def asdict_row(row: g.HourRow) -> dict[str, Any]:
    from dataclasses import asdict

    return asdict(row)


def test_read_failed_ticks_carry_and_reset(cli: dict[str, Any]) -> None:
    cli["prev"] = {"table": [], "read_failed_ticks": 2}
    cli["open"] = None
    g.main(["--reading-out", str(cli["out"])])
    assert json.loads(cli["out"].read_text())["read_failed_ticks"] == 3
    cli["open"] = False
    g.main(["--reading-out", str(cli["out"])])
    assert json.loads(cli["out"].read_text())["read_failed_ticks"] == 0


def test_status_line_shape(cli: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    g.main(["--reading-out", str(cli["out"])])
    line = capsys.readouterr().out.strip().splitlines()[-1]
    pattern = (
        r"^\[actions_capacity\] verdict=(green|red_measured|red_projected|insufficient_data|amber) evaluated_hours=\d+ "
        r"chained=[01] calls=\d+ elapsed_s=\d+ peak_slot_demand=[\d.]+ p90_queue_s=\S+ projected_peak=[\d.]+ "
        r"late_start_minutes=[\d.]+ alarm=\S+ rate_remaining=\S+ read_failed_ticks=\d+$"
    )
    assert re.match(pattern, line), line
