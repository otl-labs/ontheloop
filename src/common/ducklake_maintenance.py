"""DuckLake table-maintenance primitives (T2.18 / CD.33, Decision 81).

Scheduled maintenance pipeline for the DuckLake ops estate. Implements the full
maintenance sequence as composable primitives:

  flush_inlined_data -> merge_adjacent_files -> expire_snapshots ->
  cleanup_old_files -> delete_orphaned_files (+ optional rewrite)

Two cadences:
  run_merge:  daily non-destructive (merge only; no snapshot expiry or file deletion)
  run_gc:     weekly guarded destructive (all five steps, fail-closed guard-set protected)

Design invariants (CD.33 / Decision 81):
  - Inlining disabled at the connection level (ducklake_default_data_inlining_row_limit=0 in
    the runtime), so flush_inlined_data is a no-op safety net that will always return empty.
  - Destructive GC guardrails are module-level constants -- tunable knobs, but NEVER relaxed
    to make a gate pass (Decision 55). Changing them requires a Decision superseding CD.33.
  - The G1-G4 guard set (src/common/ducklake_maintenance_ops.py) aborts the GC pass (raises,
    deletes nothing) on a reachability, retention-floor, or catalog-sanity violation; an
    over-budget pass is deferred WHOLESALE rather than partially applied. The abort/defer is
    the safety-critical invariant (Decision 188, amending Decision 81 clause 6).
  - Singleton enforced by reserved_concurrent_executions=1 on the Lambda.
  - No LLM / agent invocation anywhere in this path (CD.33 clause 5 / Decision 81 clause 6).
  - cleanup_all is NEVER passed as True in scheduled runs (CD.33 H1/R-3/O-3/M-3).

T2.19 expansion forward pointer:
  GC_TABLE_SCOPE is scoped to ducklake_smoke_* for T2.18 FP-A. At the Phase-4 maintenance repoint (Decision 84), the scope
  GENERALISES to the full ducklake_ops catalog and the real ops_* business tables
  (ops_recommendations, ops_decisions, etc.). Wiring rides Phase 4; the restore drill is rec-2113 (T2.26 gate).

CALL signature notes (verified against DuckLake v1.0):
  - All maintenance functions are table functions, not SQL CALL procedures.
  - Use SELECT * FROM / FROM syntax.
  - ducklake_merge_adjacent_files takes (catalog, table, schema=schema) per-table
    or (catalog) catalog-wide (no per-table schema column for the catalog-wide form).
  - ducklake_expire_snapshots, ducklake_cleanup_old_files, ducklake_delete_orphaned_files
    are catalog-wide only; they take keyword args (older_than, dry_run, cleanup_all, versions).
  - ducklake_list_files(catalog, table) returns (data_file, data_file_size_bytes, ...).
  - ducklake_snapshots(catalog) returns (snapshot_id, snapshot_time, ...).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from src.common import ducklake_catalog_index_status
from src.common.ducklake_runtime import CATALOG_ALIAS, SMOKE_CURRENT_TABLE, SMOKE_HISTORY_TABLE, libpq_conninfo

# A bare SQL identifier (meta-schema name) -- guards the f-string-interpolated catalog_stats query.
_META_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

StorageLister = Callable[[str], dict[str, int]]

# ---------------------------------------------------------------------------
# Scope -- smoke tables only for T2.18 FP-A (production repoint = consolidation Phase 4, Decision 84)
# ---------------------------------------------------------------------------

GC_TABLE_SCOPE: tuple[str, ...] = (SMOKE_HISTORY_TABLE, SMOKE_CURRENT_TABLE)

# HOT_TABLE_SCOPE: higher-frequency merge cadence for high-write-rate tables (T2.18 FP-B, CD.34).
# Scoped to ducklake_smoke_* for T2.18 (same as GC_TABLE_SCOPE; Decision 88 cl.4, Decision 143
# cl.2 -- unchanged by compaction-scope-policy-matrix / Decision 191). Expanding this cadence to
# real high-write-rate ops_* tables (ops_recommendations, ops_decisions) remains open forward
# work -- NOT one of T2.19's ratified exit criteria (verified false against all 15 T2.19 criteria; the
# claim traced only to a T2.18 progress_note, compaction-scope-policy-matrix).
HOT_TABLE_SCOPE: tuple[str, ...] = (SMOKE_HISTORY_TABLE, SMOKE_CURRENT_TABLE)

MAINTENANCE_SCOPE_NOTE = (
    "T2.18 FP-A: GC_TABLE_SCOPE / HOT_TABLE_SCOPE (this module's smoke-only merge/gc/hot_merge "
    "cadences) stay ducklake_smoke_* (Decision 88 cl.4; Decision 143 cl.2) -- unchanged by this "
    "note. T2.19 already introduced a SEPARATE scheduled cadence, merge_ops "
    "(src/lambdas/ducklake_maintenance/handler.py::action_merge_ops), against the full "
    "PRODUCTION ducklake_ops catalog and every ops_* table; it now discovers that scope by "
    "catalog enumeration crossed with the declared maintenance_policy matrix "
    "(compaction-scope-policy-matrix, Decision 191), never a naming-convention predicate."
)

# ---------------------------------------------------------------------------
# Guardrail constants (CD.33 / Decision 81 clause 6)
# These are tunable knobs -- but tuning them to make a gate pass is a Decision-55 violation.
# ---------------------------------------------------------------------------

SNAPSHOT_RETAIN_DAYS: int = 30
FILE_CLEANUP_GRACE_DAYS: int = 7
SNAPSHOT_FLOOR: int = 2

# CloudWatch metric namespace for maintenance metrics (CD.33 T2-d).
MAINTENANCE_CLOUDWATCH_NAMESPACE = "DuckLakeMaintenance"


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class DuckLakeMaintenanceError(RuntimeError):
    """Loud-fail for any maintenance abort condition (a G1-G4 guard-set trip, invariant violation).

    Raised in full -- never caught-and-relaxed (Decision 55).
    """


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _ts_str(dt: datetime) -> str:
    """Format a UTC datetime as 'YYYY-MM-DD HH:MM:SS+00' for DuckDB TIMESTAMPTZ literals."""
    utc = dt.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%d %H:%M:%S+00")


def _count_files(con: Any, catalog: str, table: str) -> int:
    """Count data files tracked by DuckLake for *table* in *catalog*.

    Raises on introspection failure -- no permissive default (rec-3772: a swallowed exception
    here previously collapsed the retired breaker's denominator to 0 and made it fail open).
    """
    row = con.execute(f"SELECT count(*) FROM ducklake_list_files('{catalog}', '{table}')").fetchone()
    return int(row[0]) if row else 0


def _sum_file_bytes(con: Any, catalog: str, table: str) -> int:
    """Sum data_file_size_bytes for *table* in *catalog*; 0 when no files. Raises on introspection
    failure -- no permissive default (rec-3772)."""
    row = con.execute(
        f"SELECT coalesce(sum(data_file_size_bytes), 0) FROM ducklake_list_files('{catalog}', '{table}')"
    ).fetchone()
    return int(row[0]) if row else 0


def _collect_file_paths(con: Any, catalog: str, table: str) -> dict[str, int]:
    """Return {data_file_path: size_bytes} for all tracked data files in *table*.

    Raises on introspection failure -- no permissive default (rec-3772).
    """
    rows = con.execute(f"SELECT data_file, data_file_size_bytes FROM ducklake_list_files('{catalog}', '{table}')").fetchall()
    return {r[0]: int(r[1] or 0) for r in rows}


def _dry_run_cleanup_paths(con: Any, catalog: str, older_than: datetime) -> list[str]:
    """Return file paths that cleanup_old_files (dry_run=True) would delete.

    Raises on introspection failure -- no permissive default (rec-3772).
    """
    ts = _ts_str(older_than)
    rows = con.execute(
        f"SELECT path FROM ducklake_cleanup_old_files('{catalog}', dry_run=True, "
        f"cleanup_all=False, older_than=TIMESTAMPTZ '{ts}')"
    ).fetchall()
    return [r[0] for r in rows]


def _dry_run_orphan_paths(con: Any, catalog: str, older_than: datetime) -> list[str]:
    """Return file paths that delete_orphaned_files (dry_run=True) would delete.

    Raises on introspection failure -- no permissive default (rec-3772).
    """
    ts = _ts_str(older_than)
    rows = con.execute(
        f"SELECT path FROM ducklake_delete_orphaned_files('{catalog}', dry_run=True, older_than=TIMESTAMPTZ '{ts}')"
    ).fetchall()
    return [r[0] for r in rows]


def _parse_s3_uri(data_path: str) -> tuple[str, str]:
    if not data_path.startswith("s3://"):
        raise DuckLakeMaintenanceError(f"gc_ops: data_path must be an s3:// URI, got {data_path!r}")
    without_scheme = data_path[len("s3://") :]
    bucket, _, prefix = without_scheme.partition("/")
    if not bucket:
        raise DuckLakeMaintenanceError(f"gc_ops: data_path {data_path!r} carries no bucket")
    return bucket, prefix


def _default_list_storage(data_path: str, *, client: Any = None) -> dict[str, int]:
    """ListObjectsV2 walk of the production prefix -- the managed-primitive storage-side source for
    `referenced_missing` and for G4 byte sizing (Decision 100: no client-tooling substitution for a
    managed primitive).

    Returns {s3://bucket/key: size_bytes}, the same path shape DuckLake's own `data_file` column
    uses, so the result compares directly against the catalog live-set paths.

    Lives here (not in ducklake_gc_ops) so the smoke Lambda's run_gc cadence can build a
    storage-derived size source without importing the production verb module (ducklake_gc_ops
    must not be part of the smoke Lambda's import graph). ducklake_gc_ops re-exports both this
    and `_parse_s3_uri` as its own module attributes for its seven shipped patch sites.
    """
    import boto3  # noqa: PLC0415

    bucket, prefix = _parse_s3_uri(data_path)
    s3 = client if client is not None else boto3.client("s3")
    paginator = s3.get_paginator("list_objects_v2")
    paths: dict[str, int] = {}
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            paths[f"s3://{bucket}/{obj['Key']}"] = int(obj.get("Size", 0))
    return paths


# ---------------------------------------------------------------------------
# Maintenance primitives (all table-function calls, no CALL syntax)
# ---------------------------------------------------------------------------


def flush_inlined_data(
    con: Any,
    tables: tuple[str, ...] | list[str],
    *,
    catalog: str = CATALOG_ALIAS,
    schema: str = "main",
) -> None:
    """No-op safety net: flush any inlined rows to S3 Parquet.

    Inlining is disabled (ducklake_default_data_inlining_row_limit=0) on every connection
    via ducklake_runtime.open_connection (CD.34 / EC11), so this function always returns
    empty results. It is retained as a safety net in case the row_limit setting drifts.
    """
    for table in tables:
        con.execute(
            f"SELECT * FROM ducklake_flush_inlined_data('{catalog}', table_name='{table}', schema_name='{schema}')"
        ).fetchall()


def merge_adjacent_files(
    con: Any,
    tables: tuple[str, ...] | list[str],
    *,
    catalog: str = CATALOG_ALIAS,
    schema: str = "main",
) -> None:
    """Merge small adjacent Parquet files into larger ones (non-destructive compaction).

    This is the daily non-destructive step. It does NOT delete any S3 objects; it only creates
    merged files and updates the catalog metadata. Safe to run frequently.
    """
    for table in tables:
        con.execute(f"SELECT * FROM ducklake_merge_adjacent_files('{catalog}', '{table}', schema='{schema}')").fetchall()


def _run_destructive_count(con: Any, sql: str) -> int:
    """Run a destructive DuckLake table function to COMPLETION and return its count(*) row.

    fetchall(), never fetchone(): DuckLake deletes storage files while the statement runs but records
    the catalog change only when the statement completes, and fetchone() leaves the one-row result
    open, so the next statement discards it. That split the 2026-09-20 production gc_ops pass:
    18,744 files deleted from S3 and still listed in the catalog as scheduled for deletion.
    """
    return int(con.execute(sql).fetchall()[0][0])


def expire_snapshots(
    con: Any,
    *,
    catalog: str = CATALOG_ALIAS,
    retain_days: int = SNAPSHOT_RETAIN_DAYS,
    floor: int = SNAPSHOT_FLOOR,
    _now: datetime | None = None,
) -> int:
    """Expire old snapshots, retaining at least `floor` (default 2) snapshots.

    Returns the number of snapshots expired. 0 when the floor guard fires or there is nothing
    to expire. This step does NOT delete S3 data files; cleanup_old_files must follow.

    Guardrail: never expires below the `floor` most-recent snapshots (CD.33 clause 6).
    """
    now = _now or _now_utc()
    history_cutoff = now - timedelta(days=retain_days)

    # Read current snapshot list in descending time order (most recent first).
    rows = con.execute(
        f"SELECT snapshot_id, snapshot_time FROM ducklake_snapshots('{catalog}') ORDER BY snapshot_time DESC"
    ).fetchall()

    if len(rows) <= floor:
        return 0

    # The `floor`-th snapshot (0-indexed) is the oldest we want to RETAIN.
    # Expire only those older than BOTH the history cutoff AND the floor snapshot.
    floor_time: datetime = rows[floor - 1][1]  # time of the last retained snapshot
    # Expire strictly older than the older of the two cutoffs.
    expire_cutoff = min(history_cutoff, floor_time)

    if expire_cutoff >= now:
        return 0

    ts = _ts_str(expire_cutoff)
    return _run_destructive_count(
        con, f"SELECT count(*) FROM ducklake_expire_snapshots('{catalog}', older_than=TIMESTAMPTZ '{ts}')"
    )


def _resolve_cleanup_cutoff(*, grace_days: int, older_than: datetime | None, now: datetime, caller: str) -> datetime:
    """Resolve the effective older_than cutoff for a destructive cleanup/orphan call.

    `older_than`, when given, overrides the grace_days derivation -- the drain walk's mechanism for
    deleting exactly the rung it selected. FILE_CLEANUP_GRACE_DAYS is the absolute safety floor: an
    override younger than `now - FILE_CLEANUP_GRACE_DAYS` is REFUSED, never clamped (Decision 55 /
    Decision 193 -- clamping would silently narrow what the caller asked to delete into something it
    did not ask for).
    """
    if older_than is None:
        return now - timedelta(days=grace_days)
    floor_cutoff = now - timedelta(days=FILE_CLEANUP_GRACE_DAYS)
    if older_than > floor_cutoff:
        raise DuckLakeMaintenanceError(
            f"{caller}: older_than={older_than.isoformat()} is younger than the "
            f"{FILE_CLEANUP_GRACE_DAYS}-day safety floor ({floor_cutoff.isoformat()}) -- refusing "
            "rather than clamping (Decision 55 / Decision 193)."
        )
    return older_than


def cleanup_old_files(
    con: Any,
    *,
    catalog: str = CATALOG_ALIAS,
    grace_days: int = FILE_CLEANUP_GRACE_DAYS,
    older_than: datetime | None = None,
    _now: datetime | None = None,
) -> int:
    """Delete S3 data files associated with expired snapshots (with grace period).

    Only deletes files that have been in the expired state for at least grace_days (default 7),
    unless `older_than` explicitly overrides the cutoff (the drain walk's mechanism -- see
    _resolve_cleanup_cutoff). Never passes cleanup_all=True (CD.33 H1 / Decision 81 clause 6).

    Returns the number of files deleted.
    """
    now = _now or _now_utc()
    cutoff = _resolve_cleanup_cutoff(grace_days=grace_days, older_than=older_than, now=now, caller="cleanup_old_files")
    ts = _ts_str(cutoff)
    return _run_destructive_count(
        con,
        f"SELECT count(*) FROM ducklake_cleanup_old_files('{catalog}', dry_run=False, "
        f"cleanup_all=False, older_than=TIMESTAMPTZ '{ts}')",
    )


def delete_orphaned_files(
    con: Any,
    *,
    catalog: str = CATALOG_ALIAS,
    grace_days: int = FILE_CLEANUP_GRACE_DAYS,
    older_than: datetime | None = None,
    _now: datetime | None = None,
) -> int:
    """Delete S3 files not referenced by any snapshot (orphans), with grace period.

    Only deletes orphans older than grace_days (default 7), unless `older_than` explicitly overrides
    the cutoff (the drain walk's mechanism -- see _resolve_cleanup_cutoff). Never passes
    cleanup_all=True.

    Returns the number of files deleted.
    """
    now = _now or _now_utc()
    cutoff = _resolve_cleanup_cutoff(grace_days=grace_days, older_than=older_than, now=now, caller="delete_orphaned_files")
    ts = _ts_str(cutoff)
    return _run_destructive_count(
        con, f"SELECT count(*) FROM ducklake_delete_orphaned_files('{catalog}', dry_run=False, older_than=TIMESTAMPTZ '{ts}')"
    )


def rewrite(
    con: Any,
    tables: tuple[str, ...] | list[str],
    *,
    catalog: str = CATALOG_ALIAS,
    schema: str = "main",
) -> None:
    """Optional: rewrite data files (full compaction / column-store optimisation).

    Not included in the default run_merge or run_gc cadences -- invoked explicitly when
    deep compaction is required. More expensive than merge_adjacent_files.
    """
    for table in tables:
        con.execute(f"SELECT * FROM ducklake_rewrite_data_files('{catalog}', '{table}', schema='{schema}')").fetchall()


# ---------------------------------------------------------------------------
# Orchestrators: the two cadences
# ---------------------------------------------------------------------------


def run_merge(
    con: Any,
    tables: tuple[str, ...] | list[str] = GC_TABLE_SCOPE,
    *,
    catalog: str = CATALOG_ALIAS,
    schema: str = "main",
) -> dict[str, Any]:
    """Daily non-destructive cadence: flush + merge only. No snapshot expiry or file deletion.

    Safe to run frequently. Returns a stats dict for CloudWatch emission (includes
    files_before so smoke-gate VP9 can assert files_after_merge <= files_before).
    """
    files_before = sum(_count_files(con, catalog, t) for t in tables)
    flush_inlined_data(con, tables, catalog=catalog, schema=schema)
    merge_adjacent_files(con, tables, catalog=catalog, schema=schema)
    files_after_merge = sum(_count_files(con, catalog, t) for t in tables)
    return {
        "ok": True,
        "action": "merge",
        "tables": list(tables),
        "files_before": files_before,
        "files_after_merge": files_after_merge,
    }


def run_hot_merge(
    con: Any,
    tables: tuple[str, ...] | list[str] = HOT_TABLE_SCOPE,
    *,
    catalog: str = CATALOG_ALIAS,
    schema: str = "main",
) -> dict[str, Any]:
    """Higher-frequency merge-only cadence for high-write-rate tables (T2.18 FP-B / CD.34).

    Runs merge_adjacent_files ONLY -- no snapshot expiry, no cleanup, no orphan deletion.
    Bounds the small-file COUNT between weekly GC passes without reclaiming storage (that is
    the weekly GC cadence's job). Safe to invoke frequently.

    Table scope: HOT_TABLE_SCOPE (ducklake_smoke_* for T2.18; Decision 88 cl.4 / Decision 143
    cl.2 -- unchanged). Expanding this cadence to the real high-write-rate ops_* tables remains
    open forward work, NOT one of T2.19's ratified exit criteria (compaction-scope-policy-matrix).

    Returns a stats dict so the smoke gate (VP12) can assert files_after <= files_before and
    confirm no destructive calls were issued.
    """
    files_before = sum(_count_files(con, catalog, t) for t in tables)
    merge_adjacent_files(con, tables, catalog=catalog, schema=schema)
    files_after = sum(_count_files(con, catalog, t) for t in tables)
    return {
        "ok": True,
        "action": "hot_merge",
        "tables": list(tables),
        "files_before": files_before,
        "files_after": files_after,
    }


def _default_pg_connect(conninfo: str) -> Any:
    """psycopg2.connect, imported lazily (the layer provides it; keeps module import dependency-free)."""
    import psycopg2  # noqa: PLC0415

    return psycopg2.connect(conninfo)


def catalog_stats(
    *,
    meta_schema: str,
    dsn: dict[str, str],
    ops_table_filter: str = "ops_%",
    _connect: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Read-only catalog observability (D3a / neon-egress measurement obligation).

    Returns the Postgres catalog-metadata footprint -- the bytes a pg_dump exports and the DuckDB
    postgres scanner sequential-COPYs per query (ducklake #859), which is the Neon egress driver this
    plan attacks. Pure metadata read over the catalog's own Postgres tables (psycopg2): no DuckLake
    ATTACH, no data_path needed, and NO merge/expire/cleanup/orphan -- safe to run against production.

    Reports: total catalog-metadata bytes (exact, via pg_total_relation_size), per-metadata-table bytes
    + estimated row counts (reltuples; refreshed by ANALYZE/autovacuum), the snapshot / data_file /
    file_column_stats row estimates pulled out by name, and a best-effort per-ops_*-table data_file
    count (joined from the metadata; degrades to a note if the catalog's column names differ).

    _connect injects the connection factory for tests (defaults to psycopg2.connect).
    """
    if not _META_IDENT_RE.match(meta_schema or ""):
        raise DuckLakeMaintenanceError(f"catalog_stats: invalid meta_schema identifier {meta_schema!r}")

    connect = _connect or _default_pg_connect
    conn = connect(libpq_conninfo(dsn))
    try:
        with conn.cursor() as cur:
            # Read FIRST: the per-ops-table failure branch below leaves the non-autocommit transaction aborted.
            stats_index = ducklake_catalog_index_status.catalog_index_status(cur, meta_schema)
            # Exact bytes per metadata table + estimated rows, in one query (no per-table count scan).
            cur.execute(
                "SELECT c.relname, pg_total_relation_size(c.oid), c.reltuples::bigint "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = %s AND c.relkind = 'r' AND c.relname LIKE 'ducklake_%%' "
                "ORDER BY pg_total_relation_size(c.oid) DESC",
                [meta_schema],
            )
            metadata_tables = [
                {"table": r[0], "bytes": int(r[1] or 0), "est_rows": max(int(r[2] or 0), 0)} for r in cur.fetchall()
            ]
            catalog_metadata_bytes = sum(m["bytes"] for m in metadata_tables)

            def _est(name_sub: str) -> int | None:
                for m in metadata_tables:
                    if name_sub in m["table"]:
                        return m["est_rows"]
                return None

            per_ops_table: list[dict[str, Any]] = []
            per_ops_note = ""
            try:
                cur.execute(
                    f"SELECT t.table_name, count(*) FROM {meta_schema}.ducklake_data_file df "
                    f"JOIN {meta_schema}.ducklake_table t ON df.table_id = t.table_id "
                    f"WHERE t.table_name LIKE %s GROUP BY t.table_name ORDER BY t.table_name",
                    [ops_table_filter],
                )
                per_ops_table = [{"table": r[0], "data_file_count": int(r[1] or 0)} for r in cur.fetchall()]
            except Exception as exc:  # noqa: BLE001 -- observability degrades, never crashes the stats action
                per_ops_note = f"per-ops-table breakdown unavailable ({type(exc).__name__}); catalog totals still reported"
    finally:
        conn.close()

    return {
        "ok": True,
        "meta_schema": meta_schema,
        "catalog_metadata_bytes": catalog_metadata_bytes,
        "snapshot_rows_est": _est("snapshot"),
        "data_file_rows_est": _est("data_file"),
        "file_column_stats_rows_est": _est("file_column_stat"),
        "metadata_table_count": len(metadata_tables),
        "metadata_tables": metadata_tables,
        "per_ops_table": per_ops_table,
        "per_ops_table_note": per_ops_note,
        "stats_index": stats_index,
    }


def run_gc(
    con: Any,
    tables: tuple[str, ...] | list[str] = GC_TABLE_SCOPE,
    *,
    data_path: str,
    catalog: str = CATALOG_ALIAS,
    schema: str = "main",
    retain_days: int = SNAPSHOT_RETAIN_DAYS,
    grace_days: int = FILE_CLEANUP_GRACE_DAYS,
    floor: int = SNAPSHOT_FLOOR,
    list_storage: StorageLister | None = None,
    _now: datetime | None = None,
) -> dict[str, Any]:
    """Weekly guarded destructive cadence: full maintenance sequence behind the G1-G4 guard set.

    Sequence:
      1. flush_inlined_data  (no-op safety net)
      2. merge_adjacent_files  (non-destructive prep)
      3. G1-G4 guard set (src/common/ducklake_maintenance_ops.py::run_guarded_gc): reachability,
         retention-floor re-assertion, an absolute per-pass deletion bound, and a post-pass
         catalog-sanity check wrap expire_snapshots / cleanup_old_files / delete_orphaned_files.

    `data_path` (required, no default -- a default would silently re-point the size source at the
    wrong prefix) is listed via `list_storage` (defaults to `_default_list_storage`, injectable for
    tests) to build G4's byte-sizing source: a would-delete candidate is a file already
    superseded/expired, so it is structurally absent from the catalog live set and can only be
    sized from real storage (rec-3871).

    Returns a stats dict for CloudWatch emission. Raises DuckLakeMaintenanceError on a G1/G2/G3
    guard trip; a G4 over-budget pass drains partially or, if no cutoff at or above the grace floor
    admits a positive count under both caps, defers the whole destructive step (see guard_stats).
    """
    from src.common import ducklake_maintenance_ops as gcops  # noqa: PLC0415 -- see the import-cycle note in that module

    now = _now or _now_utc()
    list_storage = list_storage or _default_list_storage

    files_before = sum(_count_files(con, catalog, t) for t in tables)

    # Pre-pass size inventory -- BEFORE flush/merge can supersede any file -- so G4's byte
    # accounting has real sizes for paths that later show up as dry-run cleanup/orphan candidates
    # (rec-3773: joining against a POST-merge inventory always yields 0 for a superseded path).
    live_before: dict[str, int] = {}
    for t in tables:
        live_before.update(_collect_file_paths(con, catalog, t))

    flush_inlined_data(con, tables, catalog=catalog, schema=schema)
    merge_adjacent_files(con, tables, catalog=catalog, schema=schema)

    candidate_size_source = list_storage(data_path)

    guard_result = gcops.run_guarded_gc(
        con,
        tables,
        catalog=catalog,
        live_before=live_before,
        grace_days=grace_days,
        retain_days=retain_days,
        floor=floor,
        now=now,
        candidate_size_source=candidate_size_source,
    )

    files_after = sum(_count_files(con, catalog, t) for t in tables)

    return {
        "ok": True,
        "action": "gc",
        "tables": list(tables),
        "files_before": files_before,
        "files_after": files_after,
        "snapshots_expired": guard_result["snapshots_expired"],
        "files_cleaned": guard_result["files_cleaned"],
        "orphans_deleted": guard_result["orphans_deleted"],
        "guard_stats": guard_result["guard_stats"],
    }
