"""Resolve the pull request a verdict run judges, from GitHub-set workflow_run fields only (T4.2 c9 identity and allowlist).

Never reads workflow_run.pull_requests[] or any trigger-run artifact. Also holds the one stdlib GitHub client the other
verbs call through this module, so tests patch a single seam. Stdlib only.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

API = "https://api.github.com"
ALLOWED_ROLES = frozenset({"admin", "maintain"})
OVERRIDE_WORKFLOW = ".github/workflows/persona-verdict-override.yml"
RESOLUTION = Path("prepared") / "resolution.json"


def request(
    method: str, url: str, token: str | None, body: Any = None, accept: str = "application/vnd.github+json"
) -> tuple[int, bytes]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "persona-verdict"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url if url.startswith("https://") else API + url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 - fixed https host
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def get_json(path: str, token: str | None) -> Any:
    status, raw = request("GET", path, token)
    if status != 200:
        raise RuntimeError(f"GET {path} returned {status}")
    return json.loads(raw)


def _unresolved(head_sha: str, reason: str) -> dict[str, Any]:
    return {
        "resolved": False,
        "change_id": "",
        "number": 0,
        "head_sha": head_sha,
        "base_sha": "",
        "base_ref": "",
        "draft": False,
        "author_login": "",
        "author_id": 0,
        "allowlisted": False,
        "reason": reason,
    }


def is_allowlisted(repo: str, login: str, token: str | None) -> bool:
    try:
        status, raw = request("GET", f"/repos/{repo}/collaborators/{login}/permission", token)
        if status != 200:
            return False
        return json.loads(raw).get("role_name") in ALLOWED_ROLES
    except Exception:  # noqa: BLE001 - any error is fail closed
        return False


def resolve_override(run: dict[str, Any], repo: str, token: str | None) -> dict[str, Any]:
    """An approved override run resolves to its PR (open, head unmoved, same repository) with no routes."""
    from scripts.personas import override  # noqa: PLC0415 - override imports this module

    head_sha = str(run.get("head_sha") or "")
    try:
        run_id = int(str(run.get("id")))
        proven = override.approval(repo, token, run_id)
    except (TypeError, ValueError, override.Refused):
        return _unresolved(head_sha, "the override run is not proven")
    if proven is None:
        return _unresolved(head_sha, "the override run holds no proven approval")
    try:
        row = get_json(f"/repos/{repo}/pulls/{proven['pr']}", token)
    except Exception:  # noqa: BLE001 - fail closed
        return _unresolved(head_sha, "the override pull request is unreadable")
    head = row.get("head") if isinstance(row, dict) else None
    if not (
        isinstance(head, dict)
        and row.get("state") == "open"
        and head.get("sha") == proven["head_sha"]
        and (head.get("repo") or {}).get("full_name") == repo
    ):
        return _unresolved(head_sha, "the override is stale or foreign")
    author = row.get("user") or {}
    return {
        "resolved": True,
        "change_id": f"{repo}#{proven['pr']}",
        "number": int(proven["pr"]),
        "head_sha": proven["head_sha"],
        "base_sha": str((row.get("base") or {}).get("sha") or ""),
        "base_ref": str((row.get("base") or {}).get("ref") or ""),
        "draft": bool(row.get("draft")),
        "author_login": str(author.get("login") or ""),
        "author_id": int(author.get("id") or 0),
        "allowlisted": False,
        "reason": "override",
        "override": {
            k: proven[k]
            for k in ("run_id", "pr", "head_sha", "route", "check_run_id", "ids_digest", "approver", "approved_at", "url")
        },
    }


def resolve(event: dict[str, Any], repo: str, token: str | None) -> dict[str, Any]:
    run = event.get("workflow_run") if isinstance(event, dict) else None
    run = run if isinstance(run, dict) else {}
    head_sha = str(run.get("head_sha") or "")
    if run.get("event") == "workflow_dispatch" and run.get("path") == OVERRIDE_WORKFLOW:
        return resolve_override(run, repo, token)
    if run.get("event") != "pull_request":
        return _unresolved(head_sha, "the trigger run was not a pull_request event")
    head_repo = (run.get("head_repository") or {}).get("full_name")
    branch = run.get("head_branch")
    if not (head_sha and head_repo and branch):
        return _unresolved(head_sha, "the workflow_run lacks head fields")
    owner = str(head_repo).split("/")[0]
    rows = get_json(
        f"/repos/{repo}/pulls?state=open&per_page=100&head={urllib.parse.quote(f'{owner}:{branch}', safe=':')}", token
    )
    match = [
        r
        for r in rows
        if isinstance(r, dict)
        and (r.get("head") or {}).get("sha") == head_sha
        and ((r.get("head") or {}).get("repo") or {}).get("full_name") == head_repo
    ]
    if len(match) != 1:
        return _unresolved(head_sha, f"{len(match)} open pull requests match the head")
    row = match[0]
    author = row.get("user") or {}
    login = str(author.get("login") or "")
    allowed = bool(login) and is_allowlisted(repo, login, token)
    number = int(row["number"])
    return {
        "resolved": True,
        "change_id": f"{repo}#{number}",
        "number": number,
        "head_sha": head_sha,
        "base_sha": str((row.get("base") or {}).get("sha") or ""),
        "base_ref": str((row.get("base") or {}).get("ref") or ""),
        "draft": bool(row.get("draft")),
        "author_login": login,
        "author_id": int(author.get("id") or 0),
        "allowlisted": allowed,
        "reason": "allowlisted" if allowed else "author is not an admin or maintainer",
    }


def write_output(**values: str) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            for key, value in values.items():
                handle.write(f"{key}={value}\n")


def main(argv: list[str]) -> int:
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as handle:
        event = json.load(handle)
    resolution = resolve(event, os.environ["GITHUB_REPOSITORY"], os.environ.get("GH_TOKEN"))
    RESOLUTION.parent.mkdir(parents=True, exist_ok=True)
    RESOLUTION.write_text(json.dumps(resolution, sort_keys=True), encoding="utf-8")
    change = resolution["change_id"] or "none"
    allowed = "true" if resolution["allowlisted"] else "false"
    sha = resolution["head_sha"] if re.fullmatch(r"[0-9a-f]{40}", resolution["head_sha"]) else "unknown"
    override_id = (resolution.get("override") or {}).get("run_id") or "none"
    print(f"PERSONA-VERDICT head_sha={sha} change_id={change} allowlisted={allowed} override={override_id}")
    write_output(allowlisted=allowed)
    return 0
