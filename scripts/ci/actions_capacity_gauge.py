#!/usr/bin/env python3
"""GitHub Actions capacity gauge (rec-5022): pure computation plus the CLI.

Measures this repository's slot demand and main-validate queue wait per closed UTC hour from the Actions API, chains an
hourly table across ticks through an artifact, projects the planned-load ledger and reads one verdict against
docs/contracts/actions-capacity.yaml. Alarm, never a gate: exit 0 on every verdict and GitHub or warehouse read failure.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from scripts import repo_identity
from scripts.ci import actions_capacity_github as gh
from scripts.ci.actions_capacity_contract import Contract, JobRef, load_contract

CONTRACT_VERSION = 1
BLOCKING = frozenset({"queued", "requested", "in_progress"})
FILLED_OF_168 = 144


@dataclass
class Job:
    workflow: str
    name: str
    event: str
    run_id: str
    created_at: Optional[datetime]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    status: str
    conclusion: Optional[str]
    run_created_at: Optional[datetime] = None


@dataclass
class Run:
    id: str
    workflow: str
    event: str
    branch: str
    created_at: datetime
    conclusion: Optional[str] = None


@dataclass
class HourRow:
    hour: str
    evaluated: bool
    reason: Optional[str] = None
    slot_demand: float = 0.0
    peak_concurrency: int = 0
    queue_p90_s: Optional[float] = None
    queue_samples: int = 0
    queue_pool: Optional[str] = None
    merges: int = 0
    queue_waits_s: list[int] = field(default_factory=list)
    job_minutes: dict[str, float] = field(default_factory=dict)

    @property
    def start(self) -> datetime:
        return gh.parse_ts(self.hour) or datetime.min.replace(tzinfo=timezone.utc)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "HourRow":
        known = {k: raw[k] for k in HourRow.__dataclass_fields__ if k in raw}
        return cls(**known)


@dataclass
class Reading:
    verdict: str
    green_predicate: dict[str, bool]
    projected_peak: float
    evaluated_hours_24h: int
    peak_slot_demand: float
    p90_queue_s: Optional[float]
    breaching_hours: list[str]


def _floor(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def _wf(path: Any) -> str:
    return str(path or "").split("@", 1)[0]


def normalize(fetched: gh.Fetched) -> tuple[list[Run], list[Job]]:
    runs = [
        Run(
            str(r["id"]),
            _wf(r.get("path")),
            str(r.get("event")),
            str(r.get("head_branch")),
            gh.parse_ts(r.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
            r.get("conclusion"),
        )
        for r in fetched.runs
    ]
    jobs = []
    for j in fetched.jobs:
        run = j.get("_run") or {}
        jobs.append(
            Job(
                _wf(run.get("path")),
                str(j.get("name")),
                str(run.get("event")),
                str(j.get("run_id") or run.get("id")),
                gh.parse_ts(j.get("created_at")),
                gh.parse_ts(j.get("started_at")),
                gh.parse_ts(j.get("completed_at")),
                str(j.get("status")),
                j.get("conclusion"),
                gh.parse_ts(run.get("created_at")),
            )
        )
    return runs, jobs


def nearest_rank(values: list[float], q: float = 0.9) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    return float(ordered[max(0, math.ceil(q * len(ordered)) - 1)])


def _span(job: Job, now: datetime) -> Optional[tuple[datetime, datetime]]:
    if job.started_at is None or job.conclusion == "skipped":
        return None
    return job.started_at, job.completed_at or now


def _minutes_in(span: tuple[datetime, datetime], lo: datetime, hi: datetime) -> float:
    start, end = max(span[0], lo), min(span[1], hi)
    return max(0.0, (end - start).total_seconds() / 60.0)


def _blocks(job: Job, lo: datetime, hi: datetime, own_run: Optional[str]) -> bool:
    if job.status not in BLOCKING or (own_run and job.run_id == own_run):
        return False
    start = job.started_at or job.created_at
    return start is not None and start < hi


def _peak(spans: Iterable[tuple[datetime, datetime]], lo: datetime, hi: datetime) -> int:
    events: list[tuple[datetime, int]] = []
    for s, e in spans:
        s, e = max(s, lo), min(e, hi)
        if e > s:
            events += [(s, 1), (e, -1)]
    cur = best = 0
    for _, delta in sorted(events, key=lambda ev: (ev[0], ev[1])):
        cur += delta
        best = max(best, cur)
    return best


def _is_queue_job(job: Job, contract: Contract) -> bool:
    ref = contract.trigger.queue_p90.job
    return job.workflow == ref.workflow and job.name == ref.job and (ref.event is None or job.event == ref.event)


def hour_table(
    jobs: list[Job],
    runs: list[Run],
    coverage: datetime,
    read_from: datetime,
    now: datetime,
    contract: Contract,
    own_run_id: Optional[str] = None,
) -> list[HourRow]:
    """One row per closed UTC hour from read_from + overlap_hours up to now (the lead-in hours are read context only)."""
    overlap = timedelta(hours=contract.window.overlap_hours)
    hour = _floor(read_from) + overlap
    last = _floor(now)
    rows: list[HourRow] = []
    while hour < last:
        end = hour + timedelta(hours=1)
        row = HourRow(gh.iso(hour), evaluated=True)
        if coverage < end or read_from > hour - overlap:
            row.evaluated, row.reason = False, "not_read_in_full"
        elif any(_blocks(j, hour, end, own_run_id) for j in jobs):
            row.evaluated, row.reason = False, "job_open"
        spans = []
        waits: list[int] = []
        minutes: dict[str, float] = {}
        for j in jobs:
            span = _span(j, now)
            if span is None:
                continue
            inside = _minutes_in(span, hour, end)
            if inside > 0:
                spans.append(span)
                key = f"{j.workflow}|{j.name}|{j.event}"
                minutes[key] = round(minutes.get(key, 0.0) + inside, 6)
            if _is_queue_job(j, contract) and j.created_at and hour <= span[0] < end:
                waits.append(max(0, int((span[0] - j.created_at).total_seconds())))
        row.job_minutes = minutes
        row.slot_demand = round(sum(minutes.values()) / 60.0, 6)
        row.peak_concurrency = _peak(spans, hour, end)
        row.queue_waits_s = waits
        row.queue_samples = len(waits)
        if len(waits) >= contract.validity.min_queue_samples_per_hour:
            row.queue_p90_s, row.queue_pool = nearest_rank([float(w) for w in waits]), "hour"
        row.merges = sum(
            1
            for r in runs
            if r.workflow.endswith("ci.yml") and r.event == "push" and r.branch == "main" and hour <= r.created_at < end
        )
        rows.append(row)
        hour = end
    return rows


def merge_tables(previous: list[HourRow], fresh: list[HourRow], contract: Contract) -> list[HourRow]:
    merged = {r.hour: r for r in previous}
    for row in fresh:
        old = merged.get(row.hour)
        if old is not None and old.evaluated and not row.evaluated:
            continue
        merged[row.hour] = row
    ordered = [merged[h] for h in sorted(merged)]
    if not ordered:
        return ordered
    cutoff = ordered[-1].start - timedelta(hours=contract.window.table_hours - 1)
    return [r for r in ordered if r.start >= cutoff]


def pool_table(table: list[HourRow], contract: Contract) -> list[HourRow]:
    """Give every thin evaluated hour the p90 of the narrowest trailing pool that meets its floor (nearest rank)."""
    v = contract.validity
    for row in table:
        if not row.evaluated or row.queue_pool == "hour" and row.queue_samples >= v.min_queue_samples_per_hour:
            continue
        row.queue_p90_s, row.queue_pool = None, None
        for name, hours, floor in (("24h", 24, v.pooled_min_samples_24h), ("168h", 168, v.pooled_min_samples_168h)):
            lo = row.start - timedelta(hours=hours - 1)
            waits = [w for r in table if r.evaluated and lo <= r.start <= row.start for w in r.queue_waits_s]
            if len(waits) >= floor:
                row.queue_p90_s, row.queue_pool, row.queue_samples = nearest_rank([float(w) for w in waits]), name, len(waits)
                break
    return table


def start_hour(previous: Optional[list[HourRow]], now: datetime, contract: Contract) -> datetime:
    w = contract.window
    overlap = timedelta(hours=w.overlap_hours)
    cutoff = _floor(now) - timedelta(hours=w.table_hours)
    rows = [r for r in previous or [] if r.start >= cutoff]
    holes = [r for r in rows if not r.evaluated]
    if holes:
        return min(r.start for r in holes) - overlap
    evaluated = [r for r in rows if r.evaluated]
    if evaluated:
        return max(r.start for r in evaluated) + timedelta(hours=1) - overlap
    return _floor(now) - timedelta(hours=w.bootstrap_hours) - overlap


def _matches(key: str, ref: JobRef) -> bool:
    workflow, name, event = (key.split("|") + ["", "", ""])[:3]
    return workflow == ref.workflow and ref.job in ("*", name) and (ref.event is None or ref.event == event)


def projection(table: list[HourRow], contract: Contract) -> dict[str, dict[str, float]]:
    """Projected job-minutes per hour for each ledger row not yet live: {hour: {row id: minutes}}."""
    evaluated = [r for r in table if r.evaluated]
    keys = {k for r in table for k in r.job_minutes}
    total_merges = sum(r.merges for r in evaluated)
    out: dict[str, dict[str, float]] = {r.hour: {} for r in evaluated}
    for led in contract.ledger.rows:
        if led.live_signal is not None and any(_matches(k, led.live_signal) for k in keys):
            continue
        if led.source == "assumed":
            per_merge = led.job_minutes_per_unit or 0.0
        else:
            spent = sum(
                m for r in evaluated for k, m in r.job_minutes.items() if any(_matches(k, a) for a in led.analog or [])
            )
            per_merge = (spent / total_merges if total_merges else 0.0) * (led.multiplier or 1.0)
        for r in evaluated:
            out[r.hour][led.id] = per_merge * r.merges
    return out


def _exceeds(row: HourRow, contract: Contract) -> bool:
    return row.slot_demand > contract.slot_threshold or (
        row.queue_p90_s is not None and row.queue_p90_s > contract.queue_threshold_s
    )


def trailing(table: list[HourRow], now: datetime, hours: int = 24) -> list[HourRow]:
    last = _floor(now)
    return [r for r in table if r.evaluated and last - timedelta(hours=hours) <= r.start < last]


def breaching_hours(table: list[HourRow], now: datetime, contract: Contract) -> list[str]:
    return [r.hour for r in trailing(table, now) if _exceeds(r, contract)]


def class_clear(cls: str, table: list[HourRow], now: datetime, contract: Contract) -> bool:
    enough = len(trailing(table, now)) >= contract.validity.min_evaluated_hours_24h
    return enough if cls == "gauge_blind" else enough and not breaching_hours(table, now, contract)


def verdict(
    table: list[HourRow],
    proj: dict[str, dict[str, float]],
    contract: Contract,
    now: datetime,
    no_open_rec: bool = False,
) -> Reading:
    evaluated = [r for r in table if r.evaluated]
    last = _floor(now)
    in24 = trailing(table, now)
    breach24 = [r for r in in24 if _exceeds(r, contract)]
    filled = sum(1 for r in evaluated if last - timedelta(hours=168) <= r.start < last) >= FILLED_OF_168
    peak = max((r.slot_demand + sum(proj.get(r.hour, {}).values()) / 60.0 for r in evaluated), default=0.0)
    samples = sum(len(r.queue_waits_s) for r in evaluated)
    queue_blind = all(r.queue_p90_s is None for r in evaluated) and samples < contract.validity.pooled_min_samples_168h
    legs = {
        "fresh": True,
        "table_filled": filled,
        "no_open_rec": no_open_rec,
        "no_evaluated_breach": not any(_exceeds(r, contract) for r in evaluated),
        "projected_peak_ok": peak <= contract.slot_threshold,
    }
    if breach24:
        v = "red_measured"
    elif len(in24) < contract.validity.min_evaluated_hours_24h or queue_blind:
        v = "insufficient_data"
    elif peak > contract.slot_threshold:
        v = "red_projected"
    elif all(legs.values()):
        v = "green"
    else:
        v = "amber"
    p90s = [r.queue_p90_s for r in in24 if r.queue_p90_s is not None]
    return Reading(
        v,
        legs,
        round(peak, 3),
        len(in24),
        max((r.slot_demand for r in in24), default=0.0),
        max(p90s) if p90s else None,
        [r.hour for r in breach24],
    )


def late_start_minutes(jobs: list[Job], now: datetime, contract: Contract) -> float:
    limit = timedelta(hours=contract.window.overlap_hours)
    total = 0.0
    for j in jobs:
        span = _span(j, now)
        if span and j.run_created_at and span[0] - j.run_created_at > limit:
            total += (span[1] - span[0]).total_seconds() / 60.0
    return round(total, 3)


def _summary(reading: dict[str, Any]) -> str:
    rows = reading["table"][-24:]
    lines = [
        "## Actions capacity (rec-5022)",
        (
            f"verdict **{reading['verdict']}**, evaluated hours (24h) {reading['evaluated_hours']}, "
            f"projected peak {reading['projected_peak']}, alarm {reading['alarm']}"
        ),
        "",
        "| hour | evaluated | slot demand | peak | queue p90 s | merges |",
        "|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {r['hour']} | {r['evaluated']} | {r['slot_demand']} | {r['peak_concurrency']} | "
        f"{r['queue_p90_s']} | {r['merges']} |"
        for r in rows
    ]
    return "\n".join(lines) + "\n"


def assert_clear(cls: str, path: str, contract: Contract) -> int:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    now = gh.parse_ts(payload.get("generated_at")) or datetime.now(timezone.utc)
    table = [HourRow.from_dict(r) for r in payload.get("table", [])]
    ok = class_clear(cls, table, now, contract)
    print(f"[actions_capacity] assert-clear {cls}: {'clear' if ok else 'not clear'}")
    return 0 if ok else 1


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="actions_capacity_gauge")
    parser.add_argument("--reading-out")
    parser.add_argument("--assert-clear")
    parser.add_argument("--reading")
    args = parser.parse_args(argv)
    contract = load_contract()
    if args.assert_clear:
        return assert_clear(args.assert_clear, args.reading or gh.ARTIFACT_MEMBER, contract)
    from scripts.ci import actions_capacity_alarm as alarm  # noqa: PLC0415

    now = datetime.now(timezone.utc).replace(microsecond=0)
    token = os.environ.get("GH_TOKEN", "") or os.environ.get("GITHUB_TOKEN", "")
    owner, repo = repo_identity.owner_and_name()
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    caller = gh.JsonCaller(token)
    budget = gh.Budget(contract.window.request_budget_per_tick, contract.window.time_budget_seconds)
    budget.job_seconds_left = gh.job_seconds_left(caller, budget, run_id, now, owner, repo)
    prev = gh.previous_table(caller, gh.make_artifact_fetcher(token), budget, owner, repo)
    prev_rows = [HourRow.from_dict(r) for r in (prev or {}).get("table", [])] if prev else None
    read_from = start_hour(prev_rows, now, contract)
    fetched = gh.fetch_runs_and_jobs(caller, read_from, budget, now, owner, repo)
    runs, jobs = normalize(fetched)
    fresh = hour_table(jobs, runs, fetched.coverage, read_from, now, contract, run_id or None)
    table = pool_table(merge_tables(prev_rows or [], fresh, contract), contract)
    proj = projection(table, contract)
    prior_failed = int((prev or {}).get("read_failed_ticks", 0) or 0)
    open_rec = alarm.open_rec_exists()
    reading = verdict(table, proj, contract, now, no_open_rec=open_rec is False)
    failed_ticks = prior_failed + 1 if open_rec is None else 0
    run_url = f"https://github.com/{owner}/{repo}/actions/runs/{run_id}"
    write_error: Optional[Exception] = None
    action = "skipped_read_failed"
    if open_rec is not None:
        try:
            action = alarm.act(
                reading, table, contract, now, run_url, diagnose=bool(os.environ.get("CONVERGENCE_HEALTH_DIAGNOSE"))
            )
        except Exception as exc:  # noqa: BLE001
            write_error, action = exc, "write_failed"
    payload = {
        "contract_version": CONTRACT_VERSION,
        "generated_at": gh.iso(now),
        "run_id": run_id,
        "coverage": gh.iso(fetched.coverage),
        "table": [asdict(r) for r in table],
        "projection": proj,
        "verdict": reading.verdict,
        "green_predicate": reading.green_predicate,
        "projected_peak": reading.projected_peak,
        "evaluated_hours": reading.evaluated_hours_24h,
        "late_start_minutes": late_start_minutes(jobs, now, contract),
        "calls": budget.calls,
        "elapsed_s": round(budget.elapsed_s),
        "read_failed_ticks": failed_ticks,
        "alarm": action,
        "fetch_error": fetched.error,
    }
    if args.reading_out:
        Path(args.reading_out).write_text(json.dumps(payload, indent=1), encoding="utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(_summary(payload))
    rate = getattr(caller, "rate_remaining", None)
    print(
        f"[actions_capacity] verdict={reading.verdict} evaluated_hours={sum(1 for r in table if r.evaluated)} "
        f"chained={int(prev is not None)} calls={budget.calls} elapsed_s={round(budget.elapsed_s)} "
        f"peak_slot_demand={reading.peak_slot_demand:.2f} p90_queue_s={reading.p90_queue_s} "
        f"projected_peak={reading.projected_peak:.2f} late_start_minutes={payload['late_start_minutes']} "
        f"alarm={action} rate_remaining={rate} read_failed_ticks={failed_ticks}"
    )
    if write_error is not None:
        print(f"[actions_capacity] portal write failed: {type(write_error).__name__}: {write_error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
