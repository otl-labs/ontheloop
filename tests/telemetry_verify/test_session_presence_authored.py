"""The arrival oracle's author-date rule for lost windows (rec-5084, Decision 235)."""

from __future__ import annotations

import http.client
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from scripts.telemetry_verify import session_presence as sp

NOW = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
EXPECT = {"kind": "enforced", "sla": "60m", "check": {"workflow": "telemetry-arrival", "module": sp.MODULE}}
W_FROM, W_TO = datetime(2026, 11, 1, tzinfo=timezone.utc), datetime(2026, 11, 3, tzinfo=timezone.utc)
CONFIG: dict[str, Any] = {
    "judge_days": 3,
    "window_days": 7,
    "max_lookback_days": 56,
    "max_pr_lookups": 60,
    "collapse_min_commits": 3,
    "authored_grace_days": 7,
    "lost_windows": [
        {"to": "2026-10-28T00:00:00Z", "owner": "Decision 250", "reason": "x"},
        {"from": W_FROM.isoformat(), "to": W_TO.isoformat(), "owner": "rec-5055", "reason": "x"},
    ],
    "excused_sessions": [],
}
ROOT = Path(__file__).resolve().parents[2]
GRACE = timedelta(days=7)
HOUR = timedelta(hours=1)


def commit(n: int, key: str | None, hours_ago: float = 10, *, pr: int | None = None, extra: str = "") -> sp.Commit:
    body = f"plan\n\nClaude-Session: https://claude.ai/code/session_{key}\n" if key else "plan\n"
    return sp.Commit(f"{n:040x}", NOW - timedelta(hours=hours_ago), f"msg ({'#' + str(pr if pr else n)})", body + extra)


def no_roots(day_from: str, day_to: str) -> list[dict[str, Any]]:
    return []


def two_roots(day_from: str, day_to: str) -> list[dict[str, Any]]:
    return [{"harness_session_key": "AAA"}]


class Boom(Exception):
    pass


class Reader:
    def named(self, *_: Any, **__: Any) -> list[dict[str, Any]]:
        return []


class Fakes:
    def __init__(self, created_at: datetime | Exception, authored: datetime | Exception | None) -> None:
        self.created_at, self.authored, self.keys = created_at, authored, []

    def pr(self, n: int) -> datetime:
        if isinstance(self.created_at, Exception):
            raise self.created_at
        return self.created_at

    def key(self, n: int, key: str) -> datetime | None:
        self.keys.append(key)
        if isinstance(self.authored, Exception):
            raise self.authored
        return self.authored


def judge(
    commits: list[sp.Commit], fakes: Fakes, config: Any = CONFIG, roots: Any = no_roots, authored: Any = "fake"
) -> sp.ArrivalResult:
    callback = fakes.key if authored == "fake" else authored
    return sp.judge(commits, roots, NOW, EXPECT, config, fakes.pr, pr_key_authored_at=callback)


def test_a_key_whose_own_commit_was_authored_in_a_lost_window_is_lost_window() -> None:
    fakes = Fakes(W_TO + HOUR, W_FROM + HOUR)
    result = judge([commit(1, "AAA", pr=5)], fakes)
    assert (result.lost_window, result.lost_window_authored, result.missing, result.pr_lookups) == (1, 1, 0, 2)
    assert result.status == "green"
    text = sp.lines(result)
    assert f"ARRIVAL_LOST_WINDOW_AUTHORED sha={1:040x}"[:-28] in text[-1] and "AAA" not in "".join(text)
    import re

    assert re.search(r" elapsed_s=\S+ lost_window_authored=1 authored_lookup_failed=0$", text[0])
    two = Fakes(W_TO + HOUR, None)
    two.authored = W_FROM + HOUR
    both = commit(2, "AAA", extra="Claude-Session: https://claude.ai/code/session_BBB\n")
    result = judge([both], two, roots=two_roots)
    assert two.keys == ["BBB"] and result.lost_window_authored == 1 and result.missing == 0


@pytest.mark.parametrize(
    ("created_at", "authored", "expected"),
    [
        (W_TO + HOUR, W_FROM + HOUR, 1),
        (W_TO + HOUR, W_FROM, 1),
        (W_TO, W_FROM + HOUR, 1),
        (W_TO + GRACE - timedelta(seconds=1), W_FROM, 1),
        (W_TO + HOUR, W_TO + timedelta(seconds=1), 0),
        (W_TO + HOUR, W_TO, 0),
        (W_TO + HOUR, None, 0),
        (W_TO + HOUR, W_FROM - timedelta(seconds=1), 0),
        (W_TO + GRACE, W_FROM + HOUR, 0),
        (W_FROM - HOUR, W_FROM + HOUR, 0),
        (datetime(2026, 10, 28, 1, tzinfo=timezone.utc), datetime(2026, 10, 27, 23, tzinfo=timezone.utc), 0),
    ],
)
def test_the_author_date_rule_holds_its_approved_bounds(created_at: datetime, authored: Any, expected: int) -> None:
    result = judge([commit(1, "AAA", pr=5)], Fakes(created_at, authored))
    assert (result.lost_window, result.missing) == (expected, 1 - expected)


def test_the_grace_is_measured_from_the_window_the_commit_was_authored_in() -> None:
    a_from, a_to = datetime(2026, 10, 20, tzinfo=timezone.utc), datetime(2026, 10, 22, tzinfo=timezone.utc)
    windows = [
        {"from": a_from.isoformat(), "to": a_to.isoformat(), "owner": "rec-1111", "reason": "x"},
        *CONFIG["lost_windows"][1:],
    ]
    config = {**CONFIG, "lost_windows": windows}
    kept = judge([commit(1, "AAA", pr=5)], Fakes(a_to + HOUR, a_from + HOUR), config)
    assert (kept.lost_window, kept.missing) == (1, 0)
    past = judge([commit(1, "AAA", pr=5)], Fakes(W_TO + HOUR, a_from + HOUR), config)
    assert (past.lost_window, past.missing) == (0, 1)


@pytest.mark.parametrize(
    "error", [sp.Unreadable("X"), OSError("x"), ValueError("x"), KeyError("commit"), http.client.IncompleteRead(b""), Boom()]
)
def test_an_authored_lookup_failure_is_missing_and_the_cap_bounds_both_lookups(error: Exception) -> None:
    result = judge([commit(1, "AAA", pr=5)], Fakes(W_TO + HOUR, error))
    assert (result.authored_lookup_failed, result.pr_lookup_failed, result.missing, result.status) == (1, 0, 1, "red")
    capped = judge(
        [commit(1, "AAA", pr=5), commit(2, "BBB", pr=6)], Fakes(W_TO + HOUR, error), {**CONFIG, "max_pr_lookups": 1}
    )
    assert capped.pr_lookups == 1 and capped.authored_lookup_failed == 0 and capped.missing == 2
    fakes = Fakes(OSError("x"), W_FROM + HOUR)
    down = judge([commit(1, "AAA", pr=5)], fakes)
    assert (down.pr_lookups, down.pr_lookup_failed, fakes.keys, down.missing) == (1, 1, [], 1)


def test_the_author_date_rule_is_off_without_its_config_key_or_callable() -> None:
    config = {k: v for k, v in CONFIG.items() if k != "authored_grace_days"}
    fakes = Fakes(W_TO + HOUR, W_FROM + HOUR)
    assert judge([commit(1, "AAA", pr=5)], fakes, config).missing == 1
    assert judge([commit(1, "AAA", pr=5)], fakes, authored=None).missing == 1
    assert fakes.keys == []


def test_a_created_in_window_pr_makes_no_authored_lookup() -> None:
    fakes = Fakes(W_FROM + HOUR, W_FROM + HOUR)
    result = judge([commit(1, "AAA", pr=5)], fakes)
    assert (result.lost_window, result.lost_window_authored, result.pr_lookups, fakes.keys) == (1, 0, 1, [])


@pytest.mark.parametrize("bad", [0, True, "7"])
def test_a_bad_authored_grace_days_is_unreadable(bad: Any) -> None:
    result = judge([commit(1, "AAA", pr=5)], Fakes(W_TO, W_FROM), {**CONFIG, "authored_grace_days": bad})
    assert (result.status, result.cause) == ("unreadable", "BadConfig")


class Response:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self, *_: Any) -> bytes:
        return self.payload


def item(n: int, key: str, author: str | None, *, top: bool = True) -> dict[str, Any]:
    message = f"x\n\nClaude-Session: https://claude.ai/code/session_{key}\n"
    when = {"author": {"date": author}} if author else {}
    return {
        "sha": f"{n:040x}",
        "author": {"login": "a"} if top else None,
        "commit": {"message": message, **when, "committer": {"date": "2026-12-01T00:00:00Z"}},
    }


def serve(monkeypatch: pytest.MonkeyPatch, pages: list[Any]) -> list[Any]:
    import json

    seen: list[Any] = []

    def fake(request: urllib.request.Request, timeout: int) -> Response:
        seen.append((request.full_url, request.get_header("Authorization"), timeout))
        return Response(json.dumps(pages[len(seen) - 1]).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    return seen


def filler(count: int) -> list[dict[str, Any]]:
    return [item(900 + i, "OTHER", "2026-11-01T00:00:00Z") for i in range(count)]


def test_github_adapter_reads_the_earliest_own_commit_author_date(monkeypatch: pytest.MonkeyPatch) -> None:
    page1 = [item(1, "KKK", "2026-11-10T16:00:00Z"), item(2, "OTHER", "2026-11-01T00:00:00Z"), *filler(98)]
    page2 = [
        item(3, "KKK", "2026-11-10T15:22:45Z", top=False),
        item(4, "KKK", "2026-11-10T14:52:45-01:00"),
        item(5, "KKK", "2026-11-11T09:00:00Z"),
    ]
    prose = {"sha": "p", "commit": {"message": "mentions KKK in prose", "author": {"date": "2026-11-01T00:00:00Z"}}}
    seen = serve(monkeypatch, [page1, [prose, *page2]])
    got = sp.github_pr_key_authored_at("o/r", 7, "KKK")
    assert got == datetime(2026, 11, 10, 15, 22, 45, tzinfo=timezone.utc)
    assert seen == [
        ("https://api.github.com/repos/o/r/pulls/7/commits?per_page=100&page=1", "Bearer tok", 10),
        ("https://api.github.com/repos/o/r/pulls/7/commits?per_page=100&page=2", "Bearer tok", 10),
    ]


def test_github_adapter_returns_none_when_no_commit_carries_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, [[item(1, "OTHER", "2026-11-01T00:00:00Z")]])
    assert sp.github_pr_key_authored_at("o/r", 7, "KKK") is None


@pytest.mark.parametrize(
    ("pages", "cause"),
    [
        ([filler(100), filler(100), filler(50)], "CommitListTruncated"),
        ([[item(1, "KKK", None)]], "NoAuthorDate"),
        ([[{"sha": "x", "commit": {}}]], "BadCommitMessage"),
        ([{"not": "a list"}], "BadCommitPage"),
        ([["a string"]], "BadCommitItem"),
    ],
)
def test_github_adapter_refuses_unreadable_evidence(monkeypatch: pytest.MonkeyPatch, pages: list[Any], cause: str) -> None:
    seen = serve(monkeypatch, pages)
    with pytest.raises(sp.Unreadable, match=cause):
        sp.github_pr_key_authored_at("o/r", 7, "KKK")
    assert len(seen) <= 3


def test_github_adapter_accepts_a_listing_one_short_of_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = serve(monkeypatch, [filler(100), filler(100), [item(1, "KKK", "2026-11-01T00:00:00Z"), *filler(48)]])
    assert sp.github_pr_key_authored_at("o/r", 7, "KKK") == W_FROM
    assert len(seen) == 3


def test_run_wires_the_github_authored_lookup_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    row = next(w for w in sp.load_config(ROOT)["lost_windows"] if w["owner"] == "rec-5055")
    start, end = datetime.fromisoformat(row["from"]), datetime.fromisoformat(row["to"])
    recorded: list[Any] = []
    monkeypatch.setattr(sp, "github_pr_created_at", lambda repo, n: end + HOUR)
    monkeypatch.setattr(sp, "github_pr_key_authored_at", lambda repo, n, k: recorded.append((repo, n, k)) or start + HOUR)
    result = sp.run(ROOT, Reader(), NOW, repo="o/r", commits=[commit(1, "KKK", pr=5)])
    assert recorded == [("o/r", 5, "KKK")] and result.lost_window_authored == 1
    explicit: list[Any] = []
    again = sp.run(
        ROOT,
        Reader(),
        NOW,
        repo="o/r",
        commits=[commit(1, "KKK", pr=5)],
        pr_key_authored_at=lambda n, k: explicit.append(k) or start,
    )
    assert explicit == ["KKK"] and recorded == [("o/r", 5, "KKK")] and again.lost_window_authored == 1


def test_the_shipped_config_enables_the_author_date_rule() -> None:
    config = sp.load_config(ROOT)
    sp.check_config(config)
    assert config["authored_grace_days"] == 7
