"""Mirror test for src/common/ducklake_telemetry_blobs.py: the begin_blobs verb core and the append hook's pure parts
(fake store, part 2's fakes of part 1's primitives; fast tier)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from src.common import ducklake_telemetry_blobs as core
from src.common.ducklake_scd2_schema import load_field_semantics
from src.common.ducklake_telemetry_blobstore import BlobIntegrityError, BlobStoreUnavailableError
from src.common.ducklake_telemetry_txn import TelemetryRequestError
from src.telemetry.append import prepare_batch
from src.telemetry.blobs import parse_begin_blobs_request
from tests.fixtures.telemetry_append_fakes import RESOLVED, TENANT, AdapterKit, parse, specs, transcript_row
from tests.fixtures.telemetry_blob_fakes import FakeBlobStore

SEMANTICS = load_field_semantics()
STARTED = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
SESSION_AT = "2026-01-01T12:00:00.000Z"
REF = "repo:example"
THRESHOLD, CAP = 65536, 8388608


def item(sha: str = "a" * 64, size: int = 70000, session: str = "sess") -> dict[str, Any]:
    return {"session_ref": session, "session_started_at": SESSION_AT, "content_sha256": sha, "content_bytes": size}


def run(items: list[dict[str, Any]], store: FakeBlobStore, ref: str = REF) -> core.BeginBlobsOutcome:
    body = {"action": "begin_blobs", "project_ref": ref, "items": items}
    request = parse_begin_blobs_request(body, threshold=THRESHOLD, cap=CAP)
    return core.begin_blobs(object(), request, tenant_id=TENANT, store=store, semantics=SEMANTICS, now=STARTED)


def uri(sha: str, session: str = "sess", project: str = RESOLVED) -> str:
    return core.content_uri_for(TENANT, project, session, STARTED, sha)


def test_begin_blobs_registers_first_sight_and_keys_use_the_committed_id(monkeypatch: pytest.MonkeyPatch) -> None:
    kit = AdapterKit().install(monkeypatch)
    store = FakeBlobStore(default="missing")
    out = run([item()], store)
    assert (out.project_id, out.project_registration) == (RESOLVED, "created")
    assert kit.events == ["lookup", "BEGIN", "resolve", "COMMIT"] and kit.cache_at_commit == [{}]
    assert kit.cache == {(TENANT, REF): RESOLVED}
    assert store.heads == [store.grants[0][0]] == [uri("a" * 64)] and store.grants[0][0].startswith(f"{TENANT}/{RESOLVED}/")
    kit.events.clear()
    again = run([item()], FakeBlobStore())
    assert again.project_registration == "existing" and kit.events == []
    hit = AdapterKit(lookup_result=(RESOLVED, "registered")).install(monkeypatch)
    assert run([item()], FakeBlobStore()).project_registration == "existing" and hit.events == ["lookup"]


def test_begin_blobs_reserved_ref_is_403_before_any_head(monkeypatch: pytest.MonkeyPatch) -> None:
    kit = AdapterKit().install(monkeypatch)
    store = FakeBlobStore()
    for ref in ("drill:fresh", "synthetic:deploy-smoke-x"):
        with pytest.raises(TelemetryRequestError) as caught:
            run([item()], store, ref)
        assert (caught.value.status, caught.value.error_type) == (403, "project_unregistered")
    assert store.heads == [] and kit.events == ["lookup", "lookup"]
    registered = AdapterKit(lookup_result=(RESOLVED, "registered")).install(monkeypatch)
    assert run([item()], store, "drill:registered").project_id == RESOLVED and registered.events == ["lookup"]
    with pytest.raises(TelemetryRequestError) as bad:
        run([item(session="x" * 5000)], store)
    assert bad.value.status == 400


def test_begin_blobs_dedupes_and_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    AdapterKit(lookup_result=(RESOLVED, "registered")).install(monkeypatch)
    store = FakeBlobStore(default="missing")
    out = run([item(), item(), item("b" * 64, 90000), item("a" * 64, session="other")], store)
    assert [(i.existed, i.grant is not None, i.duplicate_of) for i in out.items] == [
        (False, True, None),
        (False, False, 0),
        (False, True, None),
        (False, True, None),
    ]
    assert len(store.heads) == 3 and len(store.grants) == 3 and len(set(store.heads)) == 3
    assert {k: v for k, v in out.counts.items() if k != "head_ms"} == {
        "items": 4,
        "distinct_keys": 3,
        "existed": 0,
        "granted": 3,
        "stale": 0,
        "declared_bytes_total": 70000 + 90000 + 70000,
        "declared_bytes_max": 90000,
    }
    assert isinstance(out.counts["head_ms"], int)
    present = run([item()], FakeBlobStore())
    assert present.items[0].existed is True and present.items[0].grant is None and present.counts["existed"] == 1
    failing = FakeBlobStore()
    failing.error = BlobStoreUnavailableError("x")
    with pytest.raises(TelemetryRequestError) as caught:
        run([item()], failing)
    assert (caught.value.status, caught.value.error_type) == (503, "blob_store_unavailable")


def test_only_missing_or_stale_objects_are_regranted(monkeypatch: pytest.MonkeyPatch) -> None:
    AdapterKit(lookup_result=(RESOLVED, "registered")).install(monkeypatch)
    shas = {"verified": "1" * 64, "stale": "2" * 64, "missing": "4" * 64}
    store = FakeBlobStore({uri(sha): state for state, sha in shas.items()})
    out = run([item(sha) for sha in shas.values()], store)
    assert [i.existed for i in out.items] == [True, False, False] and out.items[0].grant is None
    assert (out.counts["existed"], out.counts["granted"], out.counts["stale"]) == (1, 2, 1) and "mismatched" not in out.counts
    assert [g[0] for g in store.grants] == [uri(shas["stale"]), uri(shas["missing"])]


def test_begin_blobs_integrity_mismatch_is_500_and_grants_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    AdapterKit(lookup_result=(RESOLVED, "registered")).install(monkeypatch)
    store = FakeBlobStore({uri("3" * 64): "mismatch", uri("5" * 64): "size_mismatch", uri("4" * 64): "missing"})
    with pytest.raises(BlobIntegrityError):
        run([item("4" * 64), item("5" * 64), item("3" * 64)], store)
    assert store.grants == []


def test_begin_blobs_size_mismatch_is_422_and_grants_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    AdapterKit(lookup_result=(RESOLVED, "registered")).install(monkeypatch)
    store = FakeBlobStore({uri("5" * 64): "size_mismatch", uri("6" * 64): "size_mismatch", uri("4" * 64): "missing"})
    with pytest.raises(TelemetryRequestError) as caught:
        run([item("4" * 64), item("5" * 64), item("6" * 64)], store)
    assert (caught.value.status, caught.value.error_type) == (422, "blob_not_verified")
    assert caught.value.fields == {"row": 1, "reason": "size_mismatch"} and store.grants == []


def test_begin_blobs_same_uri_different_size_is_422(monkeypatch: pytest.MonkeyPatch) -> None:
    kit = AdapterKit(lookup_result=None).install(monkeypatch)
    store = FakeBlobStore()
    with pytest.raises(TelemetryRequestError) as caught:
        run([item(), item("b" * 64), item(size=70001)], store)
    assert (caught.value.status, caught.value.error_type) == (422, "blob_not_verified")
    assert caught.value.fields == {"row": 2, "reason": "size_mismatch"}
    assert store.heads == [] and store.grants == [] and "BEGIN" not in kit.events
    AdapterKit(lookup_result=(RESOLVED, "registered")).install(monkeypatch)
    assert run([item(), item(session="other", size=70001)], FakeBlobStore()).counts["distinct_keys"] == 2


def decoded(**over: Any) -> dict[str, Any]:
    """One transcript row as the grammar hands it to the adapter (timestamps decoded)."""
    return parse(REF, ("telemetry_transcripts", [transcript_row(**over)])).batches[0][1][0]


def test_content_uri_session_segment_equals_the_kernel_session_id() -> None:
    row = decoded()
    prepared = prepare_batch(specs()["telemetry_transcripts"], [row], tenant_id=TENANT, project_id=RESOLVED, now=STARTED)
    built = core.content_uri_for(TENANT, RESOLVED, row["session_ref"], row["session_started_at"], row["content_sha256"])
    assert built.split("/") == [TENANT, RESOLVED, prepared.rows[0]["session_id"], row["content_sha256"]]
    assert (
        core.session_prefix(TENANT, RESOLVED, row["session_ref"], row["session_started_at"]) == built.rsplit("/", 1)[0] + "/"
    )


def blob_rows(count: int = 2) -> list[dict[str, Any]]:
    rows = [
        decoded(external_ref=f"t#{i}", entity_ref=f"t#{i}", content_sha256=f"{i:064x}", content_bytes=70000)
        for i in range(count)
    ]
    return [{k: v for k, v in r.items() if k != "content"} for r in rows]


def test_attach_blob_uris_is_pure_and_only_for_blob_rows() -> None:
    plain = decoded()
    rows = [plain, *blob_rows(1)]
    snapshot = [dict(r) for r in rows]
    out = core.attach_blob_uris(rows, tenant_id=TENANT, project_id=RESOLVED, threshold=THRESHOLD, cap=CAP)
    assert rows == snapshot and "content_uri" not in out[0] and out[0] == plain
    assert out[1]["content_uri"] == uri(f"{0:064x}", "sess") and core.blob_row_indexes(rows, THRESHOLD, CAP) == [1]
    with pytest.raises(TelemetryRequestError) as caught:
        core.attach_blob_uris(
            [{**blob_rows(1)[0], "session_ref": ""}], tenant_id=TENANT, project_id=RESOLVED, threshold=THRESHOLD, cap=CAP
        )
    assert caught.value.status == 400
    with pytest.raises(TelemetryRequestError) as many:
        core.check_blob_cap(201)
    assert (many.value.status, many.value.error_type) == (413, "too_many_blobs")
    core.check_blob_cap(200)


def test_verify_blob_rows_returns_unverified_and_raises_on_a_mismatch() -> None:
    rows = core.attach_blob_uris(blob_rows(3), tenant_id=TENANT, project_id=RESOLVED, threshold=THRESHOLD, cap=CAP)
    indexes = [0, 1, 2]
    store = FakeBlobStore({rows[1]["content_uri"]: "missing", rows[2]["content_uri"]: "stale"})
    bad = core.verify_blob_rows(rows, indexes, store=store, now=STARTED)
    assert [(u.row, u.reason) for u in bad] == [(1, "missing"), (2, "stale")] and len(store.heads) == 3
    shared = FakeBlobStore({rows[0]["content_uri"]: "missing"})
    both = core.verify_blob_rows([rows[0], rows[0]], [0, 1], store=shared, now=STARTED)
    assert len(shared.heads) == 1 and [u.row for u in both] == [0, 1]
    with pytest.raises(BlobIntegrityError):
        core.verify_blob_rows(rows, indexes, store=FakeBlobStore({rows[2]["content_uri"]: "mismatch"}), now=STARTED)
    sized = FakeBlobStore({rows[1]["content_uri"]: "size_mismatch", rows[2]["content_uri"]: "size_mismatch"})
    with pytest.raises(TelemetryRequestError) as refused:
        core.verify_blob_rows(rows, indexes, store=sized, now=STARTED)
    assert (refused.value.status, refused.value.error_type) == (422, "blob_not_verified")
    assert refused.value.fields == {"table": "telemetry_transcripts", "row": 1, "reason": "size_mismatch"}
    both_bad = FakeBlobStore({rows[1]["content_uri"]: "size_mismatch", rows[2]["content_uri"]: "mismatch"})
    with pytest.raises(BlobIntegrityError):
        core.verify_blob_rows(rows, indexes, store=both_bad, now=STARTED)
    down = FakeBlobStore()
    down.error = BlobStoreUnavailableError("x")
    with pytest.raises(TelemetryRequestError) as caught:
        core.verify_blob_rows(rows, indexes, store=down, now=STARTED)
    assert caught.value.status == 503


def test_verify_blob_rows_same_uri_different_size_is_422() -> None:
    rows = core.attach_blob_uris(blob_rows(2), tenant_id=TENANT, project_id=RESOLVED, threshold=THRESHOLD, cap=CAP)
    twin = {**rows[0], "content_bytes": 70001}
    store = FakeBlobStore()
    with pytest.raises(TelemetryRequestError) as caught:
        core.verify_blob_rows([rows[0], rows[1], twin], [0, 1, 2], store=store, now=STARTED)
    assert (caught.value.status, caught.value.fields) == (
        422,
        {"table": "telemetry_transcripts", "row": 2, "reason": "size_mismatch"},
    )
    assert store.heads == []


class Con:
    def __init__(self, stored: list[tuple[Any, ...]]) -> None:
        self.stored, self.sql = stored, []

    def execute(self, sql: str, params: Any = None) -> Con:
        self.sql.append((sql, params))
        return self

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.stored


def test_require_stored_reads_once_and_names_the_first_unstored_row() -> None:
    rows = core.attach_blob_uris(blob_rows(3), tenant_id=TENANT, project_id=RESOLVED, threshold=THRESHOLD, cap=CAP)
    batch = prepare_batch(specs()["telemetry_transcripts"], rows, tenant_id=TENANT, project_id=RESOLVED, now=STARTED)
    keys = [(r["producer"], r["event_id"], r["parser_version"]) for r in batch.rows]
    bad = [core.Unverified(0, "missing"), core.Unverified(2, "stale")]
    core.require_stored(Con(keys), "ops_catalog", batch, bad)
    con = Con(keys[:2])
    with pytest.raises(TelemetryRequestError) as caught:
        core.require_stored(con, "ops_catalog", batch, bad)
    assert caught.value.fields == {"table": "telemetry_transcripts", "row": 2, "reason": "stale"} and len(con.sql) == 1
    core.require_stored(Con([]), "ops_catalog", batch, [])
    core.require_stored(Con([]), "ops_catalog", batch, [core.Unverified(99, "missing")])
    assert core.verify_blob_rows(rows, [], store=FakeBlobStore(), now=STARTED) == []
