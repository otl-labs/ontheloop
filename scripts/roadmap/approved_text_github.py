"""GitHub change-surface adapter for the approved-text approval (rec-4769; ports.yaml SA-05).

The operator approves a sidecar on the `approved-text` Environment gate of an approved-text-approval.yml run
dispatched from main; this adapter reads GitHub's record of that approval. Imports only the standard library,
approved_words, approved_text_provenance and scripts.repo_identity.

CLI (each subcommand takes --repo OWNER/NAME; omitted, it defaults to repo_identity.full_name(): GITHUB_REPOSITORY in
CI, the repository fallback elsewhere):
  check --slug S [--digest H]                    print the grandfather and lookup verdicts
  approvals --run-id N                           print each approval of a run (the read-only shape probe)
  wake --slug S --sha X --digest H --run-id R    comment on open PRs whose head is X, once run R records an approver
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from scripts import repo_identity
from scripts.roadmap import approved_text_provenance as prov
from scripts.roadmap import approved_words
from scripts.roadmap.approved_text_provenance import Verdict

ROOT = Path(__file__).resolve().parents[2]
API = "https://api.github.com"
TOKEN_ENV = "APPROVED_TEXT_GH_TOKEN"
WORKFLOW_PATH = ".github/workflows/approved-text-approval.yml"
WORKFLOW_FILE = "approved-text-approval.yml"
ENVIRONMENT = "approved-text"
APPROVE_JOB = "approve"
REQUEST_TIMEOUT = 10
RUN_PAGES = 10
PULL_PAGES = 3
CANDIDATE_RUNS = 5
WAKE_MARKER = "<!-- approved-text-wake:{digest} -->"


class GitHubError(Exception):
    """A request or its body could not be used; the message is the UNVERIFIED reason."""


def token() -> str | None:
    return os.environ.get(TOKEN_ENV) or None


def auth_label() -> str:
    return "token" if token() else "anonymous"


def _headers(tok: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "otl-approved-text",
    }
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    return headers


def _send(request: urllib.request.Request, timeout: float) -> Any:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 -- https GitHub API only
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise GitHubError(f"HTTP {exc.code} from {request.full_url.split('?')[0]}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GitHubError(f"request failed: {exc}") from exc
    except (ValueError, UnicodeDecodeError) as exc:
        raise GitHubError(f"unparsable body from {request.full_url.split('?')[0]}") from exc


def http_get(url: str, tok: str | None, timeout: float) -> Any:
    return _send(urllib.request.Request(url, headers=_headers(tok)), timeout)  # noqa: S310


def http_post(url: str, tok: str | None, body: dict[str, Any], timeout: float) -> Any:
    request = urllib.request.Request(  # noqa: S310
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={**_headers(tok), "Content-Type": "application/json"},
        method="POST",
    )
    return _send(request, timeout)


def run_title(slug: str, approved: str) -> str:
    return f"approved-text {slug} {approved_words.fingerprint(approved)} {approved}"


def _get(url: str, deadline: prov.Deadline) -> Any:
    if deadline.expired():
        raise GitHubError("deadline exceeded")
    return http_get(url, token(), min(float(REQUEST_TIMEOUT), max(deadline.remaining(), 0.1)))


def _is_ancestor(root: Path, sha: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", sha, "origin/main"], cwd=root, capture_output=True, text=True, encoding="utf-8"
    )
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise GitHubError(f"git cannot place {sha[:12]} against origin/main: {result.stderr.strip()[:120]}")


def _list_runs(repo: str, deadline: prov.Deadline) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for page in range(1, RUN_PAGES + 1):
        body = _get(f"{API}/repos/{repo}/actions/runs?event=workflow_dispatch&branch=main&per_page=100&page={page}", deadline)
        batch = body.get("workflow_runs") if isinstance(body, dict) else None
        if not isinstance(batch, list):
            raise GitHubError("runs list has no workflow_runs array")
        runs += [r for r in batch if isinstance(r, dict)]
        if len(batch) < 100:
            break
    return runs


def _cannot_hold_approval(run: dict[str, Any]) -> bool:
    status, conclusion = run.get("status"), run.get("conclusion")
    return status == "waiting" or (status == "completed" and conclusion != "success")


def _approve_job_succeeded(repo: str, run_id: int, deadline: prov.Deadline) -> bool:
    body = _get(f"{API}/repos/{repo}/actions/runs/{run_id}/jobs", deadline)
    jobs = body.get("jobs") if isinstance(body, dict) else None
    if not isinstance(jobs, list):
        raise GitHubError(f"run {run_id} jobs has no jobs array")
    return any(isinstance(j, dict) and j.get("name") == APPROVE_JOB and j.get("conclusion") == "success" for j in jobs)


def approvals(repo: str, run_id: int, deadline: prov.Deadline) -> list[dict[str, Any]]:
    body = _get(f"{API}/repos/{repo}/actions/runs/{run_id}/approvals", deadline)
    if not isinstance(body, list):
        raise GitHubError(f"run {run_id} approvals is not a list")
    return [a for a in body if isinstance(a, dict)]


def _approver(entries: list[dict[str, Any]]) -> str | None:
    for entry in entries:
        names = [e.get("name") for e in entry.get("environments") or [] if isinstance(e, dict)]
        if entry.get("state") == "approved" and ENVIRONMENT in names:
            user = entry.get("user")
            return str(user.get("login")) if isinstance(user, dict) and user.get("login") else "unknown"
    return None


def lookup(slug: str, approved: str, root: Path, deadline: prov.Deadline, repo: str | None = None) -> Verdict:
    """APPROVED only for a main-dispatched run of the approval workflow with this title, a successful approve job and
    an approved entry for approved-text; UNVERIFIED on any transport, deadline or git failure; else MISSING."""
    repo = repo or repo_identity.full_name()
    title = run_title(slug, approved)
    try:
        candidates = [
            r
            for r in _list_runs(repo, deadline)
            if r.get("path") == WORKFLOW_PATH
            and r.get("display_title") == title
            and r.get("head_branch") == "main"
            and not _cannot_hold_approval(r)
        ]
        viable = [r for r in candidates if isinstance(r.get("head_sha"), str) and _is_ancestor(root, r["head_sha"])]
        for run in viable[:CANDIDATE_RUNS]:
            run_id = run.get("id")
            if not isinstance(run_id, int) or not _approve_job_succeeded(repo, run_id, deadline):
                continue
            approver = _approver(approvals(repo, run_id, deadline))
            if approver:
                return Verdict(prov.APPROVED, f"run {run_id} approver {approver}")
    except GitHubError as exc:
        return Verdict(prov.UNVERIFIED, str(exc))
    return Verdict(prov.MISSING, f"no approved run titled `{title}` on main")


def resolve(
    slug: str,
    sidecar: approved_words.Sidecar,
    root: Path,
    in_push: bool,
    deadline: prov.Deadline,
    *,
    informational: bool,
    event_name: str | None = None,
) -> tuple[Verdict | None, str, str | None]:
    """The two checks' shared seam: (verdict, mode, info). A push skips the lookup (verdict None)."""
    event = os.environ.get("GITHUB_EVENT_NAME") if event_name is None else event_name
    check_mode = prov.mode(in_push, event == "pull_request")
    if check_mode == prov.SKIP:
        return None, check_mode, None
    verdict = prov.provenance(slug, sidecar, root, lambda s, d: lookup(s, d, root, deadline))
    if verdict.kind == prov.GRANDFATHERED and informational and check_mode == prov.ENFORCE:
        seen = lookup(slug, approved_words.approved_digest(sidecar), root, deadline)
        return verdict, check_mode, f"lookup (informational): {seen.kind} auth={auth_label()}"
    return verdict, check_mode, None


def _cmd_check(args: argparse.Namespace) -> int:
    root = args.root
    try:
        sidecar = approved_words.parse_sidecar((root / approved_words.sidecar_rel(args.slug)).read_text(encoding="utf-8"))
    except (OSError, approved_words.ApprovedTextError) as exc:
        print(f"APPROVED-TEXT ERROR {exc}")
        return 2
    approved = args.digest or approved_words.approved_digest(sidecar)
    pinned = prov.grandfather(args.slug, sidecar, root)
    print(f"grandfather: {pinned if pinned is not None else 'NOT GRANDFATHERED'}")
    found = lookup(args.slug, approved, root, prov.Deadline(prov.DEADLINE_SECONDS), args.repo)
    print(f"lookup: {found} auth={auth_label()}")
    grandfathered = pinned is not None and pinned.kind == prov.GRANDFATHERED
    return 0 if found.kind == prov.APPROVED or grandfathered else 1


def _cmd_approvals(args: argparse.Namespace) -> int:
    try:
        entries = approvals(args.repo or repo_identity.full_name(), args.run_id, prov.Deadline(prov.DEADLINE_SECONDS))
    except GitHubError as exc:
        print(f"approvals: ERROR {exc}")
        return 2
    for entry in entries:
        envs = ",".join(str(e.get("name")) for e in entry.get("environments") or [] if isinstance(e, dict))
        user = entry.get("user")
        login = user.get("login") if isinstance(user, dict) else None
        print(f"state={entry.get('state')} environment={envs} login={login}")
    return 0


def _cmd_wake(args: argparse.Namespace) -> int:
    repo = args.repo or repo_identity.full_name()
    deadline = prov.Deadline(prov.DEADLINE_SECONDS)
    try:
        if _approver(approvals(repo, int(args.run_id), deadline)) is None:
            print(f"wake: no approver recorded for run {args.run_id}; nothing posted")
            return 1
        targets: list[int] = []
        for page in range(1, PULL_PAGES + 1):
            batch = _get(f"{API}/repos/{repo}/pulls?state=open&per_page=100&page={page}", deadline)
            if not isinstance(batch, list):
                raise GitHubError("pulls list is not a list")
            targets += [
                int(p["number"]) for p in batch if isinstance(p, dict) and (p.get("head") or {}).get("sha") == args.sha
            ]
            if len(batch) < 100:
                break
        body = (
            f"{WAKE_MARKER.format(digest=args.digest)}\n"
            f"Approved text `{args.slug}` ({approved_words.fingerprint(args.digest)}) was approved in run {args.run_id}: "
            "re-run pr-validate on this PR."
        )
        for number in targets:
            http_post(f"{API}/repos/{repo}/issues/{number}/comments", token(), {"body": body}, REQUEST_TIMEOUT)
    except (GitHubError, KeyError, ValueError, TypeError) as exc:
        print(f"wake: ERROR {exc}")
        return 1
    print(f"wake: {len(targets)} open PRs at {args.sha} auth={auth_label()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.roadmap.approved_text_github")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, handler in (("check", _cmd_check), ("approvals", _cmd_approvals), ("wake", _cmd_wake)):
        p = sub.add_parser(name)
        p.add_argument("--repo", default=None)
        p.add_argument("--root", type=Path, default=ROOT)
        p.set_defaults(handler=handler)
        if name == "check":
            p.add_argument("--slug", required=True)
            p.add_argument("--digest", default=None)
        elif name == "approvals":
            p.add_argument("--run-id", type=int, required=True)
        else:
            p.add_argument("--slug", required=True)
            p.add_argument("--sha", required=True)
            p.add_argument("--digest", required=True)
            p.add_argument("--run-id", required=True)
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
