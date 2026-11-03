"""rec-4282 client half: a typed 400 named_read_input and a typed 500 unmapped raise after one POST; 503 is retried.

HEAVY-DEP MARKER MODULE: the SigV4 path needs boto3/requests/botocore, so the module-level boto3 import makes
--collect-only fail where they are absent and the fast tier defers it.
"""

from __future__ import annotations

import json

import boto3  # noqa: F401
import pytest

import src.common.ducklake_reader_client as ir

pytestmark = pytest.mark.unit


class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def _patch_post(monkeypatch, responses, calls):
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


def _named(reader):
    return reader.named("recs_related", files="", near="", terms="", exclude_ids="x", max_rows=1)


def test_named_read_input_raises_after_one_post(monkeypatch):
    calls: list = []
    body = {"ok": False, "error_type": "named_read_input", "verb": "recs_related", "error_class": "InvalidInputException"}
    _patch_post(monkeypatch, [_Resp(400, body)], calls)
    with pytest.raises(ir.ReaderInvokeError) as info:
        _named(ir.DuckLakeReader())
    assert info.value.status == 400 and info.value.body["error_type"] == "named_read_input"
    assert [c[0] for c in calls] == ["post"]


def test_unmapped_500_raises_after_one_post(monkeypatch):
    calls: list = []
    body = {"ok": False, "error_type": "unmapped", "error_class": "KeyError", "request_id": "r"}
    _patch_post(monkeypatch, [_Resp(500, body)], calls)
    with pytest.raises(ir.ReaderInvokeError) as info:
        _named(ir.DuckLakeReader())
    assert info.value.status == 500 and info.value.body["error_type"] == "unmapped"
    assert [c[0] for c in calls] == ["post"]


def test_catalog_unavailable_is_retried(monkeypatch):
    calls: list = []
    bad = {"ok": False, "error_type": "catalog_unavailable", "error_class": "OperationalError", "reopened": True}
    ok = {"ok": True, "verb": "recs_related", "rows": [{"id": "rec-1"}], "row_count": 1}
    _patch_post(monkeypatch, [_Resp(503, bad), _Resp(200, ok)], calls)
    assert _named(ir.DuckLakeReader()) == [{"id": "rec-1"}]
    assert [c[0] for c in calls] == ["post", "sleep", "post"]
