"""Real-DuckLake evidence for the telemetry append adapter (T2.36 slice 2a-2 plan 3 part 2; Decisions 199, 200, 207).

pytest.mark.integration: never gates CI (rec-4071), a local proof. A file-backed DuckLake catalog aliased ops_catalog is
provisioned through the owner create step and provision_telemetry_catalog, then the REAL adapter runs on it: four tables in
one transaction, first-sight registration composing with the append, atomicity of a stored 409 and of an injected failure,
a re-send inserting nothing, a grain conflict never retried, two cursors converging on one project_id, and the first local
timing of the 2000-row MERGE. The fixture FAILS when the extension cannot load (a skip is a FAIL).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

duckdb = pytest.importorskip("duckdb")

from scripts.ducklake_smoke import telemetry_append_vectors as vec  # noqa: E402
from src.common import ducklake_telemetry_append as adapter  # noqa: E402
from src.common import ducklake_telemetry_provision as prov  # noqa: E402
from src.common import ducklake_telemetry_registration as reg  # noqa: E402
from src.common.ducklake_scd2_schema import load_field_semantics  # noqa: E402
from src.common.ducklake_telemetry_txn import run_telemetry_txn  # noqa: E402
from src.common.ducklake_writes import mint_write_identity  # noqa: E402
from src.lambdas.ducklake_telemetry_writer import telemetry_edge as edge  # noqa: E402
from src.telemetry.gate import GrainConflictError  # noqa: E402
from src.telemetry.request import parse_append_request  # noqa: E402

pytestmark = pytest.mark.integration

SEM = load_field_semantics()
PRINCIPAL = reg.CallerPrincipal("aws_account", "acct-under-test", "SomeRole")
NOW = datetime.now(timezone.utc)
STARTED = (NOW - timedelta(seconds=120)).replace(microsecond=123000)
REAL_REF = "repo:integration"
EVENT_TABLES = tuple(vec.GOLDEN_ROWS)


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
    edge.reset_cache()
    run_telemetry_txn(connection, lambda c: prov.create_telemetry_tables(c, semantics=SEM))
    run_telemetry_txn(connection, lambda c: prov.provision_telemetry_catalog(c, semantics=SEM))
    yield connection
    connection.close()


@pytest.fixture
def tenant(con: Any) -> str:
    return reg.register_tenant(con, PRINCIPAL, sleep=lambda _s: None).entity_id


def count(con: Any, table: str) -> int:
    return int(con.execute(f"SELECT count(*) FROM ops_catalog.{table}").fetchone()[0])


def counts(con: Any) -> dict[str, int]:
    return {t: count(con, t) for t in EVENT_TABLES}


def run(con: Any, tenant_id: str, body: dict[str, Any]) -> adapter.AppendOutcome:
    specs, shapes = edge._specs_and_shapes(SEM)
    request = parse_append_request(body, shapes)
    return adapter.append_request(
        con, request, tenant_id=tenant_id, specs=specs, semantics=SEM, now=NOW, sleep=lambda _s: None
    )


def golden(ref: str = REAL_REF, tag: str = "g1") -> dict[str, Any]:
    return vec.golden_body(ref, tag, STARTED)


def test_first_sight_registers_and_writes_four_tables_in_one_transaction(con: Any, tenant: str) -> None:
    out = run(con, tenant, golden())
    assert out.project_registration == "created" and out.occ_retries == 0
    assert {t: r.inserted for t, r in out.tables.items()} == vec.GOLDEN_ROWS
    assert counts(con) == vec.GOLDEN_ROWS
    assert reg.lookup_project(con, tenant, REAL_REF, semantics=SEM) == (out.project_id, "unregistered")
    assert run(con, tenant, golden()).project_registration == "existing"


def test_a_resend_inserts_nothing(con: Any, tenant: str) -> None:
    run(con, tenant, golden())
    again = run(con, tenant, golden())
    assert all(r.inserted == 0 and r.submitted == vec.GOLDEN_ROWS[t] for t, r in again.tables.items())
    assert counts(con) == vec.GOLDEN_ROWS


def test_a_stored_conflict_in_the_third_batch_leaves_the_first_two_unwritten(con: Any, tenant: str) -> None:
    first = golden()
    run(con, tenant, first)
    before = counts(con)
    fresh = golden(tag="g2")
    agent = fresh["batches"][2]["rows"][0]
    stored_agent = first["batches"][2]["rows"][0]
    agent["external_ref"], agent["entity_ref"] = stored_agent["external_ref"], stored_agent["entity_ref"]
    agent["agent_name"] = "differs-from-the-stored-row"
    with pytest.raises(GrainConflictError) as caught:
        run(con, tenant, fresh)
    assert (caught.value.table, caught.value.row) == ("telemetry_agents", 0)
    assert counts(con) == before


def test_an_injected_failure_in_a_first_sight_request_leaves_nothing(
    con: Any, tenant: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    real, calls = adapter.execute_prepared, []

    def failing(c: Any, prepared: Any, *, catalog: str) -> Any:
        calls.append(prepared.table_spec.table)
        if len(calls) == 3:
            raise RuntimeError("injected third-batch failure")
        return real(c, prepared, catalog=catalog)

    monkeypatch.setattr(adapter, "execute_prepared", failing)
    with pytest.raises(RuntimeError, match="injected"):
        run(con, tenant, golden())
    assert calls == ["telemetry_transcripts", "telemetry_observations", "telemetry_agents"]
    assert counts(con) == dict.fromkeys(EVENT_TABLES, 0)
    assert reg.lookup_project(con, tenant, REAL_REF, semantics=SEM) is None
    assert reg.cached_project_id(tenant, REAL_REF) is None


def test_a_grain_conflict_is_not_retried(con: Any, tenant: str, monkeypatch: pytest.MonkeyPatch) -> None:
    run(con, tenant, golden())
    real, calls = adapter.execute_prepared, []

    def counting(c: Any, prepared: Any, *, catalog: str) -> Any:
        calls.append(prepared.table_spec.table)
        return real(c, prepared, catalog=catalog)

    monkeypatch.setattr(adapter, "execute_prepared", counting)
    with pytest.raises(GrainConflictError):
        run(con, tenant, vec.conflicting_body(golden()))
    assert calls == ["telemetry_transcripts", "telemetry_observations"]


def test_two_cursors_appending_first_sight_to_one_project_ref_end_with_one_project_id(
    con: Any, tenant: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cur_a, cur_b = con.cursor(), con.cursor()
    cur_b.execute("SET TimeZone='UTC'")
    cur_a.execute("BEGIN TRANSACTION")
    first_id, created = reg.resolve_or_register_project(
        cur_a,
        tenant,
        REAL_REF,
        explicit=False,
        attributes={},
        identity=mint_write_identity(),
        semantics=SEM,
        candidate_id=reg.new_entity_id(),
    )
    assert created is True
    real, committed = adapter.execute_prepared, []

    def commit_a_first(c: Any, prepared: Any, *, catalog: str) -> Any:
        if not committed:
            committed.append(1)
            cur_a.execute("COMMIT")
        return real(c, prepared, catalog=catalog)

    monkeypatch.setattr(adapter, "execute_prepared", commit_a_first)
    out = run(cur_b, tenant, golden())
    assert out.project_id == first_id and out.project_registration == "existing" and out.occ_retries == 1
    assert (
        int(
            con.execute("SELECT count(*) FROM ops_catalog.ops_projects_current WHERE project_ref = ?", [REAL_REF]).fetchone()[
                0
            ]
        )
        == 1
    )
    assert counts(con) == vec.GOLDEN_ROWS


def test_a_2000_row_observations_batch_commits_and_reports_its_time(con: Any, tenant: str) -> None:
    out = run(con, tenant, vec.row_cap_body(REAL_REF, "rowcap", STARTED))
    assert out.tables["telemetry_observations"].inserted == vec.MAX_ROWS_PER_TABLE
    assert count(con, "telemetry_observations") == vec.MAX_ROWS_PER_TABLE
    print(f"local_rowcap_commit_ms={int(out.commit_ms)}")
