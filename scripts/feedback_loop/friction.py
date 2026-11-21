"""The friction detector core: pure apart from the injected reader.

Reads one window of friction_signature_counts through scripts.telemetry_usage's windowed reads, sums the per-tree rows per
classifier_version and signature group, and derives friction_coverage and friction_recurring findings under the versioned
DETECTOR_PARAMS. rec_states reads the feedback_loop recs once so a filed, rejected or covered subject is listed as decided.
"""

from __future__ import annotations

import hashlib
import io
import json
from argparse import Namespace
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from scripts import telemetry_usage as tu
from scripts.feedback_loop import proposals
from src.common.ducklake_event_read_verbs import FRICTION_RULES

DETECTOR_VERSION = 1
DETECTOR_PARAMS: dict[str, Any] = {
    "detector_version": DETECTOR_VERSION,
    "window_days": 7,
    "coverage_bar": 0.20,
    "min_process_event_facts": 20,
    "min_sessions": 3,
    "min_facts": 5,
    "classes": ("exception", "rework"),
    "min_trees": 10,
}
COVERAGE = "friction_coverage"
RECURRING = "friction_recurring"
COVERAGE_SUBJECT = "coverage"
GROUP_KEYS = ("observation_type", "signature", "disposition", "rule_id", "label", "class", "generic")
REPEAT_RULE_ID = "run"
REC_KEYS = ("id", "status", "source", "created_timestamp", "last_updated_timestamp", "context_v2_json")
_RULES = {r[0]: r for r in FRICTION_RULES["rules"]}
_PUBLISHED = frozenset(r[2] for r in FRICTION_RULES["rules"] if r[2] is not None)
_LIVE = {"declined": "rejected", "superseded": "covered"}


@dataclass
class Finding:
    detector_id: str
    subject_key: str
    group: dict[str, Any] | None = None
    share: float | None = None
    state: str = "new"
    rec_id: str | None = None

    @property
    def proposed(self) -> bool:
        return self.state in ("new", "recurred")


@dataclass
class Result:
    first: date
    last: date
    classifier_version: int
    calls: int = 0
    rows: int = 0
    friction_trees: int = 0
    process_event_facts: int = 0
    unmapped_share: float | None = None
    coverage: str = "unmeasurable"
    findings: list[Finding] = field(default_factory=list)
    groups: list[dict[str, Any]] = field(default_factory=list)
    other_version_rows: int = 0
    conflicted_rows: int = 0
    unparsed_recs: int = 0
    response_bytes: int = 0
    elapsed_s: int = 0
    fact_rows: list[dict[str, Any]] = field(default_factory=list)


def is_published(signature: Any) -> bool:
    return signature in _PUBLISHED


def subject_key(rule_id: str, signature: str) -> str:
    rule = _RULES.get(rule_id)
    if rule is not None and rule[2] is not None and rule[2] == signature:
        return f"{rule_id}|{signature}"
    return f"{rule_id}|sha256:{hashlib.sha256(signature.encode('utf-8')).hexdigest()[:12]}"


def _named_failure(group: dict[str, Any]) -> bool:
    if group["rule_id"] == REPEAT_RULE_ID:
        return True
    rule = _RULES.get(group["rule_id"])
    return rule is not None and rule[2] is not None and rule[2] == group["signature"]


def read_window(reader: Any, first: date, last: date) -> tuple[list[dict[str, Any]], int]:
    reads = tu._Reads(reader, tu.VERBS["friction"], io.StringIO())
    for start, end in tu.windows(first, last):
        reads.read(start, end)
    return reads.rows, reads.calls


def _tree(row: dict[str, Any]) -> tuple[Any, ...]:
    return (row.get("root_session_id"), row.get("harness_session_key"), row.get("session_started_at"))


def sum_groups(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        key = tuple(row.get(k) for k in GROUP_KEYS)
        group = groups.setdefault(key, {**dict(zip(GROUP_KEYS, key)), "fact_count": 0, "session_count": 0, "trees": set()})
        group["fact_count"] += row["fact_count"]
        group["session_count"] += row["session_count"]
        group["trees"].add(_tree(row))
    out = []
    for group in groups.values():
        trees = group.pop("trees")
        out.append({**group, "tree_count": len(trees)})
    return out


def _fires(group: dict[str, Any], params: dict[str, Any]) -> bool:
    return (
        group["generic"] is False
        and group["class"] in params["classes"]
        and _named_failure(group)
        and group["session_count"] >= params["min_sessions"]
        and group["fact_count"] >= params["min_facts"]
    )


def _rank(finding: Finding) -> tuple[Any, ...]:
    group = finding.group or {}
    return (
        finding.detector_id != COVERAGE,
        -group.get("session_count", 0),
        -group.get("fact_count", 0),
        finding.subject_key,
    )


def detect(
    rows: list[dict[str, Any]], first: date, last: date, calls: int = 0, params: dict[str, Any] | None = None
) -> Result:
    params = params or DETECTOR_PARAMS
    version = FRICTION_RULES["classifier_version"]
    mine = [r for r in rows if r["classifier_version"] == version]
    result = Result(first, last, version, calls=calls, rows=len(rows), fact_rows=mine)
    result.other_version_rows = len(rows) - len(mine)
    result.conflicted_rows = sum(1 for r in rows if r.get("derivation_conflicted"))
    result.response_bytes = len(json.dumps(rows, default=str).encode("utf-8"))
    result.friction_trees = len({_tree(r) for r in mine})
    figures = tu.friction(rows, Namespace(workflow=None)).get(str(version), {})
    result.process_event_facts = figures.get("process_event_facts", 0)
    result.unmapped_share = figures.get("unmapped_failure_share")
    result.groups = sorted(
        sum_groups(mine), key=lambda g: (-g["fact_count"], str(g["rule_id"]), str(g["signature"]), str(g["observation_type"]))
    )
    if result.process_event_facts >= params["min_process_event_facts"]:
        fires = result.unmapped_share is not None and result.unmapped_share > params["coverage_bar"]
        result.coverage = "finding" if fires else "ok"
        if fires:
            result.findings.append(Finding(COVERAGE, COVERAGE_SUBJECT, share=result.unmapped_share))
    for group in result.groups:
        if _fires(group, params):
            result.findings.append(Finding(RECURRING, subject_key(str(group["rule_id"]), str(group["signature"])), group))
    result.findings.sort(key=_rank)
    return result


def rec_states(reader: Any) -> list[dict[str, Any]]:
    rows = reader.current_state("ops_recommendations", row_filter=f"source = '{proposals.SOURCE}'")
    for row in rows:
        missing = [k for k in REC_KEYS if k not in row]
        if missing:
            raise RuntimeError(f"rec read row {row.get('id', '<unknown>')!r} lacks keys {missing}")
    return rows


def _context(row: dict[str, Any]) -> dict[str, Any] | None:
    raw = row["context_v2_json"]
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    return raw if isinstance(raw, dict) else None


def _day(value: Any) -> date:
    return date.fromisoformat(str(value)[:10])


def _rec_order(row: dict[str, Any]) -> tuple[str, int]:
    digits = "".join(c for c in str(row["id"]) if c.isdigit())
    return (str(row["created_timestamp"]), int(digits) if digits else 0)


def apply_rec_states(result: Result, rec_rows: list[dict[str, Any]]) -> None:
    known: dict[tuple[Any, Any], list[dict[str, Any]]] = {}
    for row in rec_rows:
        context = _context(row)
        if context is None:
            result.unparsed_recs += 1
            continue
        known.setdefault((context.get("detector_id"), context.get("subject_key")), []).append(row)
    for finding in result.findings:
        matches = known.get((finding.detector_id, finding.subject_key))
        if not matches:
            continue
        newest = max(matches, key=_rec_order)
        finding.rec_id = str(newest["id"])
        status = newest["status"]
        if status in ("closed", "failed"):
            reopened = result.first > _day(newest["last_updated_timestamp"])
            finding.state = "recurred" if reopened else "closing"
        else:
            finding.state = _LIVE.get(status, "filed")
