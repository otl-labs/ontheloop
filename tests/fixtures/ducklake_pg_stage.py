"""The Neon-shaped local Postgres 16 stage shared by the real-engine DuckLake tests (Decision 216, Decision 230;
rec-4294, rec-4297).

Moved out of tests/common/test_ducklake_telemetry_catalog_ownership.py so the owner-role, least-privilege and
ownership modules share one fixture. It refuses any non-local host, FAILS (never skips, rec-4071) when
DUCKLAKE_TEST_PG_ADMIN_DSN is unset, an extension cannot load, or duckdb differs from the Decision 99 pin. Per build
it drops and recreates the database ducklake_ops, every role the plan names, a NOLOGIN neon_superuser-like role
(CREATEDB CREATEROLE BYPASSRLS, with and without pg_read_all_data and pg_write_all_data) and the NON-superuser owner
login ducklake_ops (CREATEDB CREATEROLE, member of that role), which owns the database.

Two modes. The default builds the converged catalogs: both owner roles adopted, DuckLake initialised in ducklake_smoke
and ducklake_telemetry, and the four closed logins provisioned through the REAL verbs. `bare` builds only the owner
login and the database, so a test creates every schema, role and login through the real verbs in the order the
sittings run them. The verbs run unchanged: only rt.fetch_dsn, the two data paths and the Lambda-only extension
directory and S3 credentials are pointed at the stage."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.common import ducklake_runtime as rt
from src.lambdas.ducklake_maintenance import catalog_actions as ca
from src.lambdas.ducklake_maintenance import catalog_logins as cl
from src.lambdas.ducklake_maintenance import login_actions as la

ROOT = Path(__file__).resolve().parents[2]
OWNER = "ducklake_ops"
SMOKE = rt.SMOKE_META_SCHEMA
TELEMETRY = rt.TELEMETRY_PROD_META_SCHEMA
PASSWORD = "test-only"  # pragma: allowlist secret
_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}
_ALL_ROLES = (
    *cl.LOGINS,
    *cl.OWNER_ROLES.values(),
    OWNER,
    "neon_superuser",
)


def admin_params() -> dict[str, str]:
    import psycopg2  # noqa: PLC0415

    dsn = os.environ.get("DUCKLAKE_TEST_PG_ADMIN_DSN", "")
    if not dsn:
        pytest.fail("DUCKLAKE_TEST_PG_ADMIN_DSN is unset -- a skip is a FAIL (rec-4071)")
    params = psycopg2.extensions.parse_dsn(dsn)
    host = params.get("host", "")
    if host not in _LOCAL_HOSTS and not host.startswith("/"):
        pytest.fail("DUCKLAKE_TEST_PG_ADMIN_DSN must point at a local server; this test never touches a remote catalog")
    return params


def pg(params: dict[str, str], user: str | None, db: str, sql: str) -> list[tuple[Any, ...]]:
    import psycopg2  # noqa: PLC0415

    conn = psycopg2.connect(**{**params, "dbname": db, **({"user": user} if user else {})})
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall() if cur.description else []
    finally:
        conn.close()


def build_stage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, read_all: bool, bare: bool = False) -> SimpleNamespace:
    import duckdb  # noqa: PLC0415
    import yaml  # noqa: PLC0415

    params = admin_params()
    pinned = yaml.safe_load((ROOT / "config" / "lambda" / "ducklake" / "version.yaml").read_text(encoding="utf-8"))
    if duckdb.__version__ != str(pinned["duckdb_version"]):
        pytest.fail(f"duckdb {duckdb.__version__} differs from the pinned {pinned['duckdb_version']} (Decision 99)")
    host, port = params.get("host", "127.0.0.1"), params.get("port", "5432")

    def admin(sql: str, user: str | None = None, db: str = "postgres") -> list[tuple[Any, ...]]:
        return pg(params, user, db, sql)

    admin(f"DROP DATABASE IF EXISTS {OWNER} WITH (FORCE)")
    for role in _ALL_ROLES:
        admin(f"DROP ROLE IF EXISTS {role}")
    admin("CREATE ROLE neon_superuser NOLOGIN CREATEDB CREATEROLE BYPASSRLS")
    if read_all:
        admin("GRANT pg_read_all_data, pg_write_all_data TO neon_superuser")
    admin(f"CREATE ROLE {OWNER} LOGIN CREATEDB CREATEROLE IN ROLE neon_superuser PASSWORD '{PASSWORD}'")
    admin(f"CREATE DATABASE {OWNER} OWNER {OWNER}")

    data = {SMOKE: tmp_path / "smoke-data", TELEMETRY: tmp_path / "telemetry-data"}
    data_paths = {schema: f"{path}/" for schema, path in data.items()}
    monkeypatch.setenv("PGPORT", str(port))
    monkeypatch.setattr(rt, "TELEMETRY_PROD_DATA_PATH", data_paths[TELEMETRY])
    monkeypatch.setattr(cl, "DATA_PATHS", {SMOKE: data_paths[SMOKE], TELEMETRY: data_paths[TELEMETRY]})
    monkeypatch.setattr(ca, "EXTENSION_DIRECTORY", None)
    monkeypatch.setattr(la, "EXTENSION_DIRECTORY", None)
    monkeypatch.setattr(rt.ducklake_spike, "_set_s3_credentials", lambda con, profile=None: None)

    def dsn(user: str) -> dict[str, str]:
        return {"host": host, "dbname": OWNER, "username": user, "password": PASSWORD, "sslmode": "require"}

    by_secret = {login.parameter_name: dsn(login.name) for login in cl.LOGINS.values()}
    monkeypatch.setattr(
        rt, "fetch_dsn", lambda parameter_name=rt.DSN_PARAMETER_NAME, **_: by_secret.get(parameter_name) or dsn(OWNER)
    )

    opened: list[Any] = []

    def duck(user: str, schema: str, *, read_only: bool = False) -> Any:
        try:
            con = rt.open_connection(dsn=dsn(user), data_path=data_paths[schema], meta_schema=schema, read_only=read_only)
        except Exception as exc:  # noqa: BLE001
            pytest.fail(
                f"stage connection as {user} on {schema} failed -- a skip is a FAIL (rec-4071): {type(exc).__name__}: {exc}"
            )
        opened.append(con)
        return con

    def adopt(schema: str, mode: str = "adopt") -> dict[str, Any]:
        return ca.action_provision_catalog_owner({"meta_schema": schema, "confirm": schema, "mode": mode}, None)

    def login(name: str) -> dict[str, Any]:
        return la.action_provision_telemetry_login({"confirm": name}, None)

    def telemetry_catalog() -> dict[str, Any]:
        return ca.action_provision_telemetry_catalog(
            {"meta_schema": TELEMETRY, "confirm": TELEMETRY, "data_path": rt.TELEMETRY_PROD_DATA_PATH}, None
        )

    stage = SimpleNamespace(
        params=params,
        tmp=tmp_path,
        data_paths=data_paths,
        dsn=dsn,
        duck=duck,
        adopt=adopt,
        login=login,
        telemetry_catalog=telemetry_catalog,
        sql=lambda stmt, user=OWNER: admin(stmt, user=user, db=OWNER),
        admin=admin,
        responses={},
    )
    if bare:
        yield_stage = stage
    else:
        stage.responses["adopt_smoke"] = adopt(SMOKE)
        stage.responses["maintainer"] = login("ducklake_smoke_maintainer")
        smoke_owner = duck("ducklake_smoke_maintainer", SMOKE)
        smoke_owner.execute(f"CREATE TABLE {rt.CATALOG_ALIAS}.warm(a INT)")
        stage.responses["adopt_telemetry"] = adopt(TELEMETRY)
        duck(OWNER, TELEMETRY).execute(f"CREATE TABLE {rt.CATALOG_ALIAS}.warm(a INT)")
        adopt(TELEMETRY)
        for name in ("ducklake_telemetry_writer", "ducklake_telemetry_writer_prod", "ducklake_telemetry_reader"):
            stage.responses[name] = login(name)
        stage.owner = smoke_owner
        stage.scoped = lambda: duck("ducklake_telemetry_writer", SMOKE)
        stage.shared_owner = lambda schema=SMOKE: duck(OWNER, schema)
        stage.reconnect_owner = lambda: duck("ducklake_smoke_maintainer", SMOKE)
        yield_stage = stage
    stage.close = lambda: [con.close() for con in opened]
    return yield_stage
