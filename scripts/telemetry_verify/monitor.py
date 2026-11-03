"""The daily verification monitor (PLAN-telemetry-verification-monitor, rec-4025, rec-4101).

run_monitor finds every real root tree of the trailing 28 days that went quiet during *quiet_day*, reads each one through
session_tree_rows, re-derives every per-session verb's answer from the stored rows with scripts.telemetry_verify.derive and
compares it with the deployed reader's, compares the window verb's rows and the orphan counts, and runs the stored-row silent
checks. It reaches the reader only through the injected *named* and *describe* callables. Output is counts only (Decision 101).

Every reader call but the two describes passes one start gate: it starts only while two CALL_WORST_CASE_S of MONITOR_BUDGET_S
remain (its own and the closing describe's) and fewer than MAX_READER_CALLS have been made. A call that cannot start is
counted, never silently skipped.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from scripts.telemetry_verify import derive, monitor_compare, orphans, silent_checks
from scripts.telemetry_verify.dedupe import normalise_row, parse_timestamp
from scripts.telemetry_verify.derive import Scope
from scripts.telemetry_verify.joins import load_telemetry_joins
from scripts.telemetry_verify.rows import TreeMovedError, decode_rows, read_tree
from src.common import ducklake_event_reads as event_reads
from src.common.ducklake_event_read_verbs import IDLE_THRESHOLD_SECONDS
from src.common.ducklake_reader_client import ResponseTooLargeError

WINDOW_DAYS = 28
RUN_LEVEL_WINDOW_DAYS = 1
MAX_SESSIONS_PER_TREE = 6
MONITOR_BUDGET_S = 2000
CALL_WORST_CASE_S = 450
FILING_RESERVE_S = 1450
MAX_READER_CALLS = 800
MAX_TREE_CALLS = 60
PAGE_ROWS = 2000
_VERB_CALLS_PER_TREE = 4 + 2 * (MAX_SESSIONS_PER_TREE - 1)
ROOT_VERBS = (
    "session_state_and_duration",
    "session_process_event_rollup",
    "session_friction_rollup",
    "model_call_cost_estimate",
)
CHILD_VERBS = ("agent_run_state_and_duration", "agent_run_token_rollup")
_AS_OF_VERBS = frozenset({"session_state_and_duration", "agent_run_state_and_duration"})
STRUCTURAL_VERBS = ("session_tree_rows", "telemetry_root_sessions", "telemetry_orphan_counts", "sessions_window")
MONITORED_VERBS = (*STRUCTURAL_VERBS, *ROOT_VERBS, *CHILD_VERBS)
_SESSIONS, _OBSERVATIONS = "telemetry_sessions", "telemetry_observations"

Named = Callable[..., list[dict[str, Any]]]


class MonitorIncomplete(RuntimeError):
    """The run could not complete (a describe failed): skew is unknown, so nothing is judged or filed."""


class _Refused(Exception):
    """The start gate will not start another call."""


class _TooLarge(Exception):
    """A tree needs more reader calls than MAX_TREE_CALLS."""


class Budget:
    """Seconds left of MONITOR_BUDGET_S on an injectable clock."""

    def __init__(self, total_s: float = MONITOR_BUDGET_S, clock: Callable[[], float] = time.monotonic) -> None:
        self._total, self._clock = total_s, clock
        self._start = clock()

    def elapsed(self) -> float:
        return self._clock() - self._start

    def remaining(self) -> float:
        return self._total - self.elapsed()


class _Gate:
    def __init__(self, named: Named, budget: Budget) -> None:
        self._named, self.budget = named, budget
        self.calls = 0
        self.max_call_s = 0.0
        self.max_bytes = 0

    def open(self) -> bool:
        return self.calls + 1 < MAX_READER_CALLS and self.budget.remaining() >= 2 * CALL_WORST_CASE_S

    def __call__(self, verb: str, **params: Any) -> list[dict[str, Any]]:
        if not self.open():
            raise _Refused
        self.calls += 1
        began = self.budget.elapsed()
        try:
            page = self._named(verb, **params)
        finally:
            self.max_call_s = max(self.max_call_s, self.budget.elapsed() - began)
        self.max_bytes = max(self.max_bytes, len(json.dumps(page, default=str)))
        return page

    def describe(self, describe: Callable[[], Mapping[str, Any]]) -> Mapping[str, Any]:
        self.calls += 1
        began = self.budget.elapsed()
        try:
            return describe()
        except Exception as exc:  # noqa: BLE001 -- the class alone is reported
            raise MonitorIncomplete(type(exc).__name__) from None
        finally:
            self.max_call_s = max(self.max_call_s, self.budget.elapsed() - began)


class _TreeCalls:
    """One tree's calls: counts them against MAX_TREE_CALLS and sizes the tree from its first page."""

    def __init__(self, gate: _Gate) -> None:
        self._gate = gate
        self.calls = 0
        self.first: tuple[int, int] | None = None

    def _call(self, verb: str, **params: Any) -> list[dict[str, Any]]:
        if self.calls >= MAX_TREE_CALLS:
            raise _TooLarge
        self.calls += 1
        return self._gate(verb, **params)

    def __call__(self, verb: str, **params: Any) -> list[dict[str, Any]]:
        page = self._call(verb, **params)
        if verb == "session_tree_rows" and page:
            totals = (page[0]["total_rows"], page[0]["roots_in_tree"])
            if self.first is None:
                self.first = totals
                if math.ceil(totals[0] / PAGE_ROWS) + 1 + _VERB_CALLS_PER_TREE > MAX_TREE_CALLS:
                    raise _TooLarge
        return page

    def reread(self, session_id: str) -> tuple[int, int] | None:
        page = self._call("session_tree_rows", session_id=session_id, offset=0, max_rows=1)
        return (page[0]["total_rows"], page[0]["roots_in_tree"]) if page else None


def _transient(exc: BaseException) -> bool:
    return not isinstance(exc, ResponseTooLargeError)


@dataclass
class _Outcome:
    root: dict[str, Any]
    status: str = "pending"
    transient: bool = False
    verb_failed: int = 0
    verb_failed_transient: bool = False
    not_compared: int = 0
    results: list[tuple[str, bool]] = field(default_factory=list)
    tables: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    @property
    def start_day(self) -> str:
        return self.root["session_started_at"].strftime("%Y-%m-%d")


@dataclass
class MonitorReport:
    quiet_day: str = ""
    window: tuple[str, str] = ("", "")
    roots: int = 0
    quiet: int = 0
    skip_trees: int = 0
    trees_read: int = 0
    tree_moved: int = 0
    tree_collision: int = 0
    tree_read_failed: int = 0
    not_read_budget: int = 0
    next_skip: int = 0
    run_level_days_unread: int = 0
    day_level_unread: int = 0
    tree_too_large: int = 0
    sessions_not_compared: int = 0
    session_verb_failed: int = 0
    verb_skew: int = 0
    skewed: tuple[str, ...] = ()
    compared_trees: int = 0
    agree: dict[str, list[int]] = field(default_factory=dict)
    window_agree: tuple[int, int] = (0, 0)
    orphan_days: int = 0
    orphan_quiescent: dict[str, int] = field(default_factory=dict)
    orphan_child_rows: dict[str, int] = field(default_factory=dict)
    orphan_pending: int = 0
    orphan_undercount: dict[str, int] = field(default_factory=dict)
    orphan_independent_rows: dict[str, int] = field(default_factory=dict)
    rootless_trees: int = 0
    conflicted_keys: int = 0
    silent: silent_checks.Counts = field(default_factory=dict)
    reader_calls: int = 0
    run_level_s: int = 0
    elapsed_s: int = 0
    max_call_s: int = 0
    max_response_bytes: int = 0

    @property
    def coverage_incomplete(self) -> int:
        count = (
            self.not_read_budget
            + self.tree_read_failed
            + self.tree_too_large
            + self.session_verb_failed
            + self.run_level_days_unread
            + self.day_level_unread
        )
        return count + (1 if self.quiet - self.skip_trees > 0 and self.compared_trees == 0 and self.verb_skew == 0 else 0)

    def findings(self) -> dict[str, int]:
        """Positive finding classes with their counts (declared classes included; the filing helper skips those)."""
        out: dict[str, int] = {}
        for verb, (agreeing, compared) in self.agree.items():
            if compared > agreeing:
                out[f"rederivation_mismatch:{verb}"] = compared - agreeing
        if self.window_agree[1] > self.window_agree[0]:
            out["rederivation_mismatch:sessions_window"] = self.window_agree[1] - self.window_agree[0]
        for prefix, counts in (("orphan_quiescent", self.orphan_quiescent), ("orphan_undercount", self.orphan_undercount)):
            out.update({f"{prefix}:{join}": n for join, n in counts.items() if n > 0})
        if self.coverage_incomplete:
            out["monitor_coverage_incomplete"] = self.coverage_incomplete
        out.update({name: n for name, (n, _) in self.silent.items() if n > 0})
        return out

    def denominators(self) -> dict[str, int]:
        """The --assert-zero denominator of every class (what the class could have counted this run)."""
        out = {f"rederivation_mismatch:{verb}": compared for verb, (_, compared) in self.agree.items()}
        out["rederivation_mismatch:sessions_window"] = self.window_agree[1]
        for join, rows in self.orphan_child_rows.items():
            out[f"orphan_quiescent:{join}"] = rows
        for join, rows in self.orphan_independent_rows.items():
            out[f"orphan_undercount:{join}"] = rows
        out["monitor_coverage_incomplete"] = self.quiet
        out.update({name: d for name, (_, d) in self.silent.items()})
        return out


def _days(first: date, count: int) -> list[str]:
    return [(first + timedelta(days=i)).isoformat() for i in range(count)]


def rotation_days(day: date) -> list[str]:
    """The window days a whole number of weeks before *day* (four of the 28): seven consecutive runs count every window day."""
    return [(day - timedelta(days=7 * k)).isoformat() for k in range(1, WINDOW_DAYS // 7 + 1)]


def _root_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "session_id": row["session_id"],
        "session_started_at": parse_timestamp(row["session_started_at"]),
        "last_event_timestamp": parse_timestamp(row["last_event_timestamp"]),
    }


def _try(gate: _Gate, verb: str, **params: Any) -> list[dict[str, Any]] | None:
    try:
        return gate(verb, **params)
    except Exception:  # noqa: BLE001 -- _Refused, a 413, a 5xx or a timeout: the caller counts it unread
        return None


def _run_level(gate: _Gate, days: list[str]) -> tuple[list[dict[str, Any]], set[str]]:
    rows: list[dict[str, Any]] = []
    unread: set[str] = set()
    for i in range(0, len(days), RUN_LEVEL_WINDOW_DAYS):
        chunk = days[i : i + RUN_LEVEL_WINDOW_DAYS]
        got = _try(gate, "telemetry_root_sessions", day_from=chunk[0], day_to=chunk[-1])
        if got is not None:
            rows += got
            continue
        for day in chunk if len(chunk) > 1 else ():
            one = _try(gate, "telemetry_root_sessions", day_from=day, day_to=day)
            if one is None:
                unread.add(day)
            else:
                rows += one
        if len(chunk) == 1:
            unread.add(chunk[0])
    return rows, unread


def _verb_pair(
    tables: Mapping[str, list[dict[str, Any]]], verb: str, session_id: str, as_of: datetime
) -> list[dict[str, Any]]:
    if verb in _AS_OF_VERBS:
        return getattr(derive, verb)(tables, session_id, as_of)
    return getattr(derive, verb)(tables, session_id)


def _session_plan(tables: Mapping[str, list[dict[str, Any]]], root: str) -> tuple[list[str], int]:
    first: dict[str, tuple[datetime, str]] = {}
    for row in map(normalise_row, tables.get(_SESSIONS) or []):
        sid = row["session_id"]
        if sid != root:
            first[sid] = min(first.get(sid, (row["event_timestamp"], sid)), (row["event_timestamp"], sid))
    ordered = [sid for _, sid in sorted(first.values())]
    keep = ordered[: MAX_SESSIONS_PER_TREE - 1]
    return keep, len(ordered) - len(keep)


def _compare_sessions(
    calls: _TreeCalls, out: _Outcome, tables: dict[str, list[dict[str, Any]]], root: str, as_of: datetime
) -> None:
    keep, skipped = _session_plan(tables, root)
    out.not_compared = skipped
    plan = [(verb, root) for verb in ROOT_VERBS] + [(verb, sid) for sid in keep for verb in CHILD_VERBS]
    for verb, sid in plan:
        params: dict[str, Any] = {"session_id": sid}
        if verb in _AS_OF_VERBS:
            params["as_of"] = as_of.isoformat()
        try:
            deployed = calls(verb, **params)
        except (_Refused, _TooLarge):
            raise
        except Exception as exc:  # noqa: BLE001 -- counted by class below
            out.verb_failed += 1
            out.verb_failed_transient = out.verb_failed_transient or _transient(exc)
            continue
        derived = _verb_pair(tables, verb, sid, as_of)
        out.results.append((verb, not monitor_compare.compare_session(verb, deployed, derived)))


def _last_event(tables: Mapping[str, list[dict[str, Any]]]) -> datetime | None:
    scope = Scope(tables, "telemetry_root_sessions")
    stamps = [row["event_timestamp"] for rows in scope.tables.values() for row in rows]
    return max(stamps) if stamps else None


def _process(gate: _Gate, root: dict[str, Any], as_of: datetime) -> _Outcome:
    out = _Outcome(root)
    calls = _TreeCalls(gate)
    sid = root["session_id"]
    try:
        if not gate.open():
            raise _Refused
        try:
            pages = read_tree(calls, sid, PAGE_ROWS)
        except TreeMovedError:
            out.status = "moved"
            return out
        totals = (pages[0][0]["total_rows"], pages[0][0]["roots_in_tree"]) if pages and pages[0] else None
        if totals is None:
            out.status = "read_failed"
            return out
        if totals[1] > 1:
            out.status = "collision"
            return out
        tables = decode_rows(pages)
        newest = _last_event(tables)
        if newest != root["last_event_timestamp"]:
            out.status = "moved"
            return out
        _compare_sessions(calls, out, tables, sid, as_of)
        again = calls.reread(sid)
        if again != totals:
            out.status = "moved"
            return out
        out.status, out.tables = "compared", tables
    except _Refused:
        out.status = "budget"
    except _TooLarge:
        out.status = "too_large"
    except Exception as exc:  # noqa: BLE001 -- a failed read or re-read: the class alone is kept
        out.status, out.transient = "read_failed", _transient(exc)
    return out


def _merge(trees: list[_Outcome]) -> dict[str, list[dict[str, Any]]]:
    merged: dict[str, list[dict[str, Any]]] = {}
    for tree in trees:
        for name, rows in tree.tables.items():
            merged.setdefault(name, []).extend(rows)
    return merged


def _projects(tables: Mapping[str, list[dict[str, Any]]]) -> dict[str, str]:
    return {row["project_id"]: "real" for rows in tables.values() for row in rows}


def _stops(out: _Outcome, day_unread: set[str], run_unread: set[str]) -> bool:
    return (
        out.status == "budget"
        or (out.status == "read_failed" and out.transient)
        or out.verb_failed_transient
        or out.start_day in day_unread
        or (bool(run_unread) and out.start_day >= min(run_unread))
    )


def run_monitor(
    named: Named,
    describe: Callable[[], Mapping[str, Any]],
    *,
    quiet_day: str,
    as_of: datetime,
    budget: Budget,
    skip_trees: int = 0,
) -> MonitorReport:
    gate = _Gate(named, budget)
    as_of = as_of.replace(microsecond=as_of.microsecond // 1000 * 1000)  # the reader keeps milliseconds only
    wanted = event_reads.describe_all()

    def skewed_in(deployed: Mapping[str, Any]) -> set[str]:
        return {v for v in MONITORED_VERBS if (deployed.get(v) or {}).get("read_version") != wanted[v]["read_version"]}

    start_skew = skewed_in(gate.describe(describe))
    day = date.fromisoformat(quiet_day)
    window = _days(day - timedelta(days=WINDOW_DAYS), WINDOW_DAYS)
    report = MonitorReport(quiet_day=quiet_day, window=(window[0], window[-1]), skip_trees=skip_trees)

    raw_roots, run_unread = _run_level(gate, window)
    report.run_level_days_unread = len(run_unread)
    report.run_level_s = int(budget.elapsed())
    roots = {r["session_id"]: _root_row(r) for r in raw_roots}
    report.roots = len(roots)
    lo = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
    hi = lo + timedelta(days=1)
    idle = timedelta(seconds=IDLE_THRESHOLD_SECONDS)
    quiet = sorted(
        (r for r in roots.values() if lo <= r["last_event_timestamp"] + idle < hi),
        key=lambda r: (r["session_started_at"], r["session_id"]),
    )
    report.quiet = len(quiet)
    to_read = quiet[skip_trees:]

    wanted_days = sorted({r["session_started_at"].strftime("%Y-%m-%d") for r in to_read})
    rotation = rotation_days(day)
    orphan_rows: dict[str, list[dict[str, Any]]] = {}
    window_rows: dict[str, list[dict[str, Any]]] = {}
    day_unread: set[str] = set()
    for counted in sorted(set(wanted_days) | set(rotation)):
        orphan = _try(gate, "telemetry_orphan_counts", day_from=counted, day_to=counted, as_of=as_of.isoformat())
        sessions = (
            _try(gate, "sessions_window", day_from=counted, day_to=counted, as_of=as_of.isoformat())
            if counted in wanted_days
            else []
        )
        if orphan is None or sessions is None:
            day_unread.add(counted)
            continue
        orphan_rows[counted] = orphan
        if counted in wanted_days:
            window_rows[counted] = sessions
    report.day_level_unread = len(day_unread)
    report.orphan_days = len(orphan_rows)

    outcomes = [_process(gate, root, as_of) for root in to_read]
    report.next_skip = skip_trees + next(
        (i for i, o in enumerate(outcomes) if _stops(o, day_unread, run_unread)), len(outcomes)
    )

    end_skew = skewed_in(gate.describe(describe))
    skewed = start_skew | end_skew
    report.skewed, report.verb_skew = tuple(sorted(skewed)), len(skewed)
    report.reader_calls = gate.calls
    report.max_call_s, report.max_response_bytes = int(math.ceil(gate.max_call_s)), gate.max_bytes

    compared = [o for o in outcomes if o.status == "compared"]
    report.trees_read = sum(1 for o in outcomes if o.status in ("compared", "moved", "collision"))
    report.tree_moved = sum(1 for o in outcomes if o.status == "moved")
    report.tree_collision = sum(1 for o in outcomes if o.status == "collision")
    report.tree_read_failed = sum(1 for o in outcomes if o.status == "read_failed")
    report.not_read_budget = sum(1 for o in outcomes if o.status == "budget")
    report.tree_too_large = sum(1 for o in outcomes if o.status == "too_large")
    report.session_verb_failed = sum(o.verb_failed for o in outcomes)
    report.sessions_not_compared = sum(o.not_compared for o in compared)
    report.compared_trees = len(compared)

    everything_skewed = bool({"session_tree_rows", "telemetry_root_sessions"} & skewed)
    judged = [] if everything_skewed else compared
    for verb in (*ROOT_VERBS, *CHILD_VERBS):
        if verb in skewed or everything_skewed:
            continue
        results = [ok for o in judged for v, ok in o.results if v == verb]
        report.agree[verb] = [sum(results), len(results)]

    usable = [o for o in judged if o.start_day not in day_unread]
    if "sessions_window" not in skewed and not everything_skewed:
        agreeing = total = 0
        for counted in sorted({o.start_day for o in usable}):
            group = [o for o in usable if o.start_day == counted]
            tables = _merge(group)
            derived = derive.sessions_window(tables, _projects(tables), counted, counted, as_of)
            a, c = monitor_compare.compare_window(window_rows.get(counted, []), derived, [o.root["session_id"] for o in group])
            agreeing, total = agreeing + a, total + c
        report.window_agree = (agreeing, total)

    if "telemetry_orphan_counts" not in skewed and not everything_skewed:
        tables = _merge(usable)
        independent = (
            orphans.orphan_counts(tables, load_telemetry_joins(), as_of, IDLE_THRESHOLD_SECONDS, _projects(tables))
            if usable
            else []
        )
        discovered: dict[str, int] = {}
        for root in roots.values():
            started = root["session_started_at"].strftime("%Y-%m-%d")
            if started not in run_unread:
                discovered[started] = discovered.get(started, 0) + 1
        verb_rows = [row for rows in orphan_rows.values() for row in rows]
        result = monitor_compare.compare_orphans(verb_rows, independent, {d: discovered.get(d, 0) for d in orphan_rows})
        report.orphan_quiescent, report.orphan_child_rows = result["quiescent"], result["child_rows"]
        report.orphan_pending, report.orphan_undercount = result["pending"], result["undercount"]
        report.orphan_independent_rows = result["independent_child_rows"]
        report.rootless_trees, report.conflicted_keys = result["rootless"], result["conflicted_keys"]

    report.silent = silent_checks.check_trees(o.tables for o in judged)
    report.elapsed_s = int(budget.elapsed())
    return report
