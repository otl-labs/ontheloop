"""GitHub App installation-token helpers for the scheduled workflow dispatcher (Decision 267, rec-4233).

Stdlib urllib plus PyJWT; jwt and cryptography are imported inside the functions that need them, so importing this
module does no work. Error text carries only a status, a method, a path and the response's message field: never a
token, a JWT or a header.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

USER_AGENT = "otl-workflow-dispatcher"
API_VERSION = "2022-11-28"


class GitHubApiError(RuntimeError):
    def __init__(self, status: int, method: str, path: str, message: str) -> None:
        self.status = status
        self.method = method
        self.path = path
        super().__init__(f"GitHub API {method} {path} returned {status}: {message}")


def app_jwt(client_id: str, private_key_pem: str, now: int) -> str:
    import jwt

    payload = {"iat": now - 60, "exp": now + 540, "iss": client_id}
    return jwt.encode(payload, private_key_pem, algorithm="RS256")


def _message(raw: bytes) -> str:
    try:
        parsed = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        return "(no message)"
    if isinstance(parsed, dict) and isinstance(parsed.get("message"), str):
        return parsed["message"][:200]
    return "(no message)"


class GitHubApi:
    def __init__(self, base: str = "https://api.github.com", timeout: int = 10, opener: Any = None) -> None:
        self.base = base.rstrip("/")
        self.timeout = timeout
        self._open = opener or urllib.request.urlopen

    def call(self, method: str, path: str, token: str, body: Any = None) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(self.base + path, data=data, method=method)
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", API_VERSION)
        request.add_header("User-Agent", USER_AGENT)
        request.add_header("Authorization", f"Bearer {token}")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with self._open(request, timeout=self.timeout) as response:
                status = response.status
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise GitHubApiError(exc.code, method, path.split("?")[0], _message(exc.read() or b"")) from None
        if status < 200 or status >= 300:
            raise GitHubApiError(status, method, path.split("?")[0], _message(raw))
        if not raw:
            return status, None
        return status, json.loads(raw.decode("utf-8"))


def installation_token(api: GitHubApi, app_token: str, repository: str, permissions: dict[str, str]) -> str:
    _, installation = api.call("GET", f"/repos/{repository}/installation", app_token)
    installation_id = installation["id"]
    name = repository.split("/", 1)[1]
    body = {"repositories": [name], "permissions": permissions}
    _, minted = api.call("POST", f"/app/installations/{installation_id}/access_tokens", app_token, body)
    problem = None
    if minted.get("permissions") != permissions:
        problem = f"installation token permissions {sorted(minted.get('permissions') or {})} != requested"
    elif minted.get("repository_selection") != "selected":
        problem = "installation token repository_selection is not 'selected'"
    if problem:
        if minted.get("token"):
            revoke(api, str(minted["token"]))
        raise RuntimeError(problem)
    return str(minted["token"])


def revoke(api: GitHubApi, token: str) -> None:
    status, _ = api.call("DELETE", "/installation/token", token)
    if status != 204:
        raise RuntimeError(f"token revoke returned {status}, expected 204")
