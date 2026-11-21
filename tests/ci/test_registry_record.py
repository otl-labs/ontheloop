"""scripts/ci/registry_record.py: the GHCR channel record reader over a fake opener."""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from scripts.ci import registry_record as rr
from src.persona_hosts import load_pin

ROOT = Path(__file__).resolve().parents[2]
IMAGE = load_pin(ROOT).image
SHA = "a" * 40
CONFIG_DIGEST = "sha256:" + "c" * 64


class Resp:
    def __init__(self, body: Any) -> None:
        self.raw = body if isinstance(body, bytes) else json.dumps(body).encode()

    def __enter__(self) -> "Resp":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def read(self) -> bytes:
        return self.raw


def http_error(status: int, code: str | None) -> urllib.error.HTTPError:
    body = json.dumps({"errors": [{"code": code}]}).encode() if code else b"not json"
    return urllib.error.HTTPError("u", status, "x", {}, io.BytesIO(body))  # type: ignore[arg-type]


class Opener:
    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.requests: list[Any] = []

    def __call__(self, request: Any, timeout: int = 0) -> Resp:
        assert timeout == 30
        self.requests.append(request)
        for fragment, answer in self.routes.items():
            if fragment in request.full_url:
                if callable(answer):
                    raise answer()
                return Resp(answer)
        raise AssertionError(f"unrouted {request.full_url}")


def routes(manifest: Any = None, config: Any = None) -> dict[str, Any]:
    return {
        "/token": {"token": "pull-token"},
        "/manifests/main": manifest or {"mediaType": rr.OCI_MANIFEST, "config": {"digest": CONFIG_DIGEST}},
        f"/blobs/{CONFIG_DIGEST}": config if config is not None else {"config": {"Labels": {rr.REVISION_LABEL: SHA}}},
    }


def test_label_returned_and_scope_names_the_image() -> None:
    opener = Opener(routes())
    assert rr.read_record(IMAGE, token=None, opener=opener) == SHA
    token_url = opener.requests[0].full_url
    assert f"scope=repository:{rr.repository(IMAGE)}:pull" in token_url and f"service={rr.REGISTRY}" in token_url
    assert opener.requests[1].get_header("Accept") == rr.ACCEPT
    assert "oci.image.index" in rr.ACCEPT and "manifest.list" in rr.ACCEPT


def test_label_absent_is_none() -> None:
    assert rr.read_record(IMAGE, token=None, opener=Opener(routes(config={"config": {"Labels": {}}}))) is None


@pytest.mark.parametrize("code", ["NAME_UNKNOWN", "MANIFEST_UNKNOWN"])
def test_explicit_unknown_answers_are_none(code: str) -> None:
    opener = Opener({"/token": {"token": "t"}, "/manifests/main": lambda: http_error(404, code)})
    assert rr.read_record(IMAGE, token=None, opener=opener) is None


def test_denied_fails_closed_except_under_first_publish() -> None:
    opener = Opener({"/token": {"token": "t"}, "/manifests/main": lambda: http_error(401, "DENIED")})
    with pytest.raises(rr.RecordError):
        rr.read_record(IMAGE, token=None, opener=opener)
    assert rr.read_record(IMAGE, token=None, first_publish=True, opener=opener) is None


def test_other_failures_raise() -> None:
    for answer in (lambda: http_error(500, None), b"<html>", {"mediaType": rr.OCI_INDEX, "manifests": []},
                   {"mediaType": rr.DOCKER_LIST, "manifests": []}):  # fmt: skip
        opener = Opener({"/token": {"token": "t"}, "/manifests/main": answer})
        with pytest.raises(rr.RecordError):
            rr.read_record(IMAGE, token=None, first_publish=True, opener=opener)


def test_basic_auth_only_with_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_ACTOR", "bot")
    anonymous = Opener(routes())
    rr.read_record(IMAGE, token=None, opener=anonymous)
    assert anonymous.requests[0].get_header("Authorization") is None
    authed = Opener(routes())
    rr.read_record(IMAGE, token="s3cret-tok", opener=authed)
    assert authed.requests[0].get_header("Authorization").startswith("Basic ")
    assert "s3cret-tok" not in authed.requests[0].full_url
    assert authed.requests[1].get_header("Authorization") == "Bearer pull-token"


def test_headers_are_unredirected_and_image_must_be_a_bare_ghcr_name() -> None:
    opener = Opener(routes())
    rr.read_record(IMAGE, token="s3cret-tok", opener=opener)
    assert all(not r.headers.get("Authorization") for r in opener.requests)
    for bad in (f"{IMAGE}:main", "docker.io/x/y", f"{IMAGE}@sha256:{'a' * 64}"):
        with pytest.raises(rr.RecordError):
            rr.repository(bad)


class HeaderResp(Resp):
    def __init__(self, body: Any, headers: dict[str, str]) -> None:
        super().__init__(body)
        self.headers = headers


def _digest_of(body: Any) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(json.dumps(body).encode()).hexdigest()


MANIFEST = {"mediaType": rr.OCI_MANIFEST, "config": {"digest": CONFIG_DIGEST}}


def _header_opener(header: str | None) -> Opener:
    class Wrapped(Opener):
        def __call__(self, request: Any, timeout: int = 0) -> Resp:
            resp = super().__call__(request, timeout)
            if "/manifests/" in request.full_url and header is not None:
                return HeaderResp(MANIFEST, {"Docker-Content-Digest": header})
            return resp

    table = routes()
    table["/manifests/sha256:"] = MANIFEST
    return Wrapped(table)


@pytest.mark.parametrize("reference", ["main", _digest_of(MANIFEST)])
def test_read_image_returns_digest_and_label_by_tag_and_by_digest(reference: str) -> None:
    record = rr.read_image(IMAGE, reference, token=None, opener=_header_opener(_digest_of(MANIFEST)))
    assert record == rr.ImageRecord(digest=_digest_of(MANIFEST), revision=SHA)


def test_read_image_digest_header_disagreeing_with_bytes_is_a_record_error() -> None:
    with pytest.raises(rr.RecordError) as err:
        rr.read_image(IMAGE, "main", token=None, opener=_header_opener("sha256:" + "d" * 64))
    assert not isinstance(err.value, rr.RecordAbsent)


def test_read_image_requested_digest_disagreeing_with_bytes_is_a_record_error() -> None:
    with pytest.raises(rr.RecordError) as err:
        rr.read_image(IMAGE, "sha256:" + "e" * 64, token=None, opener=_header_opener(_digest_of(MANIFEST)))
    assert not isinstance(err.value, rr.RecordAbsent)


@pytest.mark.parametrize("code", ["NAME_UNKNOWN", "MANIFEST_UNKNOWN", "DENIED"])
def test_read_image_absence_raises_record_absent_with_the_code(code: str) -> None:
    opener = Opener({"/token": {"token": "t"}, "/manifests/main": lambda: http_error(404, code)})
    with pytest.raises(rr.RecordAbsent) as err:
        rr.read_image(IMAGE, "main", token=None, opener=opener)
    assert err.value.code == code


def test_read_record_returns_none_only_on_record_absent() -> None:
    denied = Opener({"/token": {"token": "t"}, "/manifests/main": lambda: http_error(401, "DENIED")})
    with pytest.raises(rr.RecordAbsent):
        rr.read_record(IMAGE, token=None, opener=denied)
    other = Opener({"/token": {"token": "t"}, "/manifests/main": lambda: http_error(500, "UNAVAILABLE")})
    with pytest.raises(rr.RecordError):
        rr.read_record(IMAGE, token=None, opener=other)
