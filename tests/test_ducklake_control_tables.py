"""Tests for src/common/ducklake_control_tables.py (T2.26
control-table-class-and-counter-conformance): control spec shape, DDL, partition, and write-verb
refusal, plus the ducklake_scd2_schema shim's directed-raise delegation.
"""

from __future__ import annotations

import dataclasses
import inspect

import pytest

from src.common import ducklake_control_tables as ct
from src.common import ducklake_scd2_schema as schema
from src.common import ducklake_tables as tables
from src.common import ducklake_writes as writes

pytestmark = pytest.mark.unit


class _RecordingCon:
    def __init__(self):
        self.executed: list[str] = []

    def execute(self, sql, params=None):
        self.executed.append(sql)
        return self


def test_control_spec_has_no_history_pair():
    """Control-class resolution returns a single partitioned table with no history/current pair."""
    spec = ct.resolve_control_spec("ops_entity_counters")
    assert spec.table == "ops_entity_counters"
    assert spec.partition_key == "counter_name"
    assert spec.ordered_columns == (("counter_name", "VARCHAR"), ("current_value", "BIGINT"))
    assert not hasattr(spec, "history_table")
    assert not hasattr(spec, "current_table")


def test_resolve_control_spec_rejects_unknown_table():
    with pytest.raises(schema.DuckLakeRuntimeError, match="unknown control table"):
        ct.resolve_control_spec("ops_not_a_real_table")


def test_control_table_names_and_is_control_table():
    assert ct.control_table_names() == ("ops_entity_counters",)
    assert ct.is_control_table("ops_entity_counters") is True
    assert ct.is_control_table("ops_recommendations") is False
    assert ct.is_control_table(None) is False
    assert ct.is_control_table(123) is False


def test_control_ddl_single_table():
    """Control-class creation issues one CREATE plus one SET PARTITIONED BY."""
    con = _RecordingCon()
    tables.create_control_table(con, table="ops_entity_counters")
    creates = [s for s in con.executed if s.startswith("CREATE TABLE")]
    partitions = [s for s in con.executed if "SET PARTITIONED BY" in s]
    drops = [s for s in con.executed if s.startswith("DROP TABLE")]
    assert len(creates) == 1
    assert len(partitions) == 1
    assert not drops
    assert "counter_name VARCHAR NOT NULL" in creates[0]
    assert "current_value BIGINT NOT NULL" in creates[0]
    assert "(counter_name)" in partitions[0]


def test_control_ddl_force_recreate_drops_first():
    con = _RecordingCon()
    tables.create_control_table(con, table="ops_entity_counters", force_recreate=True)
    assert con.executed[0].startswith("DROP TABLE IF EXISTS")
    assert any(s.startswith("CREATE TABLE") for s in con.executed)


def test_write_verb_refusal_on_control_table():
    """write_ops, file_ops and update_ops each raise on a control-class table -- the write verbs
    refuse it rather than falling through into the SCD2 branch against current_table=None."""
    con = object()  # never touched: the refusal fires before any catalog work
    with pytest.raises(schema.SchemaGateError, match="control-class table"):
        writes.write_scd2(con, {"counter_name": "ops_recommendations"}, table="ops_entity_counters")
    with pytest.raises(schema.SchemaGateError, match="control-class table"):
        writes.file_scd2(con, {"counter_name": "ops_recommendations"}, table="ops_entity_counters")
    with pytest.raises(schema.SchemaGateError, match="control-class table"):
        writes.write_scd2(con, {"counter_name": "ops_recommendations"}, table="ops_entity_counters", require_exists=True)


_SPEC_BASELINE: tuple[tuple[str, str, bool], ...] = (
    ("table", "str | None", False),
    ("history_table", "str", False),
    ("current_table", "str | None", False),
    ("merge_key", "str", False),
    ("fields", "dict[str, Any]", False),
    ("ordered_columns", "tuple[tuple[str, str], ...]", False),
    ("partition_history", "str", False),
    ("partition_current", "str | None", False),
    ("entity_id_prefix", "str | None", True),
    ("id_keyspace", "str", True),
    ("write_mode", "str", True),
)
# Approved additions to ScdTableSpec beyond the baseline, each naming its admitting plan. A new field needs a reviewed
# line here and a default; widening or reordering a baseline field edits _SPEC_BASELINE in the same PR.
_SPEC_APPROVED_ADDITIONS = {"id_scheme": "PLAN-telemetry-table-registration"}


def _annotation_text(annotation) -> str:
    if isinstance(annotation, str):
        return annotation
    return inspect.formatannotation(annotation).replace("typing.", "")


def _has_default(f: dataclasses.Field) -> bool:
    return f.default is not dataclasses.MISSING or f.default_factory is not dataclasses.MISSING


def _assert_spec_shape(cls) -> None:
    assert cls.__dataclass_params__.frozen, "ScdTableSpec must stay frozen"
    fields = dataclasses.fields(cls)
    lead = tuple((f.name, _annotation_text(f.type), _has_default(f)) for f in fields[: len(_SPEC_BASELINE)])
    assert lead == _SPEC_BASELINE
    for f in fields[len(_SPEC_BASELINE) :]:
        assert f.name in _SPEC_APPROVED_ADDITIONS, f"unrostered ScdTableSpec field {f.name!r}"
        assert _has_default(f), f"approved addition {f.name!r} must carry a default"


def test_scd2_schema_delegates_without_widening():
    """resolve_table_spec raises a directed error for a control-class table; ScdTableSpec.history_table
    stays non-optional (mypy is ratchet-enforced) -- the shim delegates, it never widens. Growth is admitted only
    through an explicit roster line and a default."""
    with pytest.raises(schema.SchemaGateError, match="ducklake_control_tables"):
        schema.resolve_table_spec("ops_entity_counters")

    history_field = next(f for f in dataclasses.fields(schema.ScdTableSpec) if f.name == "history_table")
    assert history_field.type == "str"  # unchanged, non-Optional
    _assert_spec_shape(schema.ScdTableSpec)


def _spec_fixture(*, drop=None, extra=(), retype=None, swap=None, frozen=True):
    fields = [(n, a, dataclasses.field(default=None) if d else dataclasses.field()) for n, a, d in _SPEC_BASELINE]
    fields = [f for f in fields if f[0] != drop]
    if retype:
        fields = [(n, retype[1] if n == retype[0] else a, d) for n, a, d in fields]
    if swap:
        i, j = (next(k for k, f in enumerate(fields) if f[0] == n) for n in swap)
        fields[i], fields[j] = fields[j], fields[i]
    return dataclasses.make_dataclass("SpecFixture", [*fields, *extra], frozen=frozen)


def test_spec_shape_guard_admits_only_approved_defaulted_growth():
    _assert_spec_shape(_spec_fixture())
    _assert_spec_shape(_spec_fixture(extra=[("id_scheme", "str", dataclasses.field(default="x"))]))
    rejected = {
        "dropped baseline field": _spec_fixture(drop="merge_key"),
        "unrostered field": _spec_fixture(extra=[("surprise", "str", dataclasses.field(default="x"))]),
        "widened annotation": _spec_fixture(retype=("history_table", "str | None")),
        "reordered fields": _spec_fixture(swap=("history_table", "current_table")),
        "not frozen": _spec_fixture(frozen=False),
        "rostered without default": _spec_fixture(extra=[("id_scheme", "str", dataclasses.field(kw_only=True))]),
    }
    for label, cls in rejected.items():
        with pytest.raises(AssertionError):
            _assert_spec_shape(cls)
            pytest.fail(f"guard admitted: {label}")


# ---------------------------------------------------------------------------
# Counter bookkeeping (ensure_entity_counters_table / bootstrap_entity_counter /
# _safe_rollback) -- relocated here from ducklake_writes.py (T2.26 SLOC decompose). This is now
# the sole dedicated coverage home for src/common/ducklake_control_tables.py (Decision 131 mirror
# convention: tests/test_ducklake_control_tables.py), so these three primitives need their own
# direct tests independent of ducklake_writes' file_scd2/write_scd2 integration coverage.
# ---------------------------------------------------------------------------


class _BootstrapCon:
    """Minimal scripted double for the bootstrap_entity_counter transaction shape."""

    def __init__(self, *, seed_max: int = 2170, fail_on: str | None = None):
        self.executed: list[str] = []
        self._seed_max = seed_max
        self._fail_on = fail_on
        self._last = ""

    def execute(self, sql, params=None):
        self._last = sql
        if self._fail_on is not None and self._fail_on in sql:
            raise RuntimeError("boom")
        self.executed.append(sql)
        return self

    def fetchone(self):
        if "coalesce(max(CAST(regexp_extract" in self._last:
            return (self._seed_max,)
        return None


def test_ensure_entity_counters_table_issues_create_if_not_exists():
    con = _RecordingCon()
    ct.ensure_entity_counters_table(con)
    assert len(con.executed) == 1
    assert con.executed[0].startswith("CREATE TABLE IF NOT EXISTS")
    assert "counter_name VARCHAR NOT NULL" in con.executed[0]


def test_safe_rollback_swallows_error():
    class _RollbackFailsCon:
        def execute(self, sql, params=None):
            raise RuntimeError("no active transaction")

    ct._safe_rollback(_RollbackFailsCon())  # must not raise


def test_bootstrap_entity_counter_seeds_from_history_max():
    con = _BootstrapCon(seed_max=2178)
    spec = schema.resolve_table_spec("ops_recommendations")
    seed = ct.bootstrap_entity_counter(con, spec)
    assert seed == 2178
    assert any(s.startswith("CREATE TABLE IF NOT EXISTS") for s in con.executed)
    assert any(s.startswith("DELETE FROM") and ct.ENTITY_COUNTERS_TABLE in s for s in con.executed)
    assert any("INSERT INTO" in s and ct.ENTITY_COUNTERS_TABLE in s for s in con.executed)
    assert "COMMIT" in con.executed


def test_bootstrap_entity_counter_rejects_unprefixed_table():
    con = _BootstrapCon()
    spec = schema.resolve_table_spec("ops_priority_queue")
    with pytest.raises(schema.DuckLakeRuntimeError, match="no allocation counter"):
        ct.bootstrap_entity_counter(con, spec)


def test_bootstrap_entity_counter_rejects_caller_keyspace():
    con = _BootstrapCon()
    spec = schema.resolve_table_spec("ops_decisions")
    with pytest.raises(schema.DuckLakeRuntimeError, match="no writer-owned keyspace"):
        ct.bootstrap_entity_counter(con, spec)


def test_bootstrap_entity_counter_rolls_back_and_reraises_on_failure():
    """A mid-transaction failure (e.g. the INSERT) rolls back and re-raises -- never swallowed."""
    con = _BootstrapCon(fail_on="INSERT INTO")
    spec = schema.resolve_table_spec("ops_recommendations")
    with pytest.raises(RuntimeError, match="boom"):
        ct.bootstrap_entity_counter(con, spec)
    assert "ROLLBACK" in con.executed
