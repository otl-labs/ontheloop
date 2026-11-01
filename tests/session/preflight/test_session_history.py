"""session_history: telemetry-backed recent sessions (T3.20 c6)."""

from __future__ import annotations

import io
import logging
import threading
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from scripts.preflight import session_history as sh
from src.common.ducklake_reader_client import ReadDeadlineExceededError

NOW = datetime(2026, 10, 28, 12, 30, 45, tzinfo=timezone.utc)


def _row(started: str, sid: str = "abcdef0123456789", **over: object) -> dict:
    return {
        "session_id": sid,
        "session_started_at": started,
        "state": "closed",
        "outcome": "success",
        "duration_seconds": 120.0,
        "last_event_timestamp": started,
        **over,
    }


def _reader(rows: list[dict]) -> MagicMock:
    reader = MagicMock()
    reader.named.return_value = rows
    return reader


def test_ok_orders_newest_first_and_caps_at_limit() -> None:
    rows = [_row(f"2026-10-2{d}T10:00:00+00:00", sid=f"s{d}" * 4) for d in (1, 5, 3, 7, 2, 6, 4)]
    result = sh.read_session_history(_reader(rows), now=NOW)
    assert result["status"] == "ok"
    assert len(result["recent_sessions"]) == 5
    assert result["last_session"] == result["recent_sessions"][0]
    assert result["recent_sessions"][0].startswith("2026-10-27T10:00Z")
    assert result["recent_sessions"][-1].startswith("2026-10-23T10:00Z")


def test_rendered_line_shape_with_nulls_and_running() -> None:
    row = _row("2026-10-28T09:05:59+00:00", state="running", outcome=None, duration_seconds=None, last_event_timestamp=None)
    line = sh.read_session_history(_reader([row]), now=NOW)["last_session"]
    assert line == "2026-10-28T09:05Z state=running outcome=- duration=- last=- session=abcdef01"


def test_rendered_line_with_values() -> None:
    line = sh.read_session_history(_reader([_row("2026-10-28T09:05:00+00:00")]), now=NOW)["last_session"]
    assert line == "2026-10-28T09:05Z state=closed outcome=success duration=120s last=2026-10-28T09:05Z session=abcdef01"


def test_empty_is_empty_list_and_empty_string() -> None:
    result = sh.read_session_history(_reader([]), now=NOW)
    assert result == {"status": "empty", "window_days": 2, "recent_sessions": [], "last_session": ""}


def test_exception_and_typed_deadline_error_are_unavailable_null() -> None:
    for exc in (RuntimeError("boom"), ReadDeadlineExceededError("named_read", "slow", None)):
        reader = MagicMock()
        reader.named.side_effect = exc
        result = sh.read_session_history(reader, now=NOW)
        assert result["status"] == "unavailable"
        assert result["recent_sessions"] is None and result["last_session"] is None
        assert type(exc).__name__ in result["reason"]


def test_verb_and_params_exact_and_no_limit() -> None:
    reader = _reader([])
    sh.read_session_history(reader, now=NOW)
    reader.named.assert_called_once_with(
        "sessions_window", day_from="2026-10-27", day_to="2026-10-28", as_of="2026-10-28T12:30:45+00:00"
    )


def test_sleeping_reader_returns_within_deadline_from_start() -> None:
    release = threading.Event()
    reader = MagicMock()
    reader.named.side_effect = lambda *a, **k: release.wait(30) or []
    handle = sh.start_session_history(reader, now=NOW)
    import time

    began = time.monotonic()
    result = handle.result(deadline_s=0.3)
    release.set()
    assert time.monotonic() - began < 1.3
    assert result["status"] == "unavailable"
    assert result["recent_sessions"] is None and result["last_session"] is None


def test_deadline_counts_from_start_with_fake_clock() -> None:
    release = threading.Event()
    reader = MagicMock()
    reader.named.side_effect = lambda *a, **k: release.wait(30) or []
    ticks = iter([1000.0, 1100.0, 1100.0])
    handle = sh.start_session_history(reader, now=NOW, clock=lambda: next(ticks))
    result = handle.result(deadline_s=60)
    release.set()
    assert result["status"] == "unavailable"


def test_result_ok_through_thread() -> None:
    handle = sh.start_session_history(_reader([_row("2026-10-28T09:05:00+00:00")]), now=NOW)
    assert handle.result()["status"] == "ok"


def test_format_history_lines() -> None:
    ok = sh.read_session_history(_reader([_row("2026-10-28T09:05:00+00:00")]), now=NOW)
    assert sh.format_history(ok) == "Session history: ok (1 root session(s) in 2 UTC day(s))"
    empty = sh.read_session_history(_reader([]), now=NOW)
    assert sh.format_history(empty) == "Session history: empty (0 root session(s) in 2 UTC day(s))"
    assert sh.format_history(sh.unavailable("nope", 2)) == "Session history: unavailable (nope)"
    assert sh.format_history({}).startswith("Session history: unavailable")


def test_worker_logs_are_silenced_main_thread_logs_remain_and_filter_removed() -> None:
    logger = logging.getLogger("src.common.ducklake_reader_client")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger.addHandler(handler)
    before = list(logger.filters)
    try:
        reader = MagicMock()

        def named(*_a: object, **_k: object) -> list:
            logger.warning("worker-warning")
            return []

        reader.named.side_effect = named
        handle = sh.start_session_history(reader, now=NOW)
        handle.result()
        handle._thread.join(5)
        logger.warning("main-warning")
    finally:
        logger.removeHandler(handler)
    out = stream.getvalue()
    assert "worker-warning" not in out
    assert "main-warning" in out
    assert logger.filters == before


def test_malformed_row_values_never_raise_or_read_as_timeout() -> None:
    rows = [_row("2026-10-28 09:05:59.123+00", duration_seconds="12.5"), _row("2026-10-27T09:05:00Z", duration_seconds="x")]
    result = sh.read_session_history(_reader(rows), now=NOW)
    assert result["status"] == "ok"
    assert result["recent_sessions"][0].startswith("2026-10-28T09:05Z") and "duration=12s" in result["recent_sessions"][0]
    assert "duration=-" in result["recent_sessions"][1]
    bad = sh.read_session_history(_reader(["not-a-row"]), now=NOW)
    assert bad["status"] == "unavailable" and "deadline" not in bad["reason"]


def test_worker_crash_reports_its_own_reason_not_a_timeout() -> None:
    handle = sh.start_session_history(_reader([_row("2026-10-28T09:05:00+00:00")]), now=NOW)
    with patch.object(sh, "read_session_history", side_effect=ValueError("boom")):
        crashed = sh.start_session_history(now=NOW)
        result = crashed.result(5)
    handle.result()
    assert result["status"] == "unavailable" and "ValueError: boom" in result["reason"]
