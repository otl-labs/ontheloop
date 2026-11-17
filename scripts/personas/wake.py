"""The wake verb: one value-free PR comment per verdict run so a session subscribed to the PR wakes when checks land.

The comment carries one marker per (head SHA, route) pair not yet marked and never a verdict, title, summary, count or
any text read from the PR or a result file; the verdict is read only from the persona-verdict/ check runs. A run whose
prepared artifact landed but which ends without every check run posted gets a value-free fault wake instead. Route ids
are validated by pattern only; this module names no persona id and reads no branch name (PLAN-persona-verdict-wake).
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from scripts.personas import resolve_pr, route

BOT_LOGIN = "github-actions[bot]"
CHECK_PREFIX = "persona-verdict/"
RETRIES = 3
PER_PAGE = 100
SHA = re.compile(r"[0-9a-f]{40}")
ROUTE_ID = re.compile(r"[a-z][a-z0-9_]*(\.[a-z0-9_]+)?")
DIGITS = re.compile(r"[0-9]{1,20}")
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
WAKE_TEXT = (
    "Persona verdict check runs are posted for {sha} (run {url}). Automated wake signal for a watching session; "
    "this comment carries no verdict - read the persona-verdict/ check runs on that head."
)
FAULT_TEXT = (
    "Persona verdict run {url} for {sha} ended without posting every check run. Automated wake signal for a "
    "watching session; this comment carries no verdict - read that run."
)
Sleep = Callable[[float], None]


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _paged(path: str, token: str | None, key: str | None, sleep: Sleep) -> list[Any]:
    sep = "&" if "?" in path else "?"
    for attempt in range(RETRIES):
        try:
            rows: list[Any] = []
            page = 1
            while True:
                status, raw = resolve_pr.request("GET", f"{path}{sep}per_page={PER_PAGE}&page={page}", token)
                if status != 200:
                    raise RuntimeError(f"GET {path} returned {status}")
                doc = json.loads(raw)
                chunk = doc.get(key, []) if key else doc
                if not isinstance(chunk, list):
                    raise RuntimeError(f"GET {path} returned an unexpected shape")
                rows.extend(chunk)
                if len(chunk) < PER_PAGE:
                    return rows
                page += 1
        except Exception:  # noqa: BLE001 - listing failures are handled by the caller's policy
            if attempt < RETRIES - 1:
                sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"listing {path} failed after {RETRIES} tries")


def landed_names(repo: str, head_sha: str, token: str | None, sleep: Sleep) -> set[str] | None:
    try:
        rows = _paged(f"/repos/{repo}/commits/{head_sha}/check-runs", token, "check_runs", sleep)
    except RuntimeError:
        return None
    return {str(r.get("name")) for r in rows if isinstance(r, dict)}


def bot_marker_lines(repo: str, number: int, token: str | None, sleep: Sleep) -> set[str]:
    try:
        rows = _paged(f"/repos/{repo}/issues/{number}/comments", token, None, sleep)
    except RuntimeError:
        return set()
    lines: set[str] = set()
    for row in rows:
        user = row.get("user") if isinstance(row, dict) else None
        if isinstance(user, dict) and user.get("login") == BOT_LOGIN and user.get("type") == "Bot":
            lines.update(line.strip() for line in str(row.get("body") or "").splitlines())
    return lines


def post_comment(repo: str, number: int, token: str | None, markers: list[str], text: str, sleep: Sleep) -> None:
    body = "\n".join([*markers, text])
    status = 0
    for attempt in range(RETRIES):
        if attempt > 0:
            sleep(2.0 * attempt)
            if all(m in bot_marker_lines(repo, number, token, sleep) for m in markers):
                return
        status, _ = resolve_pr.request("POST", f"/repos/{repo}/issues/{number}/comments", token, {"body": body})
        if status == 201:
            return
    raise RuntimeError(f"wake comment POST failed with {status}")


def _skip(reason: str) -> int:
    print(f"PERSONA-VERDICT wake skipped reason={reason}")
    return 0


def _gate(work: Path, event: dict[str, Any], env: dict[str, str]) -> tuple[str, dict[str, Any]]:
    """Return (skip reason, {}) or ("", the validated inputs)."""
    repo = env.get("GITHUB_REPOSITORY", "")
    resolution = _read(work / resolve_pr.RESOLUTION)
    routes_doc = _read(work / route.ROUTES)
    if not isinstance(resolution, dict) or not isinstance(routes_doc, dict):
        return "no_prepared_artifact", {}
    if not resolution.get("resolved"):
        return "unresolved", {}
    if not resolution.get("allowlisted"):
        return "author_not_allowlisted", {}
    routes = routes_doc.get("routes")
    if not isinstance(routes, list) or not routes:
        return "no_routes", {}
    run = event.get("workflow_run") if isinstance(event, dict) else None
    head_repo = ((run or {}).get("head_repository") or {}).get("full_name") if isinstance(run, dict) else None
    if not REPO.fullmatch(repo) or head_repo != repo:
        return "head_repository_differs", {}
    head_sha = str(resolution.get("head_sha") or "")
    number = resolution.get("number")
    if not SHA.fullmatch(head_sha) or not isinstance(number, int) or number <= 0:
        return "bad_head", {}
    run_id, attempt = env.get("GITHUB_RUN_ID", ""), env.get("GITHUB_RUN_ATTEMPT", "1")
    if not (DIGITS.fullmatch(run_id) and DIGITS.fullmatch(attempt)):
        return "bad_run_identity", {}
    return "", {"routes": routes, "head_sha": head_sha, "number": number, "run_id": run_id, "attempt": attempt}


def wake(work: Path, event: dict[str, Any], env: dict[str, str], sleep: Sleep = time.sleep) -> int:
    repo = env.get("GITHUB_REPOSITORY", "")
    token = env.get("GH_TOKEN")
    reason, ctx = _gate(work, event, env)
    if reason:
        return _skip(reason)
    routes, head_sha, number = ctx["routes"], ctx["head_sha"], ctx["number"]
    run_id, attempt = ctx["run_id"], ctx["attempt"]
    valid = [r for r in routes if isinstance(r, str) and ROUTE_ID.fullmatch(r)]
    if len(valid) != len(routes):
        print("PERSONA-VERDICT wake dropped an invalid route id")
    names = landed_names(repo, head_sha, token, sleep)
    landed = [r for r in valid if names is not None and f"{CHECK_PREFIX}{r}" in names]
    unlanded = len(routes) - len(landed)
    markers_seen = bot_marker_lines(repo, number, token, sleep)
    server = env.get("GITHUB_SERVER_URL", "https://github.com")
    url = f"{server}/{repo}/actions/runs/{run_id}"
    errors: list[str] = []
    new = [f"<!-- persona-verdict-wake:{head_sha}:{r} -->" for r in landed]
    new = [m for m in new if m not in markers_seen]
    if new:
        try:
            post_comment(repo, number, token, new, WAKE_TEXT.format(sha=head_sha, url=url), sleep)
        except RuntimeError as exc:
            errors.append(str(exc))
    fault = env.get("POST_RESULT") != "success" or unlanded > 0
    fault_marker = f"<!-- persona-verdict-fault:{run_id}.{attempt} -->"
    if fault and fault_marker not in markers_seen:
        try:
            post_comment(repo, number, token, [fault_marker], FAULT_TEXT.format(sha=head_sha, url=url), sleep)
        except RuntimeError as exc:
            errors.append(str(exc))
    print(f"PERSONA-VERDICT wake head_sha={head_sha} new={len(new)} fault={fault}")
    if errors:
        raise RuntimeError("; ".join(errors))
    return 0


def main(argv: list[str]) -> int:
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as handle:
        event = json.load(handle)
    return wake(Path.cwd(), event, dict(os.environ))
