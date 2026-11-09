"""Neon control-plane REST client (orchestrator-only; never imported by Lambda handlers).

Provides thin wrappers around the Neon HTTPS API (console.neon.tech/api/v2) for the
OQ.12 canary rehearsal branch lifecycle. Uses stdlib urllib only -- zero new dependencies.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

_NEON_API_BASE = "https://console.neon.tech/api/v2"
_NEON_PARAMETER_NAME = "/secret/neon-api-key"


_LOCKED_RETRIES = 5
_LOCKED_BACKOFF_S = 2.0
_OPERATION_POLL_S = 2.0
_OPERATION_WAIT_S = 120.0
_OPERATION_DONE = frozenset({"finished", "failed", "error", "cancelled", "skipped"})


class NeonApiError(RuntimeError):
    """Raised on any non-2xx response from the Neon control-plane API.

    ``status`` carries the HTTP status (None for errors that are not an HTTP response). No
    response body is ever quoted: a branch response can carry connection URIs.
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def fetch_api_key(profile: str | None = None) -> str:
    """Read the Neon API key from SSM Parameter Store (SecureString /secret/neon-api-key).

    Needs ssm:GetParameter with decryption on this parameter, which the operator profile
    (PlatformAdmin, SecretParameterAdmin) holds; PlatformDev does not.
    """
    import boto3  # noqa: PLC0415

    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    client = session.client("ssm", region_name="eu-west-2")
    response = client.get_parameter(Name=_NEON_PARAMETER_NAME, WithDecryption=True)
    secret = response["Parameter"]["Value"]
    try:
        parsed = json.loads(secret)
        return parsed.get("api_key") or parsed.get("NEON_API_KEY") or secret.strip()
    except (json.JSONDecodeError, AttributeError):
        return secret.strip()


def resolve_org_id(api_key: str) -> str:
    """GET /users/me/organizations and return the sole organization id.

    Neon org-scoped API keys require ``org_id`` on the projects list endpoint. Raises
    NeonApiError when zero or more than one organization is visible (ambiguous -- the
    caller must then pass org_id explicitly).
    """
    url = f"{_NEON_API_BASE}/users/me/organizations"
    data = _api_get(api_key, url)
    orgs = data.get("organizations", [])
    if len(orgs) != 1:
        raise NeonApiError(
            f"resolve_org_id: expected exactly 1 organization, found {len(orgs)} -- "
            "pass org_id explicitly or check the Neon API key's org scope"
        )
    org_id = orgs[0].get("id")
    if not org_id:
        raise NeonApiError(f"resolve_org_id: organization has no id: {orgs[0]}")
    return org_id


def resolve_project_id(api_key: str, name: str = "ducklake-catalog", org_id: str | None = None) -> str:
    """GET /projects?org_id=... and return the project_id matching `name`.

    Neon's projects list endpoint requires ``org_id`` for org-scoped API keys; when not
    supplied it is resolved via :func:`resolve_org_id`. Raises NeonApiError if not found.
    """
    if org_id is None:
        org_id = resolve_org_id(api_key)
    url = f"{_NEON_API_BASE}/projects?org_id={urllib.parse.quote(org_id)}"
    data = _api_get(api_key, url)
    for project in data.get("projects", []):
        if project.get("name") == name:
            return project["id"]
    raise NeonApiError(f"Neon project {name!r} not found -- check the Neon console or the project name")


def create_branch(api_key: str, project_id: str, name: str | None = None) -> dict[str, str]:
    """POST /projects/{project_id}/branches (copy-on-write from the main branch).

    Returns ``{"branch_id": str, "host": str}`` where host is the branch endpoint hostname.
    Requests a read_write compute endpoint alongside the branch so the host is available
    immediately in the response (without a separate endpoint-create call).
    """
    url = f"{_NEON_API_BASE}/projects/{project_id}/branches"
    payload: dict[str, Any] = {"endpoints": [{"type": "read_write"}]}
    if name:
        payload["branch"] = {"name": name}
    body = _api_post(api_key, url, payload=payload)
    branch = body.get("branch", {})
    endpoints = body.get("endpoints", [])
    branch_id = branch.get("id")
    if not branch_id:
        raise NeonApiError("create_branch: no branch id in response")
    host: str | None = None
    for ep in endpoints:
        if ep.get("type") in ("read_write", "primary"):
            host = ep.get("host")
            break
    if not host and endpoints:
        host = endpoints[0].get("host")
    if not host:
        # Branch was created but no endpoint host: delete it before raising to avoid a leak.
        try:
            delete_branch(api_key, project_id, branch_id)
        except NeonApiError:
            pass
        raise NeonApiError(f"create_branch: no endpoint host in response (branch {branch_id} deleted)")
    return {"branch_id": branch_id, "host": host}


def delete_branch(api_key: str, project_id: str, branch_id: str) -> None:
    """DELETE /projects/{project_id}/branches/{branch_id}. Raises NeonApiError on non-2xx."""
    url = f"{_NEON_API_BASE}/projects/{project_id}/branches/{branch_id}"
    for attempt in range(_LOCKED_RETRIES + 1):
        wait_operations(api_key, project_id, branch_id)
        try:
            _api_delete(api_key, url)
            return
        except NeonApiError as exc:
            if exc.status != 423 or attempt == _LOCKED_RETRIES:
                raise
            time.sleep(_LOCKED_BACKOFF_S)
    raise NeonApiError("unreachable")  # pragma: no cover


def list_branches(api_key: str, project_id: str) -> list[dict[str, str]]:
    """GET /projects/{project_id}/branches; names and ids only."""
    data = _api_get(api_key, f"{_NEON_API_BASE}/projects/{project_id}/branches")
    return [{"id": b.get("id", ""), "name": b.get("name", "")} for b in data.get("branches", [])]


def wait_operations(
    api_key: str,
    project_id: str,
    branch_id: str,
    *,
    timeout_s: float = _OPERATION_WAIT_S,
    poll_s: float = _OPERATION_POLL_S,
) -> None:
    """Block until every operation on the branch has finished (Neon serialises operations per branch)."""
    deadline = time.monotonic() + timeout_s
    url = f"{_NEON_API_BASE}/projects/{project_id}/operations"
    while True:
        data = _api_get(api_key, url)
        pending = [
            op
            for op in data.get("operations", [])
            if op.get("branch_id") == branch_id and op.get("status") not in _OPERATION_DONE
        ]
        if not pending:
            return
        if time.monotonic() >= deadline:
            raise NeonApiError(f"wait_operations: branch {branch_id} still has {len(pending)} unfinished operation(s)")
        time.sleep(poll_s)


def _branch_post(api_key: str, project_id: str, branch_id: str, path: str, payload: Any) -> Any:
    url = f"{_NEON_API_BASE}/projects/{project_id}/branches/{branch_id}/{path}"
    for attempt in range(_LOCKED_RETRIES + 1):
        wait_operations(api_key, project_id, branch_id)
        try:
            return _api_post(api_key, url, payload)
        except NeonApiError as exc:
            if exc.status != 423 or attempt == _LOCKED_RETRIES:
                raise
            time.sleep(_LOCKED_BACKOFF_S)
    raise NeonApiError("unreachable")  # pragma: no cover


def create_role(api_key: str, project_id: str, branch_id: str, role_name: str) -> str:
    """POST .../branches/{branch_id}/roles and return the generated password (the only place it is returned)."""
    body = _branch_post(api_key, project_id, branch_id, "roles", {"role": {"name": role_name}})
    password = (body.get("role") or {}).get("password")
    if not password:
        raise NeonApiError("create_role: no password in response")
    return password


def create_database(api_key: str, project_id: str, branch_id: str, db_name: str, owner_name: str) -> None:
    """POST .../branches/{branch_id}/databases owned by ``owner_name``."""
    _branch_post(api_key, project_id, branch_id, "databases", {"database": {"name": db_name, "owner_name": owner_name}})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _api_request(api_key: str, url: str, method: str, payload: Any = None) -> Any:
    body: bytes | None = None
    headers: dict[str, str] = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }
    if payload is not None:
        body = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raise NeonApiError(f"Neon API {method} {url} -> {exc.code}", status=exc.code) from exc


def _api_get(api_key: str, url: str) -> Any:
    return _api_request(api_key, url, "GET")


def _api_post(api_key: str, url: str, payload: Any) -> Any:
    return _api_request(api_key, url, "POST", payload)


def _api_delete(api_key: str, url: str) -> None:
    _api_request(api_key, url, "DELETE")
