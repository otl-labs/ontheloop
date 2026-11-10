"""restore_rehearsal duckdb_copy step and the shared checksum expression (rec-5023, Decision 251).

Split out of restore_rehearsal_actions.py to keep both under the SLOC limit and to avoid an import cycle:
the actions module imports checksum_sql from here for verify and prod_checksum. The DuckDB-file catalog is
written only under /tmp, never uploaded or returned, and dies with the function.
"""

from __future__ import annotations

import os
import tempfile
import time
from typing import Any

from src.common import ducklake_runtime as rt
from src.lambdas.ducklake_maintenance._shared import EXTENSION_DIRECTORY

COPY_ALIAS = "src"
FILE_ALIAS = "cp"


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def checksum_sql(alias: str, schema: str, table: str, snapshot_id: int) -> str:
    """The one checksum expression: row count and an order-independent hash of whole rows at a snapshot."""
    return (
        f"SELECT count(*), sum(hash(t))::VARCHAR FROM {quote_ident(alias)}.{quote_ident(schema)}.{quote_ident(table)} t "
        f"AT (VERSION => {int(snapshot_id)})"
    )


def _digest(con: Any, schema: str, table: str, alias: str, snapshot_id: int) -> tuple[Any, Any] | None:
    try:
        row = con.execute(checksum_sql(alias, schema, table, snapshot_id)).fetchone()
    except Exception:  # noqa: BLE001 -- a read error is recorded as a mismatch, never raised (non-gating leg)
        return None
    return (row[0], row[1])


def _match(con: Any, schema: str, table: str, snapshot_id: int) -> bool:
    a = _digest(con, schema, table, COPY_ALIAS, snapshot_id)
    b = _digest(con, schema, table, FILE_ALIAS, snapshot_id)
    return a is not None and a == b


def _copy_catalog(con: Any, file_path: str, conninfo: str, meta_schema: str, data_path: str) -> int:
    con.execute(f"ATTACH 'ducklake:{file_path}' AS init (DATA_PATH '{data_path}')")
    con.execute("DETACH init")
    con.execute(f"ATTACH '{file_path}' AS dst")
    con.execute(f"ATTACH 'postgres:{conninfo}' AS pg (READ_ONLY)")
    src_tables = [
        r[0]
        for r in con.execute(
            "SELECT table_name FROM pg.information_schema.tables WHERE table_schema = ? ORDER BY 1", [meta_schema]
        ).fetchall()
    ]
    dst_tables = {r[0] for r in con.execute("SELECT table_name FROM duckdb_tables() WHERE database_name = 'dst'").fetchall()}
    rows = 0
    con.execute("BEGIN TRANSACTION")
    for name in src_tables:
        t = quote_ident(name)
        if name in dst_tables:
            con.execute(f"DELETE FROM dst.main.{t}")
            con.execute(f"INSERT INTO dst.main.{t} SELECT * FROM pg.{quote_ident(meta_schema)}.{t}")
        else:
            # DuckLake creates ducklake_inlined_data_* tables per user table on demand; a fresh file catalog has none
            con.execute(f"CREATE TABLE dst.main.{t} AS SELECT * FROM pg.{quote_ident(meta_schema)}.{t}")
        rows += con.execute(f"SELECT count(*) FROM dst.main.{t}").fetchone()[0]
    con.execute("COMMIT")
    con.execute("DETACH dst")
    return rows


def run_duckdb_copy(
    dsn: dict[str, str], *, meta_schema: str, data_path: str, tables: list[tuple[str, str]], snapshot_id: int
) -> dict[str, Any]:
    """Copy the restored catalog into a /tmp DuckDB file and compare every table at two snapshots."""
    started = time.monotonic()
    failed = {
        "copy_ms": 0,
        "catalog_rows": 0,
        "file_bytes": 0,
        "tables": [{"table": f"{s}.{t}", "match_restored": False, "match_median": False} for s, t in tables],
    }
    try:
        with tempfile.TemporaryDirectory(prefix="rr-file-catalog-") as tmp:
            file_path = os.path.join(tmp, "copy.ducklake")
            con = rt.open_connection(
                dsn=dsn,
                data_path=data_path,
                meta_schema=meta_schema,
                extension_directory=EXTENSION_DIRECTORY,
                alias=COPY_ALIAS,
                read_only=True,
            )
            try:
                rows = _copy_catalog(con, file_path, rt.libpq_conninfo(dsn), meta_schema, data_path)
                copy_ms = int((time.monotonic() - started) * 1000)
                con.execute(f"ATTACH 'ducklake:{file_path}' AS {FILE_ALIAS} (DATA_PATH '{data_path}', READ_ONLY)")
                median = int(
                    con.execute(
                        f"SELECT quantile_disc(snapshot_id, 0.5) FROM pg.{quote_ident(meta_schema)}.ducklake_snapshot"
                    ).fetchone()[0]
                )
                out_tables = [
                    {
                        "table": f"{s}.{t}",
                        "match_restored": _match(con, s, t, snapshot_id),
                        "match_median": _match(con, s, t, median),
                    }
                    for s, t in tables
                ]
                file_bytes = os.path.getsize(file_path)
            finally:
                con.close()
        return {"copy_ms": copy_ms, "catalog_rows": rows, "file_bytes": file_bytes, "tables": out_tables}
    except Exception:  # noqa: BLE001 -- every init, copy or type error is reported as all-false matches
        return failed
