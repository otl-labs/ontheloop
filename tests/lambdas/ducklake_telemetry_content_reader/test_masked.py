"""serve_row and serve_session: mask then page, budget and time stops, too_large rows, closed-set faults."""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest

from src.common import ducklake_event_content as content
from src.common.ducklake_event_content import BlobContentFault
from src.common.ducklake_event_reads import EventReadInputError
from src.common.ducklake_telemetry_blobstore import BlobIntegrityError, BlobStoreAccessError, BlobStoreUnavailableError
from src.lambdas.ducklake_telemetry_content_reader import masked

pytestmark = pytest.mark.unit


def inline_row(text: str, versions: int = 1) -> dict[str, Any]:
    return {
        "content": text,
        "content_uri": None,
        "content_sha256": "0" * 64,
        "content_bytes": len(text.encode("utf-8")),
        "content_omitted_reason": None,
        "compared_versions": versions,
    }


_TOKEN = "ghp_" + "b" * 40


class _Store:
    def __init__(self, data: bytes | Exception) -> None:
        self.data, self.calls = data, []

    def get_blob(self, uri: str, sha: str, size: int, *, offset: int, length: int) -> bytes:
        self.calls.append((offset, length))
        if isinstance(self.data, Exception):
            raise self.data
        return self.data


def _blob_row(size: int) -> dict[str, Any]:
    return {**inline_row(""), "content": None, "content_uri": "s3://b/k", "content_bytes": size}


def _values(**over: Any) -> dict[str, Any]:
    return {"offset": 0, "max_bytes": 1000, **over}


def _page(envelope: dict[str, Any]) -> bytes:
    return base64.b64decode(envelope["data"])


def test_a_value_straddling_the_page_boundary_is_masked_before_paging() -> None:
    text = "x" * 20 + " " + _TOKEN + " " + "y" * 20
    pages, offset = [], 0
    while True:
        (env,), _ = masked.serve_row([inline_row(text)], _values(offset=offset, max_bytes=30))
        pages.append(_page(env))
        offset += env["length"]
        if offset >= env["masked_bytes"]:
            break
    whole = b"".join(pages).decode()
    assert _TOKEN not in whole and all(_TOKEN[:12].encode() not in p for p in pages) and "[REDACTED:GITHUB_TOKEN]" in whole
    assert len(pages) > 1 and max(len(p) for p in pages) <= 30


def test_offset_past_masked_bytes_is_400_and_empty_row_is_served() -> None:
    with pytest.raises(EventReadInputError):
        masked.serve_row([inline_row("abc")], _values(offset=3))
    (env,), served = masked.serve_row([inline_row("")], _values())
    assert env["length"] == 0 and env["masked_bytes"] == 0 and served.rows == 1
    assert masked.serve_row([], _values()) == ([], masked.Served(0, 0, {}))


def test_conflicting_rows_and_omitted_rows() -> None:
    with pytest.raises(BlobContentFault) as err:
        masked.serve_row([inline_row("a", versions=2)], _values())
    assert err.value.error_type == "grain_conflict" and err.value.status == 500
    omitted = {**inline_row(""), "content": None, "content_omitted_reason": "too_large"}
    (env,), _ = masked.serve_row([omitted], _values())
    assert env["storage"] == "omitted" and env["data"] is None and env["omitted_reason"] == "too_large"


def test_non_utf8_blob_is_content_not_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(content, "BLOB_STORE", _Store(b"\xff\xfe\x00"))
    with pytest.raises(BlobContentFault) as err:
        masked.serve_row([_blob_row(3)], _values())
    assert (err.value.status, err.value.error_type) == (500, "content_not_text")


def test_blob_rows_are_read_whole_and_zero_length_blobs_make_no_call(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _Store(f"see {_TOKEN}".encode())
    monkeypatch.setattr(content, "BLOB_STORE", store)
    (env,), _ = masked.serve_row([_blob_row(len(f"see {_TOKEN}"))], _values())
    assert store.calls == [(0, len(f"see {_TOKEN}"))] and env["storage"] == "object" and _TOKEN not in _page(env).decode()
    empty = _Store(b"")
    monkeypatch.setattr(content, "BLOB_STORE", empty)
    (env,), _ = masked.serve_row([_blob_row(0)], _values())
    assert empty.calls == [] and env["length"] == 0


@pytest.mark.parametrize(
    ("raised", "status", "error_type"),
    [
        (BlobStoreUnavailableError("x"), 503, "blob_unavailable"),
        (BlobIntegrityError("x"), 500, "blob_integrity"),
        (BlobStoreAccessError("x"), 500, "blob_misconfigured"),
    ],
)
def test_blob_faults_are_typed(monkeypatch: pytest.MonkeyPatch, raised: Exception, status: int, error_type: str) -> None:
    monkeypatch.setattr(content, "BLOB_STORE", _Store(raised))
    with pytest.raises(BlobContentFault) as err:
        masked.serve_row([_blob_row(4)], _values())
    assert (err.value.status, err.value.error_type) == (status, error_type)


def _srow(i: int, text: str | None = "hi", purpose: str = "prompt", **over: Any) -> dict[str, Any]:
    return {
        "session_id": "S",
        "producer": "claude_code",
        "event_id": f"E{i}",
        "parser_version": 3,
        "event_timestamp": "2026-10-01T00:00:00+00:00",
        "source_ordinal": i,
        "purpose": purpose,
        "origin": "user",
        "content": text,
        "content_uri": None,
        "content_sha256": "0" * 64,
        "content_bytes": len(text or ""),
        "content_omitted_reason": None,
        "total_rows": over.pop("total", 10),
        "conflicted_grain_keys": [],
        **over,
    }


def test_session_order_tool_output_filter_and_next_offset_counts_sql_rows() -> None:
    rows = [_srow(0), _srow(1, purpose="tool_output"), _srow(2)]
    (env,), served = masked.serve_session(rows, _values(), False, None)
    assert [r["event_id"] for r in env["rows"]] == ["E0", "E2"] and env["next_offset"] == 3 and served.rows == 2
    (env,), _ = masked.serve_session(rows, _values(offset=2), True, None)
    assert [r["event_id"] for r in env["rows"]] == ["E0", "E1", "E2"] and env["next_offset"] == 5
    (env,), _ = masked.serve_session([_srow(0, total=3), _srow(1, total=3), _srow(2, total=3)], _values(), True, None)
    assert env["next_offset"] is None and env["total_rows"] == 3


def test_budget_stop_keeps_the_full_response_under_the_cap() -> None:
    text = "é" * 20_000
    rows = [_srow(i, text, total=500) for i in range(400)]
    (env,), _ = masked.serve_session(rows, _values(), True, None)
    assert 0 < len(env["rows"]) < 400 and env["next_offset"] == len(env["rows"])
    response = {"statusCode": 200, "body": json.dumps({"rows": [env]})}
    assert len(json.dumps(response).encode()) < 6_000_000


def test_a_single_oversized_row_is_returned_without_text() -> None:
    big = "z" * 6_000_000
    (env,), _ = masked.serve_session([_srow(0, big, total=2), _srow(1, total=2)], _values(), True, None)
    first, second = env["rows"]
    assert first["too_large"] is True and first["text"] is None and second["text"] == "hi" and env["next_offset"] is None


def test_oversized_blob_rows_are_not_fetched(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _Store(b"")
    monkeypatch.setattr(content, "BLOB_STORE", store)
    row = _srow(0, None, content_uri="s3://b/k", content_bytes=masked.SESSION_BUDGET_BYTES + 1, total=1)
    (env,), _ = masked.serve_session([row], _values(), True, None)
    assert env["rows"][0]["too_large"] is True and store.calls == []


def test_low_remaining_time_stops_before_a_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _Store(b"text")
    monkeypatch.setattr(content, "BLOB_STORE", store)
    blob = lambda i: _srow(i, None, content_uri="s3://b/k", content_bytes=4, total=5)  # noqa: E731
    (env,), _ = masked.serve_session([blob(0), blob(1), blob(2)], _values(), True, lambda: masked.MIN_REMAINING_MS - 1)
    assert len(env["rows"]) == 1 and env["next_offset"] == 1 and len(store.calls) == 1


def test_a_blob_row_that_cannot_fit_the_remaining_budget_is_not_fetched(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _Store(b"a" * 3_000_000)
    monkeypatch.setattr(content, "BLOB_STORE", store)
    rows = [_srow(i, None, content_uri="s3://b/k", content_bytes=3_000_000, total=2) for i in range(2)]
    (env,), _ = masked.serve_session(rows, _values(), True, None)
    assert len(env["rows"]) == 1 and env["next_offset"] == 1 and len(store.calls) == 1


def test_session_counts_masks_and_serves_omitted_rows() -> None:
    rows = [
        _srow(0, f"token {_TOKEN}", total=2),
        _srow(1, None, total=2, content_omitted_reason="not_stored"),
    ]
    (env,), served = masked.serve_session(rows, _values(), True, None)
    first, second = env["rows"]
    assert sum(served.mask_counts.values()) >= 1 and first["mask_counts"]
    assert second["storage"] == "omitted" and second["text"] is None and second["omitted_reason"] == "not_stored"
