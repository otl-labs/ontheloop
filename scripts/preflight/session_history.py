"""Telemetry-backed recent sessions for session_preflight (T3.20 c6, Decisions 236 cl.2, 215 cl.2, 199, 228).

One `sessions_window` named read through the routed telemetry reader replaces the retired session-log parser. The read
runs on a daemon thread so its cold cost overlaps preflight's serial phases, and result() joins it for whatever remains of
SESSION_HISTORY_DEADLINE_S counted from the read's start. An unknown is None, never [] or "". State and outcome are printed as
the reader derived them. Never raises.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from scripts.preflight import _common

SESSION_HISTORY_DEADLINE_S = 60
_VERB = "sessions_window"
_READER_LOGGER = "src.common.ducklake_reader_client"


class _ThreadOnlyFilter(logging.Filter):
    """Drop only the records emitted from one thread; the main thread's reader warnings still print."""

    def __init__(self, ident: int) -> None:
        super().__init__()
        self._ident = ident

    def filter(self, record: logging.LogRecord) -> bool:
        return record.thread != self._ident


def unavailable(reason: str, window_days: int = 2) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "window_days": window_days,
        "recent_sessions": None,
        "last_session": None,
        "reason": reason[:300],
    }


def _ts(row: dict[str, Any], field: str) -> datetime | None:
    ts = _common._row_ts(row, field)
    if ts is not None:
        return ts
    try:
        parsed = datetime.fromisoformat(str(row.get(field)).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _minute(row: dict[str, Any], field: str) -> str | None:
    ts = _ts(row, field)
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ") if ts else None


def _render(row: dict[str, Any]) -> str:
    try:
        duration = f"{int(float(row['duration_seconds']))}s"
    except (KeyError, TypeError, ValueError):
        duration = "-"
    return (
        f"{_minute(row, 'session_started_at') or 'unknown'} state={row.get('state') or '-'} "
        f"outcome={row.get('outcome') or '-'} duration={duration} "
        f"last={_minute(row, 'last_event_timestamp') or '-'} session={str(row.get('session_id') or '')[:8]}"
    )


def _sort_key(row: dict[str, Any]) -> datetime:
    return _ts(row, "session_started_at") or datetime.min.replace(tzinfo=timezone.utc)


def read_session_history(
    reader: Any = None, *, now: datetime | None = None, window_days: int = 2, limit: int = 5
) -> dict[str, Any]:
    """One sessions_window read over *window_days* UTC days ending today; the newest *limit* root sessions, descending."""
    try:
        moment = now or datetime.now(timezone.utc)
        today = moment.astimezone(timezone.utc).date()
        active: Any = reader if reader is not None else _common._make_event_reader()
        rows = active.named(
            _VERB,
            day_from=(today - timedelta(days=window_days - 1)).isoformat(),
            day_to=today.isoformat(),
            as_of=moment.astimezone(timezone.utc).isoformat(),
        )
        if not isinstance(rows, list):
            rows = list(rows)
        if not rows:
            return {"status": "empty", "window_days": window_days, "recent_sessions": [], "last_session": ""}
        lines = [_render(row) for row in sorted(rows, key=_sort_key, reverse=True)[:limit]]
    except Exception as exc:  # noqa: BLE001 -- every failure path is the same loud degraded signal
        return unavailable(f"{type(exc).__name__}: {exc}", window_days)
    return {"status": "ok", "window_days": window_days, "recent_sessions": lines, "last_session": lines[0]}


class SessionHistoryHandle:
    def __init__(self, window_days: int, clock: Callable[[], float]) -> None:
        self._window_days = window_days
        self._clock = clock
        self._started = clock()
        self._result: dict[str, Any] | None = None
        self._thread: threading.Thread | None = None

    def result(self, deadline_s: float = SESSION_HISTORY_DEADLINE_S) -> dict[str, Any]:
        """Join the read for what remains of *deadline_s* counted from its start; unavailable on timeout."""
        thread = self._thread
        if thread is not None:
            thread.join(max(deadline_s - (self._clock() - self._started), 0.0))
        if self._result is None:
            return unavailable(f"read exceeded the {deadline_s}s deadline", self._window_days)
        return self._result


def start_session_history(
    reader: Any = None,
    *,
    now: datetime | None = None,
    window_days: int = 2,
    limit: int = 5,
    clock: Callable[[], float] = time.monotonic,
) -> SessionHistoryHandle:
    handle = SessionHistoryHandle(window_days, clock)

    def work() -> None:
        flt = _ThreadOnlyFilter(threading.get_ident())
        logger = logging.getLogger(_READER_LOGGER)
        logger.addFilter(flt)
        try:
            handle._result = read_session_history(reader, now=now, window_days=window_days, limit=limit)
        except BaseException as exc:  # noqa: BLE001 -- a dead worker must never read as a timeout
            handle._result = unavailable(f"{type(exc).__name__}: {exc}", window_days)
        finally:
            logger.removeFilter(flt)

    thread = threading.Thread(target=work, name="session-history", daemon=True)
    handle._thread = thread
    try:
        thread.start()
    except RuntimeError as exc:
        handle._thread = None
        handle._result = unavailable(f"{type(exc).__name__}: {exc}", window_days)
    return handle


def format_history(result: dict[str, Any]) -> str:
    """One stderr line in the telemetry gauge's shape. Never raises."""
    try:
        if result.get("status") == "unavailable":
            return f"Session history: unavailable ({result.get('reason') or 'unknown'})"
        count = len(result.get("recent_sessions") or [])
        return f"Session history: {result['status']} ({count} root session(s) in {result['window_days']} UTC day(s))"
    except Exception:  # noqa: BLE001
        return "Session history: unavailable (unformattable result)"
