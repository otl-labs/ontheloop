"""C6 for the telemetry writer (REVIEW.md C6; T2.36 plan 3 part 2): ONE JSON line per answered request, with a
CloudWatch Embedded Metric Format block on the six data verbs.

Fixed fields only. Never an account id, an ARN, a hash of either (Decision 101; ops_tenants.yaml) or a row value:
caller_role is CallerPrincipal.role_name, tenant_id and project_id are the registry's ULIDs, and every millisecond value
is an integer (a raw float repr can carry a 12-digit run that a leak scan would misread).

EMF: namespace DuckLakeTelemetryWriter, metric RequestCount with dimension set [Verb] (six series; begin_blobs is the
sixth, plan 4), with the target members `Verb` and `RequestCount` at the line's top level beside the lowercase `verb`
field. An append_telemetry line also carries OccRetryCount and CommitLatencyMs under an empty dimension set (the series
part 1's sink already creates) and under [MetaSchema], read from the function's own TELEMETRY_META_SCHEMA env, never a
caller value, so a production alarm cannot see another catalog's traffic. That directive and its members appear only
when the recording sink saw values: a directive whose members are absent invalidates the block and drops RequestCount
with it. The Lambda text log format is required for extraction (the function sets no JSON log format; a test pins that).

A begin_blobs line carries counts and declared bytes only (items, distinct_keys, existed, granted, stale,
declared_bytes_total, declared_bytes_max, head_ms); an append_telemetry line carries blob_rows and blob_head_ms. Never a
presigned URL, key or digest (Decision 101; REVIEW.md C7).
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

from src.common.ducklake_telemetry_registration import CallerPrincipal
from src.telemetry.request import COMPONENT_RE, DEFAULT_COMPONENT

NAMESPACE = "DuckLakeTelemetryWriter"
DATA_VERBS = frozenset(
    {"append_telemetry", "begin_blobs", "register_tenant", "register_project", "provision_telemetry", "telemetry_probe"}
)
BLOB_COUNT_FIELDS = (
    "items",
    "distinct_keys",
    "existed",
    "granted",
    "stale",
    "declared_bytes_total",
    "declared_bytes_max",
    "head_ms",
)
_ID_RE = re.compile(r"^[0-9A-Z]{26}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class RequestStats:
    """What one request learned, filled by the edge and the recording metric sink, read when the line is emitted."""

    tenant_id: str | None = None
    project_id: str | None = None
    tables: dict[str, tuple[int, int]] = field(default_factory=dict)  # table -> (submitted, inserted)
    connect_reused: bool | None = None
    masked_commit_retries: int | None = None  # a log field only: never a metric, no EMF member
    blob_rows: int | None = None
    blob_head_ms: int | None = None
    blob_counts: dict[str, int] = field(default_factory=dict)  # begin_blobs only
    recorded: dict[str, float] = field(default_factory=dict)

    def sink(self, name: str, value: float) -> None:
        self.recorded[name] = value


def _component(payload: Any) -> str:
    value = payload.get("component") if isinstance(payload, dict) else None
    return value if isinstance(value, str) and COMPONENT_RE.match(value) else DEFAULT_COMPONENT


def _clean_id(value: str | None) -> str | None:
    return value if value is not None and _ID_RE.match(value) else None


def _meta_schema() -> str:
    value = os.environ.get("TELEMETRY_META_SCHEMA", "")
    return value if _IDENTIFIER_RE.match(value) else "unknown"


def _emf(verb: str, recorded: dict[str, float]) -> dict[str, Any]:
    directives: list[dict[str, Any]] = [
        {"Namespace": NAMESPACE, "Dimensions": [["Verb"]], "Metrics": [{"Name": "RequestCount", "Unit": "Count"}]}
    ]
    members: dict[str, Any] = {"Verb": verb, "RequestCount": 1}
    metrics = []
    if "OccRetryCount" in recorded:
        metrics.append({"Name": "OccRetryCount", "Unit": "Count"})
        members["OccRetryCount"] = int(recorded["OccRetryCount"])
    if "CommitLatencyMs" in recorded:
        metrics.append({"Name": "CommitLatencyMs", "Unit": "Milliseconds"})
        members["CommitLatencyMs"] = int(round(recorded["CommitLatencyMs"]))
    if metrics and verb == "append_telemetry":
        members["MetaSchema"] = _meta_schema()
        directives.append({"Namespace": NAMESPACE, "Dimensions": [[], ["MetaSchema"]], "Metrics": metrics})
    else:
        for name in ("OccRetryCount", "CommitLatencyMs"):
            members.pop(name, None)
    return {"_aws": {"Timestamp": int(time.time() * 1000), "CloudWatchMetrics": directives}, **members}


def build_request_line(
    action: str,
    status: int,
    error_type: str | None,
    *,
    payload: Any,
    principal: CallerPrincipal | None,
    request_bytes: int | None,
    invoke_bytes: int | None,
    stats: RequestStats,
) -> dict[str, Any]:
    recorded = stats.recorded
    commit_ms = recorded.get("CommitLatencyMs")
    line: dict[str, Any] = {
        "event": "telemetry_request",
        "verb": action,
        "component": _component(payload),
        "status": status,
        "error_type": error_type,
        "tables": {t: {"submitted": s, "inserted": i} for t, (s, i) in stats.tables.items()},
        "request_bytes": request_bytes,
        "invoke_bytes": invoke_bytes,
        "tenant_id": _clean_id(stats.tenant_id),
        "project_id": _clean_id(stats.project_id),
        "occ_retries": int(recorded["OccRetryCount"]) if "OccRetryCount" in recorded else None,
        "commit_ms": int(round(commit_ms)) if commit_ms is not None else None,
        "connect_reused": stats.connect_reused,
        "masked_commit_retries": stats.masked_commit_retries,
        "caller_role": principal.role_name if principal is not None else None,
    }
    if action == "append_telemetry":
        line.update(blob_rows=stats.blob_rows, blob_head_ms=stats.blob_head_ms)
    if action == "begin_blobs":
        line.update({name: stats.blob_counts.get(name) for name in BLOB_COUNT_FIELDS})
    if action in DATA_VERBS:
        line.update(_emf(action, recorded))
    return line


def emit_request_line(*args: Any, **kwargs: Any) -> None:
    print(json.dumps(build_request_line(*args, **kwargs), separators=(",", ":")), flush=True)
