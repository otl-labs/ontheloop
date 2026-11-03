"""Shared fakes for the claim-check (blob) tests (T2.36, plan 4): one scripted blob store, a fake S3 client and the
producer-row helper. No botocore, no network. Credential-shaped strings are built at runtime so no scanner finds a literal."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from src.common.ducklake_telemetry_blobstore import MISMATCH, MISSING, SIZE_MISMATCH, STALE, VERIFIED, Grant
from src.telemetry.blobs import sha256_hex_to_b64, spill_item

X_AMZ = "X-Amz-"
SIGNED = "content-length;host;x-amz-checksum-sha256"


def presigned_url(key: str = "k", *, signed: str = SIGNED, extra: str = "", token: bool = True) -> str:
    """A credential-shaped presigned URL assembled at runtime (never a literal)."""
    query = f"{X_AMZ}Algorithm=AWS4-HMAC-SHA256&{X_AMZ}SignedHeaders={signed}&{X_AMZ}Signature=" + "0" * 8
    if token:
        query += f"&{X_AMZ}Security-Token=tok"
    return f"https://bucket.s3.invalid/{key}?{query}{extra}"


class FakeBlobStore:
    """verify() answers are scripted per content_uri (default `default`); grants and heads are recorded."""

    def __init__(self, results: Mapping[str, str] | None = None, *, default: str = VERIFIED) -> None:
        self.results = dict(results or {})
        self.default = default
        self.heads: list[str] = []
        self.grants: list[tuple[str, str, int]] = []
        self.objects: dict[str, dict[str, int]] = {}
        self.error: Exception | None = None

    def verify(self, content_uri: str, sha_hex: str, size: int, *, now: datetime | None = None) -> str:
        self.heads.append(content_uri)
        if self.error is not None:
            raise self.error
        return self.results.get(content_uri, self.default)

    def grant_put(self, content_uri: str, sha_hex: str, size: int, *, now: datetime | None = None) -> Grant:
        self.grants.append((content_uri, sha_hex, size))
        moment = (now or datetime(2026, 1, 1, tzinfo=timezone.utc)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        return Grant(presigned_url(content_uri), {"Content-Length": str(size), "x-amz-checksum-sha256": "c2hh"}, moment)

    def count_objects(self, prefix: str) -> dict[str, int]:
        return self.objects.get(prefix, {"count": 0, "bytes": 0})


def _s3_error(code: str) -> bytes:
    return f"<?xml version='1.0'?><Error><Code>{code}</Code><Message>m</Message></Error>".encode()


class MemoryBlobStore:
    """An in-memory object store with S3's checks: a PUT must carry the granted length and the granted SHA-256, else the
    answers are S3's (403 SignatureDoesNotMatch for a different length, 400 BadDigest for different bytes)."""

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.stale: set[str] = set()
        self.expected: dict[str, tuple[str, int]] = {}

    def verify(self, content_uri: str, sha_hex: str, size: int, *, now: datetime | None = None) -> str:
        data = self.blobs.get(content_uri)
        if data is None:
            return MISSING
        if hashlib.sha256(data).hexdigest() != sha_hex:
            return MISMATCH
        if len(data) != size:
            return SIZE_MISMATCH
        return STALE if content_uri in self.stale else VERIFIED

    def grant_put(self, content_uri: str, sha_hex: str, size: int, *, now: datetime | None = None) -> Grant:
        self.expected[content_uri] = (sha_hex, size)
        headers = {"Content-Length": str(size), "x-amz-checksum-sha256": sha256_hex_to_b64(sha_hex)}
        return Grant(presigned_url(content_uri), headers, "2026-01-01T12:15:00.000Z")

    def put(self, url: str, headers: Mapping[str, str], data: bytes) -> tuple[int, bytes]:
        key = urlparse(url).path.lstrip("/")
        sha_hex, size = self.expected[key]
        if headers.get("Content-Length") != str(len(data)) or len(data) != size:
            return 403, _s3_error("SignatureDoesNotMatch")
        if hashlib.sha256(data).hexdigest() != sha_hex:
            return 400, _s3_error("BadDigest")
        self.blobs[key] = data
        self.stale.discard(key)
        return 200, b""

    def count_objects(self, prefix: str) -> dict[str, int]:
        keys = [k for k in self.blobs if k.startswith(prefix)]
        return {"count": len(keys), "bytes": sum(len(self.blobs[k]) for k in keys)}


class FakeS3Client:
    """A hand fake of the three S3 calls the adapter makes. `head` is a response dict or an exception to raise."""

    def __init__(self, head: Any = None, listing: Any = None, url: str | None = None) -> None:
        self.head = head
        self.listing = listing if listing is not None else {"Contents": []}
        self.url = url or presigned_url()
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def head_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("head_object", kwargs))
        if isinstance(self.head, Exception):
            raise self.head
        return dict(self.head)

    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("list_objects_v2", kwargs))
        if isinstance(self.listing, Exception):
            raise self.listing
        return dict(self.listing)

    def generate_presigned_url(self, operation: str, **kwargs: Any) -> str:
        self.calls.append((operation, kwargs))
        return self.url


class ClientErrorLike(Exception):
    """The botocore ClientError shape without botocore."""

    def __init__(self, code: str, status: int) -> None:
        super().__init__(f"{code} secret-bucket/secret-key")
        self.response = {"Error": {"Code": code, "Message": "secret-key"}, "ResponseMetadata": {"HTTPStatusCode": status}}


def as_written(rows: Iterable[Mapping[str, Any]], threshold: int) -> list[dict[str, Any]]:
    """Producer rows as the writer stores them: a row that spill_item marks loses its content and gains a synthetic
    content_uri. The 3a core emits above-threshold content inline by design and spilling is the transport's job."""
    out: list[dict[str, Any]] = []
    for row in rows:
        fresh = dict(row)
        if spill_item(row, threshold):
            fresh.pop("content", None)
            fresh["content_uri"] = f"t/p/s/{row['content_sha256']}"
        out.append(fresh)
    return out


__all__ = [
    "ClientErrorLike",
    "FakeBlobStore",
    "FakeS3Client",
    "MISSING",
    "MemoryBlobStore",
    "VERIFIED",
    "as_written",
    "presigned_url",
]
