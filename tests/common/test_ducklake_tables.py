"""MIRROR for src/common/ducklake_tables.py (rec-2709 Wave 7).

Split out of the former tests/test_ducklake_runtime.py monolith: create_scd2_tables (partition
application, force-recreate drop-first, the ops-DDL real-types check, and the append_only
single-table variants) and reconcile_table_columns (append_only history-only ALTER).
"""

from __future__ import annotations

import pytest

from src.common import ducklake_runtime as rt
from src.common import ducklake_scd2_schema as schema
from tests.fixtures.ducklake_fakes import FakeCon

pytestmark = pytest.mark.unit

_CALENDAR_TRIPLE = "year(created_timestamp), month(created_timestamp), day(created_timestamp)"


def test_create_scd2_tables_applies_partitions():
    con = FakeCon()
    rt.create_scd2_tables(con)
    sqls = [s for s, _ in con.executed]
    assert any(f"SET PARTITIONED BY ({_CALENDAR_TRIPLE})" in s for s in sqls)
    assert any("SET PARTITIONED BY (bucket(8, rec_id))" in s for s in sqls)
    assert not any(s.startswith("DROP TABLE") for s in sqls)


def test_history_partition_is_calendar_day_triple():
    """rec-4068 acceptance: every resolved history partition is the calendar-day triple (hermetic:
    FakeCon + the committed projection, no integration marker, no extension -- Decision 201
    closure-time evaluator)."""
    smoke_spec = schema.resolve_table_spec(None)
    assert smoke_spec.partition_history == _CALENDAR_TRIPLE

    semantics = schema.load_field_semantics()
    for table in schema.ops_table_names():
        if semantics["ops_tables"][table].get("write_mode") == "control":
            continue  # control-class tables (e.g. ops_entity_counters) have no history/current pair
        if schema.is_event_table(table, semantics):
            entry_partition = semantics["ops_tables"][table]["partition"]["history"]
            column = semantics["ops_tables"][table]["partition_column"]
            assert entry_partition == f"year({column}), month({column}), day({column})", f"{table}: {entry_partition}"
            continue
        spec = schema.resolve_table_spec(table)
        assert spec.partition_history == _CALENDAR_TRIPLE, f"{table}: {spec.partition_history}"


def test_create_scd2_tables_force_recreate_drops_first():
    con = FakeCon()
    rt.create_scd2_tables(con, force_recreate=True)
    sqls = [s for s, _ in con.executed]
    drops = [i for i, s in enumerate(sqls) if s.startswith("DROP TABLE")]
    creates = [i for i, s in enumerate(sqls) if s.startswith("CREATE TABLE")]
    assert len(drops) == 2
    assert min(drops) < min(creates)  # drops precede creates


def test_create_scd2_tables_ops_ddl_has_real_types(monkeypatch):
    con = FakeCon()
    rt.create_scd2_tables(con, table="ops_decisions", force_recreate=True)
    ddl = [sql for sql, _ in con.executed if sql.startswith("CREATE TABLE")]
    assert any("ops_decisions_history" in s for s in ddl)
    assert any("related_decisions BIGINT[]" in s for s in ddl)
    assert any("decision_id BIGINT" in s for s in ddl)
    # partition ALTERs applied before first write
    alters = [sql for sql, _ in con.executed if "SET PARTITIONED BY" in sql]
    assert any("bucket(8, id)" in s for s in alters)


def test_create_scd2_tables_append_only_single_table():
    """create_scd2_tables for an append_only table creates only the history table."""
    con = FakeCon()
    rt.create_scd2_tables(con, table="ops_smoke_events")
    sql_stmts = [sql for sql, _ in con.executed]
    creates = [s for s in sql_stmts if s.startswith("CREATE TABLE")]
    alters = [s for s in sql_stmts if "SET PARTITIONED BY" in s]
    assert len(creates) == 1
    assert "ops_smoke_events_history" in creates[0]
    assert len(alters) == 1
    assert "ops_smoke_events_history" in alters[0]
    assert not any("ops_smoke_events_current" in s for s in sql_stmts)


def test_create_scd2_tables_append_only_force_recreate_single_drop():
    """force_recreate on an append_only table drops only the history table."""
    con = FakeCon()
    rt.create_scd2_tables(con, table="ops_smoke_events", force_recreate=True)
    sql_stmts = [sql for sql, _ in con.executed]
    drops = [s for s in sql_stmts if s.startswith("DROP TABLE")]
    assert len(drops) == 1
    assert "ops_smoke_events_history" in drops[0]
    assert not any("ops_smoke_events_current" in s for s in sql_stmts)


class _ReconcileCon:
    """Fake connection for reconcile_table_columns/_physical_columns's two lookup paths:
    information_schema.columns (primary) and PRAGMA table_info (fallback when the primary query
    returns no rows, e.g. against a catalog that doesn't populate information_schema)."""

    def __init__(self, *, info_schema_cols: dict[str, list[str]] | None = None, pragma_cols: list[str] | None = None):
        self.executed: list[tuple[str, list | None]] = []
        self._info_schema_cols = info_schema_cols or {}
        self._pragma_cols = pragma_cols or []
        self._last = ""

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        self._last = sql
        return self

    def fetchall(self):
        if "information_schema.columns" in self._last:
            table_name = self._last.split("table_name = '")[1].split("'")[0]
            return [(c,) for c in self._info_schema_cols.get(table_name, [])]
        if self._last.startswith("PRAGMA table_info"):
            return [(i, c, "VARCHAR", 0, None, 0) for i, c in enumerate(self._pragma_cols)]
        return []


def test_physical_columns_uses_information_schema_when_available():
    """When information_schema.columns returns rows, that IS the existing-column set -- no PRAGMA
    fallback query is issued."""
    all_fields = sorted(rt.resolve_table_spec("ops_recommendations").fields)
    con = _ReconcileCon(
        info_schema_cols={"ops_recommendations_history": all_fields, "ops_recommendations_current": all_fields}
    )
    result = rt.reconcile_table_columns(con, table="ops_recommendations")
    assert result["added_history"] == []  # every column already present -- all `continue`
    assert not any(sql.startswith("PRAGMA") for sql, _ in con.executed)


def test_reconcile_table_columns_scd2_processes_both_history_and_current():
    """A non-append_only table reconciles BOTH the history and current sides -- and a column
    already present on one side is skipped (`continue`) while a missing column is ALTERed in."""
    all_fields = sorted(rt.resolve_table_spec("ops_recommendations").fields)
    con = _ReconcileCon(
        info_schema_cols={
            "ops_recommendations_history": all_fields,
            "ops_recommendations_current": [],  # nothing present -- every field gets ALTERed in
        }
    )
    result = rt.reconcile_table_columns(con, table="ops_recommendations")
    assert result["added_history"] == []
    assert set(result["added_current"]) == set(all_fields)
    alters = [sql for sql, _ in con.executed if sql.startswith("ALTER TABLE") and "ADD COLUMN" in sql]
    assert all("ops_recommendations_current" in sql for sql in alters)


def test_physical_columns_falls_back_to_pragma_when_information_schema_empty():
    """information_schema.columns returning no rows (e.g. a catalog that doesn't populate it)
    falls back to PRAGMA table_info."""
    all_fields = sorted(rt.resolve_table_spec("ops_recommendations").fields)
    con = _ReconcileCon(pragma_cols=all_fields)
    result = rt.reconcile_table_columns(con, table="ops_recommendations")
    assert result["added_history"] == []
    assert any(sql.startswith("PRAGMA table_info") for sql, _ in con.executed)


def test_create_control_table_issues_create_and_partition():
    con = FakeCon()
    rt.create_control_table(con, table="ops_entity_counters")
    sqls = [s for s, _ in con.executed]
    assert any(s.startswith("CREATE TABLE IF NOT EXISTS") and "ops_entity_counters" in s for s in sqls)
    assert any("SET PARTITIONED BY (counter_name)" in s for s in sqls)
    assert not any(s.startswith("DROP TABLE") for s in sqls)


def test_create_control_table_force_recreate_drops_first():
    con = FakeCon()
    rt.create_control_table(con, table="ops_entity_counters", force_recreate=True)
    sqls = [s for s, _ in con.executed]
    drops = [i for i, s in enumerate(sqls) if s.startswith("DROP TABLE")]
    creates = [i for i, s in enumerate(sqls) if s.startswith("CREATE TABLE")]
    assert drops and creates
    assert min(drops) < min(creates)


def test_reconcile_table_columns_append_only_history_only():
    """reconcile_table_columns for append_only tables issues ALTER TABLE only on history; added_current is empty."""
    con = FakeCon()
    result = rt.reconcile_table_columns(con, table="ops_smoke_events")
    alters = [sql for sql, _ in con.executed if sql.startswith("ALTER TABLE") and "ADD COLUMN" in sql]
    assert all("_history" in sql for sql in alters), f"All ALTERs must target history table; got: {alters}"
    assert not any("_current" in sql for sql in alters), "No ALTER should target a current table"
    assert result["added_current"] == [], "added_current must be empty for append_only tables"
