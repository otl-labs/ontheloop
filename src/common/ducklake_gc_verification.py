"""Post-pass GC verification predicates for the production destructive-GC pass (T2.18,
docs/plans/PLAN-production-gc-and-storage-stability.yaml).

Pure-predicate / connection-owning split, exactly as Decision 188 split
src/common/ducklake_maintenance_ops.py: `referenced_missing` and `gc_debt_ratio` are pure
computations over already-fetched path/byte sets (no connection, no I/O), directly unit-testable;
`verify_read_path` is the connection-owning independent safety check.

SAFETY: `referenced_missing(live_paths, storage_paths)` is the |L \\ S| direction -- catalog-live
paths absent from storage. This is the over-reclaim signal a storage-size metric structurally
cannot see: deleting live data makes storage look smaller, i.e. BETTER by a naive metric. Non-empty
is a breach, never silently tolerated (Decision 55).

EFFICACY: `gc_debt_ratio(storage_bytes, live_bytes)` is (S - L) / L -- the quantity GC actually
controls. Absolute storage bytes are never the assertion: live bytes legitimately grow with
ingestion, so tracking absolute storage would trend upward even under a perfectly functioning GC.

INDEPENDENT SAFETY CHECK: `verify_read_path(con, snapshot_id, tables)` re-reads every table AT the
pass-start CATALOG snapshot id and aggregates over actual column VALUES. Snapshot identity is
CATALOG-level (expire_snapshots and this check both read ducklake_snapshots('{catalog}')), so ONE
id captured before the destructive pass covers every table -- never one id per table. A bare
count(*) at a pinned snapshot is answerable from DuckLake catalog record-count metadata with every
underlying Parquet file already deleted, and column min/max statistics have the same problem
(READ-PATH VACUITY) -- this check forces a real S3 read of every column so a bug that corrupts the
live-file function identically in the delete-set computation AND in `referenced_missing` (the
recorded Iceberg remove_orphan_files failure class: s3:// vs s3a://, double slashes, URL-encoded
paths) still gets caught.
"""

from __future__ import annotations

from typing import Any, Sequence

from src.common import ducklake_maintenance as maint


def referenced_missing(live_paths: set[str], storage_paths: set[str]) -> set[str]:
    """SAFETY: catalog-tracked live paths absent from real storage (the |L \\ S| direction).

    Empty is healthy. Non-empty means the catalog considers a path live but it is not actually in
    S3 -- an over-reclaim signature (or a pre-existing incident) that must raise, never be silently
    tolerated (Decision 55). The reverse direction (a storage object the catalog does not track,
    |S \\ L|) is an ORPHAN, not a safety breach -- callers of this module never compute it here.
    """
    return live_paths - storage_paths


def gc_debt_ratio(storage_bytes: int, live_bytes: int) -> float:
    """EFFICACY: (storage_bytes - live_bytes) / live_bytes -- the GC DEBT ratio.

    This is the quantity GC actually controls; absolute storage bytes are not the assertion (live
    bytes legitimately grow with ingestion, so absolute storage trends upward even under a
    perfectly functioning GC). Raises on live_bytes <= 0: an empty live set is itself a G3
    catalog-sanity failure elsewhere, never a valid debt-ratio input.
    """
    if live_bytes <= 0:
        raise ValueError(f"gc_debt_ratio: live_bytes must be > 0 (got {live_bytes})")
    return (storage_bytes - live_bytes) / live_bytes


def verify_read_path(
    con: Any,
    snapshot_id: int,
    tables: Sequence[str],
    *,
    catalog: str,
) -> dict[str, dict[str, Any]]:
    """INDEPENDENT safety check: force a real value-level scan of every table AT the pass-start
    CATALOG snapshot id.

    Aggregates over actual column VALUES (a row-level hash), never count(*) and never a min/max
    statistic -- both are answerable from DuckLake catalog metadata with the backing Parquet
    already deleted (READ-PATH VACUITY). Raises DuckLakeMaintenanceError on any table whose scan
    fails (e.g. a referenced Parquet object missing from storage) -- fail-closed, never a
    permissive skip (rec-3772 pattern).

    Returns {table: {"row_count": int, "value_aggregate": Any}} for every table scanned.
    """
    results: dict[str, dict[str, Any]] = {}
    for table in tables:
        try:
            row = con.execute(
                f"SELECT count(*), sum(hash(t)) FROM {catalog}.{table} AS t AT (VERSION => {int(snapshot_id)})"
            ).fetchone()
        except maint.DuckLakeMaintenanceError:
            raise
        except Exception as exc:  # noqa: BLE001 -- fail-closed re-raise, never a permissive default (rec-3772)
            raise maint.DuckLakeMaintenanceError(
                f"verify_read_path: value-level read at snapshot {snapshot_id} failed for table "
                f"{table!r} -- a referenced Parquet file may be missing from storage despite the "
                f"catalog still tracking it: {exc}"
            ) from exc
        row_count = int(row[0]) if row and row[0] is not None else 0
        value_aggregate = row[1] if row else None
        results[table] = {"row_count": row_count, "value_aggregate": value_aggregate}
    return results
