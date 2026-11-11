"""The Actions capacity gauge's GitHub reads: budgets, chaining and the artifact redirect (rec-5022). No network."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from email.message import Message
from typing import Any, Callable, Optional

import pytest

from scripts.ci import actions_capacity_gauge as g
from scripts.ci import actions_capacity_github as gh
from scripts.ci.actions_capacity_contract import load_contract

CONTRACT = load_contract()
NOW = datetime(2026, 11, 11, 12, 30, tzinfo=timezone.utc)
SINCE = datetime(2026, 11, 11, 8, 0, tzinfo=timezone.utc)


def run(i: int, hour: int, conclusion: str = "success") -> dict[str, Any]:
    return {
        "id": i,
        "path": ".github/workflows/ci.yml",
        "event": "push",
        "head_branch": "main",
        "created_at": f"2026-11-11T{hour:02d}:10:00Z",
        "conclusion": conclusion,
    }


class FakeCaller:
    def __init__(self, per_hour: int = 2, skipped: bool = False, fail_after: Optional[int] = None) -> None:
        self.urls: list[str] = []
        self.per_hour = per_hour
        self.skipped = skipped
        self.fail_after = fail_after

    def __call__(self, url: str) -> Any:
        self.urls.append(url)
        if self.fail_after is not None and len(self.urls) > self.fail_after:
            raise urllib.error.HTTPError(url, 403, "rate limited", Message(), None)
        decoded = urllib.parse.unquote(url)
        if "/jobs" in decoded:
            return {
                "jobs": [
                    {
                        "name": "j",
                        "run_id": 1,
                        "created_at": "2026-11-11T08:10:00Z",
                        "started_at": "2026-11-11T08:10:05Z",
                        "completed_at": "2026-11-11T08:12:00Z",
                        "status": "completed",
                        "conclusion": "success",
                    }
                ]
            }
        hour = int(decoded.split("created=2026-11-11T")[1][:2])
        runs = [run(hour * 10 + n, hour, "skipped" if self.skipped else "success") for n in range(self.per_hour)]
        return {"workflow_runs": runs}


def budget(
    limit: int = 400, seconds: float = 180, clock: Optional[Callable[[], float]] = None, left: Optional[float] = None
) -> gh.Budget:
    return gh.Budget(limit, seconds, left, clock or (lambda: 0.0))


def test_request_budget_stops_oldest_first_and_marks_coverage() -> None:
    caller = FakeCaller(per_hour=2)
    fetched = gh.fetch_runs_and_jobs(caller, SINCE, budget(limit=5), NOW, "o", "r")
    assert fetched.coverage == SINCE + timedelta(hours=1) and fetched.error == "request_budget"
    assert fetched.calls == 5 and "T08" in urllib.parse.unquote(caller.urls[0])
    full = gh.fetch_runs_and_jobs(FakeCaller(per_hour=0), SINCE, budget(), NOW, "o", "r")
    assert full.coverage == datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc) and full.error is None


def test_reads_every_attempt() -> None:
    caller = FakeCaller(per_hour=1)
    gh.fetch_runs_and_jobs(caller, SINCE, budget(), SINCE + timedelta(hours=1, minutes=5), "o", "r")
    assert any("filter=all" in u and "/jobs" in u for u in caller.urls)


def test_time_budget_stops_fetching() -> None:
    ticks = iter(range(0, 10_000, 100))
    b = gh.Budget(400, 180, None, lambda: float(next(ticks)))
    fetched = gh.fetch_runs_and_jobs(FakeCaller(), SINCE, b, NOW, "o", "r")
    assert fetched.error == "time_budget" and fetched.calls <= 2


def test_time_budget_caps_at_job_time_left() -> None:
    clock = {"t": 0.0}
    b = gh.Budget(400, 180, 100.0, lambda: clock["t"])
    assert b.deadline_s == 10.0 and b.take()
    clock["t"] = 11.0
    assert not b.take() and b.stopped == "time_budget"
    assert gh.Budget(400, 180, 1000.0).deadline_s == 180


def test_skipped_runs_are_not_expanded() -> None:
    caller = FakeCaller(per_hour=3, skipped=True)
    fetched = gh.fetch_runs_and_jobs(caller, SINCE, budget(), SINCE + timedelta(hours=1, minutes=5), "o", "r")
    assert not any("/jobs" in u for u in caller.urls) and fetched.runs == []


def test_since_resumes_from_previous_table_minus_overlap() -> None:
    prev = [g.HourRow("2026-11-11T09:00:00Z", True), g.HourRow("2026-11-11T10:00:00Z", True)]
    start = g.start_hour(prev, NOW, CONTRACT)
    assert start == datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc)
    caller = FakeCaller(per_hour=0)
    gh.fetch_runs_and_jobs(caller, start, budget(), NOW, "o", "r")
    assert "T09" in urllib.parse.unquote(caller.urls[0])


def test_bootstrap_without_previous_table() -> None:
    start = g.start_hour(None, NOW, CONTRACT)
    assert start == datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc)
    rows = g.hour_table([], [], g._floor(NOW), start, NOW, CONTRACT)
    assert len(rows) == 24 and rows[0].hour == "2026-11-10T12:00:00Z"


def _zip(payload: Any) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(gh.ARTIFACT_MEMBER, payload if isinstance(payload, str) else json.dumps(payload))
    return buf.getvalue()


class ChainCaller:
    def __init__(self, runs: list[dict[str, Any]], artifacts: dict[int, list[dict[str, Any]]]) -> None:
        self.urls: list[str] = []
        self.runs, self.artifacts = runs, artifacts

    def __call__(self, url: str) -> Any:
        self.urls.append(url)
        if "/workflows/" in url:
            return {"workflow_runs": self.runs}
        run_id = int(url.split("/runs/")[1].split("/")[0])
        return {"artifacts": self.artifacts.get(run_id, [])}


def _main_run(i: int, branch: str = "main", repo: str = "o/r") -> dict[str, Any]:
    return {"id": i, "head_branch": branch, "head_repository": {"full_name": repo}}


def _art(expired: bool = False) -> dict[str, Any]:
    return {
        "name": gh.ARTIFACT_NAME,
        "expired": expired,
        "archive_download_url": "https://x/zip",
        "workflow_run": {"head_branch": "main"},
    }


def test_previous_table_ignores_non_main_runs() -> None:
    caller = ChainCaller(
        [_main_run(1, "feature"), _main_run(2, repo="evil/fork"), _main_run(3)], {1: [_art()], 2: [_art()], 3: [_art()]}
    )
    seen: list[str] = []

    def fetch(url: str) -> bytes:
        seen.append(url)
        return _zip({"table": [{"hour": "h"}], "generated_at": "x"})

    got = gh.previous_table(caller, fetch, budget(), "o", "r")
    assert got is not None and "branch=main" in caller.urls[0] and "status=completed" in caller.urls[0]
    assert not any("/runs/1/" in u or "/runs/2/" in u for u in caller.urls) and len(seen) == 1
    assert gh.previous_table(ChainCaller([_main_run(1, "feature")], {}), fetch, budget(), "o", "r") is None


def test_unparsable_previous_artifact_falls_back_to_bootstrap() -> None:
    for blob in (b"not a zip", _zip("not json"), _zip({"no_table": 1})):
        caller = ChainCaller([_main_run(3)], {3: [_art()]})
        assert gh.previous_table(caller, lambda url, b=blob: b, budget(), "o", "r") is None
    assert g.start_hour(None, NOW, CONTRACT) == datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc)


def test_http_error_stops_without_retry() -> None:
    caller = FakeCaller(per_hour=2, fail_after=2)
    fetched = gh.fetch_runs_and_jobs(caller, SINCE, budget(), NOW, "o", "r")
    assert fetched.error == "HTTPError" and len(caller.urls) == 3 and fetched.coverage == SINCE


def test_artifact_redirect_is_fetched_without_authorization(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, Optional[str]]] = []

    class Resp:
        def __init__(self, body: bytes) -> None:
            self.body = body

        def __enter__(self) -> "Resp":
            return self

        def __exit__(self, *a: Any) -> None:
            return None

        def read(self) -> bytes:
            return self.body

    def fake_open(request: urllib.request.Request, timeout: float) -> Resp:
        seen.append((request.full_url, request.get_header("Authorization")))
        if len(seen) == 1:
            headers = Message()
            headers["Location"] = "https://blob.example/zip?sig=1"
            raise urllib.error.HTTPError(request.full_url, 302, "Found", headers, None)
        return Resp(b"ZIPBYTES")

    monkeypatch.setattr(gh, "_open", fake_open)
    assert gh.make_artifact_fetcher("tok")("https://api.github.com/zip") == b"ZIPBYTES"
    assert seen[0][1] == "Bearer tok" and seen[1] == ("https://blob.example/zip?sig=1", None)
