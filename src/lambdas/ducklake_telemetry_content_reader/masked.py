"""Masked transcript serving for the telemetry content reader (Decision 239 cl.2).

Every served byte is masked inside the function with the read_masked profile, over the whole payload before paging. Never
returns or logs content_uri, a bucket, a key or an exception message: every fault is a closed-set reason (Decision 101).
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any, Callable, NamedTuple

from src.common import ducklake_event_content as content
from src.common.ducklake_event_content import BlobContentFault
from src.common.ducklake_event_reads import EventReadInputError
from src.common.ducklake_telemetry_blobstore import BlobIntegrityError, BlobStoreAccessError, BlobStoreUnavailableError
from src.sensitive_values import fingerprint, mask

PROFILE = "read_masked"
SESSION_BUDGET_BYTES = 5_000_000
MIN_REMAINING_MS = 15_000
ROW_VERB = "transcript_row_masked"
UNTRUSTED = "untrusted_transcript"


class Served(NamedTuple):
    rows: int
    masked_bytes: int
    mask_counts: dict[str, int]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _add(total: dict[str, int], counts: dict[str, int]) -> None:
    for key, n in counts.items():
        total[key] = total.get(key, 0) + n


def _raw_text(row: dict[str, Any]) -> tuple[str, str] | None:
    """(storage, text) for a stored row, or None when the row is omitted (no payload stored)."""
    if row["content"] is not None:
        return "inline", row["content"]
    if row["content_uri"] is None:
        return None
    size = int(row["content_bytes"])
    data = b""
    if size:
        try:
            data = content.BLOB_STORE.get_blob(row["content_uri"], row["content_sha256"], size, offset=0, length=size)
        except (BlobIntegrityError, BlobStoreUnavailableError, BlobStoreAccessError) as exc:
            raise content._store_fault(exc) from None
    try:
        return "object", data.decode("utf-8")
    except UnicodeDecodeError:
        raise BlobContentFault(500, "content_not_text", "not_utf8") from None


def serve_row(rows: list[dict[str, Any]], values: dict[str, Any]) -> tuple[list[dict[str, Any]], Served]:
    """The single masked envelope for the resolved stored rows, or no row for an unknown grain key."""
    if not rows:
        return [], Served(0, 0, {})
    row = content._choose(rows)
    offset = values["offset"]
    window = max(1, min(values["max_bytes"], content.MAX_CHUNK_BYTES))
    raw = _raw_text(row)
    base = {
        "encoding": "base64",
        "content_bytes": int(row["content_bytes"]),
        "content_sha256": row["content_sha256"],
        "detector": fingerprint(PROFILE),
        "data_class": UNTRUSTED,
    }
    if raw is None:
        return [
            {
                **base,
                "storage": "omitted",
                "offset": offset,
                "length": 0,
                "masked_bytes": 0,
                "masked_sha256": None,
                "mask_counts": {},
                "data": None,
                "omitted_reason": row["content_omitted_reason"],
            }
        ], Served(1, 0, {})
    storage, text = raw
    masked = mask(text, PROFILE)
    payload = masked.text.encode("utf-8")
    if offset != 0 and offset >= len(payload):
        raise EventReadInputError(ROW_VERB, "offset", "must be below the row's masked_bytes")
    page = payload[offset : offset + window]
    envelope = {
        **base,
        "storage": storage,
        "offset": offset,
        "length": len(page),
        "masked_bytes": len(payload),
        "masked_sha256": _sha256(payload),
        "mask_counts": masked.counts,
        "data": base64.b64encode(page).decode("ascii"),
        "omitted_reason": None,
    }
    return [envelope], Served(1, len(page), dict(masked.counts))


def _cost(row: dict[str, Any]) -> int:
    return len(json.dumps(json.dumps(row)).encode("utf-8"))


def _row_out(row: dict[str, Any]) -> dict[str, Any]:
    ts = row["event_timestamp"]
    return {
        "session_id": row["session_id"],
        "producer": row["producer"],
        "event_id": row["event_id"],
        "parser_version": row["parser_version"],
        "event_timestamp": ts.isoformat() if hasattr(ts, "isoformat") else ts,
        "purpose": row["purpose"],
        "origin": row["origin"],
        "content_bytes": int(row["content_bytes"]) if row["content_bytes"] is not None else None,
        "content_sha256": row["content_sha256"],
        "omitted_reason": row["content_omitted_reason"],
        "storage": "omitted",
        "text": None,
        "masked_bytes": 0,
        "masked_sha256": None,
        "mask_counts": {},
        "too_large": False,
    }


def _fill(out: dict[str, Any], row: dict[str, Any]) -> None:
    raw = _raw_text(row)
    if raw is None:
        return
    storage, text = raw
    masked = mask(text, PROFILE)
    payload = masked.text.encode("utf-8")
    out.update(
        storage=storage,
        text=masked.text,
        masked_bytes=len(payload),
        masked_sha256=_sha256(payload),
        mask_counts=masked.counts,
    )


def serve_session(
    rows: list[dict[str, Any]],
    values: dict[str, Any],
    include_tool_output: bool,
    remaining_ms: Callable[[], float] | None,
) -> tuple[list[dict[str, Any]], Served]:
    """One envelope row {rows, next_offset, total_rows, conflicts, detector} for a page of the session's SQL rows.

    Stops before a row that would pass SESSION_BUDGET_BYTES (measured as the size guard measures) or, once a row has been
    consumed, when the remaining time falls below MIN_REMAINING_MS before a fetch; next_offset counts SQL rows.
    """
    offset = values["offset"]
    out_rows: list[dict[str, Any]] = []
    used = consumed = text_bytes = 0
    counts: dict[str, int] = {}
    for row in rows:
        if row["purpose"] == "tool_output" and not include_tool_output:
            consumed += 1
            continue
        if out_rows and used + int(row["content_bytes"] or 0) > SESSION_BUDGET_BYTES:
            break
        item = _row_out(row)
        if row["content"] is None and row["content_uri"] is not None:
            if remaining_ms is not None and consumed and remaining_ms() < MIN_REMAINING_MS:
                break
            if int(row["content_bytes"] or 0) > SESSION_BUDGET_BYTES:
                item.update(storage="object", too_large=True)
            else:
                _fill(item, row)
        else:
            _fill(item, row)
        if not item["too_large"] and _cost(item) > SESSION_BUDGET_BYTES:
            item.update(text=None, too_large=True)
        cost = _cost(item)
        if used + cost > SESSION_BUDGET_BYTES and out_rows:
            break
        used += cost
        consumed += 1
        out_rows.append(item)
        if item["text"] is not None:
            text_bytes += item["masked_bytes"]
            _add(counts, item["mask_counts"])
    total = int(rows[0]["total_rows"]) if rows else 0
    next_offset = offset + consumed if offset + consumed < total else None
    conflicts = list(rows[0]["conflicted_grain_keys"]) if rows else []
    envelope = {
        "rows": out_rows,
        "next_offset": next_offset,
        "total_rows": total,
        "conflicts": conflicts,
        "detector": fingerprint(PROFILE),
        "data_class": UNTRUSTED,
    }
    return [envelope], Served(len(out_rows), text_bytes, counts)
