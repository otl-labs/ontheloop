"""Real Postgres 16 proof of the owner-provisioning split (Decision 216, Decision 230; rec-4294).

pytestmark integration: excluded from --pre and the full tier, run before merge by the implementer (CI gating of real-engine
tests is rec-4071). The shared stage (tests/fixtures/ducklake_pg_stage.py) builds a Neon-shaped catalog from
DUCKLAKE_TEST_PG_ADMIN_DSN, with and without pg_read_all_data, with both owner roles adopted and the four closed logins
provisioned through the real verbs; it FAILS, never skips (rec-4071), when the variable is unset, an extension cannot load, or
duckdb.__version__ differs from config/lambda/ducklake/version.yaml (the Decision 99 lockstep pin). Here `stage.owner` is the
smoke maintainer's connection: its session role is ducklake_smoke_owner, so the DDL it runs is role-owned.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

duckdb = pytest.importorskip("duckdb")
psycopg2 = pytest.importorskip("psycopg2")
pytest.importorskip("yaml")

from src.common import ducklake_telemetry_provision as prov  # noqa: E402
from src.common import ducklake_telemetry_registration as reg  # noqa: E402
from src.common.ducklake_scd2_schema import DuckLakeRuntimeError, load_field_semantics  # noqa: E402
from src.common.ducklake_telemetry_txn import run_telemetry_txn  # noqa: E402
from tests.fixtures import ducklake_pg_stage as pg_stage  # noqa: E402

pytestmark = pytest.mark.integration

SEM = load_field_semantics()
SCOPED = "ducklake_telemetry_writer"
OWNER = pg_stage.OWNER
SCHEMA = pg_stage.SMOKE
PRINCIPAL = reg.CallerPrincipal("aws_account", "acct-under-test", "SomeRole")


@pytest.fixture(params=["read_all", "no_read_all"])
def stage(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _allow_network_for_integration: None,
) -> Any:
    built = pg_stage.build_stage(tmp_path, monkeypatch, read_all=request.param == "read_all")
    yield built
    built.close()


def _create(owner: Any) -> prov.CreateResult:
    return run_telemetry_txn(owner, lambda c: prov.create_telemetry_tables(c, semantics=SEM)).result


def _provision(con: Any) -> prov.ProvisionResult:
    return run_telemetry_txn(con, lambda c: prov.provision_telemetry_catalog(c, semantics=SEM)).result


def _append(con: Any, n: int) -> None:
    con.execute(
        "INSERT INTO ops_catalog.telemetry_sessions (event_id, session_started_at, producer, parser_version, "
        "created_timestamp, tenant_id, project_id, session_id) VALUES (?, now(), 'p', 2, now(), 't', 'p', 's')",
        [f"e{n}"],
    )


def _report(con: Any) -> prov.OwnershipReport:
    return prov.ownership_report(con, meta_schema=SCHEMA, scoped_logins=(SCOPED,))


def test_role_sql_grants_the_scoped_login_no_create(stage: Any) -> None:
    priv = lambda schema, kind: stage.sql(f"SELECT has_schema_privilege('{SCOPED}', '{schema}', '{kind}')")[0][0]  # noqa: E731
    assert priv(SCHEMA, "USAGE") is True
    assert priv(SCHEMA, "CREATE") is False and priv("public", "CREATE") is False
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        stage.sql(f"CREATE TABLE {SCHEMA}.planted(x int)", user=SCOPED)
    assert prov.session_can_create(stage.scoped(), meta_schema=SCHEMA) is False
    assert prov.session_can_create(stage.owner, meta_schema=SCHEMA) is True


def test_owner_creates_then_scoped_login_verifies_seeds_and_writes(stage: Any) -> None:
    created = _create(stage.owner)
    assert len(created.created) == 9 and list(created.tables) == list(prov.target_tables(SEM))
    assert _report(stage.owner) == prov.OwnershipReport(0, True, False, False, True, "ducklake_smoke_owner")

    scoped = stage.scoped()
    provisioned = _provision(scoped)
    assert provisioned.created == () and provisioned.counters == {"ops_tenants": 0, "ops_projects": 0}
    assert reg.register_tenant(scoped, PRINCIPAL).created is True
    assert reg.register_project(scoped, PRINCIPAL, "synthetic:ownership").created is True
    _append(scoped, 1)

    owner = stage.reconnect_owner()
    assert owner.execute("SELECT count(*) FROM ops_catalog.telemetry_sessions").fetchone()[0] == 1
    assert owner.execute("SELECT count(*) FROM ops_catalog.ops_tenants_current").fetchone()[0] == 1
    pg_dump = shutil.which("pg_dump") or next(iter(sorted(Path("/usr/lib/postgresql").glob("*/bin/pg_dump"))), None)
    assert pg_dump, "pg_dump not found"
    dump = subprocess.run(
        [str(pg_dump), "--format=custom", "-h", stage.params.get("host", "127.0.0.1"), "-p", stage.params.get("port", "5432"),
         "-U", OWNER, "-f", str(stage.tmp / "dump"), OWNER],
        capture_output=True,
        check=False,
    )  # fmt: skip
    assert dump.returncode == 0, dump.stderr.decode()[-300:]
    owner.execute("CALL ducklake_flush_inlined_data('ops_catalog')").fetchall()
    owner.execute("CALL ducklake_merge_adjacent_files('ops_catalog')").fetchall()

    owner.execute("CREATE TABLE ops_catalog.unrelated(b INT)")
    owner.execute("ALTER TABLE ops_catalog.unrelated ADD COLUMN c INT")
    _append(stage.scoped(), 2)
    assert _report(owner) == prov.OwnershipReport(0, True, False, False, True, "ducklake_smoke_owner")
    assert _report(stage.scoped()) == prov.OwnershipReport(0, False, False, False, False, "ducklake_smoke_owner")


def test_scoped_login_cannot_create_tables(stage: Any) -> None:
    scoped = stage.scoped()
    before = stage.sql(f"SELECT count(*) FROM pg_tables WHERE schemaname = '{SCHEMA}'")[0][0]
    with pytest.raises(DuckLakeRuntimeError, match="telemetry tables absent"):
        _provision(scoped)
    with pytest.raises(Exception, match="permission denied|Failed to commit"):
        scoped.execute("CREATE TABLE ops_catalog.scoped_new(x INT)")
    assert stage.sql(f"SELECT count(*) FROM pg_tables WHERE schemaname = '{SCHEMA}'")[0][0] == before


def test_create_is_idempotent_and_refuses_drift(stage: Any) -> None:
    assert len(_create(stage.owner).created) == 9
    assert _create(stage.owner).created == ()
    stage.owner.execute("ALTER TABLE ops_catalog.telemetry_sessions ADD COLUMN extra_col VARCHAR")
    with pytest.raises(DuckLakeRuntimeError, match="telemetry_sessions: columns"):
        _create(stage.owner)
    scoped = stage.scoped()
    with pytest.raises(DuckLakeRuntimeError, match="telemetry_sessions: columns"):
        _provision(scoped)
    _append(scoped, 3)
    assert _report(stage.owner) == prov.OwnershipReport(0, True, False, False, True, "ducklake_smoke_owner")


_NEW_COLUMNS = {
    "telemetry_sessions": ("harness_session_key", "checkout_sha", "checkout_base_sha", "checkout_dirty"),
    "telemetry_observations": (
        "usage_final",
        "usage_source",
        "requested_model",
        "reasoning_effort",
        "cache_creation_1h_tokens",
        "cache_creation_5m_tokens",
    ),
}


def _reduced_semantics() -> dict[str, Any]:
    import copy  # noqa: PLC0415

    sem = copy.deepcopy(SEM)
    for table, cols in _NEW_COLUMNS.items():
        for col in cols:
            del sem["ops_tables"][table]["columns"][col]
    return sem


def _append_with_new_columns(con: Any, n: int) -> None:
    con.execute(
        "INSERT INTO ops_catalog.telemetry_sessions (event_id, session_started_at, producer, parser_version, "
        "created_timestamp, tenant_id, project_id, session_id, harness_session_key, checkout_sha, checkout_dirty) "
        "VALUES (?, now(), 'p', 2, now(), 't', 'p', 's', 'session_x', ?, false)",
        [f"n{n}", "a" * 40],
    )


def test_owner_adds_a_missing_nullable_column_and_the_scoped_login_still_writes(stage: Any) -> None:
    reduced = _reduced_semantics()
    created = run_telemetry_txn(stage.owner, lambda c: prov.create_telemetry_tables(c, semantics=reduced)).result
    assert len(created.created) == 9 and created.added == ()

    warm = stage.scoped()
    run_telemetry_txn(warm, lambda c: prov.provision_telemetry_catalog(c, semantics=reduced))
    _append(warm, 1)

    added = _create(stage.owner)
    assert len(added.added) == 10 and added.created == ()

    for scoped in (warm, stage.scoped()):
        assert _provision(scoped).created == ()
        _append_with_new_columns(scoped, id(scoped))
    owner = stage.reconnect_owner()
    rows = owner.execute("SELECT count(*), count(checkout_sha) FROM ops_catalog.telemetry_sessions").fetchone()
    assert tuple(rows) == (3, 2)
    assert _report(owner) == prov.OwnershipReport(0, True, False, False, True, "ducklake_smoke_owner")
    assert _report(stage.scoped()) == prov.OwnershipReport(0, False, False, False, False, "ducklake_smoke_owner")


@pytest.fixture
def bare_stage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _allow_network_for_integration: None) -> Any:
    built = pg_stage.build_stage(tmp_path, monkeypatch, read_all=False, bare=True)
    yield built
    built.close()


def test_provision_telemetry_catalog_on_a_fresh_schema_is_group_owned_and_seeded(bare_stage: Any) -> None:
    out = bare_stage.telemetry_catalog()
    assert len(out["created"]) == 9 and out["counters"] == {"ops_tenants": 0, "ops_projects": 0}
    owners = bare_stage.sql(
        "SELECT DISTINCT pg_get_userbyid(c.relowner) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'ducklake_telemetry' AND c.relkind IN ('r', 'p', 'S', 'v', 'm')"
    )
    assert {r[0] for r in owners} == {"ducklake_telemetry_owner"}
    assert out["ownership"]["schema_owner"] == "ducklake_telemetry_owner" and out["ownership"]["foreign_owned"] == 0
    seeded = bare_stage.duck(OWNER, "ducklake_telemetry")
    assert seeded.execute("SELECT count(*) FROM ops_catalog.ops_entity_counters").fetchone() == (2,)


def test_owner_login_builds_the_stats_index_on_a_group_owned_catalog(stage: Any) -> None:
    stage.telemetry_catalog()
    sql = (Path(pg_stage.ROOT) / "src/lambdas/ducklake_maintenance/catalog_stats_index.sql").read_text(encoding="utf-8")
    conn = psycopg2.connect(**{**stage.params, "dbname": OWNER, "user": OWNER})
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(sql.format(meta_schema="ducklake_telemetry"))
            cur.execute(
                "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE c.relname = 'ducklake_file_column_stats_table_id_column_id_idx' "
                "AND c.relnamespace = to_regnamespace('ducklake_telemetry')"
            )
            assert cur.fetchone() == (True,)
    finally:
        conn.close()
    owners = {r[0] for r in stage.sql("SELECT pg_get_userbyid(relowner) FROM pg_class WHERE relname LIKE '%column_id_idx'")}
    assert owners == {"ducklake_telemetry_owner"}
