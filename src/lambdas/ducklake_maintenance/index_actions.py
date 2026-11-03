"""ensure_catalog_indexes: the operator-only verb that creates DuckLake's stats index (Decision 88 invariant v).

Closed: no caller-supplied SQL, schema or index name. Invoked over 443 or from a Lambda console test event by
the operator, never by an agent (Decision 81 cl.6). Refuses unless confirm == "ducklake_file_column_stats". As the
ducklake_ops owner login, under autocommit and a bounded statement_timeout, it builds
catalog_stats_index.sql (one CREATE INDEX CONCURRENTLY) on every catalog schema, rebuilding an INVALID index, and
proves engagement with a fixed, count-only DuckLake read.

Every psycopg2.Error is re-raised carrying only its class and SQLSTATE: a connect error message names the host.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

from src.common import ducklake_catalog_index_status as status
from src.common import ducklake_maintenance as maint
from src.common import ducklake_runtime as rt
from src.lambdas.ducklake_maintenance import _shared
from src.lambdas.ducklake_maintenance._shared import EXTENSION_DIRECTORY, _require_identifier

CONFIRM = status.STATS_TABLE
PROBE_SCHEMA = "ducklake_ops"
_SQL_PATH = Path(__file__).with_name("catalog_stats_index.sql")
_STATEMENT_TIMEOUT = "SET statement_timeout = '300s'"
_POLL_SECONDS = 15
_PROBE_LITERAL = "__ducklake_index_probe__"
_PROBE_TABLE_SQL = (
    f"SELECT t.table_id, t.table_name FROM {PROBE_SCHEMA}.ducklake_data_file df "
    f"JOIN {PROBE_SCHEMA}.ducklake_table t ON t.table_id = df.table_id "
    "WHERE df.end_snapshot IS NULL AND t.end_snapshot IS NULL "
    "GROUP BY t.table_id, t.table_name ORDER BY count(*) DESC, t.table_name LIMIT 1"
)
_PROBE_COLUMN_SQL = (
    f"SELECT column_name FROM {PROBE_SCHEMA}.ducklake_column WHERE table_id = %s AND end_snapshot IS NULL "
    "AND parent_column IS NULL AND lower(column_type) = 'varchar' ORDER BY column_order LIMIT 1"
)


class ProbeTargetMissing(Exception):
    """The catalog has no live table, or that table no text column, for the fixed probe read."""


def _db_error(exc: Exception) -> rt.DuckLakeRuntimeError:
    return rt.DuckLakeRuntimeError(
        f"ensure_catalog_indexes database error: {type(exc).__name__} sqlstate={getattr(exc, 'pgcode', None)}"
    )


def _open_owner(psycopg2: Any) -> Any:
    conn = psycopg2.connect(rt.libpq_conninfo(rt.fetch_dsn()))
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(_STATEMENT_TIMEOUT)
    return conn


def _ensure_one(cur: Any, meta_schema: str, sql_text: str) -> dict[str, Any]:
    before = status.catalog_index_status(cur, meta_schema)
    result = {"existed": before["present"], "valid_before": before["valid"], "created": False, "rebuilt": False}
    if not before["valid"]:
        if before["present"] and before["index_name"] == status.INDEX_NAME:
            cur.execute(f"DROP INDEX CONCURRENTLY {meta_schema}.{status.INDEX_NAME}")
            result["rebuilt"] = True
        else:
            result["created"] = True
        cur.execute(sql_text.format(meta_schema=meta_schema))
    after = status.catalog_index_status(cur, meta_schema)
    if not after["valid"]:
        raise rt.DuckLakeRuntimeError(f"ensure_catalog_indexes: the index on {meta_schema} is not valid after the build")
    return {**result, "valid_after": True}


def _idx_scan(cur: Any) -> int:
    return int(status.catalog_index_status(cur, PROBE_SCHEMA)["idx_scan"] or 0)


def _probe_pushdown(cur: Any, dsn: dict[str, str]) -> dict[str, Any]:
    """Fixed count-only read through a fresh DuckLake ATTACH, then poll idx_scan (stats reach pg_stat late)."""
    try:
        before = _idx_scan(cur)
        cur.execute(_PROBE_TABLE_SQL)
        table_row = cur.fetchone()
        if table_row is None:
            raise ProbeTargetMissing
        table_id, table = table_row
        cur.execute(_PROBE_COLUMN_SQL, [table_id])
        column_row = cur.fetchone()
        if column_row is None:
            raise ProbeTargetMissing
        column = column_row[0]
        _require_identifier(table)
        _require_identifier(column)
        data_path = _shared.DATA_PATH
        if data_path is None:
            raise rt.DuckLakeRuntimeError("the function has no DUCKLAKE_DATA_PATH")
        con = rt.open_connection(
            dsn=dsn, data_path=data_path, meta_schema=PROBE_SCHEMA, extension_directory=EXTENSION_DIRECTORY
        )
        try:
            con.execute(
                f'SELECT count(*) FROM {maint.CATALOG_ALIAS}."{table}" WHERE "{column}" = ?', [_PROBE_LITERAL]
            ).fetchone()
        finally:
            con.close()
        after = _idx_scan(cur)
        for _ in range(_POLL_SECONDS):
            if after > before:
                break
            time.sleep(1)
            after = _idx_scan(cur)
        return {"idx_scan_before": before, "idx_scan_after": after, "engaged": after > before}
    except Exception as exc:  # noqa: BLE001 -- the ATTACH string embeds the conninfo; surface the class name only
        return {"engaged": None, "error_class": type(exc).__name__}


def action_ensure_catalog_indexes(event: dict[str, Any], _con: Any) -> dict[str, Any]:
    """OPERATIONAL, operator-invoked: create or repair the stats index on every catalog schema (Decision 88 inv. v)."""
    if event.get("confirm") != CONFIRM:
        raise rt.DuckLakeRuntimeError(f"ensure_catalog_indexes requires confirm={CONFIRM!r}")
    import psycopg2  # noqa: PLC0415

    sql_bytes = _SQL_PATH.read_bytes()
    sql_text = sql_bytes.decode("utf-8")
    schemas: dict[str, Any] = {}
    try:
        conn = _open_owner(psycopg2)
        try:
            with conn.cursor() as cur:
                for name in status.catalog_index_schemas(cur):
                    schemas[name] = _ensure_one(cur, _require_identifier(name), sql_text)
                probe = _probe_pushdown(cur, rt.fetch_dsn())
        finally:
            conn.close()
    except psycopg2.Error as exc:
        raise _db_error(exc) from None
    return {"ok": True, "migration_sha256": hashlib.sha256(sql_bytes).hexdigest(), "schemas": schemas, "pushdown_probe": probe}


def ensure_index_for(meta_schema: str) -> dict[str, Any]:
    """Build the index on one schema (catalog_reinit's ensure step); same SQL file, same redaction."""
    import psycopg2  # noqa: PLC0415

    sql_text = _SQL_PATH.read_text(encoding="utf-8")
    try:
        conn = _open_owner(psycopg2)
        try:
            with conn.cursor() as cur:
                return _ensure_one(cur, _require_identifier(meta_schema), sql_text)
        finally:
            conn.close()
    except psycopg2.Error as exc:
        raise _db_error(exc) from None
