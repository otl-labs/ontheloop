"""Producer-side claim-check delivery (T2.36, slice 2a-2, plan 4): the helper slice 3b's hook imports and the deploy smoke
gate uses first.

deliver_blobs sends every transcript row whose content exceeds the writer's inline threshold through begin_blobs, PUTs each
granted object once under the writer-issued grant (urllib, the grant's headers exactly, no other x-amz-* header) and returns
the rows with the spilled content removed, ready for append_telemetry. It reaches the writer only through the injected
`invoke(payload) -> (status, body)`. Idempotent: a re-delivery answers `existed` and uploads nothing.

Errors are BlobDeliveryError(kind, status, s3_code); kind is refused (begin_blobs 400/403, a PUT 403 after one grant
re-request), defect (a digest or length mismatch before any call, begin_blobs 500 or an unexpected answer, a PUT 400
BadDigest/InvalidDigest/IncompleteBody; never retried), transient (429/503/transport errors and PUT 5xx/timeouts after three
retries) or proxy (a 403 or 407 whose body is not S3 or writer JSON: the network, not the route). An error never carries a
URL, key, digest or payload (Decision 101).
"""

from __future__ import annotations

import hashlib
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from src.telemetry.blobs import BLOB_PROTOCOL_VERSION, spill_item

PUT_TIMEOUT_SECONDS = 120
RETRIES = 3
_BACKOFF_SECONDS = 0.5
_S3_CODE = re.compile(rb"<Code>([A-Za-z]{1,64})</Code>")
_DEFECT_CODES = frozenset({"BadDigest", "InvalidDigest", "IncompleteBody", "InvalidRequest", "MalformedHeader"})

Invoke = Callable[[dict[str, Any]], tuple[int, Any]]
Put = Callable[[str, Mapping[str, str], bytes], tuple[int, bytes]]


class HandshakeError(Exception):
    """describe does not advertise a blob protocol this helper speaks."""


class BlobDeliveryError(Exception):
    def __init__(self, kind: str, status: int | None = None, s3_code: str | None = None) -> None:
        self.kind = kind
        self.status = status
        self.s3_code = s3_code
        located = {"status": status, "s3_code": s3_code}
        super().__init__(f"blob delivery {kind}" + "".join(f" {k}={v}" for k, v in located.items() if v is not None))


@dataclass(frozen=True)
class BlobProtocol:
    version: int
    inline_threshold_bytes: int
    cap_bytes: int
    max_items: int


@dataclass(frozen=True)
class Delivery:
    wire_rows: list[dict[str, Any]]
    existed: int
    uploaded: int


def protocol_from_describe(body: Mapping[str, Any]) -> BlobProtocol:
    advertised = body.get("blob_protocol") if isinstance(body, Mapping) else None
    if not isinstance(advertised, Mapping):
        raise HandshakeError("describe advertises no blob_protocol")
    if advertised.get("version") != BLOB_PROTOCOL_VERSION:
        raise HandshakeError(f"blob_protocol version is not {BLOB_PROTOCOL_VERSION}")
    try:
        return BlobProtocol(
            BLOB_PROTOCOL_VERSION,
            int(advertised["inline_threshold_bytes"]),
            int(advertised["cap_bytes"]),
            int(advertised["max_items"]),
        )
    except (KeyError, TypeError, ValueError):
        raise HandshakeError("blob_protocol is malformed") from None


def urllib_put(url: str, headers: Mapping[str, str], data: bytes) -> tuple[int, bytes]:
    """PUT data under a grant; (status, body). A transport failure is raised as OSError/TimeoutError, never with the URL."""
    request = urllib.request.Request(url, data=data, method="PUT")  # noqa: S310 -- https grant from the writer
    request.add_header("Content-Type", "application/octet-stream")
    for name, value in headers.items():
        request.add_header(name, value)
    try:
        with urllib.request.urlopen(request, timeout=PUT_TIMEOUT_SECONDS) as response:  # noqa: S310
            return int(response.status), response.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read()
    except urllib.error.URLError:
        raise OSError("blob PUT transport failure") from None


def s3_error_code(body: bytes) -> str | None:
    found = _S3_CODE.search(body)
    return found.group(1).decode("ascii") if found else None


def _is_writer_json(body: Any) -> bool:
    return isinstance(body, Mapping) and "error_type" in body


def _begin(invoke: Invoke, payload: dict[str, Any], sleep: Callable[[float], None]) -> Mapping[str, Any]:
    for attempt in range(RETRIES + 1):
        try:
            status, body = invoke(payload)
        except (OSError, TimeoutError):
            status, body = 0, None
        if status == 200 and isinstance(body, Mapping) and body.get("ok"):
            return body
        if status in (0, 429, 503):
            if attempt < RETRIES:
                sleep(_BACKOFF_SECONDS * 2**attempt)
                continue
            raise BlobDeliveryError("transient", status or None)
        if status in (403, 407) and not _is_writer_json(body):
            raise BlobDeliveryError("proxy", status)
        if status in (400, 403):
            raise BlobDeliveryError("refused", status)
        raise BlobDeliveryError("defect", status)
    raise BlobDeliveryError("transient")  # pragma: no cover -- the loop returns or raises


def _put_once(put: Put, upload: Mapping[str, Any], data: bytes, sleep: Callable[[float], None]) -> tuple[int, bytes]:
    for attempt in range(RETRIES + 1):
        try:
            status, body = put(str(upload["url"]), upload["headers"], data)
        except (OSError, TimeoutError):
            status, body = 0, b""
        if status == 0 or status >= 500:
            if attempt < RETRIES:
                sleep(_BACKOFF_SECONDS * 2**attempt)
                continue
            raise BlobDeliveryError("transient", status or None, s3_error_code(body))
        return status, body
    raise BlobDeliveryError("transient")  # pragma: no cover


def _classify_put(status: int, body: bytes) -> None:
    code = s3_error_code(body)
    if status in (403, 407) and code is None:
        raise BlobDeliveryError("proxy", status)
    if status == 400 or code in _DEFECT_CODES:
        raise BlobDeliveryError("defect", status, code)
    if status >= 400:
        raise BlobDeliveryError("refused", status, code)


def _upload(
    item: Mapping[str, Any],
    upload: Mapping[str, Any],
    data: bytes,
    *,
    begin: Callable[[], Mapping[str, Any]],
    put: Put,
    sleep: Callable[[float], None],
) -> bool:
    """PUT once; on an S3 403 re-request the grant one time. True when an object was uploaded (False: it now exists)."""
    status, body = _put_once(put, upload, data, sleep)
    if status < 300:
        return True
    if status == 403 and s3_error_code(body) is not None:
        entry = begin()
        again = entry["items"][0]
        if again.get("existed"):
            return False
        status, body = _put_once(put, again["upload"], data, sleep)
        if status < 300:
            return True
    _classify_put(status, body)
    raise BlobDeliveryError("defect", status)


def _key(row: Mapping[str, Any]) -> tuple[Any, Any, Any]:
    return row["session_ref"], row["session_started_at"], row["content_sha256"]


def _check_integrity(row: Mapping[str, Any]) -> bytes:
    data = str(row["content"]).encode("utf-8")
    if row.get("content_bytes") != len(data) or row.get("content_sha256") != hashlib.sha256(data).hexdigest():
        raise BlobDeliveryError("defect")
    return data


def deliver_blobs(
    rows: Sequence[Mapping[str, Any]],
    *,
    project_ref: str,
    invoke: Invoke,
    protocol: BlobProtocol,
    component: str = "unknown",
    max_workers: int = 4,
    put: Put | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Delivery:
    """Spill every row whose content exceeds the protocol threshold; return the rows ready for append_telemetry."""
    transport = put or urllib_put
    spilled = [index for index, row in enumerate(rows) if spill_item(row, protocol.inline_threshold_bytes)]
    payloads = {index: _check_integrity(rows[index]) for index in spilled}
    distinct: dict[tuple[Any, Any, Any], int] = {}
    for index in spilled:
        distinct.setdefault(_key(rows[index]), index)
    firsts = list(distinct.values())

    def request(group: Sequence[int]) -> dict[str, Any]:
        items = [
            {
                "session_ref": rows[i]["session_ref"],
                "session_started_at": rows[i]["session_started_at"],
                "content_sha256": rows[i]["content_sha256"],
                "content_bytes": rows[i]["content_bytes"],
            }
            for i in group
        ]
        return {"action": "begin_blobs", "project_ref": project_ref, "component": component, "items": items}

    jobs: list[tuple[int, Mapping[str, Any]]] = []
    existed = 0
    for start in range(0, len(firsts), protocol.max_items):
        group = firsts[start : start + protocol.max_items]
        answer = _begin(invoke, request(group), sleep)
        for index, entry in zip(group, answer["items"], strict=True):
            if entry.get("existed"):
                existed += 1
            elif entry.get("upload"):
                jobs.append((index, entry["upload"]))

    def run(job: tuple[int, Mapping[str, Any]]) -> bool:
        index, upload = job
        item = request([index])["items"][0]
        return _upload(
            item,
            upload,
            payloads[index],
            begin=lambda: _begin(invoke, request([index]), sleep),
            put=transport,
            sleep=sleep,
        )

    uploaded = 0
    if jobs:
        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(jobs)))) as pool:
            uploaded = sum(1 for done in pool.map(run, jobs) if done)
    wire = [
        {k: v for k, v in row.items() if k != "content"} if index in payloads else dict(row) for index, row in enumerate(rows)
    ]
    return Delivery(wire, existed, uploaded)


__all__ = [
    "BlobDeliveryError",
    "BlobProtocol",
    "Delivery",
    "HandshakeError",
    "deliver_blobs",
    "protocol_from_describe",
    "s3_error_code",
    "urllib_put",
]
