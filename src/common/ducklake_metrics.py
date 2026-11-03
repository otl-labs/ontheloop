"""DuckLake CloudWatch metric emission (EC9; split from ducklake_runtime).

Owner concern: best-effort CloudWatch metric emission for the write path. No dependency on
ducklake_scd2_schema or any other DuckLake module -- this is a pure AWS-emission leaf.
"""

from __future__ import annotations

import json
from typing import Any, Callable

# CloudWatch metric namespace for OCC-retry + commit-latency emission (EC9).
CLOUDWATCH_NAMESPACE = "DuckLakeWriter"


def emit_metric(
    name: str,
    value: float,
    *,
    namespace: str = CLOUDWATCH_NAMESPACE,
    unit: str = "None",
    profile: str | None = None,
    client: Any = None,
    dimensions: dict[str, str] | None = None,
) -> None:
    """Emit a single CloudWatch metric datum. Best-effort: a metrics failure must not fail a write.

    Pass `client` to inject a CloudWatch client (tests / a shared client). In the Lambda the ambient
    execution-role credentials are used (no profile). `dimensions` makes the datum a distinct
    CloudWatch metric: an alarm declared with no dimensions never sees a dimensioned datum.
    """
    try:
        if client is None:
            import boto3  # noqa: PLC0415

            from scripts.aws_profile import resolve_aws_profile  # noqa: PLC0415

            session = boto3.Session(profile_name=resolve_aws_profile(profile))
            client = session.client("cloudwatch")
        datum: dict[str, Any] = {"MetricName": name, "Value": float(value), "Unit": unit}
        if dimensions:
            datum["Dimensions"] = [{"Name": k, "Value": v} for k, v in dimensions.items()]
        client.put_metric_data(Namespace=namespace, MetricData=[datum])
    except Exception:  # noqa: BLE001 -- metrics are observability, never a write-blocking failure
        pass


def make_metric_sink(
    *, namespace: str = CLOUDWATCH_NAMESPACE, client: Any = None, profile: str | None = None
) -> Callable[[str, float], None]:
    """Build a metric_sink(name, value) closure for write_scd2 that emits to CloudWatch."""

    def _sink(name: str, value: float) -> None:
        unit = "Milliseconds" if name.endswith("Ms") else "Count"
        emit_metric(name, value, namespace=namespace, unit=unit, client=client, profile=profile)

    return _sink


def log_invocation(
    function: str,
    action: str,
    status: int,
    connect_ms: float | None,
    connect_reused: bool | None,
    reopened: bool,
    elapsed_ms: float,
    *,
    response_bytes: int | None = None,
) -> None:
    """Print one compact JSON line per invocation, filterable with a CloudWatch Logs JSON pattern.

    Fixed fields only: never a payload value, table row, host or error message. The caller passes a registered
    action name or the fixed token "unknown". `response_bytes` (the measured Function-URL envelope size) is
    emitted only when passed, so a call without it keeps the original eight-key line.
    """
    line: dict[str, Any] = {
        "event": "ducklake_invocation",
        "function": function,
        "action": action,
        "status": status,
        "connect_ms": connect_ms,
        "connect_reused": connect_reused,
        "reopened": reopened,
        "elapsed_ms": round(elapsed_ms, 2),
    }
    if response_bytes is not None:
        line["response_bytes"] = response_bytes
    print(json.dumps(line, separators=(",", ":")))
