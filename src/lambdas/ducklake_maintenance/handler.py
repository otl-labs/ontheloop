"""ducklake_maintenance Lambda entrypoint (T2.18 / CD.33, Decision 81; T2.18 c9 follow-on split,
bundled Decision amending Decision 81 clause 1, runtime artifacts 3 -> 4).

Admin-gated singleton retaining the production-destructive and operational verbs. The
non-destructive smoke cadences (action=merge / action=gc / action=hot_merge / action=breaker_probe)
were split out to the CI-invokable src/lambdas/ducklake_maintenance_smoke/handler.py sibling, which
shares src/common/ducklake_maintenance.py -- this function no longer runs them, so it no longer
pre-opens a shared smoke-scoped connection (every remaining action manages its own connection, or
is connectionless). The only surviving SCHEDULED cadence on this function is action=merge_ops
(every 6h, non-destructive, production ops_* catalog).

OPERATIONAL admin actions are invoked over 443 via `aws lambda invoke` (NOT public Function URLs,
NOT agent surfaces): catalog_reinit (rec-2099 fix -- drop the squatting meta-schema + re-init at the
production DATA_PATH), restore_drill (pg_dump->pg_restore + read-your-write DR gate),
reconcile_columns, catalog_stats, and clone_catalog (OQ.12 canary rehearsal), plus the operator-only
ensure_catalog_indexes (Decision 81 cl.6: no agent invokes it). These target the
PRODUCTION catalog (ducklake_ops) via explicit event params. (The TEMPORARY
seed_ops_recommendations bootstrap action was removed at the 2026-06-09 recs sign-off -- the closed
boundary now admits recs writes only via the portal `file_rec`/`update_rec` -> writer path,
Decision 81 cl.7.)

The scheduled path is agent-free; an ADMIN-container agent may invoke the partition-layout verbs (Decision 204)
and provision_telemetry_login (Decision 213), provision_catalog_owner and provision_telemetry_catalog (Decision 230) only under
explicit human direction (CD.33 clause 5 / Decision 81 cl.6).
Singleton enforced by reserved_concurrent_executions=1 (Decision 81 clause 6; see Terraform).

See src/common/ducklake_maintenance.py::MAINTENANCE_SCOPE_NOTE.
"""

from __future__ import annotations

import json
import time
from typing import Any

from src.common import catalog_dr
from src.common import ducklake_gc_ops as gc_ops_body
from src.common import ducklake_maintenance as maint
from src.common import ducklake_maintenance_scope as scope
from src.common import ducklake_runtime as rt
from src.lambdas.ducklake_maintenance import catalog_actions, health_actions, index_actions, login_actions, partition_actions
from src.lambdas.ducklake_maintenance._shared import (  # noqa: F401 -- DATA_PATH re-bound for existing callers
    CATALOG_METRICS,
    DATA_PATH,
    EXTENSION_DIRECTORY,
    _emit_maintenance_metric,
    _require_identifier,
    catalog_dimensions,
)

# ---------------------------------------------------------------------------
# Operational actions (T2.19 recs cutover) -- invoked over 443 via `aws lambda invoke`, NOT public
# Function URLs and NOT agent surfaces. They target the PRODUCTION catalog (ducklake_ops) via explicit
# event params and manage their own connections. catalog_reinit + restore_drill are retained as
# operational DR ops. The TEMPORARY seed_ops_recommendations bootstrap was removed at the
# 2026-06-09 recs sign-off (closed boundary -- recs writes now transit only the portal -> writer
# path, Decision 81 cl.7).
# ---------------------------------------------------------------------------


def _drop_meta_schema(meta_schema: str, *, recreate: bool = False) -> bool:
    """Break-glass: DROP a DuckLake meta-schema in Neon Postgres (psycopg2). Returns True if it ran.

    The DuckLake DATA_PATH pin lives in this meta-schema's metadata tables, so dropping it is what
    makes a re-ATTACH at a new DATA_PATH possible. DESTRUCTIVE -- the caller has confirmed the state
    is disposable.

    `recreate=True` re-creates the schema EMPTY after the drop. DuckLake v1.0 does not auto-create
    the Postgres meta-schema on ATTACH -- it errors "Schema not found" -- so a reinit/init must leave an
    empty schema for the next ATTACH to initialize its metadata tables into (at the new DATA_PATH).
    """
    import psycopg2  # noqa: PLC0415

    _require_identifier(meta_schema)
    conn = psycopg2.connect(rt.libpq_conninfo(rt.fetch_dsn()))
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {meta_schema} CASCADE")
            if recreate:
                cur.execute(f"CREATE SCHEMA IF NOT EXISTS {meta_schema}")
    finally:
        conn.close()
    return True


def action_catalog_reinit(event: dict[str, Any], _con: Any) -> dict[str, Any]:
    """OPERATIONAL break-glass: drop the squatting meta-schema + re-initialize it at DATA_PATH (rec-2099).

    T2.17 smoke initialized `ducklake_ops` at the smoke DATA_PATH; DuckLake pins DATA_PATH per
    meta-schema, so a production ATTACH at `ducklake/` fails until the meta-schema is dropped and
    re-initialized. DESTRUCTIVE + IRREVERSIBLE: the existing (disposable smoke) catalog state is
    discarded. The first ATTACH to the now-empty meta-schema initializes it, pinning the new DATA_PATH.
    """
    data_path = event.get("data_path")
    if not isinstance(data_path, str) or not data_path.startswith("s3://"):
        raise rt.DuckLakeRuntimeError("catalog_reinit requires a 'data_path' s3:// URI (the production DuckLake path)")
    raw_schema = event.get("meta_schema")
    if not raw_schema:
        raise rt.DuckLakeRuntimeError(
            "catalog_reinit requires an EXPLICIT 'meta_schema' (no production default: a no-arg invoke "
            "must never drop the live catalog -- destructive-action guard, Decision 84)"
        )
    if event.get("confirm") != raw_schema:
        raise rt.DuckLakeRuntimeError(
            f"catalog_reinit is DESTRUCTIVE and IRREVERSIBLE for meta_schema {raw_schema!r}: "
            f"pass confirm={raw_schema!r} to proceed"
        )
    meta_schema = _require_identifier(raw_schema)

    # Drop the squatting catalog AND leave an empty meta-schema for the ATTACH to initialize into.
    dropped = _drop_meta_schema(meta_schema, recreate=True)

    con = rt.open_connection(
        dsn=rt.fetch_dsn(), data_path=data_path, meta_schema=meta_schema, extension_directory=EXTENSION_DIRECTORY
    )
    try:
        con.execute("SELECT 1")  # ATTACH proof at the new path (initializes the empty meta-schema)
    finally:
        con.close()
    return {
        "ok": True,
        "meta_schema": meta_schema,
        "data_path": data_path,
        "dropped_existing": dropped,
        "reinitialized": True,
        "stats_index": index_actions.ensure_index_for(meta_schema),
    }


def action_restore_drill(event: dict[str, Any], _con: Any) -> dict[str, Any]:
    """OPERATIONAL DR gate: prove a custom-format pg_dump round-trips via pg_restore + read-your-write.

    Self-contained and PRODUCTION-SAFE: initializes a SCRATCH meta-schema at a scratch DATA_PATH,
    writes a known SCD2 row, pg_dumps ONLY that schema (--schema, --format=custom), DROPs it,
    pg_restores, then re-ATTACHes and verifies the known row reads back. Loud-fail (CatalogDrError)
    stops the cutover (Decision 55). Requires the pgclient layer (pg_dump/pg_restore) on this Lambda.
    """
    import tempfile  # noqa: PLC0415

    scratch_meta = _require_identifier(event.get("scratch_meta", "ducklake_restore_drill"))
    # Scratch data lives UNDER the production prefix (ducklake/_restore_drill/) so the maintenance S3
    # grant already covers it -- no extra IAM prefix. Bucket is derived from the smoke path's bucket.
    _bucket = rt.SMOKE_DATA_PATH.split("/")[2]
    scratch_path = event.get("scratch_data_path") or f"s3://{_bucket}/ducklake/_restore_drill/"
    probe_id = event.get("probe_id", "drill-probe")
    dsn = rt.fetch_dsn()

    def _attach() -> Any:
        return rt.open_connection(
            dsn=dsn, data_path=scratch_path, meta_schema=scratch_meta, extension_directory=EXTENSION_DIRECTORY
        )

    # 1. Clean start (drop + recreate empty so ATTACH can initialize) + seed a known row into the
    # scratch catalog (smoke pair, isolated meta-schema).
    _drop_meta_schema(scratch_meta, recreate=True)
    con = _attach()
    try:
        rt.create_scd2_tables(con, force_recreate=True)
        rt.write_scd2(con, {"rec_id": probe_id, "payload": "restore-drill"})
    finally:
        con.close()

    with tempfile.TemporaryDirectory(prefix="restore-drill-") as tmp:
        dump_path = f"{tmp}/scratch.dump"
        # 2. pg_dump ONLY the scratch meta-schema (--format=custom), then 3. DROP it.
        cmd = catalog_dr.build_pg_dump_cmd(catalog_dr.dsn_uri(dsn), dump_path, schema=scratch_meta)
        result = subprocess_run(cmd)
        if result.returncode != 0:
            raise catalog_dr.CatalogDrError(f"restore-drill pg_dump exited {result.returncode}: {result.stderr.strip()[:500]}")
        _drop_meta_schema(scratch_meta)
        # 4. pg_restore the dump (loud-fail on non-zero).
        catalog_dr.run_pg_restore(dump_path, dsn)

    # 5. Re-ATTACH the restored scratch catalog + verify read-your-write.
    con = _attach()
    try:
        rows = rt.read_current(con, rec_id=probe_id)
    finally:
        con.close()
    _drop_meta_schema(scratch_meta)  # cleanup

    restored = bool(rows) and rows[0].get("rec_id") == probe_id
    if not restored:
        raise catalog_dr.CatalogDrError(
            f"restore-drill read-your-write FAILED: probe {probe_id!r} not found after pg_restore -- STOP (Decision 55)"
        )
    return {
        "ok": True,
        "scratch_meta": scratch_meta,
        "probe_id": probe_id,
        "restored": True,
        "pg_version": catalog_dr.PINNED_PG_VERSION,
    }


def subprocess_run(cmd: list[str]) -> Any:
    """Thin wrapper around subprocess.run (capture, text, no check) -- injection point for tests."""
    import subprocess  # noqa: PLC0415

    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)


def action_clone_catalog(event: dict[str, Any], _con: Any) -> dict[str, Any]:
    """OQ.12 canary rehearsal read-clone via Neon native copy-on-write branch (Decision 100).

    The orchestrator (ducklake_neon_smoke_test.canary_rehearsal) owns the branch lifecycle:
    it creates the branch before invoking this action and deletes it in its finally block.
    This action receives branch_host from the event, builds the branch DSN (prod role/password/
    dbname inherited via fetch_dsn, branch endpoint host substituted), ATTACHes the candidate
    DuckDB engine to meta_schema=ducklake_ops, and reads real catalog metadata read-only via
    information_schema. Raises CatalogDrError on ATTACH failure or empty result (Decision 55).

    Never invokes pg_dump, pg_restore, CREATE DATABASE, or DROP DATABASE. Never mutates the
    live ducklake_ops catalog. Never writes the production S3 DATA_PATH.
    """
    branch_host = event.get("branch_host")
    if not branch_host:
        raise catalog_dr.CatalogDrError(
            "clone_catalog: branch_host is required in the event -- orchestrator must create "
            "a Neon branch and pass its endpoint host (Decision 100 / Decision 55)"
        )

    data_path = event.get("data_path")
    if not isinstance(data_path, str) or not data_path.startswith("s3://"):
        raise catalog_dr.CatalogDrError(
            "clone_catalog: data_path is required in the event (production DuckLake S3 path, "
            "e.g. s3://<bucket>/ducklake/) -- the Neon branch records the production data_path "
            "and DuckLake rejects a mismatch; pass the production path, not the scratch path (Decision 55)"
        )

    prod_dsn = rt.fetch_dsn()
    branch_dsn = {**prod_dsn, "host": branch_host}
    meta_schema = "ducklake_ops"

    con = rt.open_connection(
        dsn=branch_dsn,
        data_path=data_path,
        meta_schema=meta_schema,
        extension_directory=EXTENSION_DIRECTORY,
    )
    try:
        rows = con.execute("SELECT schema_name FROM information_schema.schemata LIMIT 1").fetchall()
        if not rows:
            raise catalog_dr.CatalogDrError(
                "clone_catalog FAIL: empty information_schema.schemata on Neon branch -- "
                "candidate DuckDB engine could not read the production catalog clone (Decision 55)"
            )
    finally:
        con.close()

    return {
        "ok": True,
        "meta_schema": meta_schema,
        "branch_host": branch_host,
        "cloned": True,
    }


def action_reconcile_columns(event: dict[str, Any], _con: Any) -> dict[str, Any]:
    """OPERATIONAL: add any spec columns missing from the physical ops_* history+current tables.

    Non-destructive ALTER TABLE ADD COLUMN (never DROP). Idempotent: a second run is a no-op
    because reconcile_table_columns checks physical columns before ALTER. Requires EXPLICIT
    data_path + meta_schema event params (refuses no-arg invokes so it can never hit the smoke
    catalog -- mirrors catalog_reinit's guard, Decision 84/81).

    Expected event:
        {"action": "reconcile_columns", "data_path": "s3://.../ducklake/",
         "meta_schema": "ducklake_ops", "table": "ops_recommendations"}
    """
    data_path = event.get("data_path")
    if not isinstance(data_path, str) or not data_path.startswith("s3://"):
        raise rt.DuckLakeRuntimeError(
            "reconcile_columns requires a 'data_path' s3:// URI (the production DuckLake path); "
            "no-arg invokes refused (smoke-catalog guard, Decision 84/81)"
        )
    raw_schema = event.get("meta_schema")
    if not raw_schema:
        raise rt.DuckLakeRuntimeError(
            "reconcile_columns requires an EXPLICIT 'meta_schema' (e.g. 'ducklake_ops'); "
            "no-arg invokes refused so it can never hit the smoke catalog (Decision 84/81)"
        )
    meta_schema = _require_identifier(raw_schema)
    table = event.get("table")
    if not isinstance(table, str) or not table.strip():
        raise rt.DuckLakeRuntimeError("reconcile_columns requires a non-empty 'table' param (e.g. 'ops_recommendations')")

    con = rt.open_connection(
        dsn=rt.fetch_dsn(), data_path=data_path, meta_schema=meta_schema, extension_directory=EXTENSION_DIRECTORY
    )
    try:
        result = rt.reconcile_table_columns(con, table=table)
    finally:
        con.close()
    return {
        "ok": True,
        "action": "reconcile_columns",
        "table": table,
        "meta_schema": meta_schema,
        "data_path": data_path,
        "added_history": result["added_history"],
        "added_current": result["added_current"],
        # True when the spec columns were ALREADY present (no ALTER issued this run).
        # After reconcile the columns are present either way; this flags the no-op path.
        "columns_pre_existing": {
            "history": not result["added_history"],
            "current": not result["added_current"],
        },
    }


def _catalog_sink(meta_schema: Any) -> Any:
    """Metric emitter for one catalog: the rebound ops emitter itself for ducklake_ops (or an invalid name), else a
    (name, value) emitter limited to CATALOG_METRICS and dimensioned by MetaSchema."""
    dims = catalog_dimensions(meta_schema)
    if dims is None:
        return _emit_maintenance_metric

    def sink(name: str, value: float) -> None:
        if name in CATALOG_METRICS:
            _emit_maintenance_metric(name, value, dimensions=dims)

    return sink


def action_merge_ops(event: dict[str, Any], _con: Any) -> dict[str, Any]:
    """OPERATIONAL: non-destructive merge over the production ops_* catalog, scoped by the declared
    maintenance_policy matrix (compaction-scope-policy-matrix, Decision 191) rather than a naming
    convention.

    Connectionless: opens its own connection from the event's data_path + meta_schema (production).
    Enumerates the catalog UNFILTERED via information_schema (no naming-convention predicate
    anywhere in this path), classifies every discovered table against the field_semantics registry
    (src.common.ducklake_maintenance_scope), and merges only the tables whose class's merge_ops
    policy cell is apply=true. Unclassifiable tables are COLLECTED, never silently skipped: every
    classified table is still merged (maint.merge_adjacent_files per table), and the invocation
    raises an aggregate error after the loop naming both any unclassified tables and any per-table
    merge failure. A single table's merge_adjacent_files failure isolates to that table -- it does
    not prevent the remaining tables from merging -- but the pass still terminates
    non-successfully (Decision 188 pt 3, extended by Decision 191). Non-destructive only -- no
    expire/cleanup/orphan (those remain gated by rec-2113 / T2.26).

    Loud-fail if data_path is missing or not s3://, meta_schema is missing/invalid, or no tables at
    all are discovered in the catalog (misconfigured data_path / meta_schema guard).
    """
    data_path = event.get("data_path")
    if not isinstance(data_path, str) or not data_path.startswith("s3://"):
        raise rt.DuckLakeRuntimeError("merge_ops requires a 'data_path' s3:// URI (the production DuckLake path)")
    raw_schema = event.get("meta_schema")
    if not raw_schema:
        raise rt.DuckLakeRuntimeError(
            "merge_ops requires an explicit 'meta_schema' (e.g. 'ducklake_ops') -- no default production schema"
        )
    meta_schema = _require_identifier(raw_schema)
    emit = _catalog_sink(meta_schema)

    dsn = rt.fetch_dsn()
    con = rt.open_connection(dsn=dsn, data_path=data_path, meta_schema=meta_schema, extension_directory=EXTENSION_DIRECTORY)
    try:
        catalog = maint.CATALOG_ALIAS
        rows = con.execute(
            f"SELECT table_name FROM information_schema.tables WHERE table_catalog = '{catalog}' ORDER BY table_name"
        ).fetchall()
        discovered = [r[0] for r in rows]

        if not discovered:
            raise rt.DuckLakeRuntimeError(
                "merge_ops: no tables discovered in the catalog -- verify data_path and meta_schema "
                f"point at the production DuckLake (meta_schema {meta_schema!r})"
            )

        semantics = rt.load_field_semantics()
        table_registry = scope.build_registry(semantics)
        policy = scope.load_policy(semantics)
        resolution = scope.resolve_scope(discovered, verb="merge_ops", policy=policy, registry=table_registry)
        reconciliation = scope.reconcile_catalog(discovered, semantics=semantics, registry=table_registry)

        t0 = time.perf_counter()
        per_table: list[dict[str, Any]] = []
        files_before_total = 0
        files_after_total = 0
        any_count_unavailable = False
        merge_failures: list[dict[str, str]] = []

        for table in resolution.to_merge:
            count_unavailable = False
            before: int | None = None
            after: int | None = None
            merge_error: str | None = None

            try:
                before = maint._count_files(con, catalog, table)
            except Exception:  # noqa: BLE001 -- guard vs observability split: a transient introspection
                # failure degrades LOUDLY (null count + count_unavailable) rather than aborting the
                # every-6h non-destructive production compaction cadence (Decision 88 cl.1(iii)).
                count_unavailable = True

            try:
                maint.merge_adjacent_files(con, [table], catalog=catalog)
            except Exception as exc:  # noqa: BLE001 -- one table's failure isolates to that table
                # (recorded below + a metric emitted); the pass still raises after the loop
                # (Decision 188 pt 3, extended by Decision 191) -- see the aggregate raise below.
                merge_error = str(exc)
                merge_failures.append({"table": table, "error": merge_error})
                emit("MergeOpsTableFailure", 1.0)

            if merge_error is None and not count_unavailable:
                try:
                    after = maint._count_files(con, catalog, table)
                except Exception:  # noqa: BLE001 -- see above
                    count_unavailable = True

            if count_unavailable:
                # rec-3785: null BOTH sides of the pair when either read failed -- never an
                # asymmetric one-null-one-real pair sitting next to count_unavailable: true.
                before = None
                after = None
                any_count_unavailable = True
            elif merge_error is None:
                assert before is not None and after is not None
                files_before_total += before
                files_after_total += after

            per_table.append(
                {
                    "table": table,
                    "files_before": before,
                    "files_after": after,
                    "count_unavailable": count_unavailable,
                    "error": merge_error,
                    "merge_unavailable": merge_error is not None,
                }
            )

        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        emit("MergeOpsDurationMs", elapsed_ms)
        if not any_count_unavailable:
            emit("MergeOpsFilesBeforeTotal", float(files_before_total))
            emit("MergeOpsFilesAfterTotal", float(files_after_total))
        emit("MergeOpsTablesCount", float(len(resolution.to_merge)))
        emit("MergeOpsUnclassifiedTables", float(len(resolution.unclassified)))
        emit("MergeOpsPassFailed", float(len(merge_failures) + len(resolution.unclassified) > 0))

        if merge_failures or resolution.unclassified:
            # The per-table error detail is embedded directly in the raised message (not just
            # table names) so it survives into the caller-visible response body -- handler()
            # below catches this and returns {ok: false, error: str(exc)}, which is the ONLY
            # thing an operator sees for a 500; a bare table-name list would have discarded
            # exactly the detail an operator needs to triage which table failed and why.
            raise rt.DuckLakeRuntimeError(
                "merge_ops: pass completed with failures (per-table isolation held; classified "
                f"tables were still merged) -- merge_failures={merge_failures} "
                f"unclassified={list(resolution.unclassified)}"
            )

        return {
            "ok": True,
            "action": "merge_ops",
            "tables": list(resolution.to_merge),
            "skipped": list(resolution.skipped),
            "unclassified": list(resolution.unclassified),
            "reconciliation": {
                "registered_absent": list(reconciliation.registered_absent),
                "catalog_unregistered": list(reconciliation.catalog_unregistered),
            },
            "files_before": None if any_count_unavailable else files_before_total,
            "files_after": None if any_count_unavailable else files_after_total,
            "count_unavailable": any_count_unavailable,
            "elapsed_ms": round(elapsed_ms, 2),
            "per_table": per_table,
        }
    finally:
        con.close()


def action_gc_ops(event: dict[str, Any], _con: Any) -> dict[str, Any]:
    """SCHEDULED (weekly, DISABLED until the baseline gate clears) + human-invokable: the production
    destructive-GC pass. Thin dispatch entry -- the real body lives in src/common/ducklake_gc_ops.py
    (Decision 128 decompose-by-default: this handler has no SLOC headroom left for the pass logic).

    Scoped by catalog enumeration ONLY -- never scope.resolve_scope (the per-table-scoped path) and
    never the smoke-only table-scope constant (Decision 143 cl.2). Requires explicit data_path +
    meta_schema (no-arg invokes refused, Decision 84/81 guard); dry_run defaults to False and, when
    True, performs no deletion and no write of any kind.

    Expected event: {"action": "gc_ops", "data_path": "s3://...", "meta_schema": "ducklake_ops", "dry_run"?: bool}.
    """
    data_path = event.get("data_path")
    if not isinstance(data_path, str) or not data_path.startswith("s3://"):
        raise rt.DuckLakeRuntimeError("gc_ops requires a 'data_path' s3:// URI (the production DuckLake path)")
    raw_schema = event.get("meta_schema")
    if not raw_schema:
        raise rt.DuckLakeRuntimeError(
            "gc_ops requires an EXPLICIT 'meta_schema' (e.g. 'ducklake_ops'); no-arg invokes refused (Decision 84/81)"
        )
    meta_schema = _require_identifier(raw_schema)
    dry_run = bool(event.get("dry_run", False))

    con = rt.open_connection(
        dsn=rt.fetch_dsn(), data_path=data_path, meta_schema=meta_schema, extension_directory=EXTENSION_DIRECTORY
    )
    try:
        return gc_ops_body.gc_ops(
            con,
            catalog=maint.CATALOG_ALIAS,
            data_path=data_path,
            dry_run=dry_run,
            metric_sink=_catalog_sink(meta_schema),
        )
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

_ACTIONS: dict[str, Any] = {
    "catalog_reinit": action_catalog_reinit,
    "restore_drill": action_restore_drill,
    "merge_ops": action_merge_ops,
    "gc_ops": action_gc_ops,
    "catalog_stats": health_actions.action_catalog_stats,
    "reconcile_columns": action_reconcile_columns,
    "clone_catalog": action_clone_catalog,
    "control_health": health_actions.action_control_health,
    "reconcile_partitions": partition_actions.action_reconcile_partitions,
    "rewrite_partition_layout": partition_actions.action_rewrite_partition_layout,
    "provision_telemetry_login": login_actions.action_provision_telemetry_login,
    "provision_catalog_owner": catalog_actions.action_provision_catalog_owner,
    "provision_telemetry_catalog": catalog_actions.action_provision_telemetry_catalog,
    "ensure_catalog_indexes": index_actions.action_ensure_catalog_indexes,
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
        print(f"MAINTENANCE_FAILURE status={status} body={body}")
    return {"statusCode": status, "headers": {"Content-Type": "application/json"}, "body": body}


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Maintenance Lambda entrypoint. Dispatches `action`; loud-fail maps to 4xx/5xx (no silent drop).

    Every remaining action manages its own connection internally (or is connection-free, e.g.
    catalog_stats) -- unlike before the smoke split, this dispatcher never pre-opens a shared
    connection itself.
    """
    payload = _parse_event(event)
    action: str | None = payload.get("action")
    fn = _ACTIONS.get(action or "")
    if fn is None:
        return _response(400, {"ok": False, "error": f"unknown action {action!r}", "actions": sorted(_ACTIONS)})

    try:
        return _response(200, fn(payload, None))
    except maint.DuckLakeMaintenanceError as exc:
        _catalog_sink(payload.get("meta_schema"))("MaintenanceBreakerTrip", 1.0)
        return _response(500, {"ok": False, "error_type": "breaker", "breaker_tripped": True, "error": str(exc)})
    except catalog_dr.CatalogDrError as exc:
        return _response(500, {"ok": False, "error_type": "catalog_dr", "error": str(exc)})
    except rt.VersionMismatchError as exc:
        return _response(500, {"ok": False, "error_type": "version_mismatch", "error": str(exc)})
    except rt.DuckLakeRuntimeError as exc:
        return _response(500, {"ok": False, "error_type": "runtime", "error": str(exc)})
