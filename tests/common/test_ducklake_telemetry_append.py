"""Mirror test for src/common/ducklake_telemetry_append.py (recording fake connection and fake part 1 primitives)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from src.common import ducklake_telemetry_append as adapter
from src.common import ducklake_telemetry_blobs as blobs
from src.common.ducklake_scd2_schema import DuckLakeRuntimeError, load_field_semantics
from src.common.ducklake_telemetry_blobstore import BlobIntegrityError, BlobStoreUnavailableError
from src.common.ducklake_telemetry_txn import TelemetryRequestError
from src.telemetry.gate import GateError, GrainConflictError
from tests.fixtures.telemetry_append_fakes import (
    NOW,
    RESOLVED,
    TENANT,
    AdapterKit,
    parse,
    session_row,
    specs,
    transcript_row,
)
from tests.fixtures.telemetry_blob_fakes import FakeBlobStore

SEMANTICS = load_field_semantics()
REAL_REF = "repo:example"


def run(request: Any) -> adapter.AppendOutcome:
    return adapter.append_request(object(), request, tenant_id=TENANT, specs=specs(), semantics=SEMANTICS, now=NOW)


def test_rule_rejection_opens_no_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    kit = AdapterKit(lookup_result=("01CRZ3NDEKTSV4RRFFQ69G5FCX", "registered")).install(monkeypatch)
    bad = parse(REAL_REF, ("telemetry_sessions", [session_row(workflow=None)]))
    with pytest.raises(GateError):
        run(bad)
    conflict = parse(REAL_REF, ("telemetry_sessions", [session_row(), session_row(workflow="plan")]))
    with pytest.raises(GrainConflictError):
        run(conflict)
    assert "BEGIN" not in kit.events and not [e for e in kit.events if e.startswith("execute")]


def test_first_sight_reprepares_only_when_the_id_differs(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    real = adapter.prepare_batch

    def counting(spec: Any, rows: Any, **kw: Any) -> Any:
        calls.append(kw["project_id"])
        return real(spec, rows, **kw)

    monkeypatch.setattr(adapter, "prepare_batch", counting)
    AdapterKit().install(monkeypatch)
    out = run(parse(REAL_REF, ("telemetry_sessions", [session_row()])))
    assert calls == [adapter.SENTINEL_PROJECT_ID, RESOLVED] and out.project_registration == "created"
    calls.clear()
    AdapterKit(lookup_result=(RESOLVED, "registered")).install(monkeypatch)
    out = run(parse(REAL_REF, ("telemetry_sessions", [session_row()])))
    assert calls == [RESOLVED] and out.project_registration == "existing" and out.project_id == RESOLVED


def test_sentinel_never_reaches_execute_prepared(monkeypatch: pytest.MonkeyPatch) -> None:
    kit = AdapterKit().install(monkeypatch)
    run(parse(REAL_REF, ("telemetry_transcripts", [transcript_row()]), ("telemetry_sessions", [session_row()])))
    assert kit.executed_project_ids and all(ids == {RESOLVED} for _, ids in kit.executed_project_ids)
    kit = AdapterKit().install(monkeypatch)
    conflict = parse(REAL_REF, ("telemetry_sessions", [session_row(), session_row(workflow="plan")]))
    with pytest.raises(GrainConflictError) as caught:
        run(conflict)
    assert caught.value.event_id is None and caught.value.row == 1

    real = adapter._prepare_all
    monkeypatch.setattr(
        adapter,
        "_prepare_all",
        lambda request, specs, **kw: real(request, specs, **{**kw, "project_id": adapter.SENTINEL_PROJECT_ID}),
    )
    guarded = AdapterKit().install(monkeypatch)
    with pytest.raises(DuckLakeRuntimeError, match="resolved project id"):
        run(parse(REAL_REF, ("telemetry_sessions", [session_row()])))
    assert not [e for e in guarded.events if e.startswith("execute")]


def test_batches_execute_in_request_order_in_one_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    kit = AdapterKit().install(monkeypatch)
    out = run(parse(REAL_REF, ("telemetry_transcripts", [transcript_row()]), ("telemetry_sessions", [session_row()])))
    begin = kit.events.index("BEGIN")
    assert kit.events[begin:] == ["BEGIN", "resolve", "execute:telemetry_transcripts", "execute:telemetry_sessions", "COMMIT"]
    assert (
        list(out.tables) == ["telemetry_transcripts", "telemetry_sessions"] and out.tables["telemetry_sessions"].inserted == 1
    )
    kit = AdapterKit(fail_on_table="telemetry_sessions").install(monkeypatch)
    with pytest.raises(RuntimeError, match="injected"):
        run(parse(REAL_REF, ("telemetry_transcripts", [transcript_row()]), ("telemetry_sessions", [session_row()])))
    assert "COMMIT" not in kit.events and kit.cache == {}


def test_binding_cached_only_after_the_transaction_returns(monkeypatch: pytest.MonkeyPatch) -> None:
    kit = AdapterKit().install(monkeypatch)
    run(parse(REAL_REF, ("telemetry_sessions", [session_row()])))
    assert kit.cache_at_commit == [{}]
    assert kit.cache == {(TENANT, REAL_REF): RESOLVED}
    kit.events.clear()
    run(parse(REAL_REF, ("telemetry_sessions", [session_row()])))
    assert "lookup" not in kit.events and "resolve" not in kit.events
    hit = AdapterKit(lookup_result=(RESOLVED, "registered")).install(monkeypatch)
    run(parse(REAL_REF, ("telemetry_sessions", [session_row()])))
    assert hit.cache == {(TENANT, REAL_REF): RESOLVED} and "resolve" not in hit.events


def test_unregistered_reserved_ref_is_refused_before_any_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    kit = AdapterKit().install(monkeypatch)
    for ref in ("drill:fresh", "synthetic:deploy-smoke-x"):
        with pytest.raises(TelemetryRequestError) as caught:
            run(parse(ref, ("telemetry_sessions", [session_row()])))
        assert (caught.value.status, caught.value.error_type, caught.value.fields) == (
            403,
            "project_unregistered",
            {"remedy": "register_project"},
        )
    assert kit.events == ["lookup", "lookup"]
    registered = AdapterKit(lookup_result=(RESOLVED, "registered")).install(monkeypatch)
    assert run(parse("drill:registered", ("telemetry_sessions", [session_row()]))).project_id == RESOLVED
    assert "BEGIN" in registered.events


class StoredCon:
    """A connection double for require_stored: records the one read and answers it with the given stored grain keys."""

    def __init__(self, stored: list[tuple[Any, ...]] | None = None) -> None:
        self.stored, self.reads = stored or [], []

    def execute(self, sql: str, params: Any = None) -> Any:
        self.reads.append((sql, params))
        return self

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.stored


def blob_request(count: int = 1, ref: str = REAL_REF) -> Any:
    rows = [
        {
            k: v
            for k, v in transcript_row(
                external_ref=f"t#{i}", entity_ref=f"t#{i}", content_sha256=f"{i:064x}", content_bytes=70000
            ).items()
            if k != "content"
        }
        for i in range(count)
    ]
    return parse(ref, ("telemetry_transcripts", rows))


def run_blob(request: Any, store: FakeBlobStore, con: Any = None) -> adapter.AppendOutcome:
    kw = {"tenant_id": TENANT, "specs": specs(), "semantics": SEMANTICS, "now": NOW, "store": store}
    return adapter.append_request(con if con is not None else StoredCon(), request, **kw)


class OrderedStore(FakeBlobStore):
    def __init__(self, events: list[str], **kw: Any) -> None:
        super().__init__(**kw)
        self.events = events

    def verify(self, content_uri: str, sha_hex: str, size: int, *, now: Any = None) -> str:
        self.events.append("head")
        return super().verify(content_uri, sha_hex, size, now=now)


def test_blob_rows_get_the_writer_uri_and_head_after_prepare(monkeypatch: pytest.MonkeyPatch) -> None:
    kit = AdapterKit(lookup_result=(RESOLVED, "registered")).install(monkeypatch)
    real = adapter.prepare_batch
    seen: list[Any] = []

    def recording(spec: Any, rows: Any, **kw: Any) -> Any:
        kit.events.append("prepare")
        seen.extend(rows)
        return real(spec, rows, **kw)

    monkeypatch.setattr(adapter, "prepare_batch", recording)
    store = OrderedStore(kit.events)
    out = run_blob(blob_request(2), store)
    uris = [
        blobs.content_uri_for(TENANT, RESOLVED, "sess", datetime(2026, 1, 1, 12, tzinfo=timezone.utc), f"{i:064x}")
        for i in range(2)
    ]
    assert [r["content_uri"] for r in seen] == uris and sorted(store.heads) == sorted(uris)
    assert kit.events[:5] == ["lookup", "prepare", "head", "head", "BEGIN"]
    assert kit.events.index("prepare") < kit.events.index("head") < kit.events.index("BEGIN")
    assert (out.blob_rows, out.blobs_verified, out.tables["telemetry_transcripts"].inserted) == (2, 2, 2)
    assert isinstance(out.blob_head_ms, int)
    plain = run_blob(parse(REAL_REF, ("telemetry_transcripts", [transcript_row()])), store)
    assert (plain.blob_rows, plain.blob_head_ms) == (0, 0) and len(store.heads) == 2
    with pytest.raises(DuckLakeRuntimeError, match="blob store"):
        adapter.append_request(object(), blob_request(), tenant_id=TENANT, specs=specs(), semantics=SEMANTICS, now=NOW)


def test_unknown_project_blob_row_is_422_before_any_head(monkeypatch: pytest.MonkeyPatch) -> None:
    kit = AdapterKit().install(monkeypatch)
    store = FakeBlobStore()
    with pytest.raises(TelemetryRequestError) as caught:
        run_blob(blob_request(2), store)
    assert (caught.value.status, caught.value.error_type) == (422, "blob_not_verified")
    assert caught.value.fields == {"table": "telemetry_transcripts", "row": 0, "reason": "unknown_project"}
    assert store.heads == [] and kit.events == ["lookup"]


def test_missing_blob_for_an_unstored_row_is_422_and_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    kit = AdapterKit(lookup_result=(RESOLVED, "registered")).install(monkeypatch)
    con = StoredCon()
    for state in ("missing", "stale"):
        store = FakeBlobStore(default=state)
        with pytest.raises(TelemetryRequestError) as caught:
            run_blob(blob_request(2), store, con)
        assert caught.value.status == 422 and caught.value.fields == {
            "table": "telemetry_transcripts", "row": 0, "reason": state,
        }  # fmt: skip
    assert len(con.reads) == 2 and not [e for e in kit.events if e.startswith("execute")] and "COMMIT" not in kit.events


def test_unverified_blob_commits_only_as_a_stored_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    kit = AdapterKit(lookup_result=(RESOLVED, "registered")).install(monkeypatch)
    request = blob_request(2)
    keys = []
    attached = blobs.attach_blob_uris(
        request.batches[0][1], tenant_id=TENANT, project_id=RESOLVED, threshold=65536, cap=8388608
    )
    for row in attached:
        batch = adapter.prepare_batch(specs()["telemetry_transcripts"], [row], tenant_id=TENANT, project_id=RESOLVED, now=NOW)
        keys.append((batch.rows[0]["producer"], batch.rows[0]["event_id"], batch.rows[0]["parser_version"]))
    con = StoredCon(keys)
    uris = [
        blobs.content_uri_for(TENANT, RESOLVED, "sess", datetime(2026, 1, 1, 12, tzinfo=timezone.utc), f"{i:064x}")
        for i in range(2)
    ]
    store = FakeBlobStore({uris[0]: "missing", uris[1]: "stale"})
    out = run_blob(request, store, con)
    assert (out.blob_rows, out.blobs_verified) == (2, 0) and "COMMIT" in kit.events
    sql, params = con.reads[0]
    assert "FROM ops_catalog.telemetry_transcripts" in sql and "session_started_at >= ?" in sql and "event_id IN (?, ?)" in sql
    assert params[2:] == [k[1] for k in keys]
    partial = StoredCon(keys[:1])
    with pytest.raises(TelemetryRequestError) as caught:
        run_blob(request, store, partial)
    assert caught.value.fields == {"table": "telemetry_transcripts", "row": 1, "reason": "stale"}


def test_integrity_mismatch_raises_unmapped(monkeypatch: pytest.MonkeyPatch) -> None:
    kit = AdapterKit(lookup_result=(RESOLVED, "registered")).install(monkeypatch)
    with pytest.raises(BlobIntegrityError):
        run_blob(blob_request(), FakeBlobStore(default="mismatch"))
    assert "BEGIN" not in kit.events


def test_size_mismatch_is_422_and_opens_no_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    kit = AdapterKit(lookup_result=(RESOLVED, "registered")).install(monkeypatch)
    uris = [
        blobs.content_uri_for(TENANT, RESOLVED, "sess", datetime(2026, 1, 1, 12, tzinfo=timezone.utc), f"{i:064x}")
        for i in range(2)
    ]
    keys = []
    attached = blobs.attach_blob_uris(
        blob_request(2).batches[0][1], tenant_id=TENANT, project_id=RESOLVED, threshold=65536, cap=8388608
    )
    for row in attached:
        batch = adapter.prepare_batch(specs()["telemetry_transcripts"], [row], tenant_id=TENANT, project_id=RESOLVED, now=NOW)
        keys.append((batch.rows[0]["producer"], batch.rows[0]["event_id"], batch.rows[0]["parser_version"]))
    for con in (StoredCon(), StoredCon(keys)):
        with pytest.raises(TelemetryRequestError) as caught:
            run_blob(blob_request(2), FakeBlobStore({uris[1]: "size_mismatch"}), con)
        assert (caught.value.status, caught.value.error_type) == (422, "blob_not_verified")
        assert caught.value.fields == {"table": "telemetry_transcripts", "row": 1, "reason": "size_mismatch"}
        assert con.reads == []
    assert "BEGIN" not in kit.events


def test_same_uri_different_size_rows_are_422_and_write_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    kit = AdapterKit(lookup_result=(RESOLVED, "registered")).install(monkeypatch)
    rows = [
        {
            k: v
            for k, v in transcript_row(
                external_ref=f"t#{i}", entity_ref=f"t#{i}", content_sha256="a" * 64, content_bytes=70000 + i
            ).items()
            if k != "content"
        }
        for i in range(2)
    ]
    store = FakeBlobStore()
    with pytest.raises(TelemetryRequestError) as caught:
        run_blob(parse(REAL_REF, ("telemetry_transcripts", rows)), store)
    assert (caught.value.status, caught.value.error_type) == (422, "blob_not_verified")
    assert caught.value.fields == {"table": "telemetry_transcripts", "row": 1, "reason": "size_mismatch"}
    assert store.heads == [] and "BEGIN" not in kit.events


def test_integrity_outranks_size_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    kit = AdapterKit(lookup_result=(RESOLVED, "registered")).install(monkeypatch)
    uris = [
        blobs.content_uri_for(TENANT, RESOLVED, "sess", datetime(2026, 1, 1, 12, tzinfo=timezone.utc), f"{i:064x}")
        for i in range(2)
    ]
    with pytest.raises(BlobIntegrityError):
        run_blob(blob_request(2), FakeBlobStore({uris[0]: "size_mismatch", uris[1]: "mismatch"}))
    assert "BEGIN" not in kit.events


def test_blob_rows_over_the_cap_are_413(monkeypatch: pytest.MonkeyPatch) -> None:
    kit = AdapterKit(lookup_result=(RESOLVED, "registered")).install(monkeypatch)
    store = FakeBlobStore()
    assert run_blob(blob_request(200), store).blob_rows == 200
    store.heads.clear()
    with pytest.raises(TelemetryRequestError) as caught:
        run_blob(blob_request(201), store)
    assert (caught.value.status, caught.value.error_type) == (413, "too_many_blobs") and store.heads == []
    assert kit.events.count("BEGIN") == 1


def test_store_unavailable_is_503_and_opens_no_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    kit = AdapterKit(lookup_result=(RESOLVED, "registered")).install(monkeypatch)
    store = FakeBlobStore()
    store.error = BlobStoreUnavailableError("x")
    with pytest.raises(TelemetryRequestError) as caught:
        run_blob(blob_request(), store)
    assert (caught.value.status, caught.value.error_type) == (503, "blob_store_unavailable") and "BEGIN" not in kit.events
