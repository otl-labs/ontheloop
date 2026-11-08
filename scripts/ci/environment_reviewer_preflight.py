#!/usr/bin/env python3
"""Fail-closed reviewer-gate preflight for reviewer-gated GitHub Actions jobs (rec-4979, rec-4961).

GitHub creates a missing Environment with no protection the first time a job names it, so a gated job can run with no
recorded reviewer approval. This script is the in-job proof, run as
`python3 -I scripts/ci/environment_reviewer_preflight.py <subcommand> --environment <name>`:

  approvals     the load-bearing check: the run holds an approved entry for <name> by a login in REVIEWER_LOGINS
  environment   the early warning: <name> exists, has a required_reviewers rule with reviewers (and, with
                --require-no-admin-bypass, can_admins_bypass is exactly false)

Standard library only and its own whole import closure (it runs inside credentialed jobs before any credential step,
under -I with no installed packages; Decision 238 cl.3). It prints one marker line and never the token or a response
body, and never writes the runner's env, path or output files (later steps in the same job hold credentials). The
env it reads: GITHUB_API_URL, GITHUB_REPOSITORY, GITHUB_RUN_ID, GH_TOKEN, REVIEWER_LOGINS.

Residuals, stated not fixed: approvals are recorded per run, not per attempt, so a re-run attempt is satisfied by an
earlier attempt's approval; whether an admin bypass writes an approvals entry is unproven (a bypass by a non-listed
admin fails closed here); the check lives in each job's own definition, so a workflow dispatched from a non-main
branch could drop it and the Environment's reviewers stay the only gate on that path (Decision 94's premise, that the
environment sub is approval-gated, holds only for main-defined jobs until rec-4651/rec-4965); the preview job's
environment check runs after its credentials step, so its process environment holds session credentials, which this
script never prints or forwards.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_ATTEMPTS = 3
_REREAD_DELAY = 5


class _Unreadable(Exception):
    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


class _NotFound(Exception):
    pass


def _get(path: str) -> object:
    base = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    request = urllib.request.Request(  # noqa: S310 -- the API base comes from the runner
        f"{base}{path}",
        headers={
            "Authorization": f"Bearer {os.environ.get('GH_TOKEN', '')}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    status = "none"
    for attempt in range(_ATTEMPTS):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
                raw = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise _NotFound from None
            status = str(exc.code)
            if exc.code < 500:
                raise _Unreadable(status) from None
        except urllib.error.URLError:
            status = "none"
        else:
            try:
                return json.loads(raw)
            except ValueError:
                raise _Unreadable("none") from None
        if attempt < _ATTEMPTS - 1:
            time.sleep(2 * (attempt + 1))
    raise _Unreadable(status)


def _logins() -> set[str]:
    return {part.lower() for part in re.split(r"[,\s]+", os.environ.get("REVIEWER_LOGINS", "")) if part}


def _approvals_body(repo: str, run_id: str) -> list[dict]:
    body = _get(f"/repos/{repo}/actions/runs/{run_id}/approvals")
    if not isinstance(body, list) or not all(isinstance(entry, dict) for entry in body):
        raise _Unreadable("none")
    return body


def _approver(body: list[dict], name: str, logins: set[str]) -> str | None:
    for entry in body:
        user = entry.get("user")
        envs = entry.get("environments")
        if entry.get("state") != "approved" or not isinstance(user, dict) or not isinstance(envs, list):
            continue
        login = user.get("login")
        names = {e.get("name") for e in envs if isinstance(e, dict)}
        if name in names and isinstance(login, str) and login.lower() in logins:
            return login
    return None


def _approvals(name: str) -> int:
    logins = _logins()
    if not logins:
        print("REVIEWER_LOGINS_EMPTY")
        return 1
    repo, run_id = os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GITHUB_RUN_ID", "")
    try:
        body = _approvals_body(repo, run_id)
        approver = _approver(body, name, logins)
        if approver is None:
            time.sleep(_REREAD_DELAY)
            body = _approvals_body(repo, run_id)
            approver = _approver(body, name, logins)
    except (_Unreadable, _NotFound) as exc:
        print(f"ENVIRONMENT_CHECK_UNREADABLE approvals status={exc.status if isinstance(exc, _Unreadable) else '404'}")
        return 1
    if approver is None:
        print(f"ENVIRONMENT_APPROVAL_MISSING environment={name} entries={len(body)}")
        return 1
    print(f"ENVIRONMENT_APPROVAL_OK environment={name} approver={approver}")
    return 0


def _reviewer_rules(body: dict) -> int:
    rules = body.get("protection_rules")
    count = 0
    if isinstance(rules, list):
        for rule in rules:
            reviewers = rule.get("reviewers") if isinstance(rule, dict) else None
            if isinstance(rule, dict) and rule.get("type") == "required_reviewers" and isinstance(reviewers, list):
                count += len(reviewers)
    return count


def _environment(name: str, require_no_admin_bypass: bool) -> int:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    try:
        body = _get(f"/repos/{repo}/environments/{name}")
    except _NotFound:
        print(f"ENVIRONMENT_MISSING environment={name}")
        return 1
    except _Unreadable as exc:
        print(f"ENVIRONMENT_CHECK_UNREADABLE environment status={exc.status}")
        return 1
    if not isinstance(body, dict):
        print("ENVIRONMENT_CHECK_UNREADABLE environment status=none")
        return 1
    reviewers = _reviewer_rules(body)
    if reviewers == 0:
        print(f"ENVIRONMENT_UNGATED environment={name}")
        return 1
    bypass = body.get("can_admins_bypass")
    if require_no_admin_bypass and bypass is not False:
        print(f"ENVIRONMENT_ADMIN_BYPASS environment={name}")
        return 1
    word = "absent" if not isinstance(bypass, bool) else str(bypass).lower()
    print(f"ENVIRONMENT_GATED_OK environment={name} reviewers={reviewers} admin_bypass={word}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("approvals", "environment"):
        p = sub.add_parser(command)
        p.add_argument("--environment", required=True)
        if command == "environment":
            p.add_argument("--require-no-admin-bypass", action="store_true")
    args = parser.parse_args(argv)
    if not _NAME_RE.fullmatch(args.environment):
        print("ENVIRONMENT_NAME_INVALID")
        return 1
    if args.command == "approvals":
        return _approvals(args.environment)
    return _environment(args.environment, args.require_no_admin_bypass)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
