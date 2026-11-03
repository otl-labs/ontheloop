"""Real-DuckLake race evidence for telemetry registration (Decision 200 lock arm; tenant-id.yaml, project-id.yaml).

pytest.mark.integration: never gates CI (rec-4071), a local proof. Two cursors of one DuckDB instance share a file-backed
DuckLake catalog aliased ops_catalog, whose tables are created by create_telemetry_tables and then verified and seeded by
provision_telemetry_catalog, so the live gate is not provisioning's first real-catalog run. Both register the same tenant and
the same project_ref concurrently; one COMMIT conflicts on the counter-row UPDATE, run_telemetry_txn retries it, and both
return one id. If ids diverge, Decision 200's reversal condition fires and the model returns to /plan. The fixture FAILS when
the extension cannot load (a skip is a FAIL).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

duckdb = pytest.importorskip("duckdb")

from src.common import ducklake_telemetry_provision as prov  # noqa: E402
from src.common import ducklake_telemetry_registration as reg  # noqa: E402
from src.common.ducklake_scd2_schema import load_field_semantics  # noqa: E402
from src.common.ducklake_telemetry_txn import run_telemetry_txn  # noqa: E402
from src.common.ducklake_writes import is_occ_collision, mint_write_identity  # noqa: E402

pytestmark = pytest.mark.integration

PRINCIPAL = reg.CallerPrincipal("aws_account", "acct-under-test", "SomeRole")
SEM = load_field_semantics()


@pytest.fixture
def con(tmp_path: Path, _allow_network_for_integration: None) -> Any:
    connection = duckdb.connect()
    try:
        connection.execute("INSTALL ducklake; LOAD ducklake")
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"ducklake extension could not load -- a skip is a FAIL (rec-4071): {exc}")
    connection.execute("SET ducklake_default_data_inlining_row_limit=0")
    connection.execute("SET TimeZone='UTC'")
    connection.execute(f"ATTACH 'ducklake:{tmp_path / 'catalog.ducklake'}' AS ops_catalog (DATA_PATH '{tmp_path / 'data'}')")
    reg.clear_telemetry_caches()
    run_telemetry_txn(connection, lambda c: prov.create_telemetry_tables(c, semantics=SEM))
    run_telemetry_txn(connection, lambda c: prov.provision_telemetry_catalog(c, semantics=SEM))
    yield connection
    connection.close()


def _rows(con: Any, table: str) -> int:
    return int(con.execute(f"SELECT count(*) FROM ops_catalog.{table}").fetchone()[0])


def test_create_then_provision_on_a_real_catalog(con: Any) -> None:
    again = run_telemetry_txn(con, lambda c: prov.create_telemetry_tables(c, semantics=SEM)).result
    assert again.created == () and len(again.tables) == 9
    seeded = run_telemetry_txn(con, lambda c: prov.provision_telemetry_catalog(c, semantics=SEM)).result
    assert seeded.created == () and seeded.counters == {"ops_tenants": 0, "ops_projects": 0}


def _race(con: Any, register_a: Any, register_b: Any) -> tuple[Any, Any, int]:
    """A and B both miss and both write; A commits first; B's COMMIT conflicts and run_telemetry_txn retries it."""
    cur_a, cur_b = con.cursor(), con.cursor()
    cur_a.execute("BEGIN TRANSACTION")
    first = register_a(cur_a)
    attempts: list[int] = []

    def body_b(c: Any) -> Any:
        attempts.append(1)
        result = register_b(c)
        if len(attempts) == 1:
            cur_a.execute("COMMIT")
        return result

    outcome = run_telemetry_txn(cur_b, body_b, sleep=lambda _s: None)
    return first, outcome.result, outcome.occ_retries


def test_concurrent_tenant_registration_converges_on_one_id(con: Any) -> None:
    sem = SEM
    ident = mint_write_identity()
    first, second, retries = _race(
        con,
        lambda c: reg.resolve_or_register_tenant(
            c, PRINCIPAL, identity=ident, semantics=sem, candidate_id=reg.new_entity_id()
        ),
        lambda c: reg.resolve_or_register_tenant(
            c, PRINCIPAL, identity=ident, semantics=sem, candidate_id=reg.new_entity_id()
        ),
    )
    assert first[1] is True and retries == 1
    assert second == (first[0], False)
    assert _rows(con, "ops_tenants_current") == 1 and _rows(con, "ops_tenants_history") == 1


def test_concurrent_project_registration_converges_on_one_id(con: Any) -> None:
    tenant = reg.register_tenant(con, PRINCIPAL).entity_id
    sem, ident = SEM, mint_write_identity()
    first, second, retries = _race(
        con,
        lambda c: reg.resolve_or_register_project(
            c,
            tenant,
            "synthetic:race",
            explicit=True,
            attributes={},
            identity=ident,
            semantics=sem,
            candidate_id=reg.new_entity_id(),
        ),
        lambda c: reg.resolve_or_register_project(
            c,
            tenant,
            "synthetic:race",
            explicit=True,
            attributes={},
            identity=ident,
            semantics=sem,
            candidate_id=reg.new_entity_id(),
        ),
    )
    assert first[1] is True and retries == 1 and second == (first[0], False)
    assert _rows(con, "ops_projects_current") == 1 and _rows(con, "ops_projects_history") == 1
    assert reg.register_project(con, PRINCIPAL, "synthetic:race").entity_id == first[0]


def test_the_conflict_is_an_occ_collision_the_helper_recognises(con: Any) -> None:
    ident = mint_write_identity()
    cur_a, cur_b = con.cursor(), con.cursor()
    for cur in (cur_a, cur_b):
        cur.execute("BEGIN TRANSACTION")
        reg.resolve_or_register_tenant(cur, PRINCIPAL, identity=ident, semantics=SEM, candidate_id=reg.new_entity_id())
    cur_a.execute("COMMIT")
    with pytest.raises(Exception) as info:  # noqa: PT011 -- the engine's own conflict class
        cur_b.execute("COMMIT")
    assert is_occ_collision(info.value)
