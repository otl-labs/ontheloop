"""MIRROR for src/common/ducklake_catalog_index_status.py -- the read-only stats-index status helpers."""

from __future__ import annotations

from typing import Any

import pytest

from scripts.lambda_manifest import compute_affected_artifacts
from src.common import ducklake_catalog_index_status as st

pytestmark = pytest.mark.unit

_FIXED = st.INDEX_NAME


class _Cur:
    def __init__(self, indexes: list[tuple[Any, ...]], seq: Any = (4,), reset: Any = ("2026-10-01 00:00:00+00",)):
        self.indexes, self.seq, self.reset = indexes, seq, reset
        self.sql: list[str] = []
        self._last = ""

    def execute(self, sql: str, params: Any = None) -> None:
        self.sql.append(sql)
        self._last = sql

    def fetchall(self) -> list[Any]:
        if "pg_class c JOIN" in self._last and "relkind" in self._last:
            return [("ducklake_ops",), ("ducklake_smoke",)]
        return self.indexes

    def fetchone(self) -> Any:
        return self.seq if "pg_stat_user_tables" in self._last else self.reset


def test_schemas_come_from_pg_catalog_not_a_hard_coded_pair():
    cur = _Cur([])
    assert st.catalog_index_schemas(cur) == ["ducklake_ops", "ducklake_smoke"]
    assert "pg_catalog" in cur.sql[0]


def test_fixed_name_valid():
    out = st.catalog_index_status(_Cur([(_FIXED, True, ["table_id", "column_id"], 7, 8192)]), "ducklake_ops")
    assert out["present"] and out["valid"] and out["index_name"] == _FIXED
    assert (out["idx_scan"], out["size_bytes"], out["table_seq_scan"]) == (7, 8192, 4)


@pytest.mark.parametrize("cols", [["table_id", "column_id"], ["column_id", "table_id"]])
def test_equivalent_index_in_either_order_counts(cols):
    out = st.catalog_index_status(_Cur([("someone_elses_idx", True, cols, 1, 10)]), "ducklake_ops")
    assert out["valid"] and out["index_name"] == "someone_elses_idx"


def test_unrelated_index_is_ignored():
    out = st.catalog_index_status(_Cur([("other", True, ["table_id"], 1, 10)]), "ducklake_ops")
    assert not out["present"] and not out["valid"] and out["index_name"] is None and out["idx_scan"] is None


def test_invalid_fixed_name_is_present_not_valid():
    out = st.catalog_index_status(_Cur([(_FIXED, False, ["table_id", "column_id"], 0, 0)]), "ducklake_ops")
    assert out["present"] and not out["valid"] and out["index_name"] == _FIXED


def test_valid_equivalent_beats_invalid_fixed_name():
    rows = [(_FIXED, False, ["table_id", "column_id"], 0, 0), ("alt", True, ["column_id", "table_id"], 5, 1)]
    out = st.catalog_index_status(_Cur(rows), "ducklake_ops")
    assert out["valid"] and out["index_name"] == "alt"


def test_counters_and_reset_pass_through_with_reset_cast_to_text():
    cur = _Cur([], seq=None, reset=None)
    out = st.catalog_index_status(cur, "ducklake_ops")
    assert out["table_seq_scan"] is None and out["stats_reset"] is None
    assert any("stats_reset::text" in q for q in cur.sql)


def test_no_write_statement_is_ever_issued():
    cur = _Cur([(_FIXED, True, ["table_id", "column_id"], 1, 1)])
    st.catalog_index_schemas(cur)
    st.catalog_index_status(cur, "ducklake_ops")
    assert all(q.lstrip().upper().startswith("SELECT") for q in cur.sql)


class _RowsCur:
    def __init__(self, row: Any) -> None:
        self.row, self.calls = row, []

    def execute(self, sql: str, params: Any = None) -> None:
        self.calls.append((sql, params))

    def fetchone(self) -> Any:
        return self.row


def test_stats_rows_est_reads_pg_catalog_only():
    cur = _RowsCur((48000,))
    assert st.stats_rows_est(cur, "ducklake_telemetry") == 48000
    assert len(cur.calls) == 1
    sql, params = cur.calls[0]
    assert sql.lstrip().upper().startswith("SELECT") and "pg_catalog.pg_class" in sql and "reltuples" in sql
    assert params == ["ducklake_telemetry", st.STATS_TABLE] and "ducklake_telemetry" not in sql
    assert st.stats_rows_est(_RowsCur((-1,)), "x") is None
    assert st.stats_rows_est(_RowsCur(None), "x") is None


def test_module_excluded_from_data_pipeline():
    affected = compute_affected_artifacts(["src/common/ducklake_catalog_index_status.py"])
    assert set(affected) == {"ducklake_maintenance", "ducklake_maintenance_smoke"}
