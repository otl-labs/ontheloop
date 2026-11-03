"""The two catalog owner verbs on the ducklake_maintenance function (Decision 230, amending Decision 216 clauses 1 and 3).

Two OPERATIONAL, human-invoked actions -- invoked over 443 via `aws lambda invoke` (never a public Function URL, never
reachable from CI or PlatformDev), by PlatformAdmin or by an ADMIN-container agent only under explicit human direction,
presenting each mutation (Decision 230 cl.4). No CC-web container has TCP/5432 egress, so the work runs here as the shared
owner login.

  - action_provision_catalog_owner: create or adopt a catalog's NOLOGIN owner role (the two closed catalogs only), or
    release the smoke schema back to the shared owner login (the smoke switch's rollback).
  - action_provision_telemetry_catalog: ducklake_telemetry only. Owner step, DuckLake initialisation, the four telemetry
    tables, the counter seed, then the ownership report. It never builds the stats index (Decision 214 cl.2) and never
    runs merge or GC.

Both refuse before any connection unless their closed preconditions hold, raise only DuckLakeRuntimeError (never
DuckLakeMaintenanceError, so no breaker datapoint), and emit no metric. Every psycopg2 or duckdb error after the precondition
checks is re-raised naming only its class (and SQLSTATE for psycopg2): a connect error names the host.
"""

from __future__ import annotations

import time
from typing import Any

from src.common import ducklake_runtime as rt
from src.common import ducklake_telemetry_provision as prov
from src.common.ducklake_scd2_schema import load_field_semantics
from src.common.ducklake_telemetry_txn import run_telemetry_txn
from src.lambdas.ducklake_maintenance import catalog_logins as logins
from src.lambdas.ducklake_maintenance._shared import EXTENSION_DIRECTORY

_RELATIONS_TO_OWN_SQL = (
    "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = %s AND c.relkind IN ('r', 'p', 'S', 'v', 'm') "
    "AND c.relowner <> COALESCE((SELECT oid FROM pg_roles WHERE rolname = {target}), 0) "
    "AND NOT (c.relkind = 'S' AND EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = c.oid AND d.deptype IN ('a', 'i')))"
)
_SCHEMA_OWNER_SQL = "SELECT nspowner::regrole::text FROM pg_namespace WHERE nspname = %s"
_MEMBERSHIP_SQL = (
    "SELECT bool_or(m.inherit_option), bool_or(m.set_option) FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.roleid "
    "WHERE r.rolname = %s AND m.member = (SELECT oid FROM pg_roles WHERE rolname = current_user)"
)


def _error(exc: Exception) -> rt.DuckLakeRuntimeError:
    sqlstate = getattr(exc, "pgcode", None)
    suffix = f" sqlstate={sqlstate}" if sqlstate else ""
    return rt.DuckLakeRuntimeError(f"catalog verb failed: {type(exc).__name__}{suffix}")


def _one(cur: Any, sql: str, params: tuple[Any, ...]) -> Any:
    cur.execute(sql, params)
    row = cur.fetchone()
    return row[0] if row else None


def _run_owner_sql(meta_schema: str, mode: str) -> dict[str, Any]:
    """Run the rendered owner section as the shared owner login in one transaction; report counts and booleans only."""
    import psycopg2  # noqa: PLC0415

    owner_role = logins.OWNER_ROLES[meta_schema]
    sql = logins.render_owner_sql(meta_schema, mode)
    target = "'" + owner_role + "'" if mode == "adopt" else "current_user"
    conn = psycopg2.connect(rt.libpq_conninfo(rt.fetch_dsn()))
    try:
        with conn.cursor() as cur:
            owner_before = _one(cur, _SCHEMA_OWNER_SQL, (meta_schema,))
            reowned = int(_one(cur, _RELATIONS_TO_OWN_SQL.format(target=target), (meta_schema,)) or 0)
            cur.execute(sql)
            membership = None
            if mode == "adopt":
                cur.execute(_MEMBERSHIP_SQL, (owner_role,))
                membership = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    adopt = mode == "adopt"
    return {
        "ok": True,
        "meta_schema": meta_schema,
        "owner_role": owner_role,
        "mode": mode,
        "schema_created": adopt and owner_before is None,
        "schema_adopted": adopt and owner_before is not None and owner_before != owner_role,
        "schema_released": not adopt and owner_before == owner_role,
        "relations_reowned": reowned,
        "owner_sql_sha256": logins.template_sha256("owner"),
        "member_inherit": bool(membership[0]) if membership else None,
        "member_set": bool(membership[1]) if membership else None,
    }


def action_provision_catalog_owner(event: dict[str, Any], _con: Any) -> dict[str, Any]:
    """OPERATIONAL, human-invoked: adopt a catalog's owner role, or release the smoke schema to the owner login."""
    meta_schema, mode = event.get("meta_schema"), event.get("mode", "adopt")
    if not isinstance(meta_schema, str) or meta_schema not in logins.OWNER_ROLES:
        raise rt.DuckLakeRuntimeError("provision_catalog_owner: meta_schema must be a catalog in the closed owner role table")
    if event.get("confirm") != meta_schema:
        raise rt.DuckLakeRuntimeError("provision_catalog_owner requires confirm equal to meta_schema")
    if mode not in logins.OWNER_MODES:
        raise rt.DuckLakeRuntimeError("provision_catalog_owner: mode must be adopt or release")
    if mode == "release" and meta_schema != rt.SMOKE_META_SCHEMA:
        raise rt.DuckLakeRuntimeError("provision_catalog_owner: release is refused for any catalog but ducklake_smoke")
    try:
        return _run_owner_sql(meta_schema, mode)
    except rt.DuckLakeRuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001 -- the message can name the host: class and SQLSTATE only
        raise _error(exc) from None


def _check_ownership(report: prov.OwnershipReport) -> dict[str, Any]:
    fields = {
        "foreign_owned": report.foreign_owned,
        "schema_owner": report.schema_owner,
        "schema_owner_held": report.schema_owner_held,
        "schema_owner_is_current_user": report.schema_owner_is_current_user,
        "scoped_create_on_schema": report.scoped_create_on_schema,
        "scoped_create_on_public": report.scoped_create_on_public,
    }
    owner_role = logins.OWNER_ROLES[rt.TELEMETRY_PROD_META_SCHEMA]
    if (
        report.foreign_owned != 0
        or report.schema_owner != owner_role
        or not report.schema_owner_held
        or report.scoped_create_on_schema
        or report.scoped_create_on_public
    ):
        raise rt.DuckLakeRuntimeError(f"provision_telemetry_catalog: single-owner check failed: {fields}")
    return fields


def _provision(meta_schema: str, data_path: str) -> dict[str, Any]:
    t0 = time.perf_counter()
    owner_step = _run_owner_sql(meta_schema, "adopt")
    semantics = load_field_semantics()
    con = rt.open_connection(
        dsn=rt.fetch_dsn(), data_path=data_path, meta_schema=meta_schema, extension_directory=EXTENSION_DIRECTORY
    )
    try:
        _run_owner_sql(meta_schema, "adopt")
        created = run_telemetry_txn(con, lambda c: prov.create_telemetry_tables(c, semantics=semantics)).result
        _run_owner_sql(meta_schema, "adopt")
        seeded = run_telemetry_txn(con, lambda c: prov.provision_telemetry_catalog(c, semantics=semantics)).result
        report = prov.ownership_report(
            con,
            meta_schema=meta_schema,
            scoped_logins=(
                logins.LOGINS["ducklake_telemetry_writer_prod"].name,
                logins.LOGINS["ducklake_telemetry_reader"].name,
            ),
        )
    finally:
        con.close()
    return {
        "ok": True,
        "meta_schema": meta_schema,
        "tables": sorted(created.tables),
        "created": sorted(created.created),
        "added": sorted(created.added),
        "counters": dict(seeded.counters),
        "ownership": _check_ownership(report),
        "owner_sql_sha256": owner_step["owner_sql_sha256"],
        "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 2),
    }


def action_provision_telemetry_catalog(event: dict[str, Any], _con: Any) -> dict[str, Any]:
    """OPERATIONAL, human-invoked: create the production telemetry catalog's owner, tables and counter seed (idempotent)."""
    meta_schema, data_path = event.get("meta_schema"), event.get("data_path")
    if meta_schema != rt.TELEMETRY_PROD_META_SCHEMA:
        raise rt.DuckLakeRuntimeError("provision_telemetry_catalog: meta_schema must be ducklake_telemetry")
    if data_path != rt.TELEMETRY_PROD_DATA_PATH:
        raise rt.DuckLakeRuntimeError("provision_telemetry_catalog: data_path must be the production telemetry data path")
    if event.get("confirm") != meta_schema:
        raise rt.DuckLakeRuntimeError("provision_telemetry_catalog requires confirm equal to meta_schema")
    try:
        return _provision(meta_schema, data_path)
    except rt.DuckLakeRuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001 -- the message can name a host or path: class and SQLSTATE only
        raise _error(exc) from None
