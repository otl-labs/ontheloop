"""oidc_assume: the real GitHub token-request and STS AssumeRoleWithWebIdentity shapes, no socket, no secret printed."""

from __future__ import annotations

import base64
import io
import json
import urllib.error
import urllib.request
from typing import Any

import pytest

from scripts.personas import oidc_assume

ACCOUNT = "123456789"
ACCOUNT_OK = "9" * 12
REGION = "xx-test-1"
JWT = (
    "aaa." + base64.urlsafe_b64encode(json.dumps({"sub": "s", "job_workflow_ref": "r"}).encode()).decode().rstrip("=") + ".ccc"
)
STS_OK = (
    b'<AssumeRoleWithWebIdentityResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/">'
    b"<AssumeRoleWithWebIdentityResult><Credentials><AccessKeyId>FAKEACCESSKEYIDFORTEST</AccessKeyId>"
    b"<SecretAccessKey>secretvalue</SecretAccessKey><SessionToken>tokenvalue</SessionToken></Credentials>"
    b"</AssumeRoleWithWebIdentityResult></AssumeRoleWithWebIdentityResponse>"
)
STS_DENIED = (
    b'<ErrorResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/"><Error><Type>Sender</Type>'
    b"<Code>AccessDenied</Code><Message>Not authorized</Message></Error></ErrorResponse>"
)


class _Resp:
    def __init__(self, status: int, body: bytes) -> None:
        self.status, self._body = status, body

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *a: Any) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _opener(
    monkeypatch: pytest.MonkeyPatch, status: int, body: bytes, seen: list[urllib.request.Request] | None = None
) -> None:
    def fake(request: urllib.request.Request, timeout: int) -> _Resp:
        if seen is not None:
            seen.append(request)
        if status >= 400:
            raise urllib.error.HTTPError(request.full_url, status, "x", {}, io.BytesIO(body))  # type: ignore[arg-type]
        return _Resp(status, body)

    monkeypatch.setattr(oidc_assume, "urlopen", fake)


ENV = {
    "ACTIONS_ID_TOKEN_REQUEST_URL": "https://pipelines.actions.githubusercontent.com/abc/idtoken?api-version=2.0",
    "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "bearer-secret",
    "PERSONA_AWS_REGION": REGION,
    "AWS_ACCOUNT_ID": ACCOUNT_OK,
    "GITHUB_RUN_ID": "42",
}


def test_token_reads_the_value_and_sends_audience_and_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[urllib.request.Request] = []
    _opener(monkeypatch, 200, json.dumps({"count": 1, "value": JWT}).encode(), seen)
    assert oidc_assume.token(ENV) == JWT
    assert "audience=sts.amazonaws.com" in seen[0].full_url and seen[0].full_url.count("?") == 1
    assert seen[0].get_header("Authorization") == "bearer bearer-secret"


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.com/x",
        "http://pipelines.actions.githubusercontent.com/x",
        "https://notactions.githubusercontent.com.evil.io/x",
        "",
    ],
)
def test_token_refuses_a_non_actions_host(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    _opener(monkeypatch, 200, b"{}")
    with pytest.raises(oidc_assume.OidcError):
        oidc_assume.token({**ENV, "ACTIONS_ID_TOKEN_REQUEST_URL": url})


def test_token_failures_are_fixed_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    _opener(monkeypatch, 200, b"not json bearer-secret")
    with pytest.raises(oidc_assume.OidcError) as exc:
        oidc_assume.token(ENV)
    assert "bearer-secret" not in str(exc.value)
    _opener(monkeypatch, 500, b"boom")
    with pytest.raises(oidc_assume.OidcError):
        oidc_assume.token(ENV)


def test_claims_decode_the_payload_only() -> None:
    assert oidc_assume.claims(JWT)["sub"] == "s"
    with pytest.raises(oidc_assume.OidcError):
        oidc_assume.claims("garbage")


def test_assume_returns_credentials_on_200_and_posts_the_regional_query(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[urllib.request.Request] = []
    _opener(monkeypatch, 200, STS_OK, seen)
    out = oidc_assume.assume(oidc_assume.ROLE, JWT, ENV)
    assert out.status == 200 and out.code == "ok" and out.credentials is not None
    assert out.credentials["SessionToken"] == "tokenvalue"
    req = seen[0]
    assert req.full_url == f"https://sts.{REGION}.amazonaws.com/" and req.get_method() == "POST"
    body = (req.data or b"").decode()
    assert "Action=AssumeRoleWithWebIdentity" in body and "Version=2011-06-15" in body
    assert "DurationSeconds=900" in body and "RoleSessionName=persona-verdict-42" in body


def test_assume_403_access_denied_is_an_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    _opener(monkeypatch, 403, STS_DENIED)
    out = oidc_assume.assume(oidc_assume.ROLE, JWT, ENV)
    assert (out.status, out.code, out.credentials) == (403, "AccessDenied", None)


@pytest.mark.parametrize(("exc", "code"), [(urllib.error.URLError("x"), "neterror"), (TimeoutError(), "timeout")])
def test_network_failures_are_outcomes_never_a_pass(monkeypatch: pytest.MonkeyPatch, exc: Exception, code: str) -> None:
    def boom(request: Any, timeout: int) -> None:
        raise exc

    monkeypatch.setattr(oidc_assume, "urlopen", boom)
    out = oidc_assume.assume(oidc_assume.ROLE, JWT, ENV)
    assert out.code == code and out.credentials is None


@pytest.mark.parametrize(
    "patch",
    [{"PERSONA_AWS_REGION": ""}, {"PERSONA_AWS_REGION": "eu west"}, {"AWS_ACCOUNT_ID": ACCOUNT}, {"AWS_ACCOUNT_ID": ""}],
)
def test_a_bad_region_or_account_fails_before_any_call(monkeypatch: pytest.MonkeyPatch, patch: dict[str, str]) -> None:
    monkeypatch.setattr(oidc_assume, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("called")))
    with pytest.raises(oidc_assume.OidcError):
        oidc_assume.assume(oidc_assume.ROLE, JWT, {**ENV, **patch})


def test_nothing_secret_is_printed(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _opener(monkeypatch, 200, STS_OK)
    out = oidc_assume.assume(oidc_assume.ROLE, JWT, ENV)
    oidc_assume.report(oidc_assume.ROLE, "ok", out)
    text = capsys.readouterr().out
    assert text == f"OIDC-ASSUME role={oidc_assume.ROLE} expect=ok got=ok\n"
    for secret in (JWT, ACCOUNT_OK, "secretvalue", "tokenvalue", "FAKEACCESS", "arn:aws"):
        assert secret not in text
