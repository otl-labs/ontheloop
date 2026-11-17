"""access_log.write: the closed field set, the masked role name, never an ARN, account id, session name or text."""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.lambdas.ducklake_telemetry_content_reader import access_log

pytestmark = pytest.mark.unit


def caller_event(body: dict[str, Any] | str, role: str = "PlatformDev") -> dict[str, Any]:
    account = "".join(["1234", "5678", "9012"])
    arn = f"arn:aws:sts::{account}:assumed-role/{role}/someone"
    return {
        "body": body if isinstance(body, str) else json.dumps(body),
        "requestContext": {"authorizer": {"iam": {"accountId": account, "userArn": arn}}},
    }


_FIELDS = {"event", "role", "verb", "status", "session_id", "rows", "masked_bytes", "mask_counts", "request_id"}


def _line(capsys: pytest.CaptureFixture[str]) -> dict:
    (out,) = capsys.readouterr().out.splitlines()
    return json.loads(out)


def test_line_has_the_closed_field_set_and_no_identity_values(capsys: pytest.CaptureFixture[str]) -> None:
    access_log.write(
        caller_event({}), "transcript_row_masked", 200, 1, 12, {"ACCOUNT_ID": 1}, "req-1", "01ARZ3NDEKTSV4RRFFQ69G5FAV"
    )
    line = _line(capsys)
    assert set(line) == _FIELDS and line["event"] == "telemetry_content_read" and line["role"] == "PlatformDev"
    text = json.dumps(line)
    assert "arn:" not in text and "someone" not in text and "".join(["1234", "5678", "9012"]) not in text


def test_role_is_masked_and_missing_identity_is_unknown(capsys: pytest.CaptureFixture[str]) -> None:
    session_like = "dev_" + "information" + "_hub"
    access_log.write(caller_event({}, role=session_like), "unknown", 400, 0, 0, {}, None, None)
    assert _line(capsys)["role"] == "[REDACTED:SSO_HUB_PROFILE]"
    for event in ({}, {"requestContext": {}}, None, {"requestContext": {"authorizer": {"iam": {"userArn": "x"}}}}):
        access_log.write(event, "unknown", 400, 0, 0, {}, None, None)
        assert _line(capsys)["role"] == "unknown"


def _rest_event(arn: str, *, api_id: str | None = "abc123", method: str | None = "POST", extra: dict | None = None) -> dict:
    account = "".join(["1234", "5678", "9012"])
    context: dict[str, Any] = {"identity": {"accountId": account, "userArn": arn}}
    if api_id:
        context["apiId"] = api_id
    event: dict[str, Any] = {"body": "{}", "requestContext": {**context, **(extra or {})}}
    if method:
        event["httpMethod"] = method
    return event


def test_rest_and_function_url_shapes_yield_the_caller_name(capsys: pytest.CaptureFixture[str]) -> None:
    account = "".join(["1234", "5678", "9012"])
    session_arn = f"arn:aws:iam::{account}:user/agent-platform-session"
    access_log.write(_rest_event(session_arn), "v", 200, 0, 0, {}, None, None)
    assert _line(capsys)["role"] == "agent-platform-session"
    access_log.write(_rest_event(session_arn, extra={"authorizer": {}}), "v", 200, 0, 0, {}, None, None)
    assert _line(capsys)["role"] == "agent-platform-session"
    access_log.write(caller_event({}, role="PlatformDev"), "v", 200, 0, 0, {}, None, None)
    assert _line(capsys)["role"] == "PlatformDev"


def test_mixed_absent_and_malformed_shapes_are_unknown(capsys: pytest.CaptureFixture[str]) -> None:
    account = "".join(["1234", "5678", "9012"])
    arn = f"arn:aws:iam::{account}:user/agent-platform-session"
    both = _rest_event(arn, extra={"authorizer": {"iam": {"accountId": account, "userArn": arn}}})
    no_method = _rest_event(arn, method=None)
    no_api = _rest_event(arn, api_id=None)
    malformed = _rest_event("not-an-arn")
    for event in (both, no_method, no_api, malformed, _rest_event(""), {"requestContext": "not-a-dict"}):
        access_log.write(event, "v", 400, 0, 0, {}, None, None)
        assert _line(capsys)["role"] == "unknown"


def test_write_never_raises(capsys: pytest.CaptureFixture[str]) -> None:
    access_log.write({}, "v", 200, 0, 0, {"x": object()}, None, None)  # type: ignore[dict-item]
    line = _line(capsys)
    assert line["role"] == "unknown" and line["event"] == "telemetry_content_read"
