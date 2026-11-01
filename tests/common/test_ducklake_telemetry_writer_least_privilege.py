"""Real Postgres 16 proof of the measured writer grants and the read-only reader (Decision 230 cl.3; rec-4297).

pytestmark integration: excluded from --pre and the full tier, run before merge by the implementer (real-engine CI gating is
rec-4071). The shared stage provisions every login through the REAL verbs, so the grants under test are the rendered templates.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

duckdb = pytest.importorskip("duckdb", reason="env: duckdb is not installed in the fast tier")
psycopg2 = pytest.importorskip("psycopg2", reason="env: psycopg2 is not installed in the fast tier")
pytest.importorskip("yaml", reason="env: yaml is not installed in the fast tier")

from src.common import ducklake_runtime as rt  # noqa: E402
from src.common import ducklake_telemetry_provision as prov  # noqa: E402
from src.common import ducklake_telemetry_registration as reg  # noqa: E402
from src.common.ducklake_scd2_schema import load_field_semantics  # noqa: E402
from src.common.ducklake_telemetry_txn import run_telemetry_txn  # noqa: E402
from src.telemetry.append import EventTableSpec, append_events  # noqa: E402
from tests.fixtures import ducklake_pg_stage as pg_stage  # noqa: E402

pytestmark = pytest.mark.integration

SEM = load_field_semantics()
OWNER, SMOKE, TELEMETRY = pg_stage.OWNER, pg_stage.SMOKE, pg_stage.TELEMETRY
PRINCIPAL = reg.CallerPrincipal("aws_account", "acct-under-test", "SomeRole")
_ALIAS = rt.CATALOG_ALIAS
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
_FILES = {"smoke": ("ducklake_telemetry_writer", SMOKE), "prod": ("ducklake_telemetry_writer_prod", TELEMETRY)}


@pytest.fixture(params=["read_all", "no_read_all"])
def stage(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _allow_network_for_integration: None
) -> Any:
    built = pg_stage.build_stage(tmp_path, monkeypatch, read_all=request.param == "read_all")
    yield built
    built.close()


def _reduced() -> dict[str, Any]:
    sem = copy.deepcopy(SEM)
    for table, cols in _NEW_COLUMNS.items():
        for col in cols:
            del sem["ops_tables"][table]["columns"][col]
    return sem


def _spec(sem: dict[str, Any]) -> EventTableSpec:
    return EventTableSpec.from_projection("telemetry_sessions", sem["ops_tables"]["telemetry_sessions"])


def _row(i: int, **extra: Any) -> dict[str, Any]:
    t = datetime(2026, 10, 19, 10, 0, 0, tzinfo=timezone.utc)
    return {
        "event_kind": "open",
        "event_timestamp": t,
        "session_started_at": t,
        "external_ref": f"src#{i}/open",
        "entity_ref": f"sess-{i}",
        "producer": "claude_code",
        "producer_version": "1",
        "parser_version": 4,
        "workflow": "w",
        "execution_attempt": 1,
        **extra,
    }


def _create(stage: Any, schema: str, sem: dict[str, Any]) -> None:
    if schema == SMOKE:
        run_telemetry_txn(stage.owner, lambda c: prov.create_telemetry_tables(c, semantics=sem))
    else:
        run_telemetry_txn(
            stage.duck("ducklake_telemetry_owner_session" if False else OWNER, TELEMETRY),
            lambda c: prov.create_telemetry_tables(c, semantics=sem),
        )
        stage.adopt(TELEMETRY)


@pytest.mark.parametrize("which", sorted(_FILES))
def test_measured_grants_cover_every_writer_verb(stage: Any, which: str) -> None:
    login, schema = _FILES[which]
    reduced = _reduced()
    _create(stage, schema, reduced)
    writer = stage.duck(login, schema)
    run_telemetry_txn(writer, lambda c: prov.provision_telemetry_catalog(c, semantics=reduced))
    tenant = reg.register_tenant(writer, PRINCIPAL)
    project = reg.register_project(writer, PRINCIPAL, "synthetic:least-privilege")
    assert tenant.created is True and project.created is True
    reg.register_project(writer, PRINCIPAL, "synthetic:least-privilege-two")
    ids = {"tenant_id": tenant.entity_id, "project_id": project.entity_id, "catalog": _ALIAS}
    assert append_events(writer, _spec(reduced), [_row(1)], **ids).inserted == 1
    assert append_events(writer, _spec(reduced), [_row(1)], **ids).inserted == 0

    _create(stage, schema, SEM)
    fresh = stage.duck(login, schema)
    for con in (writer, fresh):
        run_telemetry_txn(con, lambda c: prov.provision_telemetry_catalog(c, semantics=SEM))
    assert append_events(fresh, _spec(SEM), [_row(2, checkout_sha="a" * 40)], **ids).inserted == 1
    assert append_events(writer, _spec(SEM), [_row(3, checkout_sha="b" * 40)], **ids).inserted == 1
    owner = stage.duck(OWNER, schema)
    assert owner.execute(f"SELECT count(*), count(checkout_sha) FROM {_ALIAS}.telemetry_sessions").fetchone() == (3, 2)


def test_scoped_login_cannot_drop_a_ducklake_table(stage: Any) -> None:
    for login, schema in _FILES.values():
        _create(stage, schema, SEM)
        writer = stage.duck(login, schema)
        with pytest.raises(Exception, match="permission denied|Failed to commit|Failed"):
            writer.execute(f"DROP TABLE {_ALIAS}.telemetry_sessions")
        owner = stage.duck(OWNER, schema)
        names = {
            r[0] for r in owner.execute(f"SELECT table_name FROM duckdb_tables() WHERE database_name = '{_ALIAS}'").fetchall()
        }
        assert "telemetry_sessions" in names, (login, schema)


def test_scoped_login_cannot_delete_catalog_history(stage: Any) -> None:
    for login, schema in _FILES.values():
        _create(stage, schema, SEM)
        conn = psycopg2.connect(**{**stage.params, "dbname": OWNER, "user": login})
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                for statement in (
                    f"DELETE FROM {schema}.ducklake_snapshot",
                    f"UPDATE {schema}.ducklake_table SET end_snapshot = 1",
                    f"UPDATE {schema}.ducklake_snapshot SET schema_version = 0",
                    f"DELETE FROM {schema}.ducklake_data_file",
                    f"DROP TABLE {schema}.ducklake_snapshot",
                ):
                    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                        cur.execute(statement)
        finally:
            conn.close()
        writer = stage.duck(login, schema)
        with pytest.raises(Exception, match="permission denied|Failed"):
            writer.execute(f"CALL ducklake_expire_snapshots('{_ALIAS}', older_than => now())")
        assert stage.sql(f"SELECT count(*) > 0 FROM {schema}.ducklake_snapshot")[0][0] is True


def test_reader_attaches_read_only_and_cannot_write(stage: Any) -> None:
    stage.telemetry_catalog()
    reader = stage.duck("ducklake_telemetry_reader", TELEMETRY, read_only=True)
    for table in (
        "telemetry_sessions",
        "telemetry_observations",
        "telemetry_transcripts",
        "telemetry_agents",
        "ops_tenants_current",
        "ops_projects_current",
    ):
        assert reader.execute(f"SELECT count(*) FROM {_ALIAS}.{table}").fetchone() == (0,)
    with pytest.raises(Exception, match="read-only|read only|READ_ONLY|permission denied|Failed"):
        reader.execute(f"INSERT INTO {_ALIAS}.ops_entity_counters VALUES ('x', 1)")
    with pytest.raises(Exception, match="read-only|read only|READ_ONLY|permission denied|Failed"):
        reader.execute(f"CREATE TABLE {_ALIAS}.reader_new(a INT)")
    assert stage.login("ducklake_telemetry_reader")["read_only_attach"] == "accepted"
    plain = stage.duck("ducklake_telemetry_reader", TELEMETRY)
    with pytest.raises(Exception, match="permission denied|Failed|read-only"):
        plain.execute(f"CREATE TABLE {_ALIAS}.reader_new(a INT)")


def test_logins_cannot_reach_another_catalog(stage: Any) -> None:
    stage.telemetry_catalog()
    stage.sql("CREATE SCHEMA ducklake_ops")
    stage.sql("CREATE TABLE ducklake_ops.ducklake_snapshot(a int)")
    foreign = {
        "ducklake_telemetry_writer": (TELEMETRY, "ducklake_ops"),
        "ducklake_telemetry_writer_prod": (SMOKE, "ducklake_ops"),
        "ducklake_telemetry_reader": (SMOKE, "ducklake_ops"),
        "ducklake_smoke_maintainer": (TELEMETRY, "ducklake_ops"),
    }
    for login, schemas in foreign.items():
        conn = psycopg2.connect(**{**stage.params, "dbname": OWNER, "user": login})
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                for schema in schemas:
                    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                        cur.execute(f"SELECT count(*) FROM {schema}.ducklake_snapshot")
        finally:
            conn.close()
