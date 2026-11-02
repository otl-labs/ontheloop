"""Tests for src/common/neon_api.py (100% line+branch coverage, all network mocked)."""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from src.common import neon_api

pytestmark = pytest.mark.unit

_FAKE_API_KEY = "neon-fake-api-key-abc123"  # pragma: allowlist secret -- fake fixture value
_FAKE_PROJECT_ID = "proj-fake-abc"
_FAKE_BRANCH_ID = "br-fake-xyz"
_FAKE_BRANCH_HOST = "br-fake-xyz.us-east-2.aws.neon.tech"


# ---------------------------------------------------------------------------
# NeonApiError
# ---------------------------------------------------------------------------


def test_neon_api_error_is_runtime_error():
    """NeonApiError must subclass RuntimeError (Decision 55 loud-fail chain)."""
    err = neon_api.NeonApiError("boom")
    assert isinstance(err, RuntimeError)
    assert str(err) == "boom"


# ---------------------------------------------------------------------------
# fetch_api_key
# ---------------------------------------------------------------------------


def _make_ssm_stub(secret_string: str):
    """Return a boto3 SSM client stub that returns secret_string."""
    stub = MagicMock()
    stub.get_parameter.return_value = {"Parameter": {"Value": secret_string}}
    return stub


def test_fetch_api_key_plain_string(monkeypatch):
    """Plain string secret (not JSON) is returned as-is after strip."""
    client = _make_ssm_stub("  my-api-key  ")
    session = MagicMock()
    session.client.return_value = client
    with patch("boto3.Session", return_value=session):
        key = neon_api.fetch_api_key()
    assert key == "my-api-key"
    client.get_parameter.assert_called_once_with(Name="/secret/neon-api-key", WithDecryption=True)  # pragma: allowlist secret


def test_fetch_api_key_json_api_key_field(monkeypatch):
    """JSON secret with 'api_key' field returns that field."""
    client = _make_ssm_stub(json.dumps({"api_key": "json-api-key"}))
    session = MagicMock()
    session.client.return_value = client
    with patch("boto3.Session", return_value=session):
        key = neon_api.fetch_api_key()
    assert key == "json-api-key"


def test_fetch_api_key_json_neon_api_key_field(monkeypatch):
    """JSON secret with 'NEON_API_KEY' field returns that field."""
    client = _make_ssm_stub(json.dumps({"NEON_API_KEY": "upper-api-key"}))
    session = MagicMock()
    session.client.return_value = client
    with patch("boto3.Session", return_value=session):
        key = neon_api.fetch_api_key()
    assert key == "upper-api-key"


def test_fetch_api_key_passes_profile():
    """profile argument is forwarded to boto3.Session."""
    client = _make_ssm_stub("key-val")
    session = MagicMock()
    session.client.return_value = client
    with patch("boto3.Session", return_value=session) as mock_session:
        neon_api.fetch_api_key(profile="agent_platform_admin")
    mock_session.assert_called_once_with(profile_name="agent_platform_admin")


def test_fetch_api_key_no_profile_uses_default_session():
    """No profile uses boto3.Session() with no kwargs."""
    client = _make_ssm_stub("key-val")
    session = MagicMock()
    session.client.return_value = client
    with patch("boto3.Session", return_value=session) as mock_session:
        neon_api.fetch_api_key()
    mock_session.assert_called_once_with()


# ---------------------------------------------------------------------------
# resolve_org_id / resolve_project_id
# ---------------------------------------------------------------------------

_FAKE_ORG_ID = "org-fake-123"


def _mock_urlopen(response_body: dict, *, status: int = 200):
    """Return a context-manager mock for urllib.request.urlopen."""
    raw = json.dumps(response_body).encode()
    resp = MagicMock()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    resp.read.return_value = raw
    return resp


def _mock_urlopen_by_url(url_to_body: dict):
    """Return a urlopen side_effect that dispatches by request URL (path match)."""

    def _dispatch(req, *args, **kwargs):
        url = req.full_url if hasattr(req, "full_url") else req
        for fragment, body in url_to_body.items():
            if fragment in url:
                return _mock_urlopen(body)
        raise AssertionError(f"unexpected URL: {url}")

    return _dispatch


def test_resolve_org_id_returns_sole_org():
    """resolve_org_id returns the id of the single visible organization."""
    body = {"organizations": [{"id": _FAKE_ORG_ID, "name": "acme"}]}
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(body)):
        oid = neon_api.resolve_org_id(_FAKE_API_KEY)
    assert oid == _FAKE_ORG_ID


def test_resolve_org_id_zero_orgs_loud_fails():
    """NeonApiError raised when no organizations are visible (Decision 55)."""
    body = {"organizations": []}
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(body)):
        with pytest.raises(neon_api.NeonApiError, match="expected exactly 1 organization, found 0"):
            neon_api.resolve_org_id(_FAKE_API_KEY)


def test_resolve_org_id_multiple_orgs_loud_fails():
    """NeonApiError raised when more than one organization is visible (ambiguous)."""
    body = {"organizations": [{"id": "o1"}, {"id": "o2"}]}
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(body)):
        with pytest.raises(neon_api.NeonApiError, match="expected exactly 1 organization, found 2"):
            neon_api.resolve_org_id(_FAKE_API_KEY)


def test_resolve_org_id_org_without_id_loud_fails():
    """NeonApiError raised when the sole organization has no id."""
    body = {"organizations": [{"name": "no-id-org"}]}
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(body)):
        with pytest.raises(neon_api.NeonApiError, match="organization has no id"):
            neon_api.resolve_org_id(_FAKE_API_KEY)


def test_resolve_project_id_matches_by_name():
    """resolve_project_id resolves org_id then returns project_id when name matches."""
    urls = {
        "/users/me/organizations": {"organizations": [{"id": _FAKE_ORG_ID}]},
        "/projects": {"projects": [{"id": _FAKE_PROJECT_ID, "name": "ducklake-catalog"}, {"id": "other", "name": "x"}]},
    }
    with patch("urllib.request.urlopen", side_effect=_mock_urlopen_by_url(urls)):
        pid = neon_api.resolve_project_id(_FAKE_API_KEY)
    assert pid == _FAKE_PROJECT_ID


def test_resolve_project_id_custom_name():
    """Custom name argument is matched against project names."""
    urls = {
        "/users/me/organizations": {"organizations": [{"id": _FAKE_ORG_ID}]},
        "/projects": {"projects": [{"id": "p1", "name": "other"}, {"id": "p2", "name": "my-project"}]},
    }
    with patch("urllib.request.urlopen", side_effect=_mock_urlopen_by_url(urls)):
        pid = neon_api.resolve_project_id(_FAKE_API_KEY, name="my-project")
    assert pid == "p2"


def test_resolve_project_id_explicit_org_id_skips_org_lookup():
    """When org_id is passed explicitly, no /users/me/organizations call is made."""
    body = {"projects": [{"id": _FAKE_PROJECT_ID, "name": "ducklake-catalog"}]}
    captured = []

    def _urlopen(req, *args, **kwargs):
        captured.append(req.full_url)
        return _mock_urlopen(body)

    with patch("urllib.request.urlopen", side_effect=_urlopen):
        pid = neon_api.resolve_project_id(_FAKE_API_KEY, org_id=_FAKE_ORG_ID)
    assert pid == _FAKE_PROJECT_ID
    assert len(captured) == 1
    assert "/users/me/organizations" not in captured[0]
    assert f"org_id={_FAKE_ORG_ID}" in captured[0]


def test_resolve_project_id_not_found_loud_fails():
    """NeonApiError raised when no project matches the name (Decision 55)."""
    body = {"projects": [{"id": "p1", "name": "some-other-project"}]}
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(body)):
        with pytest.raises(neon_api.NeonApiError, match="ducklake-catalog.*not found"):
            neon_api.resolve_project_id(_FAKE_API_KEY, org_id=_FAKE_ORG_ID)


def test_resolve_project_id_non_2xx_loud_fails():
    """NeonApiError raised on HTTP error (e.g. 401 Unauthorized)."""
    http_err = urllib.error.HTTPError(
        url="https://example.com",
        code=401,
        msg="Unauthorized",
        hdrs=None,
        fp=None,  # type: ignore[arg-type]
    )
    http_err.read = lambda: b"unauthorized"
    with patch("urllib.request.urlopen", side_effect=http_err):
        with pytest.raises(neon_api.NeonApiError, match="401"):
            neon_api.resolve_project_id(_FAKE_API_KEY, org_id=_FAKE_ORG_ID)


# ---------------------------------------------------------------------------
# create_branch
# ---------------------------------------------------------------------------


def test_create_branch_returns_branch_id_and_host():
    """create_branch returns dict with branch_id and host from the API response."""
    body = {
        "branch": {"id": _FAKE_BRANCH_ID, "name": "br-fake-xyz"},
        "endpoints": [{"type": "read_write", "host": _FAKE_BRANCH_HOST}],
    }
    captured_reqs = []

    def _urlopen(req):
        captured_reqs.append(req)
        return _mock_urlopen(body)

    with patch("urllib.request.urlopen", side_effect=_urlopen):
        result = neon_api.create_branch(_FAKE_API_KEY, _FAKE_PROJECT_ID)

    assert result["branch_id"] == _FAKE_BRANCH_ID
    assert result["host"] == _FAKE_BRANCH_HOST
    assert len(captured_reqs) == 1
    sent_body = json.loads(captured_reqs[0].data)
    assert sent_body == {"endpoints": [{"type": "read_write"}]}


def test_create_branch_picks_primary_endpoint_over_first():
    """When multiple endpoints exist, the read_write/primary endpoint host is preferred."""
    body = {
        "branch": {"id": _FAKE_BRANCH_ID},
        "endpoints": [
            {"type": "read_only", "host": "ro-host.neon.tech"},
            {"type": "read_write", "host": _FAKE_BRANCH_HOST},
        ],
    }
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(body)):
        result = neon_api.create_branch(_FAKE_API_KEY, _FAKE_PROJECT_ID)
    assert result["host"] == _FAKE_BRANCH_HOST


def test_create_branch_falls_back_to_first_endpoint_when_no_rw():
    """Falls back to the first endpoint host when no read_write/primary endpoint is present."""
    body = {
        "branch": {"id": _FAKE_BRANCH_ID},
        "endpoints": [{"type": "replica", "host": "replica.neon.tech"}],
    }
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(body)):
        result = neon_api.create_branch(_FAKE_API_KEY, _FAKE_PROJECT_ID)
    assert result["host"] == "replica.neon.tech"


def test_create_branch_no_branch_id_loud_fails():
    """NeonApiError when API response has no branch id."""
    body = {"branch": {}, "endpoints": [{"type": "read_write", "host": "h.neon.tech"}]}
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(body)):
        with pytest.raises(neon_api.NeonApiError, match="no branch id"):
            neon_api.create_branch(_FAKE_API_KEY, _FAKE_PROJECT_ID)


def test_create_branch_no_endpoint_host_deletes_branch_and_loud_fails():
    """When endpoint host is missing, create_branch deletes the created branch then raises."""
    create_body = {"branch": {"id": _FAKE_BRANCH_ID}, "endpoints": []}
    captured_methods = []

    def _urlopen(req):
        captured_methods.append(req.get_method())
        if req.get_method() == "POST":
            return _mock_urlopen(create_body)
        # DELETE response (cleanup)
        resp = MagicMock()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        resp.read.return_value = b""
        return resp

    with patch("urllib.request.urlopen", side_effect=_urlopen):
        with pytest.raises(neon_api.NeonApiError, match="no endpoint host"):
            neon_api.create_branch(_FAKE_API_KEY, _FAKE_PROJECT_ID)

    assert "POST" in captured_methods
    assert "DELETE" in captured_methods


def test_create_branch_no_endpoint_host_still_raises_when_cleanup_delete_fails():
    """A failed cleanup DELETE is swallowed so the original no-endpoint error surfaces."""
    create_body = {"branch": {"id": _FAKE_BRANCH_ID}, "endpoints": []}

    def _urlopen(req):
        if req.get_method() == "POST":
            return _mock_urlopen(create_body)
        raise neon_api.NeonApiError("delete failed")

    with patch("urllib.request.urlopen", side_effect=_urlopen):
        with pytest.raises(neon_api.NeonApiError, match="no endpoint host"):
            neon_api.create_branch(_FAKE_API_KEY, _FAKE_PROJECT_ID)


def test_create_branch_non_2xx_loud_fails():
    """NeonApiError raised on HTTP error from create_branch."""
    http_err = urllib.error.HTTPError(
        url="https://example.com",
        code=500,
        msg="Server Error",
        hdrs=None,
        fp=None,  # type: ignore[arg-type]
    )
    http_err.read = lambda: b"internal server error"
    with patch("urllib.request.urlopen", side_effect=http_err):
        with pytest.raises(neon_api.NeonApiError, match="500"):
            neon_api.create_branch(_FAKE_API_KEY, _FAKE_PROJECT_ID)


# ---------------------------------------------------------------------------
# delete_branch
# ---------------------------------------------------------------------------


def test_delete_branch_issues_delete_request():
    """delete_branch issues a DELETE request to the correct URL."""
    captured_reqs = []

    def _urlopen(req):
        captured_reqs.append(req)
        resp = MagicMock()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        resp.read.return_value = b""
        return resp

    with patch("urllib.request.urlopen", side_effect=_urlopen):
        neon_api.delete_branch(_FAKE_API_KEY, _FAKE_PROJECT_ID, _FAKE_BRANCH_ID)

    assert len(captured_reqs) == 1
    req = captured_reqs[0]
    assert req.get_method() == "DELETE"
    assert _FAKE_BRANCH_ID in req.full_url


def test_delete_branch_non_2xx_loud_fails():
    """NeonApiError raised on HTTP error from delete_branch."""
    http_err = urllib.error.HTTPError(
        url="https://example.com",
        code=404,
        msg="Not Found",
        hdrs=None,
        fp=None,  # type: ignore[arg-type]
    )
    http_err.read = lambda: b"not found"
    with patch("urllib.request.urlopen", side_effect=http_err):
        with pytest.raises(neon_api.NeonApiError, match="404"):
            neon_api.delete_branch(_FAKE_API_KEY, _FAKE_PROJECT_ID, _FAKE_BRANCH_ID)


# ---------------------------------------------------------------------------
# Authorization header
# ---------------------------------------------------------------------------


def test_api_request_sends_bearer_token():
    """All API requests must include Authorization: Bearer <api_key> header."""
    captured_reqs = []

    resp_body = {"projects": [{"id": _FAKE_PROJECT_ID, "name": "ducklake-catalog"}]}
    resp = MagicMock()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    resp.read.return_value = json.dumps(resp_body).encode()

    def _urlopen(req):
        captured_reqs.append(req)
        return resp

    with patch("urllib.request.urlopen", side_effect=_urlopen):
        neon_api.resolve_project_id(_FAKE_API_KEY, org_id=_FAKE_ORG_ID)

    assert len(captured_reqs) == 1
    assert captured_reqs[0].get_header("Authorization") == f"Bearer {_FAKE_API_KEY}"
