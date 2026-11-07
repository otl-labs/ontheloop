#!/usr/bin/env python3
"""Deploy gate for the governed Lambda code-deploy channels (Decision 249, rec-4912).

Both channels (deploy-ducklake-lambdas.yml, deploy-prod-lambdas.yml) are woken by a completed CI run and call
this module through .github/workflows/deploy-gate.yml. `resolve` picks the newest main commit whose main-validate
full-tier step passed and decides deploy or skip against the channel's deploy records; `freshness` replaces the
inline deploy-record asserts. Stdlib only at import so `freshness` runs on the runner's system python3; the
pathspecs and the function lists are imported lazily inside `resolve`.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

FULL_TIER_JOB = "main-validate"
FULL_TIER_STEP = "Validate full tier (ruff, mypy, pytest, DQ runner, verifier harness)"
GATE_MODULE = "scripts/ci/deploy_gate.py"
RECORD_BUCKET = "agent-platform-data-lake"
ABSENT_RECORD_TEXT = "(404) when calling the HeadObject operation"
DEFAULT_REPO = "otl-labs/ontheloop"


class GateError(RuntimeError):
    """The gate could not decide; callers fail closed and never fall back to main HEAD."""


class Git:
    """Thin git runner over one working directory."""

    def __init__(self, cwd: Optional[str] = None) -> None:
        self.cwd = cwd

    def run(self, *args: str) -> tuple[int, str]:
        result = subprocess.run(
            ["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=self.cwd
        )
        return result.returncode, result.stdout.strip()

    def out(self, *args: str) -> str:
        rc, out = self.run(*args)
        if rc != 0:
            raise GateError(f"git {' '.join(args)} failed (rc={rc})")
        return out

    def ok(self, *args: str) -> bool:
        return self.run(*args)[0] == 0


class GithubApi:
    """Read-only GitHub REST client; any HTTP or JSON failure raises GateError."""

    def __init__(self, base: Optional[str] = None, token: Optional[str] = None, repo: Optional[str] = None) -> None:
        self.base = (base or os.environ.get("GITHUB_API_URL") or "https://api.github.com").rstrip("/")
        self.token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        self.repo = repo or os.environ.get("GITHUB_REPOSITORY") or DEFAULT_REPO

    def get(self, path: str) -> dict[str, Any]:
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "deploy-gate"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        url = f"{self.base}/repos/{self.repo}/{path.lstrip('/')}"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as resp:  # noqa: S310
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise GateError(f"GitHub API read failed for {path}: {exc}") from exc


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _full_tier_passed(api: Any, run: dict[str, Any]) -> bool:
    jobs = api.get(f"actions/runs/{run['id']}/jobs?filter=latest&per_page=100").get("jobs", [])
    for job in jobs:
        if job.get("name") == FULL_TIER_JOB and job.get("conclusion") == "success":
            return any(s.get("name") == FULL_TIER_STEP and s.get("conclusion") == "success" for s in job.get("steps", []))
    return False


def newest_green_sha(
    api: Any,
    git: Git,
    *,
    limit: int = 50,
    settled_before: Optional[datetime] = None,
    ref: str = "origin/main",
) -> Optional[str]:
    """The newest first-parent commit of `ref` whose CI push run passed the main-validate full-tier step."""
    shas = git.out("rev-list", "--first-parent", "-n", str(limit), ref).split()
    for sha in shas:
        query = urllib.parse.urlencode({"head_sha": sha, "event": "push", "branch": "main"})
        runs = api.get(f"actions/workflows/ci.yml/runs?{query}").get("workflow_runs", [])
        for run in runs:
            if run.get("head_sha") != sha or run.get("conclusion") != "success":
                continue
            if settled_before is not None and _parse_time(run["updated_at"]) > settled_before:
                continue
            if _full_tier_passed(api, run):
                return sha
    return None


def _resolvable(git: Git, sha: Optional[str]) -> bool:
    return bool(sha) and git.ok("cat-file", "-e", f"{sha}^{{commit}}")


def _latest_watched(git: Git, rev: str, pathspecs: tuple[str, ...]) -> str:
    return git.out("log", "-1", "--format=%H", rev, "--", *pathspecs)


def _oldest_record(git: Git, shas: list[str]) -> Optional[str]:
    for candidate in shas:
        if all(git.ok("merge-base", "--is-ancestor", candidate, other) for other in shas):
            return candidate
    return None


def decide(
    target: str,
    records: dict[str, Optional[str]],
    pathspecs: tuple[str, ...],
    git: Git,
    force: bool,
) -> tuple[bool, str]:
    """(deploy, reason) for `target` against the channel's deploy records; rules 0-5 in order."""
    if not git.ok("cat-file", "-e", f"{target}:{GATE_MODULE}"):
        return False, "pre_gate_target"
    for sha in records.values():
        if _resolvable(git, sha) and sha != target and git.ok("merge-base", "--is-ancestor", target, str(sha)):
            return False, "newer_deployed"
    if any(not _resolvable(git, sha) for sha in records.values()):
        return True, "baseline_unknown"
    shas = [str(s) for s in records.values()]
    if not force and all(s == target for s in shas):
        return False, "already_deployed"
    if not force:
        oldest = _oldest_record(git, shas)
        if oldest is not None and _latest_watched(git, target, pathspecs) == _latest_watched(git, oldest, pathspecs):
            return False, "no_source_change"
    return True, "source_changed"


def read_record_sha(channel: str, function: str, run: Optional[Callable[..., Any]] = None) -> Optional[str]:
    """source_git_sha of a deploy record; None for an absent record or a null sha; GateError on any other failure."""
    runner = run or subprocess.run
    cmd = ["aws", "s3", "cp", f"s3://{RECORD_BUCKET}/deploy-records/{channel}/{function}.json", "-"]
    if os.environ.get("AWS_REGION"):
        cmd += ["--region", os.environ["AWS_REGION"]]
    result = runner(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        if ABSENT_RECORD_TEXT in (result.stderr or ""):
            return None
        raise GateError(f"aws s3 cp failed for {channel}/{function}: {(result.stderr or '').strip()[-300:]}")
    try:
        return json.loads(result.stdout).get("source_git_sha") or None
    except ValueError as exc:
        raise GateError(f"deploy record {channel}/{function} is not JSON: {exc}") from exc


def _channel_inputs(channel: str) -> tuple[list[str], tuple[str, ...]]:
    # importlib, not an import statement: only `resolve` has pyyaml/pydantic installed, and `freshness` runs on the
    # runner's bare system python3, so the dependency closure of this module must stay stdlib.
    config = importlib.import_module("scripts.build_lambda_config")
    drift = importlib.import_module("scripts.convergence_health.code_drift")
    if channel == "ducklake":
        return list(config._build_ducklake_function_zip_keys()), drift.DUCKLAKE_SOURCE_PATHSPECS
    return list(config._build_prod_function_names()), drift.PROD_SOURCE_PATHSPECS


def _emit(outputs: dict[str, str], manifest: dict[str, Any], manifest_path: str) -> None:
    Path(manifest_path).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.writelines(f"{k}={v}\n" for k, v in outputs.items())


def resolve(
    channel: str,
    force: bool,
    manifest_path: str,
    api: Any = None,
    git: Optional[Git] = None,
    reader: Callable[[str, str], Optional[str]] = read_record_sha,
    inputs: Optional[tuple[list[str], tuple[str, ...]]] = None,
) -> int:
    git = git or Git()
    api = api or GithubApi()
    try:
        if git.out("rev-parse", "--is-shallow-repository") == "true":
            raise GateError("refusing to run against a shallow clone (checkout with fetch-depth 0)")
        if os.environ.get("GITHUB_ACTIONS") == "true":
            git.out("fetch", "origin", "main")
        functions, pathspecs = inputs or _channel_inputs(channel)
        sha = newest_green_sha(api, git)
        records = {fn: reader(channel, fn) for fn in functions}
        if sha is None:
            deploy, reason = False, "no_green_in_window"
            print("::notice::DEPLOY-GATE no green main commit within the window; skipping")
        else:
            deploy, reason = decide(sha, records, pathspecs, git, force)
    except GateError as exc:
        print(f"::error::DEPLOY-GATE failed closed: {exc}", file=sys.stderr)
        return 2
    shown = sha or "none"
    print(f"DEPLOY-GATE {'deploy' if deploy else 'skip'} reason={reason} sha={shown}")
    manifest = {
        "channel": channel,
        "sha": sha,
        "deploy": deploy,
        "reason": reason,
        "records": records,
        "run_id": os.environ.get("GITHUB_RUN_ID"),
    }
    _emit({"sha": sha or "", "deploy": str(deploy).lower(), "reason": reason}, manifest, manifest_path)
    return 0


def freshness(
    channel: str, expected: str, functions: list[str], reader: Callable[[str, str], Optional[str]] = read_record_sha
) -> int:
    for fn in functions:
        try:
            actual = reader(channel, fn)
        except GateError as exc:
            print(f"::error::DEPLOY_RECORD_STALE {fn}: {exc}", file=sys.stderr)
            return 1
        if actual is None:
            print(
                f"::error::DEPLOY_RECORD_STALE {fn}: record absent or null {ABSENT_RECORD_TEXT} -- a stale or skipped "
                "deploy cannot report smoke-green (Decision 55 false-green guard).",
                file=sys.stderr,
            )
            return 1
        if actual != expected:
            print(
                f"::error::DEPLOY_RECORD_STALE {fn}: source_git_sha={actual} != gate sha={expected} -- a stale or skipped "
                "deploy cannot report smoke-green (Decision 55 false-green guard).",
                file=sys.stderr,
            )
            return 1
        print(f"OK {fn} deploy record fresh: {actual}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="deploy_gate")
    sub = parser.add_subparsers(dest="cmd", required=True)
    res = sub.add_parser("resolve")
    res.add_argument("--channel", choices=["ducklake", "prod"], required=True)
    res.add_argument("--force", action="store_true")
    res.add_argument("--manifest", required=True)
    fresh = sub.add_parser("freshness")
    fresh.add_argument("--channel", choices=["ducklake", "prod"], required=True)
    fresh.add_argument("--expected-sha", required=True)
    fresh.add_argument("functions", nargs="+")
    args = parser.parse_args(argv)
    if args.cmd == "resolve":
        return resolve(args.channel, args.force, args.manifest)
    return freshness(args.channel, args.expected_sha, args.functions)


if __name__ == "__main__":
    sys.exit(main())
