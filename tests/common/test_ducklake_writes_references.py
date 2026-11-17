"""MIRROR for the writer's dependency-existence lookup (Decision 268 amending Decision 210 cl.2(a)): src/common/
ducklake_writes.py calls check_references inside the write transaction, before any MERGE, terminal on rejection."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

import src.common.ducklake_runtime as rt
from src.common.ducklake_write_rules import RowRuleViolationError
from tests.fixtures.ducklake_fakes import FakeCon, ops_rec_fields, ops_rec_record

pytestmark = pytest.mark.unit

STORED_AT = datetime(2026, 4, 1, tzinfo=timezone.utc)


class _RefCon(FakeCon):
    """FakeCon plus the reference lookup: a SELECT ... IN (?, ...) returns the asked ids that exist (None: all exist)."""

    def __init__(self, *, known_ids: set | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._known_ids = known_ids
        self._asked: list | None = None

    def execute(self, sql, params=None):
        self._asked = params
        return super().execute(sql, params)

    def fetchall(self):
        if self._last.startswith("SELECT ") and " IN (" in self._last:
            return [(p,) for p in (self._asked or []) if self._known_ids is None or p in self._known_ids]
        return super().fetchall()


def _lookups(con: FakeCon) -> list[tuple[str, list | None]]:
    return [(s, p) for s, p in con.executed if s.startswith("SELECT id FROM") and " IN (" in s]


def _merges(con: FakeCon) -> list[str]:
    return [s for s, _ in con.executed if s.startswith("MERGE INTO")]


class _InsertCon:
    """The file_scd2 shape up to the lookup: no replay row, no counter; any MERGE or counter write is a failure."""

    def __init__(self, known: set[str]) -> None:
        self.executed: list[tuple[str, list | None]] = []
        self._known = known
        self._last = ""
        self._params: list | None = None

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        self._last, self._params = sql, params
        return self

    def fetchall(self):
        if " IN (" in self._last:
            return [(p,) for p in self._params or [] if p in self._known]
        return []


def test_file_scd2_rejects_dangling_dependency_inside_transaction() -> None:
    con = _InsertCon({"rec-1"})
    with pytest.raises(RowRuleViolationError) as raised:
        rt.file_scd2(con, ops_rec_fields(dependencies=["rec-1", "rec-999999999"]), table="ops_recommendations")
    assert (raised.value.rule, raised.value.column) == ("array_element_reference", "dependencies")
    assert "rec-999999999" not in str(raised.value)
    statements = [s for s, _ in con.executed]
    assert statements[0] == "BEGIN TRANSACTION" and "ROLLBACK" in statements
    assert statements.count("BEGIN TRANSACTION") == 1  # terminal: never retried
    assert not any(s.startswith("MERGE INTO") or "ops_entity_counters" in s for s in statements)  # no counter write


def test_write_scd2_rejects_dangling_dependency_inside_transaction() -> None:
    con = _RefCon(created_lookup=None, known_ids={"rec-1"})
    with pytest.raises(RowRuleViolationError) as raised:
        rt.write_scd2(con, ops_rec_record(dependencies=["rec-1", "rec-999999999"]), table="ops_recommendations")
    assert (raised.value.rule, raised.value.column) == ("array_element_reference", "dependencies")
    statements = [s for s, _ in con.executed]
    assert statements.count("BEGIN TRANSACTION") == 1 and ("ROLLBACK", None) in con.executed
    assert not _merges(con)


def test_valid_dependencies_pass_with_one_lookup() -> None:
    con = _RefCon(created_lookup=None, known_ids={"rec-7", "rec-8"})
    rt.write_scd2(con, ops_rec_record(dependencies=["rec-7", "rec-8"]), table="ops_recommendations")
    assert len(_lookups(con)) == 1
    assert len(_merges(con)) == 2


def test_unchanged_legacy_value_issues_no_lookup() -> None:
    prior = {"created_timestamp": STORED_AT, "status": "open", "dependencies": ["rec-610"]}
    con = _RefCon(created_lookup=[prior], known_ids=set())
    rt.write_scd2(
        con,
        ops_rec_record(status="closed", dependencies=["rec-610"]),
        table="ops_recommendations",
        require_exists=True,
        sleep=lambda s: None,
    )
    assert _lookups(con) == []
    assert len(_merges(con)) == 2


def test_changed_value_is_looked_up() -> None:
    prior = {"created_timestamp": STORED_AT, "status": "open", "dependencies": ["rec-610"]}
    con = _RefCon(created_lookup=[prior], known_ids={"rec-5"})
    with pytest.raises(RowRuleViolationError):
        rt.write_scd2(
            con,
            ops_rec_record(dependencies=["rec-610", "rec-5"]),
            table="ops_recommendations",
            require_exists=True,
            sleep=lambda s: None,
        )
    assert len(_lookups(con)) == 1 and not _merges(con)


def test_empty_or_null_issues_no_lookup() -> None:
    for value in ([], None):
        con = _RefCon(created_lookup=None, known_ids=set())
        rt.write_scd2(con, ops_rec_record(dependencies=value), table="ops_recommendations")
        assert _lookups(con) == []


def test_lookup_binds_one_placeholder_per_distinct_id() -> None:
    con = _RefCon(created_lookup=None)
    rt.write_scd2(con, ops_rec_record(dependencies=["rec-3", "rec-2", "rec-3"]), table="ops_recommendations")
    ((sql, params),) = _lookups(con)
    assert params == ["rec-2", "rec-3"]
    assert sql.count("?") == 2 and sql.count(";") == 0
    assert sql == "SELECT id FROM ops_catalog.ops_recommendations_current WHERE id IN (?, ?)"
