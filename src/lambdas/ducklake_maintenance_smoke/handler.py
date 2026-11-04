"""ducklake_maintenance_smoke Lambda entrypoint (T2.18 c9 follow-on; bundled Decision amending
Decision 81 clause 1, runtime artifacts 3 -> 4).

CI-invokable smoke-safe sibling of ducklake_maintenance (src/lambdas/ducklake_maintenance/handler.py).
Dispatch table is EXACTLY {merge, gc, breaker_probe, hot_merge, create_telemetry_tables}
(Decision 216, amending Decision 143 clause 2) -- the four non-destructive, disposable-smoke-catalog
cadences, sharing src/common/ducklake_maintenance.py with the admin function, plus the owner-login telemetry DDL verb
(create-if-absent and the Decision 222 additive leg) (src/lambdas/ducklake_maintenance_smoke/telemetry_actions.py).
None of the admin function's production-destructive or operational verbs are reachable
here: the boundary is the resource shape -- a separate Lambda + IAM execution role scoped to the
smoke S3 prefix only -- not an in-handler guard an agent-authored codebase could edit out (Fable
frontier-architecture consult, bundled Decision: scope every identity by the worst verb it can
reach). See src/lambdas/ducklake_maintenance/handler.py's module docstring for the admin verb list.

Always operates on the smoke catalog: DUCKLAKE_DATA_PATH / DUCKLAKE_META_SCHEMA default to the
smoke path/schema and are NEVER read from the event -- env-pinned, so a crafted CI payload cannot
redirect this Lambda at the production catalog.

Singleton via reserved_concurrent_executions=1 (Terraform), same invariant as the admin function.
No LLM / agent invocation anywhere in this path (CD.33 clause 5 / Decision 81 clause 6).
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from src.common import ducklake_maintenance as maint
from src.common import ducklake_maintenance_ops as gcops
from src.common import ducklake_runtime as rt
from src.lambdas.ducklake_maintenance_smoke import telemetry_actions

DATA_PATH = os.environ.get("DUCKLAKE_DATA_PATH", rt.SMOKE_DATA_PATH)
META_SCHEMA = os.environ.get("DUCKLAKE_META_SCHEMA", rt.SMOKE_META_SCHEMA)
EXTENSION_DIRECTORY = os.environ.get("DUCKLAKE_EXTENSION_DIRECTORY", rt.LAMBDA_EXTENSION_DIRECTORY)
DSN_PARAMETER_NAME = os.environ.get("DUCKLAKE_DSN_PARAMETER", rt.DSN_PARAMETER_NAME)

# Table scope: ducklake_smoke_* only (this Lambda never targets the production catalog).
_SCOPE_TABLES = maint.GC_TABLE_SCOPE
_HOT_SCOPE_TABLES = maint.HOT_TABLE_SCOPE


def _open_connection() -> Any:
    """Open a smoke-scoped baked-extension connection to the Neon catalog (always ducklake_smoke)."""
    dsn = rt.fetch_dsn(DSN_PARAMETER_NAME)
    return rt.open_connection(dsn=dsn, data_path=DATA_PATH, meta_schema=META_SCHEMA, extension_directory=EXTENSION_DIRECTORY)


# Every smoke datum carries this dimension so the production breaker alarm (declared with NO
# dimensions in terraform/personal/ducklake_maintenance.tf) never fires on a smoke trip -- the
# per-deploy breaker_probe gate trips this Lambda deliberately. The smoke alarm selects it.
METRIC_DIMENSIONS: dict[str, str] = {"Catalog": "smoke"}


def _emit_maintenance_metric(name: str, value: float, *, profile: str | None = None) -> None:
    rt.emit_metric(
        name, value, namespace=maint.MAINTENANCE_CLOUDWATCH_NAMESPACE, profile=profile, dimensions=METRIC_DIMENSIONS
    )


# ---------------------------------------------------------------------------
# Actions -- smoke-safe subset only (logic mirrors the admin handler's
# action_merge/action_gc/action_hot_merge/action_breaker_probe verbatim)
# ---------------------------------------------------------------------------


def action_merge(event: dict[str, Any], con: Any) -> dict[str, Any]:
    """Daily non-destructive merge: flush_inlined_data + merge_adjacent_files.

    Accepts force_recreate_tables=True (re-creates the smoke tables, idempotent re-run).
    """
    if event.get("force_recreate_tables"):
        rt.create_scd2_tables(con, force_recreate=True)

    t0 = time.perf_counter()
    result = maint.run_merge(con, _SCOPE_TABLES)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    result["elapsed_ms"] = round(elapsed_ms, 2)

    _emit_maintenance_metric("MergeDurationMs", elapsed_ms)
    _emit_maintenance_metric("FilesBeforeMerge", float(result["files_before"]))
    _emit_maintenance_metric("FilesAfterMerge", float(result["files_after_merge"]))
    return result


def action_gc(event: dict[str, Any], con: Any) -> dict[str, Any]:
    """Weekly guarded GC: full five-step sequence behind the G1-G4 guard set.

    Accepts force_recreate_tables=True (drop/recreate smoke tables before GC; test harness). When
    set, also seeds one SCD2 row into the smoke pair so the pre-GC live set is non-empty and G3
    (empty-live-set catalog sanity) is exercised non-vacuously (rec-3802) -- the plain gc verb (no
    force_recreate_tables) issues no write, so the real weekly cadence never seeds.
    """
    if event.get("force_recreate_tables"):
        rt.create_scd2_tables(con, force_recreate=True)
        rt.write_scd2(con, {"rec_id": "rec-gc-smoke-seed", "payload": "gc-smoke-seed"})

    t0 = time.perf_counter()
    result = maint.run_gc(con, _SCOPE_TABLES, data_path=DATA_PATH)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    result["elapsed_ms"] = round(elapsed_ms, 2)

    _emit_maintenance_metric("GcDurationMs", elapsed_ms)
    _emit_maintenance_metric("FilesBeforeGc", float(result["files_before"]))
    _emit_maintenance_metric("FilesAfterGc", float(result["files_after"]))
    _emit_maintenance_metric("SnapshotsExpired", float(result["snapshots_expired"]))
    _emit_maintenance_metric("FilesCleaned", float(result["files_cleaned"]))
    _emit_maintenance_metric("OrphansDeleted", float(result["orphans_deleted"]))
    _emit_maintenance_metric("MaintenanceBreakerTrip", 0.0)
    return result


def action_hot_merge(event: dict[str, Any], con: Any) -> dict[str, Any]:
    """Higher-frequency merge-only cadence (T2.18 FP-B / CD.34).

    Invokes merge_adjacent_files ONLY over HOT_TABLE_SCOPE. No snapshot expiry, no file
    deletion. Accepts force_recreate_tables=True (re-creates the smoke tables, idempotent re-run).
    """
    if event.get("force_recreate_tables"):
        rt.create_scd2_tables(con, force_recreate=True)

    t0 = time.perf_counter()
    result = maint.run_hot_merge(con, _HOT_SCOPE_TABLES)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    result["elapsed_ms"] = round(elapsed_ms, 2)

    _emit_maintenance_metric("HotMergeDurationMs", elapsed_ms)
    _emit_maintenance_metric("FilesBeforeHotMerge", float(result["files_before"]))
    _emit_maintenance_metric("FilesAfterHotMerge", float(result["files_after"]))
    return result


def action_breaker_probe(event: dict[str, Any], _con: Any) -> dict[str, Any]:
    """Forced G1 trip: guaranteed independent of the smoke catalog's real contents.

    Successor to the retired forced-zero-threshold probe: that probe only tripped when at least
    one deletable file existed, so an empty smoke catalog silently no-op'd it.
    gcops.probe_g1_trip() forces a G1 reachability violation via an internal module-level
    constant, independent of catalog contents -- it always raises.

    On trip: re-raises DuckLakeMaintenanceError. The handler's outer catch emits
    MaintenanceBreakerTrip=1 exactly once -- do NOT emit it here (single emit point, no double-emit).

    Returns 500 with breaker_tripped=True so the smoke-test gate can assert the loud-fail.
    """
    gcops.probe_g1_trip()


# ---------------------------------------------------------------------------
# Dispatch -- EXACTLY the 5 smoke actions. No production-destructive or operational verb is
# reachable here (blast-radius invariant; enforced by the resource split, not this dict alone).
# ---------------------------------------------------------------------------

_ACTIONS: dict[str, Any] = {
    "merge": action_merge,
    "gc": action_gc,
    "hot_merge": action_hot_merge,
    "breaker_probe": action_breaker_probe,
    "create_telemetry_tables": telemetry_actions.action_create_telemetry_tables,
}


def _parse_event(event: dict[str, Any]) -> dict[str, Any]:
    """Extract action payload from a Function-URL event (body JSON) or a direct-invoke dict."""
    if isinstance(event, dict) and "body" in event and event.get("body") is not None:
        body = event["body"]
        if isinstance(body, str):
            return json.loads(body) if body else {}
        if isinstance(body, dict):
            return body
    return event if isinstance(event, dict) else {}


def _response(status: int, payload: dict[str, Any]) -> dict[str, Any]:
    """Build a Function-URL response envelope. A 5xx body is also printed to CloudWatch Logs: an
    asynchronous (EventBridge) invoke discards the response, so the log is the only record of why."""
    body = json.dumps(payload)
    if status >= 500:
        print(f"MAINTENANCE_SMOKE_FAILURE status={status} body={body}")
    return {"statusCode": status, "headers": {"Content-Type": "application/json"}, "body": body}


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Smoke-safe maintenance Lambda entrypoint. Dispatches `action`; loud-fail maps to 4xx/5xx."""
    payload = _parse_event(event)
    action: str | None = payload.get("action")
    fn = _ACTIONS.get(action or "")
    if fn is None:
        return _response(400, {"ok": False, "error": f"unknown action {action!r}", "actions": sorted(_ACTIONS)})

    try:
        t0 = time.perf_counter()
        con = _open_connection()
        payload["_connect_ms"] = (time.perf_counter() - t0) * 1000.0
        try:
            return _response(200, fn(payload, con))
        finally:
            con.close()
    except maint.DuckLakeMaintenanceError as exc:
        _emit_maintenance_metric("MaintenanceBreakerTrip", 1.0)
        return _response(500, {"ok": False, "error_type": "breaker", "breaker_tripped": True, "error": str(exc)})
    except rt.VersionMismatchError as exc:
        return _response(500, {"ok": False, "error_type": "version_mismatch", "error": str(exc)})
    except rt.DuckLakeRuntimeError as exc:
        return _response(500, {"ok": False, "error_type": "runtime", "error": str(exc)})
