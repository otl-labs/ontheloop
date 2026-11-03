"""rec-4232: the reader refuses an oversized response with a typed 413 instead of the runtime's opaque 502.

Network-free: the connection and the runtime read are stubbed, so trailer closure can run it offline.
"""

from __future__ import annotations

import json

import pytest

import src.lambdas.ducklake_reader.handler as h
from src.common import ducklake_runtime as rt

pytestmark = pytest.mark.unit


class _Con:
    def execute(self, sql, params=None):  # noqa: ARG002
        return self

    def fetchall(self):
        return []


@pytest.fixture(autouse=True)
def _warm(monkeypatch):
    rt.reset_warm_connection()
    monkeypatch.setattr(h, "_open_reader_connection", lambda: _Con())
    yield
    rt.reset_warm_connection()


def _read_ops_current(monkeypatch, rows):
    monkeypatch.setattr(rt, "read_current", lambda con, **kw: rows)  # noqa: ARG005
    return h.handler({"action": "read_ops_current", "table": "ops_recommendations"})


class TestReadOpsCurrentResponseCap:
    def test_oversized_response_is_typed_413(self, monkeypatch):
        rows = [{"id": f"rec-{i}", "context": "x" * 1000} for i in range(7000)]
        r = _read_ops_current(monkeypatch, rows)
        body = json.loads(r["body"])
        assert r["statusCode"] == 413
        assert body["ok"] is False and body["error_type"] == "response_too_large"
        assert body["action"] == "read_ops_current"
        assert body["cap"] == h.MAX_RESPONSE_BYTES and body["bytes"] > body["cap"]
        assert "rec-4237" in body["hint"]
        assert "rows" not in body

    def test_in_cap_response_is_unchanged(self, monkeypatch):
        r = _read_ops_current(monkeypatch, [{"id": "rec-1", "context": "small"}])
        body = json.loads(r["body"])
        assert r["statusCode"] == 200 and body["rows"] == [{"id": "rec-1", "context": "small"}]

    def test_envelope_not_body_is_measured(self, monkeypatch):
        # Quotes are escaped a second time inside the Function-URL envelope: the body is under the cap, the envelope is not.
        rows = [{"id": "rec-1", "context": '"' * 2_000_000}]
        probe = h._response(200, {"ok": True, "table": "ops_recommendations", "rows": rows, "row_count": 1})
        assert len(probe["body"].encode("utf-8")) < h.MAX_RESPONSE_BYTES < len(json.dumps(probe).encode("utf-8"))
        assert _read_ops_current(monkeypatch, rows)["statusCode"] == 413
