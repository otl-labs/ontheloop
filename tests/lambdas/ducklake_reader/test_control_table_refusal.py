"""Reader verbs and named_read both refuse a control-class table (T2.26
control-table-class-and-counter-conformance) -- the symmetric partner of the writer-side refusal.
"""

from __future__ import annotations

import pytest

import src.lambdas.ducklake_reader.handler as h
from src.common import ducklake_reads as reads
from src.common import ducklake_runtime as rt
from src.common.ducklake_scd2_schema import NamedRead

pytestmark = pytest.mark.unit


class FakeCon:
    """A connection that must never receive a SQL statement -- the refusal fires before any."""

    def execute(self, sql, params=None):
        raise AssertionError(f"no SQL should execute before the control-class refusal fires, got: {sql!r}")


def test_reader_refuses_control_table():
    """Every reader verb (read_ops_current, read_ops_history, dq_check) refuses a control-class
    table -- registering it for governance does not grant application read access."""
    con = FakeCon()
    with pytest.raises(rt.DuckLakeRuntimeError, match="control-class table"):
        h.action_read_ops_current({"table": "ops_entity_counters"}, con)
    with pytest.raises(rt.DuckLakeRuntimeError, match="control-class table"):
        h.action_read_ops_history({"table": "ops_entity_counters"}, con)
    from src.common import ducklake_dq_checks as dq

    with pytest.raises(dq.DqCheckInputError) as err:
        h.action_dq_check({"check": "row_count_min", "table": "ops_entity_counters", "params": {"min": 1}}, con)
    assert err.value.param == "table"


def test_require_ops_table_still_rejects_unknown():
    """The control-class refusal is additive: an unrelated unknown table is unaffected."""
    with pytest.raises(rt.DuckLakeRuntimeError, match="unknown or missing ops table"):
        h._require_ops_table("nope")


def test_named_read_cannot_bind_control_table(monkeypatch):
    """named_read refuses a control-class table before ever substituting {tbl} -- its {tbl}
    substitution assumes a current projection a control table lacks, so a FUTURE verb must not be
    able to bind one either."""
    probe = NamedRead(verb="_control_probe", table="ops_entity_counters", sql="SELECT * FROM {tbl}")
    monkeypatch.setitem(reads.NAMED_READS, "_control_probe", probe)
    con = FakeCon()
    with pytest.raises(rt.DuckLakeRuntimeError, match="control-class table"):
        reads.named_read(con, verb="_control_probe", params={})
