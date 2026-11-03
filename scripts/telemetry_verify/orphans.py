"""Independent orphan counting over plain rows (telemetry_orphan_counts, rec-4101).

Written from docs/contracts/_joins.yaml (the joins arrive as the argument, loaded by scripts.telemetry_verify.joins from
the contract itself, never from the reader's projection) and the envelope's dedupe rules, never from the reader's SQL. A tree
is the rows sharing one (tenant_id, project_id, session_started_at) key. A child row is an orphan when its non-null FK value
matches no parent key among the deduped rows of the same tree; a tree is quiescent when as_of exceeds its last event by more
than the idle threshold. Conflicted grain keys are counted per day of the pin of the conflicted key's lowest session.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping

from scripts.telemetry_verify.dedupe import Row, normalise_row, parse_timestamp
from scripts.telemetry_verify.derive import AGENTS, OBSERVATIONS, SESSIONS, TRANSCRIPTS, Scope

VERB = "telemetry_orphan_counts"
_TABLES = (SESSIONS, OBSERVATIONS, AGENTS, TRANSCRIPTS)
TreeKey = tuple[str, str, datetime]


def _tree(row: Row) -> TreeKey:
    return (row["tenant_id"], row["project_id"], row["session_started_at"])


def _day(stamp: datetime) -> str:
    return stamp.strftime("%Y-%m-%d")


def orphan_counts(
    rows_by_table: Mapping[str, Iterable[Row]],
    joins: Iterable[tuple[str, str, str, str, str]],
    as_of: Any,
    idle_seconds: int,
    projects: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """The rows telemetry_orphan_counts answers over *rows_by_table* (one window's raw rows), ordered by day then join_id.

    *projects* maps project_id to purpose; when given, only real-purpose projects count (the verb's structural filter).
    """
    tables = {name: [normalise_row(r) for r in rows_by_table.get(name) or []] for name in _TABLES}
    scope = Scope(tables, VERB)
    when = parse_timestamp(as_of)
    keep = (lambda project: True) if projects is None else (lambda project: projects.get(project) == "real")

    last: dict[TreeKey, datetime] = {}
    for name in _TABLES:
        for row in scope.tables[name]:
            key = _tree(row)
            last[key] = max(last.get(key, row["event_timestamp"]), row["event_timestamp"])
    trees = {key: (when - stamp).total_seconds() > idle_seconds for key, stamp in last.items() if keep(key[1])}

    days: dict[str, dict[str, int]] = {}
    for key, quiescent in trees.items():
        counts = days.setdefault(_day(key[2]), {"trees_quiescent": 0, "trees_pending": 0, "conflicted_keys": 0})
        counts["trees_quiescent" if quiescent else "trees_pending"] += 1

    pin: dict[str, tuple[str, datetime]] = {}
    for name in _TABLES:
        for row in tables[name]:
            pin.setdefault(row["session_id"], (row["project_id"], row["session_started_at"]))
    for owner, *_ in scope.conflicts:
        project, started = pin[owner]
        if keep(project) and _day(started) in days:
            days[_day(started)]["conflicted_keys"] += 1

    joins = list(joins)
    child_rows: dict[tuple[str, str], list[int]] = {}
    for join_id, child_table, child_column, parent_table, parent_column in joins:
        parents: dict[TreeKey, set[Any]] = {}
        for row in scope.tables[parent_table]:
            parents.setdefault(_tree(row), set()).add(row.get(parent_column))
        for row in scope.tables[child_table]:
            key = _tree(row)
            value = row.get(child_column)
            if value is None or key not in trees:
                continue
            tally = child_rows.setdefault((_day(key[2]), join_id), [0, 0, 0])
            tally[0] += 1
            if value not in parents.get(key, ()):
                tally[1 if trees[key] else 2] += 1

    out = []
    for day in sorted(days):
        for join_id in sorted(j[0] for j in joins):
            total, n_quiescent, n_pending = child_rows.get((day, join_id), [0, 0, 0])
            out.append(
                {
                    "day": day,
                    "join_id": join_id,
                    "child_rows": total,
                    "orphans_quiescent": n_quiescent,
                    "orphans_pending": n_pending,
                    "trees_quiescent": days[day]["trees_quiescent"],
                    "trees_pending": days[day]["trees_pending"],
                    "conflicted_keys": days[day]["conflicted_keys"],
                }
            )
    return out
