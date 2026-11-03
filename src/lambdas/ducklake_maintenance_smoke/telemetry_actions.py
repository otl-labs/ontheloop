"""create_telemetry_tables -- the owner-login telemetry DDL verb on the smoke catalog (Decision 216, amends Decision 143).

The telemetry writer's scoped catalog login is DML-only (Decision 213 cl.3) and DuckLake's CREATE TABLE also creates a
physical Postgres table, so the telemetry tables are created here, under this function's owner login, by the governed
deploy pipeline's maintenance-smoke stage, before the telemetry gate. The verb is create-or-add-nullable-column (no
drop, no rename or retype, no row write) (Decision 222, amending Decision 216 clause 2) and reads nothing from the
event: its meta schema is the function's env pin, and it refuses in code unless that is the smoke schema, so this CI-
reachable function cannot create tables in another catalog (its Postgres login is still the all-catalog owner; a
smoke-only migrator login is ducklake_smoke_maintainer, Decision 230). It proves the single-owner invariant before and
after the DDL, and every failure after the env-pin check is a typed DuckLakeRuntimeError naming only the exception
class, so the Function URL never answers a bare 502 and a catalog host or path never reaches the public CI log."""

from __future__ import annotations

import os
import time
import traceback
from typing import Any

from src.common import ducklake_runtime as rt
from src.common import ducklake_telemetry_provision as prov
from src.common.ducklake_scd2_schema import load_field_semantics
from src.common.ducklake_telemetry_txn import run_telemetry_txn


def _check(report: prov.OwnershipReport, stage: str) -> dict[str, Any]:
    fields = {
        "foreign_owned": report.foreign_owned,
        "schema_owner_is_current_user": report.schema_owner_is_current_user,
        "schema_owner_held": report.schema_owner_held,
        "schema_owner": report.schema_owner,
        "scoped_create_on_schema": report.scoped_create_on_schema,
        "scoped_create_on_public": report.scoped_create_on_public,
    }
    clean = (
        report.foreign_owned == 0
        and report.schema_owner_held
        and not report.scoped_create_on_schema
        and not report.scoped_create_on_public
    )
    if not clean:
        raise rt.DuckLakeRuntimeError(f"single-owner check failed ({stage}-DDL): {fields}")
    return fields


def action_create_telemetry_tables(_event: dict[str, Any], con: Any) -> dict[str, Any]:
    t0 = time.perf_counter()
    meta_schema = os.environ.get("DUCKLAKE_META_SCHEMA", rt.SMOKE_META_SCHEMA)
    if meta_schema != rt.SMOKE_META_SCHEMA:
        raise rt.DuckLakeRuntimeError("create_telemetry_tables is pinned to the smoke metadata schema and refuses any other")
    try:
        _check(prov.ownership_report(con, meta_schema=meta_schema), "pre")
        semantics = load_field_semantics()
        created = run_telemetry_txn(con, lambda c: prov.create_telemetry_tables(c, semantics=semantics)).result
        ownership = _check(prov.ownership_report(con, meta_schema=meta_schema), "post")
    except rt.DuckLakeRuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001 -- the traceback stays in CloudWatch; the response names the class only
        traceback.print_exc()
        raise rt.DuckLakeRuntimeError(f"create_telemetry_tables failed: {type(exc).__name__}") from None
    return {
        "ok": True,
        "tables": sorted(created.tables),
        "created": sorted(created.created),
        "added": sorted(created.added),
        "ownership": ownership,
        "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 2),
    }
