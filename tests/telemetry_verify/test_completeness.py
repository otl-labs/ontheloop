"""The completeness join, windowing and counts (PLAN-telemetry-verification-monitor, VP step 4)."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from scripts.telemetry_verify import completeness as cp

DAY = "2026-01-01"
IN_DAY = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
OTHER_DAY = datetime(2026, 1, 3, 12, tzinfo=timezone.utc)
URL = "https://github.com/benjamin-blake/theseus"


def _root(key: str | None, started: datetime = IN_DAY) -> dict[str, Any]:
    return {"harness_session_key": key, "session_started_at": started.isoformat()}


def _listed(key: str | None, *, at: datetime = IN_DAY, source: str = "thread", repo: str | None = None) -> cp.Listed:
    return cp.Listed(cp.normalise_key(key), at, source, repo)


def test_harness_key_join_normalises_both_prefixes() -> None:
    roots = [_root("session_AAA"), _root("cse_BBB")]
    listed = [_listed("cse_AAA"), _listed("session_BBB", source="sessions", repo=URL)]
    result = cp.compare_sessions(roots, listed, judged_day=DAY)
    assert (result.listed_and_captured, result.listed_not_captured, result.captured_not_listed) == (2, 0, 0)
    assert [cp.normalise_key(v) for v in ("session_X", "cse_X", "X", "session_", "", None, 5)] == [
        "X",
        "X",
        "X",
        None,
        None,
        None,
        None,
    ]


def test_join_comes_before_classification() -> None:
    roots = [_root("session_AAA")]
    in_other_repo = _listed("session_AAA", source="sessions", repo="https://github.com/someone/else")
    no_repo = _listed("session_AAA", source="sessions", repo=None)
    result = cp.compare_sessions(roots, [in_other_repo, no_repo], judged_day=DAY)
    assert result.listed_and_captured == 2 and result.listed_scope_unknown == 0 and result.listed_not_captured == 0


def test_only_the_judged_day_is_counted() -> None:
    roots = [_root("session_AAA"), _root("session_LATE", OTHER_DAY)]
    listed = [_listed("AAA"), _listed("OLD", at=OTHER_DAY), _listed("LATE", at=OTHER_DAY)]
    result = cp.compare_sessions(roots, listed, judged_day=DAY)
    assert (result.listed, result.listed_and_captured, result.listed_not_captured, result.captured_not_listed) == (1, 1, 0, 0)


def test_unjoinable_out_of_scope_and_keyless_are_counted_apart() -> None:
    roots = [_root(None), _root("session_ONLY")]
    listed = [
        _listed(None),
        _listed("session_", source="sessions", repo=URL),
        _listed("SCOPELESS", source="sessions", repo=None),
        _listed("ELSEWHERE", source="sessions", repo="https://github.com/someone/else"),
        _listed("MISSING", source="sessions", repo=URL + ".git"),
        _listed("THREAD", source="thread"),
    ]
    result = cp.compare_sessions(roots, listed, judged_day=DAY)
    assert result.listed_unjoinable == 2 and result.listed_scope_unknown == 1 and result.listed_not_captured == 2
    assert result.captured_without_key == 1 and result.captured_not_listed == 1
    assert result.listed == 5 and result.listed_and_captured == 0


def _write(tmp_path: Path, name: str, pages: Any) -> None:
    (tmp_path / name).write_text(json.dumps(pages), encoding="utf-8")


def test_both_raw_list_files_map(tmp_path: Path) -> None:
    _write(
        tmp_path,
        cp.THREAD_FILE,
        [
            {"next_cursor": "c", "sessions": [{"session_id": "cse_T1", "created_at": "2026-01-01T01:00:00Z"}]},
            {"sessions": [{"session_id": "cse_T2", "created_at": "2026-01-01T02:00:00Z"}]},
        ],
    )
    _write(
        tmp_path,
        cp.SESSIONS_FILE,
        [
            {
                "ccr": {
                    "has_more": False,
                    "data": [
                        {
                            "id": "session_S1",
                            "created_at": "2026-01-01T03:00:00.123456Z",
                            "session_context": {"sources": [{"git_repository": {"url": URL}}]},
                        },
                        {"id": "session_S2", "created_at": "2026-01-01T04:00:00Z", "session_context": {"sources": [{"x": 1}]}},
                        {"id": "session_S3", "created_at": "2026-01-01T05:00:00Z"},
                    ],
                }
            }
        ],
    )
    lists = cp.load_lists(tmp_path)
    assert [(r.key, r.source, r.repository) for r in lists.rows] == [
        ("T1", "thread", None),
        ("T2", "thread", None),
        ("S1", "sessions", URL),
        ("S2", "sessions", None),
        ("S3", "sessions", None),
    ]
    assert lists.thread_complete and lists.sessions_complete and cp.lists_cover(lists, DAY)


def test_only_listed_not_captured_is_an_alarm() -> None:
    result = cp.compare_sessions([_root("session_ONLY")], [_listed("MISSING")], judged_day=DAY)
    assert result.captured_not_listed == 1 and result.listed_not_captured == 1
    assert result.findings() == {"completeness_listed_not_captured": 1}
    assert result.denominators() == {"completeness_listed_not_captured": 1}
    quiet = cp.compare_sessions([_root("session_ONLY")], [], judged_day=DAY)
    assert quiet.captured_not_listed == 1 and quiet.findings() == {}


def test_keyless_captures_explain_listed_rows() -> None:
    roots = [_root(None), _root(None)]
    result = cp.compare_sessions(roots, [_listed("A"), _listed("B"), _listed("C")], judged_day=DAY)
    assert result.listed_not_captured == 3 and result.captured_without_key == 2
    assert result.unexplained_not_captured == 1
    assert cp.compare_sessions(roots, [_listed("A")], judged_day=DAY).unexplained_not_captured == 0


@pytest.mark.parametrize(
    "name, pages",
    [
        (cp.THREAD_FILE, [{"nothing": []}]),
        (cp.THREAD_FILE, {"sessions": []}),
        (cp.THREAD_FILE, []),
        (cp.SESSIONS_FILE, [{"ccr": {"has_more": False}}]),
        (cp.SESSIONS_FILE, [{"ccr": "x"}]),
    ],
)
def test_a_page_without_its_row_array_raises(tmp_path: Path, name: str, pages: Any) -> None:
    good = [{"sessions": [], "next_cursor": None}] if name == cp.SESSIONS_FILE else [{"ccr": {"data": [], "has_more": False}}]
    _write(tmp_path, cp.THREAD_FILE if name == cp.SESSIONS_FILE else cp.SESSIONS_FILE, good)
    _write(tmp_path, name, pages)
    with pytest.raises(cp.ListError):
        cp.load_lists(tmp_path)


def test_unreadable_list_files_raise(tmp_path: Path) -> None:
    with pytest.raises(cp.ListError):
        cp.load_lists(tmp_path)
    (tmp_path / cp.THREAD_FILE).write_text("{not json", encoding="utf-8")
    with pytest.raises(cp.ListError):
        cp.load_lists(tmp_path)


def test_lists_that_stop_short_of_the_judged_day_are_refused(tmp_path: Path) -> None:
    _write(tmp_path, cp.THREAD_FILE, [{"sessions": [], "next_cursor": None}])
    row = {"id": "session_S", "created_at": "2026-01-02T00:00:00Z"}
    _write(tmp_path, cp.SESSIONS_FILE, [{"ccr": {"data": [row], "has_more": True}}])
    short = cp.load_lists(tmp_path)
    assert not short.sessions_complete and not cp.lists_cover(short, DAY)
    reached = cp.Lists(
        rows=short.rows,
        thread_complete=True,
        sessions_complete=False,
        sessions_oldest=datetime(2025, 12, 31, 23, tzinfo=timezone.utc),
    )
    assert cp.lists_cover(reached, DAY)
    _write(tmp_path, cp.THREAD_FILE, [{"sessions": [], "next_cursor": "more"}])
    assert not cp.lists_cover(cp.Lists(thread_complete=False, sessions_complete=True), DAY)
    assert not cp.load_lists(tmp_path).thread_complete


def test_production_first_judged_day_follows_the_flip() -> None:
    day = cp.PRODUCTION_FIRST_JUDGED_DAY
    assert isinstance(day, str) and date.fromisoformat(day).isoformat() == day
    assert date.fromisoformat(day) > date.fromisoformat(cp.V4_FIRST_JUDGED_DAY)
    assert day == "2026-10-28"


def test_first_judged_day_is_per_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    assert cp.first_judged_day("ducklake_smoke") == cp.V4_FIRST_JUDGED_DAY
    assert cp.first_judged_day("ducklake_telemetry") == cp.PRODUCTION_FIRST_JUDGED_DAY
    monkeypatch.setattr(cp, "PRODUCTION_FIRST_JUDGED_DAY", None)
    assert cp.first_judged_day("ducklake_telemetry") is None
    monkeypatch.setattr(cp, "V4_FIRST_JUDGED_DAY", "2030-01-01")
    monkeypatch.setattr(cp, "PRODUCTION_FIRST_JUDGED_DAY", "2030-02-02")
    assert (cp.first_judged_day("ducklake_smoke"), cp.first_judged_day("ducklake_telemetry")) == ("2030-01-01", "2030-02-02")
    assert cp.first_judged_day("ducklake_ops") is None and cp.first_judged_day("") is None
