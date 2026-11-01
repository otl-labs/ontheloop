"""MIRROR for src/common/ducklake_reads.py (rec-2709 Wave 7).

Split out of the former tests/test_ducklake_runtime.py monolith: read_current (smoke + ops-table
projection/filter, including the structural key_column filter with the NamedReadCon test double),
read_history and named_read (the NAMED_READS registry dispatch) -- plus the append_only read_current
loud-fail guard and the select-only check over every registry and dq_check statement.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.common import ducklake_runtime as rt
from tests.fixtures.ducklake_fakes import FakeCon

pytestmark = pytest.mark.unit


class NamedReadCon:
    def __init__(self, rows=None, cols=("id",)):
        self.executed: list[tuple[str, list | None]] = []
        self._rows = rows or []
        self.description = [(c,) for c in cols]

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        return self

    def fetchall(self):
        return self._rows


def test_read_current_all():
    rows = [("01A", "rec-1", "p", datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 1, tzinfo=timezone.utc))]
    con = FakeCon(read_rows=rows)
    out = rt.read_current(con)
    assert out[0]["rec_id"] == "rec-1"
    assert out[0]["ulid"] == "01A"


def test_read_current_by_rec_id():
    con = FakeCon(read_rows=[])
    rt.read_current(con, rec_id="rec-1")
    sql = con.executed[-1][0]
    assert "WHERE rec_id = ?" in sql
    assert con.executed[-1][1] == ["rec-1"]


def test_read_current_with_limit():
    con = FakeCon(read_rows=[])
    rt.read_current(con, limit=5)
    assert "LIMIT 5" in con.executed[-1][0]


def test_read_current_ops_table_projects_and_filters():
    """read_current(table=...) selects the ops column set and filters on the merge key."""
    rows = [("01U", "rec-1", "open")]

    class _Cur:
        description = [("ulid",), ("id",), ("status",)]

        def __init__(self):
            self.sql = ""
            self.params = None

        def execute(self, sql, params=None):
            self.sql = sql
            self.params = params
            return self

        def fetchall(self):
            return rows

    con = _Cur()
    out = rt.read_current(con, table="ops_recommendations", key="rec-1", limit=10)
    assert "ops_recommendations_current" in con.sql
    assert "WHERE id = ?" in con.sql and "LIMIT 10" in con.sql
    assert con.params == ["rec-1"]
    assert out == [{"ulid": "01U", "id": "rec-1", "status": "open"}]


def test_read_history_orders_newest_first():
    class _Cur:
        description = [("ulid",)]

        def __init__(self):
            self.sql = ""

        def execute(self, sql, params=None):
            self.sql = sql
            return self

        def fetchall(self):
            return [("01B",), ("01A",)]

    con = _Cur()
    out = rt.read_history(con, table="ops_decisions", key="dec-1")
    assert "ops_decisions_history" in con.sql
    assert "ORDER BY last_updated_timestamp DESC" in con.sql
    assert out == [{"ulid": "01B"}, {"ulid": "01A"}]


def test_read_history_applies_limit():
    class _Cur:
        description = [("ulid",)]

        def __init__(self):
            self.sql = ""

        def execute(self, sql, params=None):
            self.sql = sql
            return self

        def fetchall(self):
            return [("01B",)]

    con = _Cur()
    rt.read_history(con, table="ops_decisions", limit=5)
    assert "LIMIT 5" in con.sql


def test_named_read_executes_registry_sql():
    con = NamedReadCon(rows=[("rec-1",)])
    rows = rt.named_read(con, verb="rec_by_id", params={"id": "rec-1"})
    assert rows == [{"id": "rec-1"}]
    sql, params = con.executed[0]
    assert "ops_catalog.ops_recommendations_current" in sql
    assert "{tbl}" not in sql
    assert params == ["rec-1"]


def test_named_read_unknown_verb_loud_fails():
    with pytest.raises(rt.DuckLakeRuntimeError, match="unknown read verb"):
        rt.named_read(NamedReadCon(), verb="drop_everything", params={})


def test_named_read_param_mismatch_loud_fails():
    with pytest.raises(rt.DuckLakeRuntimeError, match="requires params"):
        rt.named_read(NamedReadCon(), verb="rec_by_id", params={})
    with pytest.raises(rt.DuckLakeRuntimeError, match="requires params"):
        rt.named_read(NamedReadCon(), verb="open_recs", params={"sneaky": "x"})


def test_named_read_applies_limit_to_paginable_verb():
    con = NamedReadCon(rows=[("rec-1",)])
    rt.named_read(con, verb="open_recs", params={}, limit=5)
    sql, _params = con.executed[0]
    assert sql.rstrip().endswith("LIMIT 5")


def test_named_read_rejects_limit_on_non_paginable_verb():
    with pytest.raises(rt.DuckLakeRuntimeError, match="not paginable"):
        rt.named_read(NamedReadCon(), verb="rec_by_id", params={"id": "rec-1"}, limit=5)


def test_named_read_cannot_bind_control_table(monkeypatch):
    """named_read refuses a control-class table before ever substituting {tbl} -- dedicated
    coverage home for the ducklake_reads.py refusal itself (the reader-handler symmetric-refusal
    test lives separately in tests/lambdas/ducklake_reader/test_control_table_refusal.py)."""
    from src.common.ducklake_scd2_schema import NamedRead

    probe = NamedRead(verb="_control_probe", table="ops_entity_counters", sql="SELECT * FROM {tbl}")
    monkeypatch.setitem(rt.NAMED_READS, "_control_probe", probe)
    with pytest.raises(rt.DuckLakeRuntimeError, match="control-class table"):
        rt.named_read(NamedReadCon(), verb="_control_probe", params={})


_DQ_REQUESTS = {
    "row_count_min": ("ops_recommendations", {"min": 1}),
    "recency_hours": ("ops_recommendations", {"column": "last_updated_timestamp", "hours": 1}),
    "not_null": ("ops_recommendations", {"column": "title", "exclude_before": "2026-05-01"}),
    "accepted_values": ("ops_recommendations", {"column": "status", "values": ["open"]}),
    "min_length": ("ops_recommendations", {"column": "context", "value": 80}),
    "pattern": ("ops_decisions", {"column": "id", "value": "^dec-[0-9]+$"}),
    "array_element_format": ("ops_recommendations", {"column": "tags", "pattern": "^a$"}),
    "not_before": ("ops_recommendations", {"column": "last_updated_timestamp", "value": "created_timestamp"}),
    "min_value": ("ops_priority_queue", {"column": "rank", "value": 1}),
    "unique": ("ops_execution_plans", {"columns": ["rec_id", "revision"], "projection": "history"}),
    "history_not_ahead_of_current": ("ops_execution_plans", {"column": "revision"}),
    "key_absent": ("ops_recommendations", {"column": "id", "value": "rec-1"}),
}


def test_registry_sql_is_select_only():
    """Every NAMED_READS statement and every rendered dq_check statement is one SELECT/WITH (no caller SQL exists)."""
    import re

    from src.common import ducklake_dq_checks as dq

    def single_select(sql: str) -> bool:
        return re.match(r"^\s*(?:SELECT|WITH)\b", sql, re.IGNORECASE) is not None and ";" not in sql.rstrip().rstrip(";")

    assert all(single_select(entry.sql) for entry in rt.NAMED_READS.values())
    assert set(_DQ_REQUESTS) == set(dq.DQ_KINDS)
    for kind, (table, params) in _DQ_REQUESTS.items():
        sql, _binds = dq.render(dq.validate({"check": kind, "table": table, "params": params}))
        assert single_select(sql), kind


def test_read_current_rejects_unknown_filter_column():
    con = NamedReadCon()
    with pytest.raises(rt.DuckLakeRuntimeError, match="unknown filter column"):
        rt.read_current(con, table="ops_recommendations", key="open", key_column="not_a_column")


def test_read_current_binds_named_column():
    con = NamedReadCon(rows=[], cols=("id", "status"))
    rt.read_current(con, table="ops_recommendations", key="open", key_column="status")
    sql, params = con.executed[0]
    assert "WHERE status = ?" in sql
    assert params == ["open"]


def test_read_current_append_only_raises():
    """read_current loud-fails for append_only tables (no current write-through projection, Decision 55)."""
    con = FakeCon()
    with pytest.raises(rt.DuckLakeRuntimeError, match="append_only"):
        rt.read_current(con, table="ops_smoke_events")
