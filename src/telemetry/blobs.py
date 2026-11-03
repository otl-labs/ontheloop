"""The plane-neutral blob (claim-check) protocol shared by the telemetry writer and the producer (T2.36, slice 2a-2, plan 4).

begin_blobs asks the writer for a grant per content-addressed payload above the inline threshold; the producer PUTs the
bytes under the grant and then appends a row that carries no content, only its SHA-256 and length. This module is the one
definition of that request grammar and of the two predicates that decide which rows travel the route. Stdlib only, plus
src.telemetry.timestamps and src.telemetry.request: it must stay importable from scripts/ and src.telemetry_client without
opening the write-boundary fence (never append, gate or identity; Decision 184 cl.2, Decision 212 cl.2).

The threshold and the cap always come from the caller (the bundled projection on the writer, describe on the producer),
never a literal here (Decision 210 cl.3). Errors are request.RequestError located by item index and field, never a value.
"""

from __future__ import annotations

import base64
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.telemetry.request import COMPONENT_RE, DEFAULT_COMPONENT, RequestError
from src.telemetry.timestamps import TimestampError, parse_wire_timestamp

BLOB_PROTOCOL_VERSION = 1
GRANT_TTL_SECONDS = 900
MAX_BLOB_ITEMS = 200
TOO_MANY_BLOBS = "too_many_blobs"
ALLOWED_BODY_KEYS = frozenset({"action", "project_ref", "component", "items"})
ITEM_KEYS = frozenset({"session_ref", "session_started_at", "content_sha256", "content_bytes"})
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class BlobItem:
    session_ref: str
    session_started_at: datetime
    content_sha256: str
    content_bytes: int


@dataclass(frozen=True)
class BeginBlobsRequest:
    project_ref: str
    component: str
    items: tuple[BlobItem, ...]


def _bad(reason: str, **where: Any) -> RequestError:
    return RequestError("bad_request", reason=reason, **where)


def _lone_surrogate(text: str) -> bool:
    return any(0xD800 <= ord(ch) <= 0xDFFF for ch in text)


def _unclean(text: Any) -> bool:
    return isinstance(text, str) and (_lone_surrogate(text) or "\x00" in text)


def _walk_unicode(body: Mapping[str, Any]) -> None:
    """Refuse a lone surrogate or U+0000 in any key or string, iteratively; position only (the item index), no value."""
    stack: list[tuple[Any, int | None]] = [(body, None)]
    while stack:
        node, row = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                if not isinstance(key, str) or _unclean(key):
                    raise _bad("invalid_unicode", row=row)
                stack.append((value, row))
        elif isinstance(node, list):
            stack.extend((item, index if row is None else row) for index, item in enumerate(node))
        elif _unclean(node):
            raise _bad("invalid_unicode", row=row)


def _parse_item(raw: Any, index: int, threshold: int, cap: int) -> BlobItem:
    if not isinstance(raw, dict) or set(raw) != ITEM_KEYS:
        raise _bad("item_shape", row=index)
    session_ref = raw["session_ref"]
    if not isinstance(session_ref, str) or not session_ref:
        raise _bad("session_ref", row=index, column="session_ref")
    try:
        started = parse_wire_timestamp(raw["session_started_at"])
    except (TimestampError, OverflowError) as exc:
        raise _bad("wire_timestamp", row=index, column="session_started_at") from exc
    digest = raw["content_sha256"]
    if not isinstance(digest, str) or _SHA256_HEX.match(digest) is None:
        raise _bad("content_sha256", row=index, column="content_sha256")
    size = raw["content_bytes"]
    if not isinstance(size, int) or isinstance(size, bool):
        raise _bad("content_bytes", row=index, column="content_bytes")
    if size <= threshold:
        raise RequestError("bad_request", reason="below_threshold", row=index, column="content_bytes", threshold=threshold)
    if size > cap:
        raise RequestError("bad_request", reason="above_cap", row=index, column="content_bytes", cap=cap)
    return BlobItem(session_ref, started, digest, size)


def parse_begin_blobs_request(body: Mapping[str, Any], *, threshold: int, cap: int) -> BeginBlobsRequest:
    """Validate one begin_blobs body; raises RequestError. More than MAX_BLOB_ITEMS items is too_many_blobs (413)."""
    _walk_unicode(body)
    if not set(body) <= ALLOWED_BODY_KEYS:
        raise _bad("unknown_key")
    project_ref = body.get("project_ref")
    if not isinstance(project_ref, str) or not project_ref:
        raise _bad("project_ref")
    component = body.get("component", DEFAULT_COMPONENT)
    if not isinstance(component, str) or COMPONENT_RE.match(component) is None:
        raise _bad("component")
    items = body.get("items")
    if not isinstance(items, list) or not items:
        raise _bad("items")
    if len(items) > MAX_BLOB_ITEMS:
        raise RequestError(TOO_MANY_BLOBS, reason="items_per_request", threshold=MAX_BLOB_ITEMS)
    return BeginBlobsRequest(
        project_ref, component, tuple(_parse_item(raw, index, threshold, cap) for index, raw in enumerate(items))
    )


def sha256_hex_to_b64(digest_hex: str) -> str:
    return base64.b64encode(bytes.fromhex(digest_hex)).decode("ascii")


def sha256_b64_to_hex(digest_b64: str) -> str:
    return base64.b64decode(digest_b64, validate=True).hex()


def spill_item(row: Mapping[str, Any], threshold: int) -> bool:
    """A producer row whose inline content exceeds the threshold (UTF-8 bytes) travels the blob route."""
    content = row.get("content")
    return isinstance(content, str) and len(content.encode("utf-8")) > threshold


def blob_row(row: Mapping[str, Any], threshold: int, cap: int) -> bool:
    """A writer-side row that already spilled: no content, no omission reason, a well-formed digest, a size in bounds."""
    digest, size = row.get("content_sha256"), row.get("content_bytes")
    return (
        row.get("content") is None
        and row.get("content_omitted_reason") is None
        and isinstance(digest, str)
        and _SHA256_HEX.match(digest) is not None
        and isinstance(size, int)
        and not isinstance(size, bool)
        and threshold < size <= cap
    )


__all__ = [
    "ALLOWED_BODY_KEYS",
    "BLOB_PROTOCOL_VERSION",
    "BeginBlobsRequest",
    "BlobItem",
    "GRANT_TTL_SECONDS",
    "ITEM_KEYS",
    "MAX_BLOB_ITEMS",
    "TOO_MANY_BLOBS",
    "blob_row",
    "parse_begin_blobs_request",
    "sha256_b64_to_hex",
    "sha256_hex_to_b64",
    "spill_item",
]
