"""Real Postgres 16 proofs for the restore rehearsal (rec-5023, Decision 251), running the shipped code.

pytestmark integration: excluded from --pre and the full tier; a missing stage DSN FAILS, never skips (rec-4071).
(1) A whole-database custom dump of two schemas restores into a new empty database as a new non-superuser role through
restore_ops_schema. (2) A local DuckLake whose data_path is rewritten with rewrite_data_path_sql's exact (sql, params)
reads every table at the new path and writes only there."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

psycopg2 = pytest.importorskip("psycopg2", reason="env: psycopg2 is not installed in the fast tier")
duckdb = pytest.importorskip("duckdb", reason="env: duckdb is not installed in the fast tier")
pytest.importorskip("yaml", reason="env: yaml is not installed in the fast tier")

from src.common import catalog_dr  # noqa: E402
from src.common import ducklake_runtime as rt  # noqa: E402
from src.lambdas.ducklake_maintenance import restore_rehearsal_actions as rra  # noqa: E402
from src.lambdas.ducklake_maintenance import restore_rehearsal_file_catalog as file_catalog  # noqa: E402
from tests.fixtures import ducklake_pg_stage as pg_stage  # noqa: E402

pytestmark = pytest.mark.integration

PG_RESTORE = shutil.which("pg_restore") or "/usr/bin/pg_restore"
PG_DUMP = shutil.which("pg_dump") or "/usr/bin/pg_dump"
TARGET_ROLE = "restore_rehearsal_pgtest"
TARGET_DB = "restore_rehearsal_pgtest"
SRC_DB = "rr_pgtest_source"
PROD_ONLY_ROLE = "rr_pgtest_prod_only"
ROLE_PW = "rr-test-only"  # pragma: allowlist secret


def _exec(params: dict[str, str], db: str, sql: str) -> list[tuple[Any, ...]]:
    return pg_stage.pg(params, None, db, sql)


def _build_source(params: dict[str, str]) -> None:
    _exec(params, "postgres", f"DROP DATABASE IF EXISTS {TARGET_DB} WITH (FORCE)")
    _exec(params, "postgres", f"DROP DATABASE IF EXISTS {SRC_DB} WITH (FORCE)")
    for role in (TARGET_ROLE, PROD_ONLY_ROLE):
        _exec(params, "postgres", f"DROP ROLE IF EXISTS {role}")
    _exec(params, "postgres", f"CREATE ROLE {PROD_ONLY_ROLE} NOLOGIN")
    _exec(params, "postgres", f"CREATE DATABASE {SRC_DB}")
    path_tables = "".join(f"CREATE TABLE ducklake_ops.{t} (id bigint, path_is_relative boolean);" for t in rra._PATH_TABLES)
    _exec(
        params,
        SRC_DB,
        "CREATE SCHEMA ducklake_ops; CREATE SCHEMA ducklake_telemetry;"
        "CREATE TABLE ducklake_ops.ducklake_snapshot (snapshot_id bigint);"
        "INSERT INTO ducklake_ops.ducklake_snapshot VALUES (1), (2), (7);"
        f"{path_tables}"
        "INSERT INTO ducklake_ops.ducklake_data_file VALUES (1, true);"
        "CREATE TABLE ducklake_telemetry.secret_events (body text);"
        f"GRANT USAGE ON SCHEMA ducklake_ops TO {PROD_ONLY_ROLE};"
        f"GRANT SELECT ON ALL TABLES IN SCHEMA ducklake_ops TO {PROD_ONLY_ROLE};",
    )


def test_restore_ops_schema_restores_only_ducklake_ops_as_a_new_role(tmp_path: Path) -> None:
    params = pg_stage.admin_params()
    host = params.get("host", "127.0.0.1")
    _build_source(params)
    dump = str(tmp_path / "whole.dump")
    subprocess.run(
        [PG_DUMP, "--format=custom", "--file", dump, "-h", host, "-U", params.get("user", "postgres"), SRC_DB],
        check=True,
        capture_output=True,
    )
    _exec(params, "postgres", f"CREATE ROLE {TARGET_ROLE} LOGIN PASSWORD '{ROLE_PW}'")
    _exec(params, "postgres", f"CREATE DATABASE {TARGET_DB} OWNER {TARGET_ROLE}")
    dsn = {"host": host, "dbname": TARGET_DB, "username": TARGET_ROLE, "password": ROLE_PW, "sslmode": "disable"}

    rra.restore_ops_schema(dump, dsn, pg_restore_path=PG_RESTORE)

    schemas = {r[0] for r in _exec(params, TARGET_DB, "SELECT nspname FROM pg_namespace WHERE nspname LIKE 'ducklake_%'")}
    assert schemas == {"ducklake_ops"}
    measured = rra._measure(dsn)
    assert measured["snapshot_id"] == 7
    assert measured["foreign_owned"] == 0 and measured["foreign_acl"] == 0 and measured["foreign_acl_grantees"] == []
    assert measured["absolute_paths"] == 0


def test_restore_without_create_schema_exits_1(tmp_path: Path) -> None:
    params = pg_stage.admin_params()
    host = params.get("host", "127.0.0.1")
    _build_source(params)
    dump = str(tmp_path / "whole.dump")
    subprocess.run(
        [PG_DUMP, "--format=custom", "--file", dump, "-h", host, "-U", params.get("user", "postgres"), SRC_DB],
        check=True,
        capture_output=True,
    )
    _exec(params, "postgres", f"CREATE ROLE {TARGET_ROLE} LOGIN PASSWORD '{ROLE_PW}'")
    _exec(params, "postgres", f"CREATE DATABASE {TARGET_DB} OWNER {TARGET_ROLE}")
    dsn = {"host": host, "dbname": TARGET_DB, "username": TARGET_ROLE, "password": ROLE_PW, "sslmode": "disable"}
    with pytest.raises(catalog_dr.CatalogDrError) as info:
        catalog_dr.run_pg_restore(dump, dsn, pg_restore_path=PG_RESTORE, clean=False, no_acl=True, schema="ducklake_ops")
    assert info.value.returncode == 1


def test_local_rewrite_reads_every_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _allow_network_for_integration: None
) -> None:
    stage = pg_stage.build_stage(tmp_path, monkeypatch, read_all=True, bare=True)
    try:
        schema = pg_stage.SMOKE
        stage.sql(f"CREATE SCHEMA {schema}")
        alias = rt.CATALOG_ALIAS
        con = stage.duck(pg_stage.OWNER, schema)
        for name in ("a", "b"):
            con.execute(f"CREATE TABLE {alias}.{name} (id BIGINT, v VARCHAR)")
            con.execute(f"INSERT INTO {alias}.{name} SELECT i, 'v' || i FROM range(25) t(i)")
        digest_sql = "SELECT count(*), sum(hash(t))::VARCHAR FROM {alias}.{name} t"
        before = {n: con.execute(digest_sql.format(alias=alias, name=n)).fetchone() for n in ("a", "b")}
        con.close()

        old = Path(stage.data_paths[schema].rstrip("/"))
        new = tmp_path / "rewritten-data"
        shutil.copytree(old, new)
        new_path = f"{new}/"
        sql, params = rra.rewrite_data_path_sql(schema, new_path)
        conn = psycopg2.connect(rt.libpq_conninfo(stage.dsn(pg_stage.OWNER)))
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(sql, params)
            assert cur.rowcount == 1
        conn.close()
        shutil.rmtree(old)

        reopened = rt.open_connection(dsn=stage.dsn(pg_stage.OWNER), data_path=new_path, meta_schema=schema)
        try:
            after = {n: reopened.execute(digest_sql.format(alias=alias, name=n)).fetchone() for n in ("a", "b")}
            assert after == before
            reopened.execute(f"INSERT INTO {alias}.a SELECT 1000 + i, 'w' FROM range(5) t(i)")
            snapshot = int(reopened.execute(f"SELECT max(snapshot_id) FROM {alias}.snapshots()").fetchone()[0])
            files = reopened.execute(
                f"SELECT data_file, delete_file FROM ducklake_list_files('{alias}', ?, schema => ?, "
                f"snapshot_version => {snapshot})",
                ["b", "main"],
            ).fetchall()
            assert files and all(f[0].startswith(new_path) for f in files)
        finally:
            reopened.close()
        assert not old.exists()
        assert sum(1 for p in new.rglob("*.parquet")) > 2

        monkeypatch.setattr(file_catalog, "EXTENSION_DIRECTORY", None)
        monkeypatch.setattr(rra, "EXTENSION_DIRECTORY", None)
        monkeypatch.setattr(rra, "META_SCHEMA", schema)
        copied = file_catalog.run_duckdb_copy(
            stage.dsn(pg_stage.OWNER),
            meta_schema=schema,
            data_path=new_path,
            tables=[("main", "a"), ("main", "b")],
            snapshot_id=snapshot,
        )
        assert copied["catalog_rows"] > 0 and copied["file_bytes"] > 0
        assert all(r["match_restored"] for r in copied["tables"]), copied

        checks, missing = rra._table_checks(
            rt.open_connection(dsn=stage.dsn(pg_stage.OWNER), data_path=new_path, meta_schema=schema, read_only=True),
            [("main", "a"), ("main", "b")],
            snapshot,
            None,
        )
        assert [c["read_ok"] for c in checks] == [True, True] and missing == 0

        client = MagicMock()
        client.list_objects_v2.return_value = {"KeyCount": 1}
        with patch.dict("sys.modules", {"boto3": MagicMock(client=MagicMock(return_value=client))}):
            probe = rra._step_write_probe({"data_path": new_path}, stage.dsn(pg_stage.OWNER), new_path)
        assert probe["write_probe_in_scratch"] is True and probe["write_probe_key"].startswith("rehearsal_probe/t/")
        assert probe["catalog_format_version"]
    finally:
        stage.close()
