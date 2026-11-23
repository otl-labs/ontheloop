"""The arrival unreadable cause names the reader's HTTP status, error_type, error_class and request_id (rec-5491)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from scripts.telemetry_verify import session_presence as sp
from src.common.ducklake_reader_client import ReaderInvokeError

_NOW = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
_EXPECT = {"kind": "enforced", "sla": "60m", "check": {"workflow": "telemetry-arrival", "module": sp.MODULE}}
_CONFIG = {
    "judge_days": 3,
    "window_days": 7,
    "max_lookback_days": 56,
    "max_pr_lookups": 60,
    "collapse_min_commits": 3,
    "lost_windows": [],
    "excused_sessions": [],
}
_COMMIT = sp.Commit(
    f"{1:040x}", _NOW - timedelta(hours=10), "msg (#1)", "plan\n\nClaude-Session: https://claude.ai/code/session_AAA\n"
)


def _unreadable(exc: Exception) -> sp.ArrivalResult:
    def roots(day_from: str, day_to: str) -> list[dict[str, Any]]:
        raise exc

    return sp.judge([_COMMIT], roots, _NOW, _EXPECT, _CONFIG, lambda n: _NOW)


def test_unreadable_cause_names_status_and_error_type() -> None:
    body = {"error_type": "unmapped", "error_class": "OutOfMemoryException", "request_id": "abc-123"}
    result = _unreadable(ReaderInvokeError("named_read", 500, "boom", body))
    want = "ReaderInvokeError status=500 error_type=unmapped error_class=OutOfMemoryException request_id=abc-123"
    assert (result.status, result.cause) == ("unreadable", want)
    assert f"ARRIVAL_UNREADABLE cause={want}" in sp.lines(result)


def test_unsafe_body_values_are_dropped() -> None:
    body = {"error_type": "bad value\nx", "error_class": "Ok.Class", "request_id": "has space"}
    assert _unreadable(ReaderInvokeError("a", 503, "t", body)).cause == "ReaderInvokeError status=503 error_class=Ok.Class"


def test_other_exceptions_keep_the_class_name() -> None:
    assert _unreadable(RuntimeError("secret-host")).cause == "RuntimeError"
