"""rec-4232 client half: HTTP 413 maps to ResponseTooLargeError, is never retried, and fetch_recs_by_ids is bounded.

HEAVY-DEP MARKER MODULE: the SigV4 path needs boto3/requests/botocore, so the module-level boto3 import makes
--collect-only fail where they are absent and the fast tier defers it.
"""

from __future__ import annotations

import json

import boto3  # noqa: F401
import pytest

import src.common.ducklake_reader_client as ir

pytestmark = pytest.mark.unit


def _patch_post(monkeypatch, responses: list, calls: list) -> None:
    monkeypatch.setenv("DUCKLAKE_READER_URL", "https://reader.example/")

    class _Creds:
        access_key = "AK"
        secret_key = "SK"  # noqa: S105 -- fake fixture  # pragma: allowlist secret
        token = None

        def get_frozen_credentials(self):
            return self

    class _Session:
        def __init__(self, profile_name=None):
            pass

        def get_credentials(self):
            return _Creds()

    import boto3  # noqa: F811
    import requests
    from botocore.auth import SigV4Auth

    monkeypatch.setattr(boto3, "Session", _Session)
    monkeypatch.setattr(SigV4Auth, "add_auth", lambda self, req: None)
    monkeypatch.setattr("scripts.aws_profile.resolve_aws_profile", lambda *a, **k: None)
    monkeypatch.setattr(ir.time, "sleep", lambda s: calls.append(("sleep", s)))
    seq = list(responses)

    def _post(url, data=None, headers=None, timeout=None):
        calls.append(("post", url))
        return seq.pop(0)

    monkeypatch.setattr(requests, "post", _post)


class _Resp:
    def __init__(self, status_code, text="", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload or {}

    def json(self):
        return self._payload


class _FakeReader:
    def __init__(self, rows):
        self.rows = rows
        self.calls: list = []

    def named(self, verb, **params):
        self.calls.append((verb, params))
        return self.rows


class TestResponseTooLargeNotRetried:
    def test_413_raises_typed_error_after_one_post(self, monkeypatch):
        calls: list = []
        body = {
            "ok": False,
            "error_type": "response_too_large",
            "action": "read_ops_current",
            "bytes": 7_000_000,
            "cap": 6_000_000,
        }
        _patch_post(monkeypatch, [_Resp(413, json.dumps(body))], calls)
        with pytest.raises(ir.ResponseTooLargeError) as exc:
            ir.DuckLakeReader().current_state("ops_recommendations")
        assert [c[0] for c in calls] == ["post"]  # exactly one POST, no backoff sleep
        assert (exc.value.status, exc.value.bytes, exc.value.cap) == (413, 7_000_000, 6_000_000)
        assert isinstance(exc.value, ir.ReaderInvokeError)

    def test_413_with_unparseable_body_still_typed(self, monkeypatch):
        calls: list = []
        _patch_post(monkeypatch, [_Resp(413, "not json")], calls)
        with pytest.raises(ir.ResponseTooLargeError) as exc:
            ir.DuckLakeReader().named("open_recs")
        assert exc.value.bytes is None and exc.value.cap is None and len(calls) == 1

    def test_413_is_not_a_transient_status(self):
        assert 413 not in ir._READER_TRANSIENT_STATUS

    def test_502_is_still_retried(self, monkeypatch):
        calls: list = []
        _patch_post(monkeypatch, [_Resp(502, "x"), _Resp(200, payload={"rows": [{"a": 1}]})], calls)
        assert ir.DuckLakeReader().named("open_recs") == [{"a": 1}]
        assert [c[0] for c in calls] == ["post", "sleep", "post"]

    def test_fetch_recs_by_ids_normalises_and_reports_missing(self):
        reader = _FakeReader([{"id": "rec-2"}])
        rows, missing = ir.fetch_recs_by_ids(reader, [" rec-1 ", "rec-2", "rec-1", "", None])
        assert reader.calls == [("recs_by_ids", {"ids": "rec-1,rec-2"})]
        assert rows == [{"id": "rec-2"}] and missing == ["rec-1"]

    def test_fetch_recs_by_ids_empty_makes_no_call(self):
        reader = _FakeReader([])
        assert ir.fetch_recs_by_ids(reader, []) == ([], [])
        assert reader.calls == []

    def test_fetch_recs_by_ids_refuses_more_than_fifty(self):
        reader = _FakeReader([])
        with pytest.raises(ValueError, match="at most 50"):
            ir.fetch_recs_by_ids(reader, [f"rec-{i}" for i in range(51)])
        assert reader.calls == []
        ir.fetch_recs_by_ids(reader, [f"rec-{i}" for i in range(50)])
        assert len(reader.calls) == 1
