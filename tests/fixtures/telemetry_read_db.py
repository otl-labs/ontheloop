"""Shared helper (never a test_ module) for the telemetry event-read tests: the four journals and ops_projects built from the
field_semantics event projection under TELEMETRY_CATALOG_ALIAS, in an in-memory DuckDB or a local DuckLake.

duckdb is imported lazily inside the functions, never at module scope (fast-tier importers must not pay for it).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.common import ducklake_runtime as rt

ALIAS = rt.TELEMETRY_CATALOG_ALIAS
TABLES = ("telemetry_sessions", "telemetry_observations", "telemetry_transcripts", "telemetry_agents")
_PARTITION_SQL = "year(session_started_at), month(session_started_at), day(session_started_at)"
_DEFAULT_STAMP = "2026-09-25T10:00:00+00:00"


def table_columns(table: str) -> dict[str, str]:
    return {name: spec["sql_type"] for name, spec in rt.load_field_semantics()["ops_tables"][table]["columns"].items()}


def projects_columns() -> list[tuple[str, str]]:
    return list(rt.resolve_table_spec("ops_projects").ordered_columns)


def open_memory_catalog() -> Any:
    import duckdb  # noqa: PLC0415

    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")
    con.execute(f"ATTACH ':memory:' AS {ALIAS}")
    create_tables(con)
    return con


def create_tables(con: Any, *, partitioned: bool = False) -> None:
    for table in TABLES:
        ddl = ", ".join(f"{name} {sql_type}" for name, sql_type in table_columns(table).items())
        con.execute(f"CREATE TABLE {ALIAS}.{table} ({ddl})")
        if partitioned:
            con.execute(f"ALTER TABLE {ALIAS}.{table} SET PARTITIONED BY ({_PARTITION_SQL})")
    current = rt.resolve_table_spec("ops_projects").current_table
    ddl = ", ".join(f"{name} {sql_type}" for name, sql_type in projects_columns())
    con.execute(f"CREATE TABLE {ALIAS}.{current} ({ddl})")


def open_local_lake(tmp_path: Path) -> Any:
    """A local DuckLake catalog under the telemetry alias; FAILS (never skips) when the extension cannot load (rec-4071)."""
    import duckdb  # noqa: PLC0415

    tmp_path.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    try:
        con.execute("INSTALL ducklake; LOAD ducklake")
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(f"ducklake extension could not load -- a skip is a FAIL (rec-4071): {exc}") from exc
    con.execute("SET ducklake_default_data_inlining_row_limit=0")
    con.execute("SET TimeZone='UTC'")
    con.execute(f"ATTACH 'ducklake:{tmp_path / 'catalog.ducklake'}' AS {ALIAS} (DATA_PATH '{tmp_path / 'data'}')")
    create_tables(con, partitioned=True)
    return con


def insert_rows(con: Any, table: str, rows: list[dict[str, Any]], *, batch: bool = False) -> None:
    """Insert dict rows (missing columns NULL); timestamps may be ISO strings.

    batch=True writes ONE multi-row INSERT (one data file per partition value on a DuckLake table), so a flush that
    interleaves sessions lands in shared files the way the producer's batches do.
    """
    columns = list(table_columns(table))
    for row in rows:
        unknown = set(row) - set(columns)
        if unknown:
            raise KeyError(f"{table}: unknown column(s) {sorted(unknown)}")
    group = "(" + ", ".join("?" for _ in columns) + ")"
    chunks = [rows] if batch else [[row] for row in rows]
    for chunk in chunks:
        if chunk:
            values = [row.get(c) for row in chunk for c in columns]
            con.execute(f"INSERT INTO {ALIAS}.{table} VALUES {', '.join([group] * len(chunk))}", values)


def insert_projects(con: Any, rows: list[dict[str, Any]]) -> None:
    columns = [name for name, _ in projects_columns()]
    current = rt.resolve_table_spec("ops_projects").current_table
    for row in rows:
        placeholders = ", ".join("?" for _ in columns)
        con.execute(f"INSERT INTO {ALIAS}.{current} VALUES ({placeholders})", [row.get(c) for c in columns])


def base_row(**overrides: Any) -> dict[str, Any]:
    """Envelope defaults shared by every journal row; callers override what a case varies."""
    row: dict[str, Any] = {
        "producer": "claude_code",
        "producer_version": "1.0",
        "parser_version": 1,
        "created_timestamp": _DEFAULT_STAMP,
        "tenant_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "project_id": "01BRZ3NDEKTSV4RRFFQ69G5FBW",
    }
    row.update(overrides)
    return row
