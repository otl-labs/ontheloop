"""The verification monitor's pure comparisons over plain rows and verb answers (no I/O).

A deployed row (JSON-decoded: timestamps as strings) and a derived row (the oracle's: aware datetimes) agree when every column
of the verb's field list agrees; a session whose Decision 207 stamps report a conflict compares the three stamps only.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence, cast

from scripts.telemetry_verify.dedupe import parse_timestamp
from src.common.ducklake_reader_client import VERB_FIELDS

STAMP_COLUMNS = ("generations", "conflicted_grain_keys", "derivation_conflicted")


def _fields(verb: str) -> Sequence[str]:
    return cast(Sequence[str], VERB_FIELDS[verb])


def _plain(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _same(left: Any, right: Any) -> bool:
    if isinstance(left, datetime) or isinstance(right, datetime):
        try:
            return parse_timestamp(left) == parse_timestamp(right)
        except (TypeError, ValueError):
            return False
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-12)
    if isinstance(left, (list, tuple, Mapping)) or isinstance(right, (list, tuple, Mapping)):
        return json.dumps(_plain(left), sort_keys=True, default=str) == json.dumps(_plain(right), sort_keys=True, default=str)
    return left == right


def _timestamp_like(value: Any) -> Any:
    """A string that parses as an ISO timestamp is compared as an instant; other strings compare as text."""
    if isinstance(value, str) and len(value) >= 19 and value[4:5] == "-" and value[10:11] in ("T", " "):
        try:
            return parse_timestamp(value)
        except ValueError:
            return value
    return value


def _conflicted(row: Mapping[str, Any]) -> bool:
    return bool(row.get("derivation_conflicted"))


def compare_row(columns: Sequence[str], deployed: Mapping[str, Any], derived: Mapping[str, Any]) -> list[str]:
    """The columns on which two rows differ; the stamps alone when either reports a Decision 207 conflict."""
    wanted = STAMP_COLUMNS if _conflicted(deployed) or _conflicted(derived) else tuple(columns)
    return [c for c in wanted if not _same(_timestamp_like(deployed.get(c)), _timestamp_like(derived.get(c)))]


def compare_session(
    verb: str, deployed_rows: Sequence[Mapping[str, Any]], derived_rows: Sequence[Mapping[str, Any]]
) -> list[str]:
    """The differing columns of one per-session verb call (``<rows>`` when the row counts differ), empty when they agree."""
    if len(deployed_rows) != len(derived_rows):
        return ["<rows>"]
    out: set[str] = set()
    for deployed, derived in zip(deployed_rows, derived_rows):
        out.update(compare_row(_fields(verb), deployed, derived))
    return sorted(out)


def compare_window(
    deployed_rows: Iterable[Mapping[str, Any]], derived_rows: Iterable[Mapping[str, Any]], roots: Iterable[str]
) -> tuple[int, int]:
    """(agreeing, compared) over *roots* only: each read root's deployed sessions_window row against the derived one."""
    deployed_rows, derived_rows = list(deployed_rows), list(derived_rows)
    deployed = {r["session_id"]: r for r in deployed_rows}
    derived = {r["session_id"]: r for r in derived_rows}
    seen = Counter(r["session_id"] for r in deployed_rows) + Counter(r["session_id"] for r in derived_rows)
    agreeing = compared = 0
    for root in sorted(set(roots)):
        compared += 1
        if (
            seen[root] == 2
            and root in deployed
            and root in derived
            and not compare_row(_fields("sessions_window"), deployed[root], derived[root])
        ):
            agreeing += 1
    return agreeing, compared


def compare_orphans(
    verb_counts: Iterable[Mapping[str, Any]],
    independent_counts: Iterable[Mapping[str, Any]],
    discovered_roots_by_day: Mapping[str, int],
) -> dict[str, Any]:
    """Orphan agreement over the counted days.

    verb_counts are telemetry_orphan_counts rows for the counted days; independent_counts the independent count over the read,
    unmoved trees of those days. Returns quiescent (per join: orphans_quiescent), child_rows (per join, verb), pending,
    undercount (per join: positive independent - verb orphans_quiescent), independent_child_rows (per join), rootless (per
    day with a discovered-root figure: verb trees beyond the roots discovered, never negative) and conflicted_keys.
    """
    verb = list(verb_counts)
    quiescent: dict[str, int] = {}
    child_rows: dict[str, int] = {}
    pending = conflicted = 0
    by_key = {(r["day"], r["join_id"]): r for r in verb}
    for row in verb:
        join = row["join_id"]
        quiescent[join] = quiescent.get(join, 0) + int(row["orphans_quiescent"])
        child_rows[join] = child_rows.get(join, 0) + int(row["child_rows"])
        pending += int(row["orphans_pending"])
    for day in {r["day"] for r in verb}:
        conflicted += int(next(r["conflicted_keys"] for r in verb if r["day"] == day))
    undercount: dict[str, int] = {}
    independent_rows: dict[str, int] = {}
    for row in independent_counts:
        join = row["join_id"]
        independent_rows[join] = independent_rows.get(join, 0) + int(row["child_rows"])
        seen = by_key.get((row["day"], join))
        verb_orphans = 0 if seen is None else int(seen["orphans_quiescent"])
        gap = int(row["orphans_quiescent"]) - verb_orphans
        if gap > 0:
            undercount[join] = undercount.get(join, 0) + gap
    rootless = 0
    for day, roots in discovered_roots_by_day.items():
        rows = [r for r in verb if r["day"] == day]
        if rows:
            rootless += max(0, int(rows[0]["trees_quiescent"]) + int(rows[0]["trees_pending"]) - roots)
    return {
        "quiescent": quiescent,
        "child_rows": child_rows,
        "pending": pending,
        "undercount": undercount,
        "independent_child_rows": independent_rows,
        "rootless": rootless,
        "conflicted_keys": conflicted,
    }
