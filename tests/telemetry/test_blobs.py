"""Mirror test for src/telemetry/blobs.py: the begin_blobs request grammar and the two row predicates."""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any

import pytest

from src.telemetry import blobs
from src.telemetry.request import RequestError

THRESHOLD, CAP = 100, 1000
SHA = "a" * 64
SECRET = "SECRET-VALUE-9f3a"  # pragma: allowlist secret
SOURCE = Path(blobs.__file__)


def item(**over: Any) -> dict[str, Any]:
    base = {
        "session_ref": "sess",
        "session_started_at": "2026-01-01T12:00:00.000Z",
        "content_sha256": SHA,
        "content_bytes": 500,
    }
    return {**base, **over}


def request(*items: dict[str, Any], **over: Any) -> dict[str, Any]:
    return {"action": "begin_blobs", "project_ref": "repo:example", "items": list(items), **over}


def parse(body: dict[str, Any]) -> blobs.BeginBlobsRequest:
    return blobs.parse_begin_blobs_request(body, threshold=THRESHOLD, cap=CAP)


def refused(body: dict[str, Any]) -> RequestError:
    with pytest.raises(RequestError) as caught:
        parse(body)
    assert SECRET not in str(caught.value) + repr(vars(caught.value))
    return caught.value


def test_begin_blobs_allowlist_and_item_shape() -> None:
    parsed = parse(request(item(), component="hook-stop"))
    assert (parsed.project_ref, parsed.component, len(parsed.items)) == ("repo:example", "hook-stop", 1)
    assert parse(request(item())).component == "unknown"
    assert (parsed.items[0].content_bytes, parsed.items[0].session_started_at.year) == (500, 2026)
    assert refused(request(item(), catalog="x")).reason == "unknown_key"
    for bad in ({}, {"items": []}, {"items": "x"}):
        assert refused({"project_ref": "p", **bad}).reason == "items"
    assert refused(request(item(), project_ref="")).reason == "project_ref"
    assert refused(request(item(), component="Bad Name")).reason == "component"
    for broken in ("x", {**item(), "extra": 1}, {k: v for k, v in item().items() if k != "session_ref"}):
        err = refused(request(item(), broken))
        assert (err.error_type, err.reason, err.row) == ("bad_request", "item_shape", 1)
    assert refused(request(item(session_ref=""))).column == "session_ref"


def test_item_bounds_come_from_the_caller() -> None:
    low = refused(request(item(content_bytes=THRESHOLD)))
    assert (low.reason, low.threshold, low.column, low.row) == ("below_threshold", THRESHOLD, "content_bytes", 0)
    assert parse(request(item(content_bytes=THRESHOLD + 1))).items[0].content_bytes == THRESHOLD + 1
    assert parse(request(item(content_bytes=CAP))).items[0].content_bytes == CAP
    high = refused(request(item(), item(content_bytes=CAP + 1)))
    assert (high.reason, high.cap, high.row) == ("above_cap", CAP, 1)
    assert blobs.parse_begin_blobs_request(request(item(content_bytes=50)), threshold=10, cap=60).items[0].content_bytes == 50


def test_items_over_the_cap_are_too_many_blobs() -> None:
    assert len(parse(request(*[item()] * blobs.MAX_BLOB_ITEMS)).items) == blobs.MAX_BLOB_ITEMS
    err = refused(request(*[item()] * (blobs.MAX_BLOB_ITEMS + 1)))
    assert (err.error_type, err.reason, err.threshold) == ("too_many_blobs", "items_per_request", blobs.MAX_BLOB_ITEMS)
    assert blobs.TOO_MANY_BLOBS == "too_many_blobs"


def test_digest_and_size_types() -> None:
    for digest in ("A" * 64, "a" * 63, "a" * 65, "g" * 64, 5, None):
        assert refused(request(item(content_sha256=digest))).reason == "content_sha256"
    for size in ("500", 500.0, True, None):
        assert refused(request(item(content_bytes=size))).reason == "content_bytes"


def test_wire_timestamp_needs_three_fraction_digits() -> None:
    for value in ("2026-01-01T12:00:00Z", "2026-01-01T12:00:00.1Z", "nope", 7):
        err = refused(request(item(session_started_at=value)))
        assert (err.reason, err.column) == ("wire_timestamp", "session_started_at")
    parsed = parse(request(item(session_started_at="2026-01-01T13:00:00.250+01:00")))
    assert parsed.items[0].session_started_at.isoformat() == "2026-01-01T12:00:00.250000+00:00"


def test_errors_never_carry_a_value() -> None:
    cases = [
        request(item(session_ref="a\ud800" + SECRET)),
        request(item(), project_ref="p\x00" + SECRET),
        request(item(), **{SECRET: 1}),
        request(item(content_sha256=SECRET)),
        request(item(content_bytes=SECRET)),
        request(item(session_started_at=SECRET)),
        request(item(), component=SECRET),
        {"project_ref": "p", "items": [{SECRET + "\x00": 1}]},
        request(item(), item(session_ref="x\ud800")),
    ]
    for body in cases:
        err = refused(body)
        assert all(not isinstance(v, str) or SECRET not in v for v in vars(err).values())
    assert refused(request(item(), item(session_ref="x\ud800"))).reason == "invalid_unicode"


def test_hex_to_base64_known_vectors() -> None:
    assert blobs.sha256_hex_to_b64("00" * 32) == "A" * 43 + "="
    empty = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"  # pragma: allowlist secret
    assert blobs.sha256_hex_to_b64(empty) == "47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU="  # pragma: allowlist secret
    assert blobs.sha256_b64_to_hex(blobs.sha256_hex_to_b64(SHA)) == SHA


def test_spill_item_and_blob_row_predicates() -> None:
    assert blobs.spill_item({"content": "x" * 101}, THRESHOLD) and not blobs.spill_item({"content": "x" * 100}, THRESHOLD)
    assert blobs.spill_item({"content": "é" * 51}, THRESHOLD) and not blobs.spill_item({"content": "é" * 50}, THRESHOLD)
    assert not blobs.spill_item({}, THRESHOLD) and not blobs.spill_item({"content": None}, THRESHOLD)
    row = {"content_sha256": SHA, "content_bytes": 500}
    assert blobs.blob_row(row, THRESHOLD, CAP) and blobs.blob_row({**row, "content": None}, THRESHOLD, CAP)
    for over in (
        {"content": "x"},
        {"content_omitted_reason": "oversize"},
        {"content_sha256": "A" * 64},
        {"content_sha256": None},
        {"content_bytes": THRESHOLD},
        {"content_bytes": CAP + 1},
        {"content_bytes": True},
        {"content_bytes": "500"},
    ):
        assert not blobs.blob_row({**row, **over}, THRESHOLD, CAP), over
    assert blobs.blob_row({**row, "content_bytes": CAP}, THRESHOLD, CAP)


def test_module_imports_only_stdlib_timestamps_and_request() -> None:
    allowed = ("src.telemetry.timestamps", "src.telemetry.request")
    for node in ast.walk(ast.parse(SOURCE.read_text(encoding="utf-8"))):
        names = [a.name for a in node.names] if isinstance(node, ast.Import) else []
        names += [node.module or ""] if isinstance(node, ast.ImportFrom) else []
        for name in names:
            assert name.split(".")[0] in sys.stdlib_module_names or name in allowed, name
