"""Hermetic proof that the live blob vectors are right before they ship: the fresh row is a spill item under the bundled
projection, and each negative case fails through the real handler, edge, grammar and adapter over the in-memory store with
exactly its expected status, error_type and reason or rule."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from scripts.ducklake_smoke import telemetry_append_vectors as base
from scripts.ducklake_smoke import telemetry_blob_vectors as vec
from src.common.ducklake_scd2_schema import load_field_semantics
from src.lambdas.ducklake_telemetry_writer import telemetry_edge as edge
from src.telemetry.blobs import MAX_BLOB_ITEMS, blob_row, spill_item
from tests.fixtures.telemetry_append_fakes import LiveFake

REF = "synthetic:deploy-smoke-hermetic"
PROJECT = "01BX5ZZKBKACTAV9WEVGEMMVRY"
TAG = "tagfortest"
STARTED = (datetime.now(timezone.utc) - timedelta(seconds=120)).replace(microsecond=123000)


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch) -> LiveFake:
    fake = LiveFake().activate(monkeypatch)
    fake.register(REF, PROJECT)
    return fake


def send(live: LiveFake, body: dict[str, Any]) -> Any:
    return live.invoke("https://fake.invalid", body)


def test_fresh_row_is_a_spill_item() -> None:
    specs, _ = edge._specs_and_shapes(load_field_semantics())
    rule = edge.payload_rule(specs)
    assert (vec.THRESHOLD_BYTES, vec.CAP_BYTES) == (rule.threshold, rule.cap) and base.INLINE_THRESHOLD_BYTES == rule.threshold
    first = vec.blob_transcript_row(TAG, 0, STARTED, vec.fresh_content(TAG))
    second = vec.blob_transcript_row(TAG, 0, STARTED, vec.fresh_content(TAG))
    assert spill_item(first, rule.threshold) and first["content_bytes"] == vec.BLOB_BYTES
    assert first["content_sha256"] != second["content_sha256"], "every run must create a new content-addressed key"
    wire = vec.wire_row(first)
    assert "content" not in wire and blob_row(wire, rule.threshold, rule.cap) and not spill_item(wire, rule.threshold)
    assert vec.item_for(first) == {
        k: first[k] for k in ("session_ref", "session_started_at", "content_sha256", "content_bytes")
    }
    assert len(vec.too_many_items(STARTED, TAG)) == MAX_BLOB_ITEMS + 1 == len(vec.too_many_blob_rows(STARTED, TAG))
    assert len(vec.tamper_same_length("abc")) == 3 and vec.tamper_same_length("abc") != "abc" != vec.tamper_same_length("Zbc")
    assert vec.different_length("abc") != "abc" and len(vec.different_length("abc")) == 4


def test_each_negative_vector_fails_as_expected(live: LiveFake) -> None:
    vectors = vec.bounds_vectors(REF, TAG, STARTED)
    assert [v.name for v in vectors] == [
        "item_at_threshold",
        "item_above_cap",
        "too_many_items",
        "too_many_blob_rows",
        "unregistered_drill_ref",
    ]
    for vector in vectors:
        resp = send(live, vector.build())
        body = resp.json()
        assert (resp.status_code, body["error_type"]) == (vector.status, vector.error_type), vector.name
        if vector.field is not None:
            assert body[vector.field] == vector.value, vector.name
        assert "blob-" not in str(body) and "x" * 8 not in str(body)
    assert live.store.blobs == {} and live.stored == {} and live.store.expected == {}
    row = vec.blob_transcript_row(TAG, 0, STARTED, vec.fresh_content(TAG))
    granted = send(live, vec.begin_body(REF, [vec.item_for(row)])).json()
    assert granted["ok"] and granted["items"][0]["upload"]["method"] == "PUT" and granted["project_registration"] == "existing"
    missing = send(live, vec.append_body(REF, [vec.wire_row(row)]))
    assert (missing.status_code, missing.json()["error_type"], missing.json()["reason"]) == (
        422,
        "blob_not_verified",
        "missing",
    )
