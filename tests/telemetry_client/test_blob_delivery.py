"""Mirror test for src/telemetry_client/blob_delivery.py: a fake invoke and a fake put, no network, no botocore."""

from __future__ import annotations

import hashlib
import io
import urllib.error
import urllib.request
from typing import Any

import pytest

from src.telemetry_client import blob_delivery as bd
from tests.fixtures.telemetry_blob_fakes import _s3_error, presigned_url

PROTOCOL = bd.BlobProtocol(1, 100, 10_000, 3)
STARTED = "2026-01-01T12:00:00.000Z"


def row(text: str, ref: str = "sess", **over: Any) -> dict[str, Any]:
    data = text.encode("utf-8")
    base = {
        "session_ref": ref,
        "session_started_at": STARTED,
        "external_ref": "t#" + text[:3],
        "content": text,
        "content_sha256": hashlib.sha256(data).hexdigest(),
        "content_bytes": len(data),
    }
    return {**base, **over}


class Writer:
    """A fake begin_blobs: scripted per call, recording payloads."""

    def __init__(self, *, existing: set[str] | None = None, answers: list[Any] | None = None) -> None:
        self.existing = existing or set()
        self.answers = answers or []
        self.payloads: list[dict[str, Any]] = []

    def __call__(self, payload: dict[str, Any]) -> tuple[int, Any]:
        self.payloads.append(payload)
        if self.answers:
            answer = self.answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            if answer is not None:
                return answer
        items = []
        for item in payload["items"]:
            if item["content_sha256"] in self.existing:
                items.append({"existed": True, "upload": None, "duplicate_of": None})
            else:
                upload = {
                    "method": "PUT",
                    "url": presigned_url(item["content_sha256"][:8]),
                    "headers": {"Content-Length": str(item["content_bytes"]), "x-amz-checksum-sha256": "c2hh"},
                }
                items.append({"existed": False, "upload": upload, "duplicate_of": None})
        return 200, {"ok": True, "items": items}


class Puts:
    def __init__(self, answers: list[Any] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, str], bytes]] = []
        self.answers = answers or []

    def __call__(self, url: str, headers: Any, data: bytes) -> tuple[int, bytes]:
        self.calls.append((url, dict(headers), data))
        if self.answers:
            answer = self.answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer
        return 200, b""


def deliver(rows: list[dict[str, Any]], writer: Writer, puts: Puts, **kw: Any) -> bd.Delivery:
    sleeps: list[float] = kw.setdefault("sleeps", [])
    return bd.deliver_blobs(
        rows,
        project_ref="repo:x",
        invoke=writer,
        protocol=PROTOCOL,
        put=puts,
        sleep=sleeps.append,
        **{k: v for k, v in kw.items() if k != "sleeps"},
    )


def fails(rows: list[dict[str, Any]], writer: Writer, puts: Puts, **kw: Any) -> bd.BlobDeliveryError:
    with pytest.raises(bd.BlobDeliveryError) as caught:
        deliver(rows, writer, puts, **kw)
    return caught.value


def test_protocol_from_describe_refuses_a_missing_or_newer_protocol() -> None:
    good = {
        "blob_protocol": {
            "version": 1,
            "inline_threshold_bytes": 65536,
            "cap_bytes": 8388608,
            "max_items": 200,
            "grant_ttl_seconds": 900,
        }
    }
    assert bd.protocol_from_describe(good) == bd.BlobProtocol(1, 65536, 8388608, 200)
    for body in (
        {},
        {"blob_protocol": None},
        {"blob_protocol": {"version": 2}},
        {"blob_protocol": {"version": 1}},
        {"blob_protocol": {"version": 1, "inline_threshold_bytes": "x", "cap_bytes": 1, "max_items": 1}},
    ):
        with pytest.raises(bd.HandshakeError):
            bd.protocol_from_describe(body)


def test_spilled_rows_lose_content_and_others_pass_through() -> None:
    big, small = row("b" * 150), row("tiny")
    writer, puts = Writer(), Puts()
    out = deliver([small, big], writer, puts)
    assert out.existed == 0 and out.uploaded == 1 and len(puts.calls) == 1
    assert "content" not in out.wire_rows[1] and out.wire_rows[1]["content_sha256"] == big["content_sha256"]
    assert out.wire_rows[0] == small and out.wire_rows[0] is not small and big["content"] == "b" * 150
    assert puts.calls[0][2] == b"b" * 150 and len(writer.payloads) == 1
    assert writer.payloads[0]["items"] == [
        {k: big[k] for k in ("session_ref", "session_started_at", "content_sha256", "content_bytes")}
    ]
    again = deliver([small, big], Writer(existing={big["content_sha256"]}), Puts())
    assert (again.existed, again.uploaded) == (1, 0)
    dup = deliver([big, dict(big, external_ref="t#other")], writer, puts)
    assert dup.uploaded == 1 and len(writer.payloads[1]["items"]) == 1 and all("content" not in r for r in dup.wire_rows)
    assert deliver([small], Writer(), Puts()).wire_rows == [small]


def test_digest_mismatch_is_a_defect_before_any_call() -> None:
    writer, puts = Writer(), Puts()
    for broken in (
        row("c" * 150, content_sha256="0" * 64),
        row("c" * 150, content_bytes=151),
        row("é" * 80, content_bytes=80),
    ):
        err = fails([broken], writer, puts)
        assert (err.kind, err.status) == ("defect", None)
    assert writer.payloads == [] and puts.calls == []


def test_items_are_chunked_by_max_items() -> None:
    rows = [row(f"{i}" * 150) for i in range(7)]
    writer, puts = Writer(), Puts()
    out = deliver(rows, writer, puts)
    assert [len(p["items"]) for p in writer.payloads] == [3, 3, 1] and out.uploaded == 7 and len(puts.calls) == 7
    assert all(
        p["action"] == "begin_blobs" and p["project_ref"] == "repo:x" and p["component"] == "unknown" for p in writer.payloads
    )
    assert deliver(rows[:1], Writer(), Puts(), component="hook-stop").uploaded == 1


def test_put_sends_exactly_the_grant_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class Response:
        status = 200

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

        def read(self) -> bytes:
            return b""

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> Response:
        seen.update(method=request.get_method(), headers=dict(request.header_items()), timeout=timeout, data=request.data)
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    grant = {"Content-Length": "3", "x-amz-checksum-sha256": "c2hh"}
    assert bd.urllib_put(presigned_url(), grant, b"abc") == (200, b"")
    assert seen["method"] == "PUT" and seen["timeout"] == 120 and seen["data"] == b"abc"
    assert {k.lower(): v for k, v in seen["headers"].items()} == {
        "content-type": "application/octet-stream",
        "content-length": "3",
        "x-amz-checksum-sha256": "c2hh",
    }

    xml = _s3_error("AccessDenied")

    def http_error(request: Any, timeout: float) -> Any:
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, io.BytesIO(xml))  # type: ignore[arg-type]

    monkeypatch.setattr(urllib.request, "urlopen", http_error)
    assert bd.urllib_put(presigned_url(), grant, b"abc") == (403, xml)

    def unreachable(request: Any, timeout: float) -> Any:
        raise urllib.error.URLError(request.full_url)

    monkeypatch.setattr(urllib.request, "urlopen", unreachable)
    with pytest.raises(OSError) as caught:
        bd.urllib_put(presigned_url(), grant, b"abc")
    assert "https" not in str(caught.value) and caught.value.__cause__ is None


def test_error_contract() -> None:
    big = [row("d" * 150)]
    # begin_blobs: 400/403 refused, a bare 403 or 407 is the proxy, 500 and 502 are defects with no retry
    for status, body, kind in [
        (400, {"error_type": "bad_request"}, "refused"),
        (403, {"error_type": "tenant_unresolved"}, "refused"),
        (403, "<html/>", "proxy"),
        (407, None, "proxy"),
        (500, {"error_type": "blob_integrity"}, "defect"),
        (502, {}, "defect"),
        (413, {"error_type": "too_many_blobs"}, "defect"),
    ]:
        writer, puts = Writer(answers=[(status, body)]), Puts()
        err = fails(big, writer, puts)
        assert (err.kind, err.status) == (kind, status) and len(writer.payloads) == 1 and puts.calls == []
    # 429 and 503 and transport errors retry three times with backoff, then transient
    for first in ((429, {}), (503, {}), OSError("net"), TimeoutError("slow")):
        sleeps: list[float] = []
        writer = Writer(answers=[first] * 4)
        err = fails(big, writer, Puts(), sleeps=sleeps)
        assert err.kind == "transient" and len(writer.payloads) == 4 and sleeps == [0.5, 1.0, 2.0]
    recovered = Writer(answers=[(503, {}), OSError("net")])
    assert deliver(big, recovered, Puts()).uploaded == 1 and len(recovered.payloads) == 3
    # PUT: 403 re-requests the grant once; a second 403 is refused; an S3-less 403 and a 407 are the proxy
    forbidden = (403, _s3_error("SignatureDoesNotMatch"))
    writer, puts = Writer(), Puts([forbidden, (200, b"")])
    assert deliver(big, writer, puts).uploaded == 1 and len(writer.payloads) == 2 and len(puts.calls) == 2
    writer, puts = Writer(), Puts([forbidden, forbidden])
    err = fails(big, writer, puts)
    assert (err.kind, err.status, err.s3_code) == ("refused", 403, "SignatureDoesNotMatch") and len(writer.payloads) == 2
    exists = Writer(answers=[None, (200, {"ok": True, "items": [{"existed": True, "upload": None, "duplicate_of": None}]})])
    assert deliver(big, exists, Puts([forbidden])).uploaded == 0
    for status, body in [(403, b"<html>blocked</html>"), (407, b"")]:
        err = fails(big, Writer(), Puts([(status, body)]))
        assert (err.kind, err.status) == ("proxy", status)
    # PUT 400 is a defect with no re-request; 5xx and timeouts retry three times, then transient
    for code in ("BadDigest", "InvalidDigest", "IncompleteBody"):
        writer, puts = Writer(), Puts([(400, _s3_error(code))])
        err = fails(big, writer, puts)
        assert (err.kind, err.s3_code) == ("defect", code) and len(writer.payloads) == 1 and len(puts.calls) == 1
    for bad in ((500, b""), (503, _s3_error("SlowDown")), TimeoutError("t")):
        sleeps = []
        puts = Puts([bad] * 4)
        err = fails(big, Writer(), puts, sleeps=sleeps)
        assert err.kind == "transient" and len(puts.calls) == 4 and sleeps == [0.5, 1.0, 2.0]
    assert deliver(big, Writer(), Puts([(500, b""), (200, b"")])).uploaded == 1


def test_errors_never_carry_a_url() -> None:
    big = [row("e" * 150)]
    secret_url = presigned_url("secret-key")
    cases = [
        fails(big, Writer(answers=[(500, {"url": secret_url})]), Puts()),
        fails(big, Writer(), Puts([OSError(secret_url)] * 4)),
        fails(big, Writer(), Puts([(403, _s3_error("AccessDenied")), (403, _s3_error("AccessDenied"))])),
        fails(big, Writer(), Puts([(403, secret_url.encode())])),
        fails([row("f" * 150, content_sha256="1" * 64)], Writer(), Puts()),
    ]
    for err in cases:
        text = str(err) + repr(vars(err)) + repr(err.__cause__)
        for banned in ("https", "X-Amz", "secret-key", "Signature", "0" * 64, "1" * 64):
            assert banned not in text, (err.kind, banned)


def test_an_unexpected_put_status_is_a_defect() -> None:
    err = fails([row("g" * 150)], Writer(), Puts([(302, b"")]))
    assert (err.kind, err.status) == ("defect", 302)
