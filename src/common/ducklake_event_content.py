"""transcript_content's work after its registry SQL has resolved the row (rec-4291, Decision 207, Decision 101).

The verb returns one stored transcript row's exact payload bytes as one standard-base64 page of at most MAX_CHUNK_BYTES, so a
caller loops while offset + length < content_bytes and checks the reassembled length and SHA-256 against the row. Storage is
inline (the content column's UTF-8 bytes), object (the content_blobs port's get_blob: HEAD-verified, then a ranged GET bound
to that HEAD's ETag) or omitted (no data; content_bytes is then the raw source size (0 on a withheld_by_policy row) and the
caller stops).

Rows under one grain key that differ in any compared column are refused (500 grain_conflict) rather than served as one, because
every page re-resolves the row and a per-page choice could splice two stored versions into one reassembled payload. Duplicates
that agree on every compared column but store the payload two ways (one inline, one object) serve the inline row.

Imported function-locally by ducklake_event_reads (reader bundle only). No import-time work. Never returns or logs content_uri,
the bucket, a key, a presigned URL or an exception message: every fault is a closed-set reason (Decision 101).
"""

from __future__ import annotations

import base64
import os
from typing import Any

from src.common.ducklake_event_reads import EventReadInputError
from src.common.ducklake_handler_errors import TypedFault
from src.common.ducklake_scd2_schema import DuckLakeRuntimeError
from src.common.ducklake_telemetry_blobstore import (
    BlobIntegrityError,
    BlobRoot,
    BlobStoreAccessError,
    BlobStoreUnavailableError,
    LazyBlobStore,
    S3BlobStore,
    make_s3_client,
)

MAX_CHUNK_BYTES = 3 * 1024 * 1024
VERB = "transcript_content"


class BlobContentFault(TypedFault):
    """A condition the caller cannot fix by changing its request: answered with its own status, type and reason."""


def _build_store() -> S3BlobStore:
    root = os.environ.get("TELEMETRY_BLOB_ROOT", "")
    if not root:
        raise BlobContentFault(500, "blob_misconfigured", "not_configured")
    try:
        parsed = BlobRoot.from_env(
            root, os.environ.get("TELEMETRY_DATA_PATH", ""), os.environ.get("TELEMETRY_META_SCHEMA", "")
        )
    except DuckLakeRuntimeError:
        raise BlobContentFault(500, "blob_misconfigured", "root_invalid") from None
    return S3BlobStore(make_s3_client(os.environ.get("AWS_REGION", "")), parsed)


BLOB_STORE = LazyBlobStore(_build_store)


def _store_fault(exc: Exception) -> BlobContentFault:
    code, status = getattr(exc, "code", ""), getattr(exc, "status", 0)
    if isinstance(exc, BlobIntegrityError):
        return BlobContentFault(500, "blob_integrity", exc.reason, cause_code=code, cause_status=status)
    if isinstance(exc, BlobStoreUnavailableError):
        return BlobContentFault(503, "blob_unavailable", "store_unavailable", cause_code=code, cause_status=status)
    return BlobContentFault(500, "blob_misconfigured", "store_access", cause_code=code, cause_status=status)


def _choose(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if int(rows[0]["compared_versions"]) > 1:
        raise BlobContentFault(500, "grain_conflict", "differing_rows")
    for row in rows:
        if row["content"] is not None:
            return row
    return rows[0]


def _page(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _envelope(
    row: dict[str, Any], storage: str, offset: int, length: int, data: str | None, reason: str | None
) -> list[dict[str, Any]]:
    return [
        {
            "storage": storage,
            "encoding": "base64",
            "offset": offset,
            "length": length,
            "content_bytes": int(row["content_bytes"]),
            "content_sha256": row["content_sha256"],
            "data": data,
            "omitted_reason": reason,
        }
    ]


def serve_content(rows: list[dict[str, Any]], values: dict[str, Any]) -> list[dict[str, Any]]:
    """The single response row for the resolved stored rows, or no row for an unknown grain key."""
    if not rows:
        return []
    row = _choose(rows)
    offset = values["offset"]
    window = max(1, min(values["max_bytes"], MAX_CHUNK_BYTES))
    size = int(row["content_bytes"])
    if offset != 0 and offset >= size:
        raise EventReadInputError(VERB, "offset", "must be below the row's content_bytes")
    if row["content"] is not None:
        payload = row["content"].encode("utf-8")
        length = max(0, min(window, len(payload) - offset))
        return _envelope(row, "inline", offset, length, _page(payload[offset : offset + length]), None)
    if row["content_uri"] is None:
        return _envelope(row, "omitted", offset, 0, None, row["content_omitted_reason"])
    length = max(0, min(window, size - offset))
    data = ""
    if length:
        try:
            data = _page(BLOB_STORE.get_blob(row["content_uri"], row["content_sha256"], size, offset=offset, length=length))
        except (BlobIntegrityError, BlobStoreUnavailableError, BlobStoreAccessError) as exc:
            raise _store_fault(exc) from None
    return _envelope(row, "object", offset, length, data, None)
