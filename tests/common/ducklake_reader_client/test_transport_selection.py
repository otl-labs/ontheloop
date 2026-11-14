"""PLAN-agent-proxy-signed-access: the OTL_OPS_VERBS_TRANSPORT switch in the DuckLake reader client.

Credential-free nodes: boto3, requests and SigV4 are patched, nothing reaches AWS.
"""

from __future__ import annotations

import pytest

import src.common.ducklake_reader_client as ir

_API_HOST = "https://abc123.execute-api.eu-west-2.amazonaws.com/v1/ops/reader"
_FN_HOST = "https://abc123.lambda-url.eu-west-2.on.aws"
_API_PARAM = "/agent-platform/ducklake/api_url/ops/reader"
_FN_PARAM = "/agent-platform/ducklake/reader_url"


def test_signing_scope_by_host() -> None:
    assert ir.signing_scope(_API_HOST, "eu-west-1") == ("execute-api", "eu-west-2")
    assert ir.signing_scope(_FN_HOST, "eu-west-1") == ("lambda", "eu-west-2")
    assert ir.signing_scope("http://localhost:9000/x", "eu-west-1") == ("lambda", "eu-west-1")
    elsewhere = "https://evil.example/x/abc.execute-api.eu-west-2.amazonaws.com/y"
    assert ir.signing_scope(elsewhere, "eu-west-1") == ("lambda", "eu-west-1")


def test_api_transport_values() -> None:
    assert ir.api_transport({}) is False
    assert ir.api_transport({ir.TRANSPORT_ENV: "function_url"}) is False
    assert ir.api_transport({ir.TRANSPORT_ENV: "execute_api"}) is True
    with pytest.raises(ValueError, match=ir.TRANSPORT_ENV):
        ir.api_transport({ir.TRANSPORT_ENV: "carrier_pigeon"})


def _no_terraform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("subprocess.run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))


def test_reader_url_uses_the_api_route_parameter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DUCKLAKE_READER_URL", raising=False)
    monkeypatch.setenv(ir.TRANSPORT_ENV, "execute_api")
    names: list[str] = []

    def _ssm(path: str, *, profile: str | None, region: str) -> str | None:
        names.append(path)
        return _API_HOST

    monkeypatch.setattr(ir, "_resolve_function_url_via_ssm", _ssm)
    monkeypatch.setattr(
        ir, "_resolve_function_url_via_api", lambda *a, **k: pytest.fail("GetFunctionUrlConfig must not be called")
    )
    _no_terraform(monkeypatch)
    assert ir.DuckLakeReader()._reader_url() == _API_HOST
    assert names == [_API_PARAM]


def test_reader_url_absent_api_parameter_raises_naming_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DUCKLAKE_READER_URL", raising=False)
    monkeypatch.setenv(ir.TRANSPORT_ENV, "execute_api")
    monkeypatch.setattr(ir, "_resolve_function_url_via_ssm", lambda *a, **k: None)
    monkeypatch.setattr(
        ir, "_resolve_function_url_via_api", lambda *a, **k: pytest.fail("GetFunctionUrlConfig must not be called")
    )
    with pytest.raises(RuntimeError, match=_API_PARAM):
        ir.DuckLakeReader()._reader_url()


def test_reader_env_override_still_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DUCKLAKE_READER_URL", "https://override.example/")
    monkeypatch.setenv(ir.TRANSPORT_ENV, "execute_api")
    monkeypatch.setattr(ir, "_resolve_function_url_via_ssm", lambda *a, **k: pytest.fail("SSM must not be read"))
    assert ir.DuckLakeReader()._reader_url() == "https://override.example"


def test_function_url_transport_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DUCKLAKE_READER_URL", raising=False)
    monkeypatch.delenv(ir.TRANSPORT_ENV, raising=False)
    names: list[str] = []

    def _ssm(path: str, *, profile: str | None, region: str) -> str | None:
        names.append(path)
        return _FN_HOST

    monkeypatch.setattr(ir, "_resolve_function_url_via_ssm", _ssm)
    assert ir.DuckLakeReader()._reader_url() == _FN_HOST
    assert names == [_FN_PARAM]


class _Creds:
    access_key = "AK"
    secret_key = "SK"  # noqa: S105 -- fake fixture  # pragma: allowlist secret
    token = None

    def get_frozen_credentials(self) -> _Creds:
        return self


class _Resp:
    status_code = 200
    text = "{}"

    def json(self) -> dict:
        return {"rows": []}


@pytest.mark.parametrize(
    ("url", "scope"),
    [(_API_HOST, "/eu-west-2/execute-api/aws4_request"), (_FN_HOST, "/eu-west-2/lambda/aws4_request")],
)
def test_invoke_signs_for_the_url_scope(monkeypatch: pytest.MonkeyPatch, url: str, scope: str) -> None:
    import boto3
    import requests

    monkeypatch.setenv("DUCKLAKE_READER_URL", url)

    class _Session:
        def __init__(self, profile_name: str | None = None) -> None:
            pass

        def get_credentials(self) -> _Creds:
            return _Creds()

    monkeypatch.setattr(boto3, "Session", _Session)
    seen: dict = {}

    def _post(u: str, data: str | None = None, headers: dict | None = None, timeout: int | None = None) -> _Resp:
        seen["auth"] = (headers or {}).get("Authorization", "")
        return _Resp()

    monkeypatch.setattr(requests, "post", _post)
    ir.DuckLakeReader(region="eu-west-2")._invoke({"action": "describe"})
    assert "Credential=AK/" in seen["auth"]
    assert scope in seen["auth"].split("Credential=")[1].split(",")[0]


def test_api_route_url_resolves_a_profile_when_none_is_given(monkeypatch: pytest.MonkeyPatch) -> None:
    import boto3

    monkeypatch.setenv(ir.TRANSPORT_ENV, "execute_api")
    seen: list[str | None] = []

    class _Client:
        def get_parameter(self, **kw: object) -> dict:
            return {"Parameter": {"Value": _API_HOST}}

    class _Session:
        def __init__(self, profile_name: str | None = None) -> None:
            seen.append(profile_name)

        def client(self, *a: object, **k: object) -> _Client:
            return _Client()

    monkeypatch.setattr(boto3, "Session", _Session)
    for var in ("AWS_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_LAMBDA_FUNCTION_NAME"):
        monkeypatch.delenv(var, raising=False)
    assert ir.api_route_url("ops/reader", None, "eu-west-2") == _API_HOST
    monkeypatch.setenv("AWS_PROFILE", "someone")
    ir.api_route_url("ops/reader", None, "eu-west-2")
    monkeypatch.delenv("AWS_PROFILE")
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "fn")
    ir.api_route_url("ops/reader", None, "eu-west-2")
    assert seen == ["agent_platform", "someone", None]
