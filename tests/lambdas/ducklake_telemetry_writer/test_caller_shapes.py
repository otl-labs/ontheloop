"""The telemetry writer builds the caller principal from the signed identity of the path that delivered the call."""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.common import ducklake_runtime as rt
from src.lambdas.ducklake_telemetry_writer import handler as h

ACCOUNT = "acct-under-test"
ARN = "arn:aws:iam::acct-under-test:user/agent-platform-session"
_FIELDS = {"accountId": ACCOUNT, "userArn": ARN}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEMETRY_META_SCHEMA", "ducklake_smoke")
    monkeypatch.setenv("TELEMETRY_DATA_PATH", rt.SMOKE_DATA_PATH)
    monkeypatch.setenv("TELEMETRY_DSN_PARAMETER", "/secret/x")
    monkeypatch.setenv("DUCKLAKE_EXTENSION_DIRECTORY", "/opt/ext")


def _function_url() -> dict[str, Any]:
    return {"requestContext": {"authorizer": {"iam": dict(_FIELDS)}}}


def _rest() -> dict[str, Any]:
    return {"httpMethod": "POST", "requestContext": {"apiId": "x", "identity": dict(_FIELDS)}}


def test_each_path_yields_its_signed_fields() -> None:
    assert h._caller_fields(_function_url()) == _FIELDS
    assert h._caller_fields(_rest()) == _FIELDS


def test_both_shapes_at_once_or_neither_is_none() -> None:
    both = _rest()
    both["requestContext"]["authorizer"] = {"iam": dict(_FIELDS)}
    assert h._caller_fields(both) is None
    mixed = _function_url()
    mixed["requestContext"]["identity"] = dict(_FIELDS)
    assert h._caller_fields(mixed) is None
    no_api = _rest()
    del no_api["requestContext"]["apiId"]
    assert h._caller_fields(no_api) is None
    assert h._caller_fields({}) is None
    assert h._caller_fields({"requestContext": "x"}) is None


def test_rest_event_without_identity_is_403_and_with_it_is_served() -> None:
    bare = {"httpMethod": "POST", "requestContext": {"apiId": "x"}, "body": json.dumps({"action": "describe"})}
    assert h.handler(bare)["statusCode"] == 403
    thin = _rest()
    thin["requestContext"]["identity"] = {"accountId": ACCOUNT}
    assert h.handler(thin)["statusCode"] == 403
    served = _rest()
    served["body"] = json.dumps({"action": "bogus"})
    assert h.handler(served)["statusCode"] == 400


def test_principal_keys_on_the_account_for_both_paths() -> None:
    for event in (_function_url(), _rest()):
        principal = h._principal(event)
        assert principal.ref == ACCOUNT and principal.role_name == "agent-platform-session"
