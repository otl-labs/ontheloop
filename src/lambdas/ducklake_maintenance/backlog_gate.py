"""Backlog gate for the scheduled telemetry merge (PLAN-telemetry-read-timeouts, Decision 236 cl.5 amendment).

merge_ops_gated decides from CloudWatch only whether to run the unchanged merge_ops pass: it merges once at least
min_commits telemetry commits have landed since the last pass, or once the oldest of them has waited
max_unmerged_minutes. A skip or an unreadable gate never opens a catalog connection; the every-6h merge rule stays
the floor, so an unreadable gate skips (and alarms) rather than merging on every tick.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3

from src.common import ducklake_maintenance as maint
from src.common import ducklake_runtime as rt
from src.lambdas.ducklake_maintenance import _shared

ACTION = "merge_ops_gated"
WRITER_NAMESPACE = "DuckLakeTelemetryWriter"
COMMIT_METRIC = "CommitLatencyMs"
PASS_METRICS = ("MergeOpsTablesCount", "MergeOpsGateTriggered")
LOOKBACK = timedelta(hours=6)
PERIOD_SECONDS = 60

_GATE_FIELDS = ("min_commits", "max_unmerged_minutes")

# This warm container's own last trigger time: a pass that fails before MergeOpsTablesCount lands still marks it.
_LAST_TRIGGER: datetime | None = None


@dataclass(frozen=True)
class GateDecision:
    merge: bool
    reason: str
    commits: int | None
    oldest_commit: datetime | None
    last_pass: datetime | None


def _now() -> datetime:
    return datetime.now(UTC)


def _datapoints(client: Any, namespace: str, metric: str, meta_schema: str, start: datetime, end: datetime) -> list[dict]:
    response = client.get_metric_statistics(
        Namespace=namespace,
        MetricName=metric,
        Dimensions=[{"Name": "MetaSchema", "Value": meta_schema}],
        StartTime=start,
        EndTime=end,
        Period=PERIOD_SECONDS,
        Statistics=["SampleCount"],
    )
    return list(response.get("Datapoints") or [])


def decide(gate: dict[str, int], meta_schema: str, now: datetime, client: Any) -> GateDecision:
    """Read the last pass and the committed appends since it; merge at min_commits or a commit max_unmerged_minutes old."""
    try:
        window_start = now - LOOKBACK
        marks = [
            point["Timestamp"]
            for metric in PASS_METRICS
            for point in _datapoints(client, maint.MAINTENANCE_CLOUDWATCH_NAMESPACE, metric, meta_schema, window_start, now)
        ]
        if _LAST_TRIGGER is not None:
            marks.append(_LAST_TRIGGER)
        last_pass = max(marks) if marks else window_start
        points = _datapoints(client, WRITER_NAMESPACE, COMMIT_METRIC, meta_schema, last_pass, now)
        commits = int(sum(point.get("SampleCount", 0) for point in points))
        stamps = [point["Timestamp"] for point in points if point.get("SampleCount", 0) > 0]
    except Exception:  # noqa: BLE001 -- any read failure is an unreadable gate: skip and alarm, never merge
        return GateDecision(False, "gate_unreadable", None, None, None)
    oldest = min(stamps) if stamps else None
    if commits >= gate["min_commits"]:
        return GateDecision(True, "backlog", commits, oldest, last_pass)
    if oldest is not None and now - oldest >= timedelta(minutes=gate["max_unmerged_minutes"]):
        return GateDecision(True, "age", commits, oldest, last_pass)
    return GateDecision(False, "below_threshold", commits, oldest, last_pass)


def _require_gate(event: dict[str, Any]) -> dict[str, int]:
    gate = event.get("backlog_gate")
    if not isinstance(gate, dict):
        raise rt.DuckLakeRuntimeError(f"{ACTION} requires a 'backlog_gate' object {{min_commits, max_unmerged_minutes}}")
    for field in _GATE_FIELDS:
        value = gate.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise rt.DuckLakeRuntimeError(f"{ACTION} backlog_gate.{field} must be a positive integer, got {value!r}")
    return {field: gate[field] for field in _GATE_FIELDS}


def _sink(meta_schema: str) -> Callable[[str, float], None]:
    dims = _shared.catalog_dimensions(meta_schema)

    def emit(name: str, value: float) -> None:
        if dims is None:
            _shared._emit_maintenance_metric(name, value)
        elif name in _shared.CATALOG_METRICS:
            _shared._emit_maintenance_metric(name, value, dimensions=dims)

    return emit


def gated(action_fn: Callable[[dict[str, Any], Any], dict[str, Any]]) -> Callable[[dict[str, Any], Any], dict[str, Any]]:
    """Wrap merge_ops: run it with the same event only when the backlog gate says merge."""

    def action_merge_ops_gated(event: dict[str, Any], con: Any) -> dict[str, Any]:
        global _LAST_TRIGGER
        gate = _require_gate(event)
        meta_schema = _shared._require_identifier(event.get("meta_schema"))
        emit = _sink(meta_schema)
        now = _now()
        decision = decide(gate, meta_schema, now, boto3.client("cloudwatch"))
        if decision.commits is None:
            emit("MergeOpsGateUnreadable", 1.0)
        else:
            emit("MergeOpsBacklogCommits", float(decision.commits))
        if not decision.merge:
            return {
                "ok": True,
                "action": ACTION,
                "skipped": True,
                "reason": decision.reason,
                "commits": decision.commits,
                "last_pass": decision.last_pass.isoformat() if decision.last_pass else None,
            }
        _LAST_TRIGGER = now
        emit("MergeOpsGateTriggered", 1.0)
        return action_fn(event, con)

    return action_merge_ops_gated
