"""Mirror test for src/common/ducklake_event_content.py: transcript_content paging, storage kinds and faults (rec-4291)."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

import pytest

import src.lambdas.ducklake_reader.handler as handler
from src.common import ducklake_event_content as content
from src.common.ducklake_event_reads import EventReadInputError
from src.common.ducklake_telemetry_blobstore import (
    BlobIntegrityError,
    BlobStoreAccessError,
    BlobStoreUnavailableError,
    LazyBlobStore,
)

pytestmark = pytest.mark.unit

CHUNK = content.MAX_CHUNK_BYTES
URI = "01ARZ3NDEKTSV4RRFFQ69G5FAV/01ARZ3NDEKTSV4RRFFQ69G5FAW/01ARZ3NDEKTSV4RRFFQ69G5FAX/" + "ab" * 32


class FakeStore:
    """The get_blob adapter over one in-memory payload; the calls are recorded."""

    def __init__(self, payload: bytes = b"", error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error
        self.calls: list[tuple[str, str, int, int, int]] = []

    def get_blob(self, uri: str, sha: str, size: int, *, offset: int, length: int) -> bytes:
        self.calls.append((uri, sha, size, offset, length))
        if self.error is not None:
            raise self.error
        return self.payload[offset : offset + length]


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> FakeStore:
    fake = FakeStore()
    monkeypatch.setattr(content, "BLOB_STORE", fake)
    return fake


def _row(payload: bytes, *, storage: str, versions: int = 1, uri: str = URI) -> dict[str, Any]:
    return {
        "content": payload.decode("utf-8") if storage == "inline" else None,
        "content_uri": uri if storage == "object" else None,
        "content_sha256": hashlib.sha256(payload).hexdigest(),
        "content_bytes": len(payload),
        "content_omitted_reason": None,
        "compared_versions": versions,
    }


def _values(offset: int = 0, max_bytes: int = CHUNK) -> dict[str, Any]:
    return {"offset": offset, "max_bytes": max_bytes}


def _reassemble(row: dict[str, Any], max_bytes: int = CHUNK) -> tuple[bytes, int]:
    out, offset, calls = b"", 0, 0
    while True:
        (page,) = content.serve_content([row], _values(offset, max_bytes))
        calls += 1
        out += base64.b64decode(page["data"])
        offset += page["length"]
        if offset >= page["content_bytes"]:
            return out, calls


def _edge_payload() -> bytes:
    emoji = "\U0001f600".encode()
    total = 8 * 1024 * 1024
    head = b"a" * (CHUNK - 2) + emoji + b"a" * (CHUNK - 4) + emoji
    return head + b"a" * (total - len(head))


def test_object_row_pages_and_reassembles_to_the_stored_bytes(store: FakeStore) -> None:
    payload = _edge_payload()
    assert len(payload) == 8 * 1024 * 1024 and payload.decode("utf-8")
    store.payload = payload
    row = _row(payload, storage="object")
    got, calls = _reassemble(row)
    assert calls == 3 and got == payload and hashlib.sha256(got).hexdigest() == row["content_sha256"]
    assert [c[3:] for c in store.calls] == [(0, CHUNK), (CHUNK, CHUNK), (2 * CHUNK, len(payload) - 2 * CHUNK)]
    assert all(c[0] == URI and c[2] == len(payload) for c in store.calls)
    (page,) = content.serve_content([row], _values(0))
    assert set(page) == {
        "storage",
        "encoding",
        "offset",
        "length",
        "content_bytes",
        "content_sha256",
        "data",
        "omitted_reason",
    }
    assert page["storage"] == "object" and page["encoding"] == "base64" and page["omitted_reason"] is None


def test_inline_row_serves_through_the_same_envelope(store: FakeStore) -> None:
    payload = 'héllo "w\\orld"\n\U0001f600'.encode()
    row = _row(payload, storage="inline")
    got, calls = _reassemble(row, max_bytes=3)
    assert got == payload and calls == -(-len(payload) // 3) and store.calls == []
    (page,) = content.serve_content([row], _values(0))
    assert page["storage"] == "inline" and page["length"] == len(payload) and page["content_bytes"] == len(payload)


def test_omitted_row_carries_its_reason_and_no_data(store: FakeStore) -> None:
    row = {**_row(b"x" * 10, storage="object"), "content_uri": None, "content_omitted_reason": "oversize"}
    row["content_bytes"] = 50_000_000
    (page,) = content.serve_content([row], _values(0))
    assert page["storage"] == "omitted" and page["data"] is None and page["length"] == 0
    assert page["omitted_reason"] == "oversize" and page["content_bytes"] == 50_000_000 and store.calls == []


def test_unknown_key_answers_zero_rows(store: FakeStore) -> None:
    assert content.serve_content([], _values()) == []


def test_offset_beyond_size_is_refused_without_the_value(store: FakeStore) -> None:
    row = _row(b"abcdef", storage="inline")
    for offset in (6, 7, 987654321):
        with pytest.raises(EventReadInputError) as caught:
            content.serve_content([row], _values(offset))
        assert "'offset'" in str(caught.value) and str(offset) not in str(caught.value)
    assert content.serve_content([row], _values(5))[0]["length"] == 1
    empty = _row(b"", storage="inline")
    page = content.serve_content([empty], _values(0))[0]
    assert page["length"] == 0 and page["data"] == ""


def test_max_bytes_is_clamped(store: FakeStore) -> None:
    payload = b"z" * (CHUNK + 10)
    store.payload = payload
    row = _row(payload, storage="object")
    assert content.serve_content([row], _values(0, 10**12))[0]["length"] == CHUNK
    assert content.serve_content([row], _values(0, 1))[0]["length"] == 1
    assert content.serve_content([row], _values(0, 0))[0]["length"] == 1
    assert content.serve_content([row], _values(CHUNK, 10**9))[0]["length"] == 10


def test_grain_conflict_is_refused(store: FakeStore) -> None:
    payload = b"same"
    inline, obj = _row(payload, storage="inline", versions=2), _row(payload, storage="object", versions=2)
    for rows in ([inline, obj], [inline, inline], [obj]):
        with pytest.raises(content.BlobContentFault) as caught:
            content.serve_content(rows, _values())
        fault = caught.value
        assert (fault.status, fault.error_type, fault.reason) == (500, "grain_conflict", "differing_rows")
    assert store.calls == []


def test_compared_equal_duplicates_serve_the_inline_row(store: FakeStore) -> None:
    payload = b"stored two ways"
    store.payload = payload
    inline, obj = _row(payload, storage="inline"), _row(payload, storage="object")
    for rows in ([obj, inline], [inline, obj]):
        (page,) = content.serve_content(rows, _values())
        assert page["storage"] == "inline" and base64.b64decode(page["data"]) == payload
    assert store.calls == []


def test_not_configured_refuses_objects_but_serves_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEMETRY_BLOB_ROOT", raising=False)
    monkeypatch.setattr(content, "BLOB_STORE", LazyBlobStore(content._build_store))
    with pytest.raises(content.BlobContentFault) as caught:
        content.serve_content([_row(b"x" * 20, storage="object")], _values())
    assert (caught.value.status, caught.value.error_type, caught.value.reason) == (500, "blob_misconfigured", "not_configured")
    assert content.serve_content([_row(b"inline", storage="inline")], _values())[0]["storage"] == "inline"
    omitted = {**_row(b"x", storage="inline"), "content": None, "content_omitted_reason": "oversize"}
    assert content.serve_content([omitted], _values())[0]["storage"] == "omitted"
    monkeypatch.setenv("TELEMETRY_BLOB_ROOT", "s3://bucket/not-the-blob-prefix/")
    monkeypatch.setenv("TELEMETRY_DATA_PATH", "s3://bucket/ducklake-neon-smoke/")
    monkeypatch.setenv("TELEMETRY_META_SCHEMA", "ducklake_smoke")
    with pytest.raises(content.BlobContentFault) as caught:
        content.serve_content([_row(b"x" * 20, storage="object")], _values())
    assert (caught.value.error_type, caught.value.reason) == ("blob_misconfigured", "root_invalid")
    assert "bucket" not in str(caught.value) and caught.value.__cause__ is None


def test_store_errors_map_to_typed_faults(store: FakeStore) -> None:
    row = _row(b"x" * 20, storage="object")
    cases: list[tuple[Exception, tuple[int, str, str]]] = [
        (BlobIntegrityError("m", reason="checksum_mismatch"), (500, "blob_integrity", "checksum_mismatch")),
        (BlobIntegrityError("m", reason="forbidden_or_missing"), (500, "blob_integrity", "forbidden_or_missing")),
        (BlobStoreUnavailableError("m"), (503, "blob_unavailable", "store_unavailable")),
        (BlobStoreAccessError("m"), (500, "blob_misconfigured", "store_access")),
    ]
    for error, expected in cases:
        error.code, error.status = "AccessDenied", 403  # type: ignore[attr-defined]
        store.error = error
        with pytest.raises(content.BlobContentFault) as caught:
            content.serve_content([row], _values())
        fault = caught.value
        assert (fault.status, fault.error_type, fault.reason) == expected
        assert (fault.cause_code, fault.cause_status) == ("AccessDenied", 403) and fault.__cause__ is None
        assert URI not in str(fault) and "m" != str(fault)


def test_max_chunk_envelope_stays_under_the_response_cap(store: FakeStore) -> None:
    payload = bytes(range(256)) * (CHUNK // 256 + 1)
    store.payload = payload
    row = _row(payload, storage="object")
    (page,) = content.serve_content([row], _values(0))
    assert page["length"] == CHUNK
    body = {
        "ok": True,
        "verb": "transcript_content",
        "registry_version": 6,
        "read_version": "x" * 16,
        "rows": [page],
        "row_count": 1,
    }
    meta: dict[str, Any] = {}
    guarded = handler._guard_response_size(handler._response(200, body), {"action": "named_read"}, meta)
    assert guarded["statusCode"] == 200 and meta["response_bytes"] < handler.MAX_RESPONSE_BYTES
    assert json.loads(guarded["body"])["rows"][0]["length"] == CHUNK


def test_a_valid_root_builds_the_s3_store_once(monkeypatch: pytest.MonkeyPatch) -> None:
    made: list[str] = []
    monkeypatch.setattr(content, "make_s3_client", lambda region: made.append(region) or object())
    monkeypatch.setenv("TELEMETRY_BLOB_ROOT", "s3://bucket/telemetry-blobs/ducklake_smoke/")
    monkeypatch.setenv("TELEMETRY_DATA_PATH", "s3://bucket/ducklake-neon-smoke/")
    monkeypatch.setenv("TELEMETRY_META_SCHEMA", "ducklake_smoke")
    monkeypatch.setenv("AWS_REGION", "eu-west-2")
    built = content._build_store()
    assert type(built).__name__ == "S3BlobStore" and made == ["eu-west-2"]
