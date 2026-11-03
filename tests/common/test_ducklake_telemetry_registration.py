"""Coverage home for src/common/ducklake_telemetry_registration.py.

Pure nodes and fake-connection nodes need no duckdb; the SQL nodes run on a plain in-memory DuckDB with a catalog
attached as ops_catalog and the dimension and counter tables created from the registry DDL (fixture-scoped importorskip,
so the module still collects in the fast tier).
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from src.common import ducklake_telemetry_registration as reg
from src.common.ducklake_control_tables import _column_ddl as control_ddl
from src.common.ducklake_control_tables import resolve_control_spec
from src.common.ducklake_scd2_schema import DuckLakeRuntimeError, _column_ddl, load_field_semantics, resolve_table_spec
from src.common.ducklake_telemetry_txn import TelemetryRequestError, run_telemetry_txn
from src.common.ducklake_write_rules import RowRuleViolationError
from src.common.ducklake_writes import OCCRetryExhaustedError, mint_write_identity

ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
PRINCIPAL = reg.CallerPrincipal("aws_account", "acct-under-test", "SomeRole")
OTHER = reg.CallerPrincipal("aws_account", "other-acct", "SomeRole")
DERIVATION = {"inputs": ["project_ref"], "prefix_map": {"drill:": "drill", "synthetic:": "synthetic"}, "default": "real"}


@pytest.fixture(autouse=True)
def _clean_caches() -> None:
    reg.clear_telemetry_caches()


def _no_sleep(_s: float) -> None:
    return None


class _Cursor:
    def __init__(self, row: Any) -> None:
        self._row = row

    def fetchone(self) -> Any:
        return self._row


class ScriptedCon:
    """Answers the registration reads (tenant miss, one counter row) and fails COMMIT with an OCC-looking error."""

    def __init__(self, commit_error: str | None = None) -> None:
        self.sql: list[str] = []
        self._commit_error = commit_error

    def execute(self, sql: str, params: object = None) -> _Cursor:
        self.sql.append(sql)
        if sql == "COMMIT" and self._commit_error:
            raise RuntimeError(self._commit_error)
        if sql.startswith("SELECT count(*)"):
            return _Cursor((1,))
        return _Cursor(None)


class NoStatementCon:
    def execute(self, sql: str, params: object = None) -> Any:
        raise AssertionError(f"no statement may run: {sql}")


def test_purpose_follows_the_registry_prefix_map() -> None:
    sem = load_field_semantics()
    derivation = reg.purpose_derivation(sem)
    assert derivation is not None and derivation["prefix_map"] == {"drill:": "drill", "synthetic:": "synthetic"}
    assert reg.purpose_for("drill:x", derivation) == "drill"
    assert reg.purpose_for("synthetic:deploy", derivation) == "synthetic"
    assert reg.purpose_for("github.com/o/r", derivation) == "real"
    custom = {"prefix_map": {"zz:": "synthetic"}, "default": "real"}
    assert reg.purpose_for("zz:a", custom) == "synthetic" and reg.purpose_for("drill:a", custom) == "real"


@pytest.mark.parametrize("derivation", [None, {}, {"prefix_map": {}}, {"default": "real"}])
def test_missing_purpose_projection_is_loud(derivation: Any) -> None:
    with pytest.raises(DuckLakeRuntimeError, match="derivations.purpose"):
        reg.purpose_for("a", derivation)
    assert reg.purpose_derivation({"ops_tables": {}}) is None


def test_caller_principal_repr_hides_the_ref() -> None:
    assert "acct-under-test" not in repr(PRINCIPAL) and "acct-under-test" not in str(PRINCIPAL)
    assert PRINCIPAL.kind in repr(PRINCIPAL) and PRINCIPAL.role_name == "SomeRole"


def test_clear_caches_empties_both_bindings() -> None:
    reg._TENANT_CACHE[("aws_account", "r")] = "T"
    reg._PROJECT_CACHE[("T", "p")] = "P"
    assert reg.cached_tenant_id("aws_account", "r") == "T" and reg.cached_project_id("T", "p") == "P"
    reg.clear_telemetry_caches()
    assert reg.cached_tenant_id("aws_account", "r") is None and reg.cached_project_id("T", "p") is None


def test_cache_untouched_when_commit_conflicts() -> None:
    con = ScriptedCon(commit_error="write-write conflict")
    with pytest.raises(OCCRetryExhaustedError):
        reg.register_tenant(con, PRINCIPAL, sleep=_no_sleep)
    assert reg.cached_tenant_id(PRINCIPAL.kind, PRINCIPAL.ref) is None
    assert any(s.startswith("UPDATE") for s in con.sql)
    with pytest.raises(OCCRetryExhaustedError):
        reg.register_project(_ProjectCon(), PRINCIPAL, "p", sleep=_no_sleep)
    assert reg._PROJECT_CACHE == {} and reg._TENANT_CACHE == {}


class _ProjectCon(ScriptedCon):
    def __init__(self) -> None:
        super().__init__(commit_error="write-write conflict")

    def execute(self, sql: str, params: object = None) -> _Cursor:
        if sql.startswith("SELECT tenant_id FROM"):
            self.sql.append(sql)
            return _Cursor(("TENANT",))
        return super().execute(sql, params)


def test_project_entry_points_refuse_an_invalid_string_before_any_statement() -> None:
    sem = load_field_semantics()
    identity = mint_write_identity()
    for bad in ("a\x00b", "a\ud800b"):
        with pytest.raises(TelemetryRequestError) as info:
            reg.lookup_project(NoStatementCon(), "T", bad, semantics=sem)
        assert info.value.status == 400 and info.value.fields == {"field": "project_ref"}
        with pytest.raises(TelemetryRequestError) as info:
            reg.resolve_or_register_project(
                NoStatementCon(), "T", bad, explicit=True, attributes={}, identity=identity, semantics=sem, candidate_id="C"
            )
        assert info.value.fields == {"field": "project_ref"}
        with pytest.raises(TelemetryRequestError) as info:
            reg.resolve_or_register_project(
                NoStatementCon(),
                "T",
                "ok",
                explicit=True,
                attributes={"remote": bad},
                identity=identity,
                semantics=sem,
                candidate_id="C",
            )
        assert info.value.fields == {"field": "remote"}


@pytest.fixture
def con() -> Any:
    duckdb = pytest.importorskip("duckdb")
    c = duckdb.connect()
    c.execute("ATTACH ':memory:' AS ops_catalog")
    sem = load_field_semantics()
    for table in (reg.TENANTS, reg.PROJECTS):
        spec = resolve_table_spec(table, sem)
        for physical in (spec.history_table, spec.current_table):
            c.execute(f"CREATE TABLE ops_catalog.{physical} ({_column_ddl(spec)})")
    ctl = resolve_control_spec("ops_entity_counters")
    c.execute(f"CREATE TABLE ops_catalog.ops_entity_counters ({control_ddl(ctl)})")
    for name in (reg.TENANTS, reg.PROJECTS):
        c.execute("INSERT INTO ops_catalog.ops_entity_counters VALUES (?, 0)", [name])
    return c


def _count(con: Any, table: str) -> int:
    return int(con.execute(f"SELECT count(*) FROM ops_catalog.{table}").fetchone()[0])


def _counter(con: Any, name: str) -> int:
    return int(
        con.execute("SELECT current_value FROM ops_catalog.ops_entity_counters WHERE counter_name = ?", [name]).fetchone()[0]
    )


def test_register_tenant_is_idempotent_and_returns_one_ulid(con: Any) -> None:
    first = reg.register_tenant(con, PRINCIPAL, sleep=_no_sleep)
    second = reg.register_tenant(con, PRINCIPAL, sleep=_no_sleep)
    other = reg.register_tenant(con, OTHER, sleep=_no_sleep)
    assert first.created is True and second.created is False and other.created is True
    assert first.entity_id == second.entity_id != other.entity_id
    assert _count(con, "ops_tenants_history") == 2 and _count(con, "ops_tenants_current") == 2
    assert reg.cached_tenant_id(PRINCIPAL.kind, PRINCIPAL.ref) == first.entity_id
    row = con.execute("SELECT status, canonical_tenant_id, principal_kind FROM ops_catalog.ops_tenants_current").fetchone()
    assert row == ("active", None, "aws_account")


def test_register_tenant_locks_the_counter_only_when_creating(con: Any) -> None:
    reg.register_tenant(con, PRINCIPAL, sleep=_no_sleep)
    assert _counter(con, "ops_tenants") == 1
    reg.register_tenant(con, PRINCIPAL, sleep=_no_sleep)
    assert _counter(con, "ops_tenants") == 1 and _counter(con, "ops_projects") == 0
    assert reg.lookup_tenant(con, PRINCIPAL.kind, PRINCIPAL.ref) is not None
    assert reg.lookup_tenant(con, "aws_account", "nobody") is None


def test_register_project_writes_history_and_current_with_derived_purpose(con: Any) -> None:
    reg.register_tenant(con, PRINCIPAL, sleep=_no_sleep)
    real = reg.register_project(con, PRINCIPAL, "github.com/o/r", attributes={"name": "R"}, sleep=_no_sleep)
    drill = reg.register_project(con, PRINCIPAL, "drill:one", sleep=_no_sleep)
    synth = reg.register_project(con, PRINCIPAL, "synthetic:deploy-smoke-x", sleep=_no_sleep)
    again = reg.register_project(con, PRINCIPAL, "github.com/o/r", sleep=_no_sleep)
    assert real.created and drill.created and synth.created and not again.created
    assert again.entity_id == real.entity_id and again.tenant_id == real.tenant_id
    purposes = dict(con.execute("SELECT project_ref, purpose FROM ops_catalog.ops_projects_current").fetchall())
    assert purposes == {"github.com/o/r": "real", "drill:one": "drill", "synthetic:deploy-smoke-x": "synthetic"}
    assert _count(con, "ops_projects_history") == 3 and _counter(con, "ops_projects") == 3
    assert con.execute("SELECT DISTINCT registration, status FROM ops_catalog.ops_projects_current").fetchall() == [
        ("registered", "active")
    ]
    assert reg.lookup_project(con, real.tenant_id, "github.com/o/r") == (real.entity_id, "registered")
    assert reg.lookup_project(con, real.tenant_id, "missing") is None
    assert reg.cached_project_id(real.tenant_id, "drill:one") == drill.entity_id


def test_register_project_attribute_update_writes_a_new_version(con: Any) -> None:
    reg.register_tenant(con, PRINCIPAL, sleep=_no_sleep)
    first = reg.register_project(con, PRINCIPAL, "github.com/o/r", attributes={"name": "R"}, sleep=_no_sleep)
    same = reg.register_project(con, PRINCIPAL, "github.com/o/r", attributes={"name": "R"}, sleep=_no_sleep)
    bare = reg.register_project(con, PRINCIPAL, "github.com/o/r", sleep=_no_sleep)
    assert _count(con, "ops_projects_history") == 1 and not same.created and not bare.created
    renamed = reg.register_project(con, PRINCIPAL, "github.com/o/r", attributes={"remote": "git@x:o/r"}, sleep=_no_sleep)
    assert renamed.entity_id == first.entity_id and not renamed.created
    assert _count(con, "ops_projects_history") == 2 and _count(con, "ops_projects_current") == 1
    name, remote, created, updated = con.execute(
        "SELECT name, remote, created_timestamp, last_updated_timestamp FROM ops_catalog.ops_projects_current"
    ).fetchone()
    assert (name, remote) == ("R", "git@x:o/r") and updated > created
    created_values = {r[0] for r in con.execute("SELECT created_timestamp FROM ops_catalog.ops_projects_history").fetchall()}
    assert len(created_values) == 1
    assert _counter(con, "ops_projects") == 1


def test_unregistered_reserved_ref_is_refused_unless_explicit(con: Any) -> None:
    sem = load_field_semantics()
    tenant = reg.register_tenant(con, PRINCIPAL, sleep=_no_sleep).entity_id

    def resolve(ref: str, explicit: bool) -> tuple[str, bool]:
        identity, cand = mint_write_identity(), reg.new_entity_id()
        return run_telemetry_txn(
            con,
            lambda c: reg.resolve_or_register_project(
                c, tenant, ref, explicit=explicit, attributes={}, identity=identity, semantics=sem, candidate_id=cand
            ),
        ).result

    with pytest.raises(TelemetryRequestError) as info:
        resolve("drill:x", False)
    assert (info.value.status, info.value.error_type, info.value.fields) == (
        403,
        "project_unregistered",
        {"remedy": "register_project"},
    )
    assert _count(con, "ops_projects_history") == 0 and _counter(con, "ops_projects") == 0
    auto_id, auto_created = resolve("github.com/o/auto", False)
    assert auto_created and con.execute("SELECT registration FROM ops_catalog.ops_projects_current").fetchone() == (
        "unregistered",
    )
    assert resolve("github.com/o/auto", False) == (auto_id, False)
    promoted = reg.register_project(con, PRINCIPAL, "github.com/o/auto", sleep=_no_sleep)
    assert promoted.entity_id == auto_id and not promoted.created
    assert con.execute("SELECT registration FROM ops_catalog.ops_projects_current").fetchone() == ("registered",)
    assert _count(con, "ops_projects_history") == 2
    assert resolve("drill:x", True)[1] is True


def test_register_project_requires_a_registered_tenant_in_the_transaction(con: Any) -> None:
    with pytest.raises(TelemetryRequestError) as info:
        reg.register_project(con, PRINCIPAL, "github.com/o/r", sleep=_no_sleep)
    assert (info.value.status, info.value.error_type, info.value.fields) == (
        403,
        "tenant_unresolved",
        {"remedy": "register_tenant"},
    )
    assert _count(con, "ops_projects_history") == 0


def test_lock_requires_exactly_one_counter_row(con: Any) -> None:
    con.execute("INSERT INTO ops_catalog.ops_entity_counters VALUES ('ops_tenants', 0)")
    with pytest.raises(DuckLakeRuntimeError, match="provision_telemetry"):
        reg.lock_counter(con, "ops_tenants")
    con.execute("DELETE FROM ops_catalog.ops_entity_counters WHERE counter_name = 'ops_projects'")
    with pytest.raises(DuckLakeRuntimeError, match="expected exactly 1"):
        reg.lock_counter(con, "ops_projects")
    with pytest.raises(DuckLakeRuntimeError):
        reg.register_tenant(con, PRINCIPAL, sleep=_no_sleep)
    assert _count(con, "ops_tenants_history") == 0


def test_row_rule_violation_writes_nothing(con: Any) -> None:
    sem = load_field_semantics()
    identity = mint_write_identity()
    with pytest.raises(RowRuleViolationError):
        run_telemetry_txn(
            con,
            lambda c: reg.resolve_or_register_tenant(c, PRINCIPAL, identity=identity, semantics=sem, candidate_id=None),
        )
    assert _count(con, "ops_tenants_history") == 0 and _count(con, "ops_tenants_current") == 0
    assert _counter(con, "ops_tenants") == 0


def test_minted_ids_are_canonical_uppercase_ulids(con: Any) -> None:
    tenant = reg.register_tenant(con, PRINCIPAL, sleep=_no_sleep)
    project = reg.register_project(con, PRINCIPAL, "github.com/o/r", sleep=_no_sleep)
    assert ULID_RE.match(tenant.entity_id) and ULID_RE.match(project.entity_id) and ULID_RE.match(reg.new_entity_id())
    ulids = [r[0] for r in con.execute("SELECT ulid FROM ops_catalog.ops_projects_history").fetchall()]
    assert all(ULID_RE.match(u) for u in ulids)
