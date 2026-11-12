#!/usr/bin/env python3
"""GitHub REST reads for the Actions capacity gauge (rec-5022). Reads only, through an injected caller.

Budgets: a Budget counts calls against window.request_budget_per_tick and wall-clock against
window.time_budget_seconds, capped at the time left in the job less a reserve for everything after fetching. Any HTTP
error or timeout stops fetching and marks coverage; nothing retries in a loop. No I/O at import.
"""

from __future__ import annotations

import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from scripts import repo_identity

API = "https://api.github.com"
HEALTH_WORKFLOW = "convergence-health.yml"
ARTIFACT_NAME = "actions-capacity-reading"
ARTIFACT_MEMBER = "actions-capacity-reading.json"
PER_CALL_TIMEOUT_S = 30
JOB_TIMEOUT_MINUTES = 10
RESERVE_S = 90
PREVIOUS_RUNS = 5
PREVIOUS_CALL_CAP = 8
_NO_TOKEN = "GH_TOKEN is not set"

Caller = Callable[[str], Any]


def parse_ts(raw: Any) -> Optional[datetime]:
    if not raw or not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _open(request: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.build_opener(_NoRedirect).open(request, timeout=timeout)  # noqa: S310


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


class JsonCaller:
    """GET url -> parsed JSON with the shared header shape; records the last X-RateLimit-Remaining."""

    def __init__(self, token: str) -> None:
        self._token = token
        self.rate_remaining: Optional[int] = None

    def __call__(self, url: str) -> Any:
        if not self._token:
            raise RuntimeError(_NO_TOKEN)
        request = urllib.request.Request(url, headers=_headers(self._token))
        with _open(request, PER_CALL_TIMEOUT_S) as resp:
            remaining = resp.headers.get("X-RateLimit-Remaining")
            self.rate_remaining = int(remaining) if remaining and remaining.isdigit() else self.rate_remaining
            return json.loads(resp.read())


def make_artifact_fetcher(token: str) -> Callable[[str], bytes]:
    """zip URL -> bytes. The zip endpoint answers 302 to blob storage, which refuses the Authorization header, so the
    redirect is not followed with credentials: the Location is fetched with no Authorization header at all."""

    def _fetch(url: str) -> bytes:
        if not token:
            raise RuntimeError(_NO_TOKEN)
        try:
            with _open(urllib.request.Request(url, headers=_headers(token)), PER_CALL_TIMEOUT_S) as resp:
                return bytes(resp.read())
        except urllib.error.HTTPError as exc:
            location = exc.headers.get("Location") if exc.code in (301, 302, 303, 307, 308) else None
            if not location:
                raise
        with _open(urllib.request.Request(location), PER_CALL_TIMEOUT_S) as resp:
            return bytes(resp.read())

    return _fetch


@dataclass
class Budget:
    request_limit: int
    time_limit_s: float
    job_seconds_left: Optional[float] = None
    clock: Callable[[], float] = time.monotonic
    calls: int = 0
    stopped: Optional[str] = None
    _t0: float = field(init=False)

    def __post_init__(self) -> None:
        self._t0 = self.clock()

    @property
    def elapsed_s(self) -> float:
        return self.clock() - self._t0

    @property
    def deadline_s(self) -> float:
        if self.job_seconds_left is None:
            return self.time_limit_s
        return min(self.time_limit_s, self.job_seconds_left - RESERVE_S)

    def take(self) -> bool:
        if self.stopped:
            return False
        if self.calls >= self.request_limit:
            self.stopped = "request_budget"
            return False
        if self.elapsed_s >= self.deadline_s:
            self.stopped = "time_budget"
            return False
        self.calls += 1
        return True


def job_seconds_left(caller: Caller, budget: Budget, run_id: str, now: datetime, owner: str, repo: str) -> Optional[float]:
    """Seconds left in this job: its started_at (read once) plus JOB_TIMEOUT_MINUTES, minus now. None when unreadable."""
    if not run_id or not budget.take():
        return None
    try:
        data = caller(f"{API}/repos/{owner}/{repo}/actions/runs/{run_id}/jobs?per_page=100")
        jobs = data.get("jobs", []) if isinstance(data, dict) else []
        starts = [s for s in (parse_ts(j.get("started_at")) for j in jobs if isinstance(j, dict)) if s]
        if not starts:
            return None
        return (min(starts) + timedelta(minutes=JOB_TIMEOUT_MINUTES) - now).total_seconds()
    except Exception as exc:  # noqa: BLE001
        print(f"[actions_capacity] job start unreadable: {type(exc).__name__}")
        return None


def previous_table(
    caller: Caller,
    fetch_artifact: Callable[[str], bytes],
    budget: Budget,
    owner: Optional[str] = None,
    repo: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """The newest readable reading among the workflow's last PREVIOUS_RUNS completed main runs, else None (never fatal)."""
    owner, repo = repo_identity.resolve(owner, repo)
    full = f"{owner}/{repo}"
    spent = 0

    def take() -> bool:
        nonlocal spent
        if spent >= PREVIOUS_CALL_CAP or not budget.take():
            return False
        spent += 1
        return True

    try:
        if not take():
            return None
        url = (
            f"{API}/repos/{full}/actions/workflows/{HEALTH_WORKFLOW}/runs"
            f"?branch=main&status=completed&per_page={PREVIOUS_RUNS}"
        )
        runs = [r for r in (caller(url) or {}).get("workflow_runs", []) if _is_main_run(r, full)][:PREVIOUS_RUNS]
        for run in runs:
            if not take():
                return None
            listing = caller(f"{API}/repos/{full}/actions/runs/{run['id']}/artifacts?name={ARTIFACT_NAME}")
            for art in (listing or {}).get("artifacts", []):
                if art.get("name") != ARTIFACT_NAME or art.get("expired") or not _artifact_from_main(art, full):
                    continue
                if not take():
                    return None
                parsed = _parse_artifact(fetch_artifact(art["archive_download_url"]))
                if parsed is not None:
                    return parsed
                print(f"[actions_capacity] previous artifact of run {run['id']} unparsable")
    except Exception as exc:  # noqa: BLE001
        print(f"[actions_capacity] previous reading unreadable: {type(exc).__name__}")
    return None


def _is_main_run(run: Any, full: str) -> bool:
    if not isinstance(run, dict) or run.get("head_branch") != "main":
        return False
    head = run.get("head_repository") or {}
    return head.get("full_name") == full


def _artifact_from_main(art: dict[str, Any], full: str) -> bool:
    wr = art.get("workflow_run") or {}
    return not wr or wr.get("head_branch") == "main"


def _parse_artifact(blob: bytes) -> Optional[dict[str, Any]]:
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as bundle:
            payload = json.loads(bundle.read(ARTIFACT_MEMBER).decode("utf-8"))
    except (zipfile.BadZipFile, KeyError, ValueError, OSError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("table"), list):
        return None
    return payload


@dataclass
class Fetched:
    runs: list[dict[str, Any]]
    jobs: list[dict[str, Any]]
    coverage: datetime
    calls: int
    elapsed_s: float
    error: Optional[str] = None


def _hour_floor(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def fetch_runs_and_jobs(
    caller: Caller,
    since: datetime,
    budget: Budget,
    now: datetime,
    owner: Optional[str] = None,
    repo: Optional[str] = None,
) -> Fetched:
    """Read runs created in [since, last closed hour end] forward one hour at a time, oldest first, then each kept run's
    jobs (every attempt). coverage is the end of the newest hour read in full (since when none was)."""
    owner, repo = repo_identity.resolve(owner, repo)
    base = f"{API}/repos/{owner}/{repo}/actions"
    cursor = _hour_floor(since)
    end = _hour_floor(now)
    coverage = cursor
    runs: list[dict[str, Any]] = []
    jobs: list[dict[str, Any]] = []
    calls0 = budget.calls
    error: Optional[str] = None
    try:
        while cursor < end:
            hour_end = cursor + timedelta(hours=1)
            hour_runs = _read_hour(caller, base, cursor, hour_end, budget)
            for run in hour_runs:
                if run.get("conclusion") == "skipped":
                    continue
                if not budget.take():
                    raise _Stop(budget.stopped or "budget")
                data = caller(f"{base}/runs/{run['id']}/jobs?filter=all&per_page=100")
                run_jobs = [j for j in (data or {}).get("jobs", []) if isinstance(j, dict)]
                runs.append(run)
                jobs.extend({**j, "_run": run} for j in run_jobs)
            coverage = hour_end
            cursor = hour_end
    except _Stop as stop:
        error = str(stop)
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}"
    return Fetched(runs, jobs, coverage, budget.calls - calls0, budget.elapsed_s, error)


class _Stop(Exception):
    pass


def _read_hour(caller: Caller, base: str, start: datetime, end: datetime, budget: Budget) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    window = urllib.parse.quote(f"{iso(start)}..{iso(end - timedelta(seconds=1))}", safe="")
    page = 1
    while True:
        if not budget.take():
            raise _Stop(budget.stopped or "budget")
        data = caller(f"{base}/runs?created={window}&per_page=100&page={page}")
        batch = [r for r in (data or {}).get("workflow_runs", []) if isinstance(r, dict)]
        out.extend(batch)
        if len(batch) < 100:
            return out
        page += 1
