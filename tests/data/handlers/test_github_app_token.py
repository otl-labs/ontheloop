"""Mirror test for src/data/handlers/github_app_token.py (PLAN-scheduled-workflow-dispatch PR 2)."""

from __future__ import annotations

import io
import json
import urllib.error
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from src.data.handlers import github_app_token as gat

PERMS = {"actions": "write", "metadata": "read"}


@pytest.fixture(scope="module")
def key_pair() -> tuple[str, Any]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    return pem, key.public_key()


class _Response:
    def __init__(self, status: int, body: Any) -> None:
        self.status = status
        self._raw = b"" if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_: Any) -> None:
        return None


class FakeOpener:
    def __init__(self, replies: list[Any]) -> None:
        self.replies = list(replies)
        self.requests: list[Any] = []

    def __call__(self, request: Any, timeout: int) -> _Response:
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _http_error(code: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://api.github.com/x", code, "err", {}, io.BytesIO(body))  # type: ignore[arg-type]


def _api(replies: list[Any]) -> tuple[gat.GitHubApi, FakeOpener]:
    opener = FakeOpener(replies)
    return gat.GitHubApi(opener=opener), opener


def test_app_jwt_claims(key_pair: tuple[str, Any]) -> None:
    pem, public = key_pair
    token = gat.app_jwt("Iv23client", pem, 1_000_000)
    assert jwt.get_unverified_header(token)["alg"] == "RS256"
    claims = jwt.decode(token, public, algorithms=["RS256"], options={"verify_exp": False, "verify_iat": False})
    assert claims == {"iss": "Iv23client", "iat": 1_000_000 - 60, "exp": 1_000_000 + 540}


def test_call_sends_headers_and_body() -> None:
    api, opener = _api([_Response(201, {"ok": True})])
    status, body = api.call("POST", "/x", "tok", {"a": 1})
    assert (status, body) == (201, {"ok": True})
    req = opener.requests[0]
    assert req.get_method() == "POST"
    assert req.full_url == "https://api.github.com/x"
    assert req.get_header("Authorization") == "Bearer tok"
    assert req.get_header("X-github-api-version") == "2022-11-28"
    assert req.get_header("Content-type") == "application/json"
    assert json.loads(req.data) == {"a": 1}


def test_call_empty_body_returns_none() -> None:
    api, opener = _api([_Response(204, None)])
    assert api.call("DELETE", "/installation/token", "tok") == (204, None)
    assert opener.requests[0].data is None


def test_http_error_text_never_carries_secrets() -> None:
    echoed = json.dumps({"message": "Bad credentials", "token": "ghs_SECRET", "auth": "Bearer eyJSECRET"}).encode()
    api, _ = _api([_http_error(401, echoed)])
    with pytest.raises(gat.GitHubApiError) as info:
        api.call("GET", "/repos/o/r/installation?x=1", "ghs_SECRET")
    text = str(info.value)
    assert "401" in text and "GET /repos/o/r/installation" in text and "Bad credentials" in text
    assert "SECRET" not in text and "Bearer" not in text and "x=1" not in text


def test_non_2xx_status_without_http_error_raises() -> None:
    api, _ = _api([_Response(302, b"not json")])
    with pytest.raises(gat.GitHubApiError, match="302: \\(no message\\)"):
        api.call("GET", "/x", "t")


def test_message_without_message_field() -> None:
    assert gat._message(json.dumps(["x"]).encode()) == "(no message)"


def _mint_replies(minted: dict[str, Any]) -> list[Any]:
    return [_Response(200, {"id": 42}), _Response(201, minted)]


def test_installation_token_happy_path() -> None:
    api, opener = _api(_mint_replies({"token": "ghs_t", "permissions": PERMS, "repository_selection": "selected"}))
    assert gat.installation_token(api, "jwt", "otl-labs/ontheloop", PERMS) == "ghs_t"
    assert opener.requests[0].full_url.endswith("/repos/otl-labs/ontheloop/installation")
    assert opener.requests[1].full_url.endswith("/app/installations/42/access_tokens")
    assert json.loads(opener.requests[1].data) == {"repositories": ["ontheloop"], "permissions": PERMS}


@pytest.mark.parametrize(
    "granted",
    [{**PERMS, "contents": "read"}, {"actions": "write"}],
)
def test_installation_token_refuses_other_permissions(granted: dict[str, str]) -> None:
    api, _ = _api(
        [*_mint_replies({"token": "ghs_t", "permissions": granted, "repository_selection": "selected"}), _Response(204, None)]
    )
    with pytest.raises(RuntimeError, match="permissions"):
        gat.installation_token(api, "jwt", "otl-labs/ontheloop", PERMS)


def test_installation_token_refuses_all_repositories() -> None:
    api, _ = _api(
        [*_mint_replies({"token": "ghs_t", "permissions": PERMS, "repository_selection": "all"}), _Response(204, None)]
    )
    with pytest.raises(RuntimeError, match="repository_selection"):
        gat.installation_token(api, "jwt", "otl-labs/ontheloop", PERMS)


def test_revoke_sends_delete() -> None:
    api, opener = _api([_Response(204, None)])
    gat.revoke(api, "ghs_t")
    assert opener.requests[0].get_method() == "DELETE"
    assert opener.requests[0].full_url.endswith("/installation/token")


def test_revoke_raises_on_401() -> None:
    api, _ = _api([_http_error(401, b"{}")])
    with pytest.raises(gat.GitHubApiError):
        gat.revoke(api, "ghs_t")


def test_revoke_raises_on_unexpected_2xx() -> None:
    api, _ = _api([_Response(200, None)])
    with pytest.raises(RuntimeError, match="expected 204"):
        gat.revoke(api, "ghs_t")


def test_mismatched_token_is_revoked_before_raising() -> None:
    minted = {"token": "ghs_t", "permissions": {**PERMS, "contents": "write"}, "repository_selection": "selected"}
    api, opener = _api([*_mint_replies(minted), _Response(204, None)])
    with pytest.raises(RuntimeError, match="permissions"):
        gat.installation_token(api, "jwt", "otl-labs/ontheloop", PERMS)
    assert opener.requests[2].get_method() == "DELETE"
    assert opener.requests[2].get_header("Authorization") == "Bearer ghs_t"


def test_mismatch_without_token_raises_without_revoke() -> None:
    api, opener = _api(_mint_replies({"permissions": {}, "repository_selection": "selected"}))
    with pytest.raises(RuntimeError, match="permissions"):
        gat.installation_token(api, "jwt", "otl-labs/ontheloop", PERMS)
    assert len(opener.requests) == 2
