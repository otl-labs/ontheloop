"""Shared local-DuckLake helper for the turn-capture integration modules.

duckdb is imported lazily inside the functions, never at module scope (fast-tier importers must not pay for
it). The four tables are built from the RATIFIED contracts (load_contract + resolve_refs +
_project_contract_table + EventTableSpec.from_projection), so every spec carries the contract-derived row rules
and compare exclusions (Decision 210) and every append through it exercises them; partitioned by the calendar-day
triple, UTC before ATTACH, inlining off. The helper FAILS, never skips, when the ducklake extension is unavailable (rec-4071).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.telemetry.append import EventTableSpec

TABLES = ("telemetry_sessions", "telemetry_observations", "telemetry_transcripts", "telemetry_agents")
_PARTITION_SQL = "year(session_started_at), month(session_started_at), day(session_started_at)"


def load_specs() -> dict[str, EventTableSpec]:
    from scripts.contracts import load_contract, resolve_refs  # noqa: PLC0415
    from scripts.schema_to_field_semantics import _CONTRACTS_DIR, _project_contract_table  # noqa: PLC0415

    specs: dict[str, EventTableSpec] = {}
    for table in TABLES:
        doc = load_contract(_CONTRACTS_DIR / f"{table}.yaml")
        resolved = resolve_refs(doc, _CONTRACTS_DIR)
        entry = _project_contract_table(
            table, resolved, None, {}, table_class="event", partition_by=doc.governance.partition_by
        )
        specs[table] = EventTableSpec.from_projection(table, entry)
    return specs


def open_local_lake(tmp_path: Path) -> Any:
    import duckdb  # noqa: PLC0415

    tmp_path.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    try:
        con.execute("INSTALL ducklake; LOAD ducklake")
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(f"ducklake extension could not load -- a skip is a FAIL (rec-4071): {exc}") from exc
    con.execute("SET ducklake_default_data_inlining_row_limit=0")
    con.execute("SET TimeZone='UTC'")
    con.execute(f"ATTACH 'ducklake:{tmp_path / 'catalog.ducklake'}' AS lake (DATA_PATH '{tmp_path / 'data'}')")
    return con


def create_tables(con: Any, specs: dict[str, EventTableSpec]) -> None:
    for table, spec in specs.items():
        ddl = ", ".join(f"{name} {sql_type}" for name, sql_type in spec.columns.items())
        con.execute(f"CREATE TABLE lake.{table} ({ddl})")
        con.execute(f"ALTER TABLE lake.{table} SET PARTITIONED BY ({_PARTITION_SQL})")
