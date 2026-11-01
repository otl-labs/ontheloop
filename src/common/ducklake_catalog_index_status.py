"""Read-only status of the DuckLake catalog's stats index (Decision 88 invariant v).

The single source the ensure_catalog_indexes verb, control_health and catalog_stats all read. Stdlib only: the
caller passes an open psycopg2 cursor. Issues SELECTs only.
"""

from __future__ import annotations

from typing import Any

STATS_TABLE = "ducklake_file_column_stats"
INDEX_NAME = "ducklake_file_column_stats_table_id_column_id_idx"
_EQUIVALENT_KEYS = (["table_id", "column_id"], ["column_id", "table_id"])

_SCHEMAS_SQL = (
    "SELECT n.nspname FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "WHERE c.relname = %s AND c.relkind IN ('r', 'p') ORDER BY n.nspname"
)
_INDEXES_SQL = (
    "SELECT ic.relname, ix.indisvalid, "
    "ARRAY(SELECT a.attname::text FROM unnest(ix.indkey::int2[]) WITH ORDINALITY k(attnum, ord) "
    "JOIN pg_catalog.pg_attribute a ON a.attrelid = ix.indrelid AND a.attnum = k.attnum ORDER BY k.ord), "
    "COALESCE(s.idx_scan, 0), pg_catalog.pg_relation_size(ic.oid) "
    "FROM pg_catalog.pg_index ix "
    "JOIN pg_catalog.pg_class tc ON tc.oid = ix.indrelid "
    "JOIN pg_catalog.pg_namespace n ON n.oid = tc.relnamespace "
    "JOIN pg_catalog.pg_class ic ON ic.oid = ix.indexrelid "
    "LEFT JOIN pg_catalog.pg_stat_user_indexes s ON s.indexrelid = ic.oid "
    "WHERE n.nspname = %s AND tc.relname = %s"
)
_SEQ_SCAN_SQL = "SELECT COALESCE(seq_scan, 0) FROM pg_catalog.pg_stat_user_tables WHERE schemaname = %s AND relname = %s"
_STATS_ROWS_SQL = (
    "SELECT c.reltuples::bigint FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = %s AND c.relname = %s AND c.relkind IN ('r', 'p')"
)
_STATS_RESET_SQL = "SELECT stats_reset::text FROM pg_catalog.pg_stat_database WHERE datname = current_database()"


def catalog_index_schemas(cur: Any) -> list[str]:
    """Every schema holding a ducklake_file_column_stats table, from pg_catalog (never a hard-coded pair)."""
    cur.execute(_SCHEMAS_SQL, [STATS_TABLE])
    return [row[0] for row in cur.fetchall()]


def stats_rows_est(cur: Any, meta_schema: str) -> int | None:
    """Estimated row count of the schema's ducklake_file_column_stats (pg_class.reltuples), or None when the table is
    absent or never analysed (reltuples is negative from PostgreSQL 14)."""
    cur.execute(_STATS_ROWS_SQL, [meta_schema, STATS_TABLE])
    row = cur.fetchone()
    if not row or row[0] is None or int(row[0]) < 0:
        return None
    return int(row[0])


def catalog_index_status(cur: Any, meta_schema: str) -> dict[str, Any]:
    """Index state for one schema: the fixed-name index or any equivalent (table_id, column_id) index counts."""
    cur.execute(_INDEXES_SQL, [meta_schema, STATS_TABLE])
    candidates = [
        {"name": r[0], "valid": bool(r[1]), "idx_scan": int(r[3] or 0), "size_bytes": int(r[4] or 0)}
        for r in cur.fetchall()
        if r[0] == INDEX_NAME or list(r[2]) in _EQUIVALENT_KEYS
    ]
    chosen = (
        next((c for c in candidates if c["valid"] and c["name"] == INDEX_NAME), None)
        or next((c for c in candidates if c["valid"]), None)
        or next((c for c in candidates if c["name"] == INDEX_NAME), None)
    )
    cur.execute(_SEQ_SCAN_SQL, [meta_schema, STATS_TABLE])
    seq_row = cur.fetchone()
    cur.execute(_STATS_RESET_SQL)
    reset_row = cur.fetchone()
    return {
        "present": bool(candidates),
        "valid": any(c["valid"] for c in candidates),
        "index_name": chosen["name"] if chosen else None,
        "idx_scan": chosen["idx_scan"] if chosen else None,
        "size_bytes": chosen["size_bytes"] if chosen else None,
        "table_seq_scan": int(seq_row[0]) if seq_row else None,
        "stats_reset": reset_row[0] if reset_row else None,
    }
