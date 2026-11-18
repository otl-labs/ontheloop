#!/usr/bin/env python3
"""Shared full-tier resolver (Decision 254 cl.2): last-green ref, covered, full_tier_state and prev.

Standard library plus scripts.ci.deploy_gate only, so a later rewire (rec-5108) may import it from the deploy channels.
`newest_green_sha` is re-exported unchanged (push runs, latest attempt; Decision 249 cl.2); `last_green_sha` is the wider
reading used by `covered()` and prev widening, never by the deploy gate. No module-level I/O: api and git are injected.
"""

from __future__ import annotations

import urllib.parse
from typing import Any, Optional

from scripts.ci.deploy_gate import FULL_TIER_JOB, FULL_TIER_STEP, Git, GithubApi, newest_green_sha

__all__ = [
    "FAST_TIER_STEP",
    "FULL_TIER_JOB",
    "FULL_TIER_STEP",
    "GithubApi",
    "Git",
    "covered",
    "fast_tier_ran",
    "full_tier_state",
    "last_green_sha",
    "newest_green_sha",
    "resolve_prev",
]

FAST_TIER_STEP = "Validate fast tier (ruff lint, ruff format, mypy, prompt checks)"
EVENTS = ("push", "workflow_dispatch")
_ABORTED = ("timed_out", "cancelled")


def _runs(api: Any, sha: str, event: str) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({"head_sha": sha, "event": event, "branch": "main"})
    runs = api.get(f"actions/workflows/ci.yml/runs?{query}").get("workflow_runs", [])
    return [r for r in runs if r.get("head_sha") == sha]


def _latest_job(api: Any, run: dict[str, Any]) -> Optional[dict[str, Any]]:
    jobs = api.get(f"actions/runs/{run['id']}/jobs?filter=latest&per_page=100").get("jobs", [])
    return next((j for j in jobs if j.get("name") == FULL_TIER_JOB), None)


def _step(job: dict[str, Any], name: str) -> Optional[dict[str, Any]]:
    return next((s for s in job.get("steps", []) if s.get("name") == name), None)


def _passed_on_any_attempt(api: Any, run: dict[str, Any]) -> bool:
    job = _latest_job(api, run)
    step = _step(job, FULL_TIER_STEP) if job else None
    if step and step.get("conclusion") == "success":
        return True
    for attempt in range(1, int(run.get("run_attempt") or 1)):
        jobs = api.get(f"actions/runs/{run['id']}/attempts/{attempt}/jobs?per_page=100").get("jobs", [])
        old = next((j for j in jobs if j.get("name") == FULL_TIER_JOB), None)
        old_step = _step(old, FULL_TIER_STEP) if old else None
        if old_step and old_step.get("conclusion") == "success":
            return True
    return False


def last_green_sha(api: Any, git: Git, *, ref: str = "origin/main", limit: int = 50) -> Optional[str]:
    """Newest first-parent commit of `ref` whose main-validate run (push or dispatch) passed the full tier on any attempt."""
    for sha in git.out("rev-list", "--first-parent", "-n", str(limit), ref).split():
        for event in EVENTS:
            if any(_passed_on_any_attempt(api, run) for run in _runs(api, sha, event)):
                return sha
    return None


def covered(api: Any, git: Git, sha: str, *, ref: str = "origin/main") -> bool:
    green = last_green_sha(api, git, ref=ref)
    return green is not None and (sha == green or git.ok("merge-base", "--is-ancestor", sha, green))


def full_tier_state(api: Any, sha: str) -> str:
    """'started', 'skipped' or 'absent' for the commit's ci.yml main-validate runs read at their latest attempt."""
    skipped = False
    for event in EVENTS:
        for run in _runs(api, sha, event):
            job = _latest_job(api, run)
            step = _step(job, FULL_TIER_STEP) if job else None
            if job is None or step is None:
                continue
            if step.get("conclusion") == "skipped":
                skipped = True
            elif step.get("status") in ("in_progress", "completed") and not (
                job.get("conclusion") in _ABORTED or step.get("conclusion") in _ABORTED
            ):
                return "started"
    return "skipped" if skipped else "absent"


def resolve_prev(api: Any, git: Git, head: str, *, limit: int = 50) -> Optional[str]:
    """Nearest first-parent ancestor of head (head excluded) whose full tier started; None past the cap."""
    shas = git.out("rev-list", "--first-parent", "-n", str(limit + 1), head).split()[1:]
    for sha in shas:
        if full_tier_state(api, sha) == "started":
            return sha
    return None


def fast_tier_ran(api: Any, sha: str) -> bool:
    """True when a main-validate job of the commit ran ci.yml's fast-tier step (the plans-only branch of the scope fork)."""
    for event in EVENTS:
        for run in _runs(api, sha, event):
            job = _latest_job(api, run)
            step = _step(job, FAST_TIER_STEP) if job else None
            if step and step.get("status") in ("in_progress", "completed") and step.get("conclusion") != "skipped":
                return True
    return False
