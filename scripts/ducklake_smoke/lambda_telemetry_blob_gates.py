"""Claim-check (blob) live vectors for the --lambda-telemetry-smoke gate, run against the smoke twin (T2.36, Decision 236).

Called by lambda_telemetry_smoke after part 2's append vectors with part 1's registered synthetic project_ref. The delivery
vectors run the producer helper (src.telemetry_client.blob_delivery) over the gate's own SigV4 transport, so the helper is
proven by its first user; the S3 reject vectors PUT directly under a real grant. Each vector is a loud SmokeTestFailure
(Decision 55).

No vector reads through a reader: no deployed function serves transcript_content after slice 2c (Decision 236), so the
content read-back is retired (rec-4699 owns re-adding one with the content reader) and blob presence keeps its writer-side
proof through telemetry_probe's blob_objects.

Output carries markers, integer measurements (blob_grant_ms, blob_put_ms, blob_append_ms) and nothing else: never a
presigned URL, X-Amz-* name, key, digest, host, account id or project_ref, failure text included. A delivery failure names
the helper's kind (refused, defect, transient or proxy) and status, so an environment block (proxy) is told from a route
fault.
"""

from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import parse_qs, urlparse

from scripts.ducklake_smoke import telemetry_blob_vectors as vec
from scripts.ducklake_smoke.lambda_telemetry_append_gates import _Gate
from scripts.ducklake_smoke.lambda_telemetry_gates import _ULID, _body, _diagnostics, _error_type, _expect_ok, _fail
from src.telemetry_client import blob_delivery as bd

_SIGNED_HEADERS = "content-length;host;x-amz-checksum-sha256"
_TABLE = "telemetry_transcripts"
_CONCURRENT_BEGINS = 3


class _Timed:
    """The helper's transports, instrumented: grant and PUT time in integer milliseconds, grants inspected in memory."""

    def __init__(self, gate: _Gate) -> None:
        self.gate = gate
        self.grant_ms = 0
        self.put_ms = 0

    def invoke(self, payload: dict[str, Any]) -> tuple[int, Any]:
        start = time.perf_counter()
        resp = self.gate.post(payload)
        self.grant_ms += int((time.perf_counter() - start) * 1000)
        body = _body(resp)
        for item in body.get("items") or []:
            upload = item.get("upload") if isinstance(item, dict) else None
            if isinstance(upload, dict):
                _check_signed_headers(str(upload.get("url", "")))
        return resp.status_code, body

    def put(self, url: str, headers: Any, data: bytes) -> tuple[int, bytes]:
        start = time.perf_counter()
        try:
            return bd.urllib_put(url, headers, data)
        finally:
            self.put_ms += int((time.perf_counter() - start) * 1000)


def _check_signed_headers(url: str) -> None:
    signed = parse_qs(urlparse(url).query).get("X-Amz-SignedHeaders", [""])[0]
    if signed != _SIGNED_HEADERS:
        raise _fail("a grant does not sign exactly content-length, host and the sha256 checksum")


def _deliver(timed: _Timed, project_ref: str, protocol: bd.BlobProtocol, rows: list[dict[str, Any]]) -> bd.Delivery:
    try:
        return bd.deliver_blobs(rows, project_ref=project_ref, invoke=timed.invoke, protocol=protocol, put=timed.put)
    except bd.BlobDeliveryError as exc:
        raise _fail(f"blob delivery failed kind={exc.kind} status={exc.status} s3_code={exc.s3_code}") from None


def _blob_objects(gate: _Gate, project_ref: str, tag: str) -> int:
    objects = gate.probe(project_ref, tag).get("blob_objects")
    count = objects.get("count") if isinstance(objects, dict) else None
    if not isinstance(count, int):
        raise _fail("telemetry_probe blob_objects missing")
    return count


def _transcripts(body: dict[str, Any], check: str) -> dict[str, Any]:
    tables = body.get("tables")
    entry = tables.get(_TABLE) if isinstance(tables, dict) else None
    if not isinstance(entry, dict):
        raise _fail(f"{check}: the response has no transcripts entry")
    return entry


def _check_protocol(gate: _Gate) -> bd.BlobProtocol:
    body = _expect_ok(gate.post({"action": "describe"}), "describe")
    try:
        protocol = bd.protocol_from_describe(body)
    except bd.HandshakeError:
        raise _fail("describe does not advertise blob_protocol version 1") from None
    if protocol.inline_threshold_bytes != vec.THRESHOLD_BYTES or protocol.cap_bytes != vec.CAP_BYTES:
        raise _fail("blob_protocol threshold or cap differ from the bundled projection's")
    schema = body.get("schema")
    tables = {"telemetry_sessions", "telemetry_observations", "telemetry_transcripts", "telemetry_agents"}
    if not isinstance(schema, dict) or set(schema) != tables:
        raise _fail("describe schema view is not the four event tables")
    for entry in schema.values():
        if not (isinstance(entry, dict) and entry.get("caller_known_columns") and entry.get("fingerprint")):
            raise _fail("describe schema view is missing columns or a fingerprint")
    print("TELEMETRY_BLOB_PROTOCOL_OK")
    return protocol


def _check_delivered(
    gate: _Gate, timed: _Timed, project_ref: str, protocol: bd.BlobProtocol
) -> tuple[int, dict[str, Any], str]:
    tag = uuid.uuid4().hex
    row = vec.blob_transcript_row(tag, 0, gate.started, vec.fresh_content(tag))
    delivery = _deliver(timed, project_ref, protocol, [row])
    if (delivery.existed, delivery.uploaded) != (0, 1):
        raise _fail("a fresh payload was not granted and uploaded exactly once")
    if "content" in delivery.wire_rows[0]:
        raise _fail("delivery left content on a spilled row")
    start = time.perf_counter()
    body = _expect_ok(gate.post(vec.append_body(project_ref, delivery.wire_rows)), "append of a delivered blob row")
    append_ms = int((time.perf_counter() - start) * 1000)
    if _transcripts(body, "delivered append").get("inserted") != 1:
        raise _fail("the delivered blob row was not inserted")
    if _blob_objects(gate, project_ref, tag) != 1:
        raise _fail("the session's blob prefix does not hold exactly one object")
    print("TELEMETRY_BLOB_DELIVERED_OK")
    return append_ms, {"row": row, "wire": delivery.wire_rows}, tag


def _check_resend(
    gate: _Gate, timed: _Timed, project_ref: str, protocol: bd.BlobProtocol, sent: dict[str, Any], tag: str
) -> None:
    delivery = _deliver(timed, project_ref, protocol, [sent["row"]])
    if (delivery.existed, delivery.uploaded) != (1, 0):
        raise _fail("a re-delivery uploaded again or did not find the object")
    body = _expect_ok(gate.post(vec.append_body(project_ref, delivery.wire_rows)), "re-send of a delivered blob row")
    if _transcripts(body, "re-send").get("inserted") != 0:
        raise _fail("re-sending a delivered blob row inserted rows")
    if _blob_objects(gate, project_ref, tag) != 1:
        raise _fail("a re-delivery changed the session's object count")
    print("TELEMETRY_BLOB_RESEND_EXISTED_OK")


def _grant_for(gate: _Gate, project_ref: str, row: dict[str, Any]) -> dict[str, Any]:
    body = _expect_ok(gate.post(vec.begin_body(project_ref, [vec.item_for(row)])), "begin_blobs")
    items = body.get("items")
    upload = items[0].get("upload") if isinstance(items, list) and items and isinstance(items[0], dict) else None
    if not isinstance(upload, dict) or not upload.get("url"):
        raise _fail("begin_blobs did not grant a fresh payload")
    _check_signed_headers(str(upload["url"]))
    return upload


def _check_unverified(gate: _Gate, project_ref: str) -> None:
    tag = uuid.uuid4().hex
    row = vec.blob_transcript_row(tag, 0, gate.started, vec.fresh_content(tag))
    _grant_for(gate, project_ref, row)
    before = gate.probe(project_ref, tag).get("grain_counts")
    resp = gate.post(vec.append_body(project_ref, [vec.wire_row(row)]))
    body = _body(resp)
    if resp.status_code != 422 or _error_type(resp) != "blob_not_verified":
        raise _fail(
            f"unverified append status={resp.status_code} error_type={_error_type(resp)} {_diagnostics(resp)} (want 422)"
        )
    if body.get("table") != _TABLE or body.get("row") != 0 or body.get("reason") != "missing":
        raise _fail("blob_not_verified did not name the table, row and reason missing")
    if gate.probe(project_ref, tag).get("grain_counts") != before:
        raise _fail("an unverified append changed the stored rows")
    print("TELEMETRY_BLOB_UNVERIFIED_OK")


def _check_size_mismatch(gate: _Gate, project_ref: str, sent: dict[str, Any], tag: str) -> None:
    before = gate.probe(project_ref, tag).get("grain_counts")
    resp = gate.post(vec.append_body(project_ref, [vec.misdeclared(sent["wire"][0])]))
    if resp.status_code != 422 or _error_type(resp) != "blob_not_verified":
        raise _fail(
            f"size-mismatch append status={resp.status_code} error_type={_error_type(resp)} {_diagnostics(resp)} (want 422)"
        )
    body = _body(resp)
    if body.get("table") != _TABLE or body.get("row") != 0 or body.get("reason") != "size_mismatch":
        raise _fail("size-mismatch append did not name the table, row and reason size_mismatch")
    if gate.probe(project_ref, tag).get("grain_counts") != before:
        raise _fail("a size-mismatch append changed the stored rows")
    resp = gate.post(vec.begin_body(project_ref, [vec.misdeclared(vec.item_for(sent["row"]))]))
    if resp.status_code != 422 or _error_type(resp) != "blob_not_verified":
        raise _fail(
            f"size-mismatch begin_blobs status={resp.status_code} error_type={_error_type(resp)} "
            f"{_diagnostics(resp)} (want 422)"
        )
    body = _body(resp)
    if body.get("row") != 0 or body.get("reason") != "size_mismatch":
        raise _fail("size-mismatch begin_blobs did not name the row and reason size_mismatch")
    print("TELEMETRY_BLOB_SIZE_MISMATCH_OK")


def _check_s3_rejects(gate: _Gate, project_ref: str) -> None:
    tag = uuid.uuid4().hex
    content = vec.fresh_content(tag)
    row = vec.blob_transcript_row(tag, 0, gate.started, content)
    upload = _grant_for(gate, project_ref, row)
    status, answer = bd.urllib_put(str(upload["url"]), upload["headers"], vec.tamper_same_length(content).encode("utf-8"))
    if (status, bd.s3_error_code(answer)) != (400, "BadDigest"):
        raise _fail(f"same-length wrong-bytes PUT status={status} (want S3 400 BadDigest)")
    without_length = {k: v for k, v in upload["headers"].items() if k.lower() != "content-length"}
    status, answer = bd.urllib_put(str(upload["url"]), without_length, vec.different_length(content).encode("utf-8"))
    if (status, bd.s3_error_code(answer)) != (403, "SignatureDoesNotMatch"):
        raise _fail(f"different-length PUT status={status} (want S3 403 SignatureDoesNotMatch)")
    print("TELEMETRY_BLOB_S3_REJECTS_OK")


def _check_bounds(gate: _Gate, project_ref: str) -> None:
    tag = uuid.uuid4().hex
    for vector in vec.bounds_vectors(project_ref, tag, gate.started):
        if vector.name == "unregistered_drill_ref":
            continue
        resp = gate.post(vector.build())
        if resp.status_code != vector.status or _error_type(resp) != vector.error_type:
            raise _fail(
                f"{vector.name} status={resp.status_code} error_type={_error_type(resp)} {_diagnostics(resp)} "
                f"(want {vector.status} {vector.error_type})"
            )
        if vector.field is not None and _body(resp).get(vector.field) != vector.value:
            raise _fail(f"{vector.name} did not name {vector.field}={vector.value}")
    print("TELEMETRY_BLOB_BOUNDS_OK")


def _check_autoregister(gate: _Gate) -> None:
    probe = _expect_ok(gate.post({"action": "telemetry_probe"}), "telemetry_probe")
    if probe.get("data_path_is_smoke") is not True:
        raise _fail("blob auto-registration requires the smoke binding (2c must replace it before production)")
    tag = uuid.uuid4().hex
    drill = next(v for v in vec.bounds_vectors("synthetic:unused", tag, gate.started) if v.name == "unregistered_drill_ref")
    resp = gate.post(drill.build())
    if resp.status_code != 403 or _error_type(resp) != "project_unregistered":
        raise _fail(
            f"fresh drill ref status={resp.status_code} error_type={_error_type(resp)} (want 403 project_unregistered)"
        )
    ref = f"repo:deploy-smoke-blob-{tag}"
    row = vec.blob_transcript_row(tag, 0, gate.started, vec.fresh_content(tag))
    payload = vec.begin_body(ref, [vec.item_for(row)])
    with ThreadPoolExecutor(max_workers=_CONCURRENT_BEGINS) as pool:
        responses = list(pool.map(lambda _i: gate.post(dict(payload)), range(_CONCURRENT_BEGINS)))
    bodies = [_expect_ok(r, "concurrent first-sight begin_blobs") for r in responses]
    ids = {b.get("project_id") for b in bodies}
    if len(ids) != 1 or not all(isinstance(i, str) and _ULID.match(i) for i in ids):
        raise _fail(f"concurrent first-sight begin_blobs returned {len(ids)} project ids (Decision 200 reversal condition)")
    if sum(1 for b in bodies if b.get("project_registration") == "created") != 1:
        raise _fail("concurrent first-sight begin_blobs did not create exactly one project")
    print("TELEMETRY_BLOB_AUTOREGISTER_OK")


def lambda_telemetry_blob(url: str, project_ref: str, *, profile: str | None = None, region: str = "eu-west-2") -> None:
    """Run plan 4's claim-check vectors against the registered synthetic project_ref (never printed)."""
    gate = _Gate(url, profile, region)
    timed = _Timed(gate)
    protocol = _check_protocol(gate)
    append_ms, sent, tag = _check_delivered(gate, timed, project_ref, protocol)
    _check_resend(gate, timed, project_ref, protocol, sent, tag)
    _check_unverified(gate, project_ref)
    _check_size_mismatch(gate, project_ref, sent, tag)
    _check_s3_rejects(gate, project_ref)
    _check_bounds(gate, project_ref)
    _check_autoregister(gate)
    print(f"blob_grant_ms={timed.grant_ms}")
    print(f"blob_put_ms={timed.put_ms}")
    print(f"blob_append_ms={append_ms}")
