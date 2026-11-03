"""Mirror test for scripts/telemetry_capture/responses.py: every row of RN-7's table, and the default stop."""

from __future__ import annotations

import pytest

from scripts.telemetry_capture import responses as r


@pytest.mark.parametrize(
    ("status", "error_type", "action"),
    [
        (200, None, r.OK),
        (403, "tenant_unresolved", r.REGISTER_TENANT),
        (403, "project_unregistered", r.HALT),
        (422, "blob_not_verified", r.REDELIVER),
        (422, "row_rule", r.HALT),
        (422, "schema_gate", r.HALT),
        (409, "grain_conflict", r.HALT),
        (400, "bad_request", r.HALT),
        (413, "payload_too_large", r.RECHUNK),
        (413, "too_many_rows", r.RECHUNK),
        (413, "too_many_blobs", r.RECHUNK),
        (413, None, r.RECHUNK),
        (429, None, r.RETRY),
        (503, "occ_exhausted", r.RETRY),
        (500, "runtime", r.STOP),
        (401, None, r.STOP),
        (403, None, r.STOP),
        (403, "forbidden", r.STOP),
        (502, None, r.STOP),
        (504, None, r.STOP),
        (0, None, r.STOP),
    ],
)
def test_classify(status: int, error_type: str | None, action: str) -> None:
    body = {"ok": status == 200}
    if error_type is not None:
        body["error_type"] = error_type
    assert r.classify(status, body) == action


def test_a_200_without_ok_stops_and_non_mapping_bodies_classify_by_status() -> None:
    assert r.classify(200, {"ok": False}) == r.STOP and r.classify(200, None) == r.STOP
    assert r.classify(409, "text") == r.HALT and r.classify(403, None) == r.STOP


def test_every_table_row_is_reachable() -> None:
    for status, error_type, action in r.RESPONSES:
        body = {"ok": True, "error_type": error_type} if error_type else {"ok": True}
        assert r.classify(status, body) == action


def test_halt_fields_are_value_free() -> None:
    body = {"error_type": "row_rule", "rule": "pattern_when", "column": "name", "row": 4, "error": "value!", "table": "t"}
    assert r.halt_fields(422, body) == {
        "status": 422,
        "error_type": "row_rule",
        "rule": "pattern_when",
        "column": "name",
        "row": 4,
    }
    hostile = {"error_type": "Has Spaces and a value", "column": 7, "row": True, "rule": "x" * 80}
    assert r.halt_fields(400, hostile) == {"status": 400}
    assert r.halt_fields(500, None) == {"status": 500}
