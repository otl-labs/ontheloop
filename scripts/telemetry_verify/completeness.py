"""Session completeness: listed sessions against captured roots (PLAN-telemetry-verification-monitor).

The coordinator fetches the harness's two session lists (agent tools, unreachable from hooks or CI) into raw JSON files; this
module joins them with the captured root sessions on the harness session key (session_ and cse_ prefixes normalised),
classifies only the unjoined rows, and counts. Counts only (Decision 101): no id, ref or branch leaves this module.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from scripts.telemetry_verify.dedupe import parse_timestamp

# The first UTC day after the day the producer fixes build's PARSER_VERSION 4 commit (12c017fd, 2026-10-17 23:23Z) reached
# main: a session created before that merge runs the parser_version 3 producer and writes no harness key.
V4_FIRST_JUDGED_DAY = "2026-10-18"
# The first full UTC day after PLAN-telemetry-production-flip's flip (8a846418) converged on ducklake_telemetry at
# 2026-10-27 11:39:15Z (first_full_production_day in that plan's FLIP-RECORD.md).
PRODUCTION_FIRST_JUDGED_DAY: str | None = "2026-10-28"
REPOSITORIES = ("benjamin-blake/theseus", "otl-labs/ontheloop")
THREAD_FILE = "thread_sessions.json"
SESSIONS_FILE = "sessions.json"
_PREFIXES = ("session_", "cse_")


class ListError(ValueError):
    """A list file is not the raw page array it must be."""


@dataclass(frozen=True)
class Listed:
    key: str | None
    created_at: datetime
    source: str
    repository: str | None = None


@dataclass
class Lists:
    rows: list[Listed] = field(default_factory=list)
    thread_complete: bool = False
    sessions_complete: bool = False
    sessions_oldest: datetime | None = None


@dataclass
class CompletenessResult:
    judged_day: str
    listed: int = 0
    listed_and_captured: int = 0
    listed_not_captured: int = 0
    unexplained_not_captured: int = 0
    captured_not_listed: int = 0
    listed_unjoinable: int = 0
    listed_scope_unknown: int = 0
    captured_without_key: int = 0

    def findings(self) -> dict[str, int]:
        return {"completeness_listed_not_captured": self.unexplained_not_captured} if self.unexplained_not_captured else {}

    def denominators(self) -> dict[str, int]:
        return {"completeness_listed_not_captured": self.listed}


def first_judged_day(catalog: str) -> str | None:
    """The first day completeness judges in *catalog*; None for a catalog with no first day yet or an unknown one."""
    if catalog == "ducklake_smoke":
        return V4_FIRST_JUDGED_DAY
    if catalog == "ducklake_telemetry":
        return PRODUCTION_FIRST_JUDGED_DAY
    return None


def normalise_key(value: Any) -> str | None:
    """The harness session key without its session_ or cse_ prefix; None when nothing remains."""
    if not isinstance(value, str):
        return None
    for prefix in _PREFIXES:
        if value.startswith(prefix):
            value = value[len(prefix) :]
            break
    return value or None


def _pages(path: Path) -> list[Mapping[str, Any]]:
    try:
        pages = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ListError(f"{path.name}: {type(exc).__name__}") from None
    if not isinstance(pages, list) or not pages or not all(isinstance(p, Mapping) for p in pages):
        raise ListError(f"{path.name}: not a non-empty array of page responses")
    return pages


def _rows(page: Mapping[str, Any], *path: str, name: str) -> list[Mapping[str, Any]]:
    node: Any = page
    for step in path:
        node = node.get(step) if isinstance(node, Mapping) else None
    if not isinstance(node, list):
        raise ListError(f"{name}: a page without its row array")
    return node


def _repository(row: Mapping[str, Any]) -> str | None:
    sources = (row.get("session_context") or {}).get("sources") or []
    for source in sources:
        url = (source.get("git_repository") or {}).get("url") if isinstance(source, Mapping) else None
        if isinstance(url, str) and url:
            return url
    return None


def load_lists(directory: Path | str) -> Lists:
    """Both raw list files of *directory*; a file that is not such an array, or a page without its row array, raises."""
    base = Path(directory)
    out = Lists()
    threads = _pages(base / THREAD_FILE)
    for page in threads:
        for row in _rows(page, "sessions", name=THREAD_FILE):
            out.rows.append(Listed(normalise_key(row.get("session_id")), parse_timestamp(row["created_at"]), "thread"))
    out.thread_complete = not threads[-1].get("next_cursor")
    sessions = _pages(base / SESSIONS_FILE)
    for page in sessions:
        for row in _rows(page, "ccr", "data", name=SESSIONS_FILE):
            created = parse_timestamp(row["created_at"])
            out.rows.append(Listed(normalise_key(row.get("id")), created, "sessions", _repository(row)))
            out.sessions_oldest = created if out.sessions_oldest is None else min(out.sessions_oldest, created)
    out.sessions_complete = not (sessions[-1].get("ccr") or {}).get("has_more")
    return out


def lists_cover(lists: Lists, judged_day: str) -> bool:
    """True when the thread list reached its last page and the sessions list reached its last page or the judged day."""
    start = datetime.combine(date.fromisoformat(judged_day), datetime.min.time(), tzinfo=timezone.utc)
    reached = lists.sessions_complete or (lists.sessions_oldest is not None and lists.sessions_oldest < start)
    return lists.thread_complete and reached


def _in_scope(repository: str) -> bool:
    return any(repository.rstrip("/").removesuffix(".git").endswith("/" + name) for name in REPOSITORIES)


def compare_sessions(
    captured_roots: Iterable[Mapping[str, Any]], listed: Iterable[Listed], *, judged_day: str
) -> CompletenessResult:
    """Join first, then classify only the unjoined rows (see the module docstring)."""
    first = datetime.combine(date.fromisoformat(judged_day), datetime.min.time(), tzinfo=timezone.utc)
    last = first + timedelta(days=1)
    roots = list(captured_roots)
    listed = list(listed)
    captured_keys = {normalise_key(r.get("harness_session_key")) for r in roots} - {None}
    listed_keys = {row.key for row in listed} - {None}
    result = CompletenessResult(judged_day)
    for row in listed:
        if not first <= row.created_at < last:
            continue
        if row.key is not None and row.key in captured_keys:
            result.listed_and_captured += 1
        elif row.key is None:
            result.listed_unjoinable += 1
        elif row.source == "sessions" and row.repository is None:
            result.listed_scope_unknown += 1
        elif row.source == "sessions" and not _in_scope(row.repository or ""):
            continue
        else:
            result.listed_not_captured += 1
    for root in roots:
        started = parse_timestamp(root["session_started_at"])
        if not first <= started < last:
            continue
        key = normalise_key(root.get("harness_session_key"))
        if key is None:
            result.captured_without_key += 1
        elif key not in listed_keys:
            result.captured_not_listed += 1
    result.listed = (
        result.listed_and_captured + result.listed_not_captured + result.listed_unjoinable + result.listed_scope_unknown
    )
    result.unexplained_not_captured = max(0, result.listed_not_captured - result.captured_without_key)
    return result
