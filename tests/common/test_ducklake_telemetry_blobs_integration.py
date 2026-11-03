"""Real-DuckLake evidence for the claim-check append hook (T2.36 slice 2a-2 plan 4; Decisions 199, 207, 210).

pytest.mark.integration: never gates CI (rec-4071), a local proof. On a file-backed DuckLake catalog provisioned through the
owner create step and provision_telemetry_catalog, the REAL adapter runs with a fake blob store: a blob row commits with NULL
content and the writer-derived content_uri, an identical re-send inserts nothing even when the store reports the object stale
or missing, and a stale or missing object for an unstored row writes nothing from the request. The fixture FAILS when the
extension cannot load (a skip is a FAIL)."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

duckdb = pytest.importorskip("duckdb")

from scripts.ducklake_smoke import telemetry_append_vectors as vec  # noqa: E402
from src.common import ducklake_telemetry_append as adapter  # noqa: E402
from src.common import ducklake_telemetry_blobs as blobs  # noqa: E402
from src.common import ducklake_telemetry_provision as prov  # noqa: E402
from src.common import ducklake_telemetry_registration as reg  # noqa: E402
from src.common.ducklake_scd2_schema import load_field_semantics  # noqa: E402
from src.common.ducklake_telemetry_txn import TelemetryRequestError, run_telemetry_txn  # noqa: E402
from src.lambdas.ducklake_telemetry_writer import telemetry_edge as edge  # noqa: E402
from src.telemetry.request import parse_append_request  # noqa: E402
from tests.fixtures.telemetry_blob_fakes import FakeBlobStore  # noqa: E402

pytestmark = pytest.mark.integration

SEM = load_field_semantics()
PRINCIPAL = reg.CallerPrincipal("aws_account", "acct-under-test", "SomeRole")
NOW = datetime.now(timezone.utc)
STARTED = (NOW - timedelta(seconds=120)).replace(microsecond=123000)
REF = "repo:integration-blobs"
TAG = "blobtag"
PAYLOAD = ("0123456789abcdef" * 4097)[:65537]


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
    tenant_id = reg.register_tenant(con, PRINCIPAL, sleep=lambda _s: None).entity_id
    run(con, tenant_id, vec.request(REF, [("telemetry_sessions", [vec.session_open_row(TAG, STARTED)])]), FakeBlobStore())
    return tenant_id


def blob_row(index: int = 0) -> dict[str, Any]:
    row = vec.transcript_row(TAG, index, STARTED, PAYLOAD)
    row.pop("content")
    return row


def run(con: Any, tenant_id: str, body: dict[str, Any], store: FakeBlobStore) -> adapter.AppendOutcome:
    specs, shapes = edge._specs_and_shapes(SEM)
    return adapter.append_request(
        con,
        parse_append_request(body, shapes),
        tenant_id=tenant_id,
        specs=specs,
        semantics=SEM,
        store=store,
        now=NOW,
        sleep=lambda _s: None,
    )


def stored(con: Any, table: str = "telemetry_transcripts") -> int:
    return int(con.execute(f"SELECT count(*) FROM ops_catalog.{table}").fetchone()[0])


def test_blob_row_commits_with_null_content_and_the_derived_uri(con: Any, tenant: str) -> None:
    store = FakeBlobStore()
    out = run(con, tenant, vec.request(REF, [("telemetry_transcripts", [blob_row()])]), store)
    assert out.tables["telemetry_transcripts"].inserted == 1 and (out.blob_rows, out.blobs_verified) == (1, 1)
    content, uri, session_id, sha, size = con.execute(
        "SELECT content, content_uri, session_id, content_sha256, content_bytes FROM ops_catalog.telemetry_transcripts"
    ).fetchone()
    assert content is None and sha == hashlib.sha256(PAYLOAD.encode()).hexdigest() and size == len(PAYLOAD)
    project_id = reg.lookup_project(con, tenant, REF, semantics=SEM)[0]
    assert uri == f"{tenant}/{project_id}/{session_id}/{sha}" and store.heads == [uri]
    assert uri == blobs.content_uri_for(tenant, project_id, vec.session_ref(TAG), STARTED, sha)


@pytest.mark.parametrize("state", ["stale", "missing"])
def test_a_resend_inserts_nothing_even_when_the_object_is_stale_or_missing(con: Any, tenant: str, state: str) -> None:
    body = vec.request(REF, [("telemetry_transcripts", [blob_row()])])
    run(con, tenant, body, FakeBlobStore())
    again = run(con, tenant, body, FakeBlobStore(default=state))
    assert again.tables["telemetry_transcripts"].inserted == 0 and again.blobs_verified == 0 and stored(con) == 1


@pytest.mark.parametrize("state", ["stale", "missing"])
def test_a_stale_or_missing_blob_for_an_unstored_row_writes_nothing_from_the_request(
    con: Any, tenant: str, state: str
) -> None:
    before = (stored(con), stored(con, "telemetry_observations"))
    body = vec.request(
        REF,
        [("telemetry_transcripts", [blob_row(1)]), ("telemetry_observations", [vec.observation_row(TAG, 7, STARTED)])],
    )
    with pytest.raises(TelemetryRequestError) as caught:
        run(con, tenant, body, FakeBlobStore(default=state))
    assert caught.value.fields == {"table": "telemetry_transcripts", "row": 0, "reason": state}
    assert (stored(con), stored(con, "telemetry_observations")) == before
