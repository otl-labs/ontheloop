"""rec-4474 client half: a typed 504 read_deadline_exceeded is raised after one POST; every other 5xx keeps its retries.

HEAVY-DEP MARKER MODULE: the SigV4 path needs boto3/requests/botocore, so the module-level boto3 import makes
--collect-only fail where they are absent and the fast tier defers it.
"""

from __future__ import annotations

import logging

import boto3  # noqa: F401
import pytest

import src.common.ducklake_reader_client as ir

pytestmark = pytest.mark.unit

_DEADLINE_BODY = {
    "ok": False,
    "error_type": "read_deadline_exceeded",
    "reason": "reader_deadline",
    "request_id": "r",
    "action": "named_read",
}


class _Resp:
    def __init__(self, status_code, text="", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload or {}

    def json(self):
        return self._payload


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


def _posts(calls: list) -> int:
    return sum(1 for kind, _ in calls if kind == "post")


def test_deadline_504_is_raised_after_one_post(monkeypatch):
    import json

    calls: list = []
    _patch_post(monkeypatch, [_Resp(504, json.dumps(_DEADLINE_BODY))], calls)
    with pytest.raises(ir.ReadDeadlineExceededError) as info:
        ir.DuckLakeReader()._invoke({"action": "named_read"})
    assert info.value.status == 504 and info.value.body == _DEADLINE_BODY
    assert _posts(calls) == 1 and not any(kind == "sleep" for kind, _ in calls)
    assert isinstance(info.value, ir.ReaderInvokeError)


def test_other_5xx_still_retry_twice(monkeypatch):
    cases = (
        (502, "Internal Server Error"),
        (503, '{"ok": false, "error_type": "catalog_unavailable"}'),
        (504, '{"ok": false, "error_type": "something_else"}'),
        (504, "gateway timeout"),
    )
    for status, text in cases:
        calls: list = []
        _patch_post(monkeypatch, [_Resp(status, text)] * 3, calls)
        with pytest.raises(ir.ReaderInvokeError) as info:
            ir.DuckLakeReader()._invoke({"action": "named_read"})
        assert not isinstance(info.value, ir.ReadDeadlineExceededError)
        assert _posts(calls) == 3 and sum(1 for kind, _ in calls if kind == "sleep") == 2
    assert ir._READER_TRANSIENT_STATUS == frozenset({502, 503, 504})


def test_error_type_literal_matches_the_reader():
    from src.common.ducklake_read_deadline import READ_DEADLINE_ERROR_TYPE

    assert ir._READ_DEADLINE_ERROR_TYPE == READ_DEADLINE_ERROR_TYPE


def test_retry_log_does_not_claim_a_cold_resume(monkeypatch, caplog):
    calls: list = []
    _patch_post(monkeypatch, [_Resp(502, "boom"), _Resp(200, payload={"ok": True})], calls)
    with caplog.at_level(logging.WARNING, logger=ir.logger.name):
        assert ir.DuckLakeReader()._invoke({"action": "named_read"}) == {"ok": True}
    assert any("retrying" in rec.getMessage() for rec in caplog.records)
    assert not any("cold-resume" in rec.getMessage() for rec in caplog.records)
