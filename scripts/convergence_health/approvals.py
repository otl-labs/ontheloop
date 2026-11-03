"""GitHub-Actions signal helpers for the convergence-health sensor (CD.35 Wave 6 / T2.35).

Queries GitHub Actions for stuck gated-apply approvals and recent Reconcile
episodes. All external dependencies (GitHub API caller) are injected so
unit tests can mock them without live network access. Part of the
scripts.convergence_health package -- see scripts/convergence_health/__init__.py
for the full public surface.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Optional

from scripts import repo_identity
from scripts.convergence_health.record import _parse_utc

STUCK_APPROVAL_THRESHOLD_HOURS: float = 6.0


def filter_stuck_runs(
    runs: list[dict[str, Any]],
    threshold_hours: float,
    now: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """Filter and parse run dicts whose age_hours >= threshold_hours.

    Pure: no network calls, no external dependencies. Shared by find_stuck_gated_approvals
    (status=waiting query) and diagnose_stuck_approvals (any-status diagnostic query).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    stuck: list[dict[str, Any]] = []
    for run in runs:
        created_at_str = run.get("created_at", "")
        if not created_at_str:
            continue
        created_at = _parse_utc(created_at_str)
        age_hours = (now - created_at).total_seconds() / 3600.0
        if age_hours >= threshold_hours:
            stuck.append(
                {
                    "run_id": run.get("id"),
                    "created_at": created_at_str,
                    "age_hours": round(age_hours, 1),
                    "url": run.get("html_url"),
                }
            )
    return stuck


def _make_github_caller(token: str) -> Callable[[str], Any]:
    """Return an authenticated GitHub API caller bound to token, or a no-op returning None.

    Shared by find_stuck_gated_approvals and diagnose_stuck_approvals so auth header
    construction is defined exactly once. Imports are deferred to avoid import-time side effects.
    """
    import json as _json  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    def _caller(url: str) -> Any:
        if not token:
            return None
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            return _json.loads(resp.read())

    return _caller


def find_stuck_gated_approvals(
    gh_caller: Optional[Callable[[str], Any]] = None,
    owner: Optional[str] = None,
    repo: Optional[str] = None,
    threshold_hours: float = STUCK_APPROVAL_THRESHOLD_HOURS,
    now: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """Return terraform-apply-sandbox runs whose gated-apply job is `waiting` beyond threshold.

    gh_caller is injected for testability. When None, reads GH_TOKEN / GITHUB_TOKEN env vars;
    if neither is set, returns [] without error (graceful degradation).
    """
    owner, repo = repo_identity.resolve(owner, repo)
    import os  # noqa: PLC0415

    if now is None:
        now = datetime.now(timezone.utc)

    token = os.environ.get("GH_TOKEN", "") or os.environ.get("GITHUB_TOKEN", "")
    caller = gh_caller or _make_github_caller(token)

    try:
        url = (
            f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/"
            f"terraform-apply-sandbox.yml/runs?status=waiting&per_page=10"
        )
        data = caller(url)
        if not data:
            return []
        return filter_stuck_runs(data.get("workflow_runs", []), threshold_hours, now)
    except Exception:  # noqa: BLE001
        pass
    return []


def find_reconcile_runs_since(
    gh_caller: Optional[Callable[[str], Any]] = None,
    owner: Optional[str] = None,
    repo: Optional[str] = None,
    per_page: int = 10,
) -> list[dict[str, Any]]:
    """Query recent .github/workflows/reconcile.yml Actions runs (any status), newest first.

    T2.37 c4: this is the signal used to detect an in-flight/recent Reconcile episode so the
    stale-rec escalation below does not double-file. gh_caller is injected for testability, same
    pattern as find_stuck_gated_approvals / diagnose_stuck_approvals. Returns [] on any error, a
    None caller result, or a missing GH_TOKEN/GITHUB_TOKEN -- graceful degradation, since this
    signal only ever SUPPRESSES a file action, it never itself triggers escalation.
    """
    owner, repo = repo_identity.resolve(owner, repo)
    import os  # noqa: PLC0415

    token = os.environ.get("GH_TOKEN", "") or os.environ.get("GITHUB_TOKEN", "")
    caller = gh_caller or _make_github_caller(token)

    try:
        url = f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/reconcile.yml/runs?per_page={per_page}"
        data = caller(url)
        if not data:
            return []
        return data.get("workflow_runs", []) or []
    except Exception:  # noqa: BLE001
        pass
    return []


def has_in_flight_reconcile_for_episode(
    runs: list[dict[str, Any]],
    red_since: datetime,
    now: Optional[datetime] = None,
) -> bool:
    """True iff any reconcile.yml run's created_at falls within [red_since, now].

    That window IS "in-flight/recent... within the current red episode window" (T2.37 c4): a
    Reconcile dispatch that started (or already completed) after this episode went red is
    evidence the episode is already being worked, regardless of whether that run is still
    running right now. Deliberately NOT matched by head_sha equality -- a workflow_dispatch run's
    head_sha is the branch tip at dispatch time and will not reliably equal the red commit once
    later commits merge onto main. Pure / injectable: `runs` is normally
    find_reconcile_runs_since()'s output, but tests pass a literal list directly.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    for run in runs:
        created_at_str = run.get("created_at", "")
        if not created_at_str:
            continue
        created_at = _parse_utc(created_at_str)
        if red_since <= created_at <= now:
            return True
    return False


def diagnose_stuck_approvals(
    gh_caller: Optional[Callable[[str], Any]] = None,
    owner: Optional[str] = None,
    repo: Optional[str] = None,
    threshold_hours: float = 0.0,
    now: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """Query recent terraform-apply-sandbox runs of ANY status and parse them via filter_stuck_runs.

    Read-only diagnostic entrypoint: exercises the real-data parse path without requiring a live
    waiting run. At threshold_hours=0 every run object is parsed and returned. Returns [] on any
    error or when the caller yields None. Never escalates or writes.
    """
    owner, repo = repo_identity.resolve(owner, repo)
    import os  # noqa: PLC0415

    if now is None:
        now = datetime.now(timezone.utc)

    token = os.environ.get("GH_TOKEN", "") or os.environ.get("GITHUB_TOKEN", "")
    caller = gh_caller or _make_github_caller(token)

    try:
        url = f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/terraform-apply-sandbox.yml/runs?per_page=20"
        data = caller(url)
        if not data:
            return []
        return filter_stuck_runs(data.get("workflow_runs", []), threshold_hours, now)
    except Exception:  # noqa: BLE001
        pass
    return []
