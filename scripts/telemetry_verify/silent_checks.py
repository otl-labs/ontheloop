"""The four stored-row silent-failure checks of the verification monitor (PLAN-telemetry-verification-monitor).

Pure over each tree's live generation (the rows scripts.telemetry_verify.dedupe keeps, so a later generation never
double-counts). Each check reports a numerator and its denominator over rows at parser_version PARSER_FLOOR or later; a zero
denominator reads untested, never green. Columns are read by name from row dicts; a column absent from a row counts as null.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from scripts.telemetry_verify.dedupe import Row, compute_generations, resolve_table

PARSER_FLOOR = 4
KNOWN_NOTICE_FAILURE_TOKENS = ("failed", "killed")
ALARM_CHECKS = ("harness_key_missing", "background_notice_unknown", "resume_without_prior_close")
REPORT_ONLY_CHECKS = ("checkout_sha_missing",)
CHECKS = ("harness_key_missing", "checkout_sha_missing", "background_notice_unknown", "resume_without_prior_close")
_SESSIONS, _OBSERVATIONS, _AGENTS = "telemetry_sessions", "telemetry_observations", "telemetry_agents"
_NOTICE_PREFIX = "notice_status:"

Counts = dict[str, tuple[int, int]]


def live_rows(tables: Mapping[str, Iterable[Row]]) -> dict[str, list[Row]]:
    """The surviving rows of every table of one tree (retired generations dropped, duplicates collapsed)."""
    sessions = list(tables.get(_SESSIONS) or [])
    generations = compute_generations(sessions)
    out: dict[str, list[Row]] = {}
    for name in (_SESSIONS, _OBSERVATIONS, _AGENTS):
        rows = sessions if name == _SESSIONS else list(tables.get(name) or [])
        collapse = (lambda r: r.get("observation_type") == "model_call") if name == _OBSERVATIONS else False
        out[name], _ = resolve_table(name, rows, generations, entity_collapse=collapse)
    return out


def _current(row: Row) -> bool:
    version = row.get("parser_version")
    return isinstance(version, int) and version >= PARSER_FLOOR


def _order(row: Row) -> tuple[Any, ...]:
    ordinal = row.get("source_ordinal")
    return (row["event_timestamp"], -1 if ordinal is None else ordinal, row["event_kind"] == "close", row["event_id"])


def _harness_key(sessions: list[Row]) -> tuple[int, int]:
    opens = [r for r in sessions if _current(r) and r.get("event_kind") == "open" and r.get("parent_session_id") is None]
    return sum(1 for r in opens if r.get("harness_session_key") is None), len(opens)


def _checkout(sessions: list[Row]) -> tuple[int, int]:
    rows = [r for r in sessions if _current(r) and r.get("event_kind") in ("open", "resume")]
    return sum(1 for r in rows if r.get("checkout_sha") is None), len(rows)


def _notice(agents: list[Row]) -> tuple[int, int]:
    background = {r.get("agent_run_id") for r in agents if r.get("trigger") == "subagent_background"}
    closes = [r for r in agents if _current(r) and r.get("event_kind") == "close" and r.get("agent_run_id") in background]
    unknown = 0
    for row in closes:
        error = row.get("error")
        if isinstance(error, str) and error.startswith(_NOTICE_PREFIX):
            if error[len(_NOTICE_PREFIX) :] not in KNOWN_NOTICE_FAILURE_TOKENS:
                unknown += 1
    return unknown, len(closes)


def _resume(sessions: list[Row], observations: list[Row]) -> tuple[int, int]:
    roots = [r for r in sessions if r.get("parent_session_id") is None and r.get("event_kind") != "annotate"]
    calls: dict[str, list[Any]] = {}
    for row in observations:
        if row.get("observation_type") == "model_call":
            calls.setdefault(row["session_id"], []).append(row["event_timestamp"])
    by_session: dict[str, list[Row]] = {}
    for row in roots:
        by_session.setdefault(row["session_id"], []).append(row)
    missing = total = 0
    for sid, rows in by_session.items():
        previous: Row | None = None
        closed = False
        for row in sorted(rows, key=_order):
            if row["event_kind"] == "close":
                closed = True
                continue
            if row["event_kind"] == "resume" and _current(row):
                total += 1
                if previous is not None and not closed:
                    start, end = previous["event_timestamp"], row["event_timestamp"]
                    if any(start <= stamp < end for stamp in calls.get(sid, ())):
                        missing += 1
            previous, closed = row, False
    return missing, total


def check_tree(tables: Mapping[str, Iterable[Row]]) -> Counts:
    """The four checks' (numerator, denominator) over one tree's rows."""
    live = live_rows(tables)
    sessions, observations, agents = live[_SESSIONS], live[_OBSERVATIONS], live[_AGENTS]
    return {
        "harness_key_missing": _harness_key(sessions),
        "checkout_sha_missing": _checkout(sessions),
        "background_notice_unknown": _notice(agents),
        "resume_without_prior_close": _resume(sessions, observations),
    }


def check_trees(trees: Iterable[Mapping[str, Iterable[Row]]]) -> Counts:
    """The checks summed over the compared trees (every check present, 0/0 when nothing was tested)."""
    totals: Counts = dict.fromkeys(CHECKS, (0, 0))
    for tables in trees:
        for name, (numerator, denominator) in check_tree(tables).items():
            n, d = totals[name]
            totals[name] = (n + numerator, d + denominator)
    return totals


def untested(counts: Counts) -> list[str]:
    return [name for name, (_, denominator) in counts.items() if denominator == 0]
