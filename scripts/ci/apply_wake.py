#!/usr/bin/env python3
"""Best-effort post-merge SHA->PR wake for terraform-apply-sandbox.yml (CD.35 5.3 / Decision 76).

Stdlib-only: the apply jobs install no Python dependencies. Extracted from the two inline wake step
bodies so the workflow stays under its structural-size budget (Decision 128/166). Every gh error or
unresolved PR prints a line and exits 0 -- the wake is best-effort and nothing depends on it.

Callers: terraform-apply-sandbox.yml (apply-sandbox, gated-apply) and reconcile.yml (apply-reconcile,
gated-apply-reconcile; the latter two extracted for the reconcile.yml size budget, byte-identical messages).

Environment: REPO, SHA (commit whose PR is resolved), HEAD_SHA (github.sha), APPLY_OUTCOME, ROUTED, and for
apply-reconcile REVIEW_OUTCOME, FRESH_REASON and RUN_URL.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Optional

_ACK_RETRY = "After review, unlatch via the workflow_dispatch acknowledge-and-retry path."
_JOBS = ("apply-sandbox", "gated-apply", "apply-reconcile", "gated-apply-reconcile")


def _render_apply_reconcile(
    apply_outcome: str, routed: str, sha: str, review_outcome: str, fresh_reason: str, run_url: str
) -> str:
    if fresh_reason == "guard_routed":
        why = (
            f"the saved plan.bin for {sha} was guard-BLOCKed (so it was never applied and could not report staleness), "
            "and Reconcile re-planned fresh in this same run"
        )
    else:
        why = (
            f"the saved plan.bin for {sha} was stale (STALE_PLAN_FRESH_REPLAN), "
            "and Reconcile re-planned fresh in this same run"
        )
    if routed == "true":
        return (
            f"Reconcile: {why}, then routed the FRESH plan to the tf-gated-apply job in reconcile.yml (out-of-budget IAM / "
            "trust / destroy). Pending reviewer approval in GitHub Actions -- the convergence record keeps its pending_gated "
            "marker until the gated apply completes. BEFORE APPROVING, review the fresh plan in this run's apply-reconcile "
            "job -- steps `Fresh re-plan (STALE_PLAN_FRESH_REPLAN fallthrough)` and `Deterministic apply guard (fail-closed) "
            f"-- fresh re-plan` at {run_url} -- because the gated job itself shows nothing of it at approval time."
        )
    if review_outcome == "starved":
        return (
            f"Reconcile's subagent reviewer was STARVED for {sha} (max-turns/no-verdict after retry); apply was blocked and "
            "the convergence record was NOT touched. Re-dispatch Reconcile once the reviewer is healthy."
        )
    if apply_outcome == "success":
        if fresh_reason:
            return (
                f"Reconcile: {why}, re-guarded it, and converged GREEN; convergence record updated. The fresh plan "
                "guard-PASSed, so it was applied here behind the digest-fed subagent review rather than the tf-gated-apply "
                "Environment (Decision 158 route (i))."
            )
        return f"Reconcile converged GREEN for {sha}; convergence record updated."
    return (
        f"Reconcile did NOT converge (RED) for {sha}. Inspect this run's logs directly (ci-rca does not watch reconcile.yml)."
    )


def render_message(
    job: str,
    apply_outcome: str,
    routed: str,
    sha: str,
    head_sha: str,
    review_outcome: str = "",
    fresh_reason: str = "",
    run_url: str = "",
) -> str:
    if job == "apply-reconcile":
        return _render_apply_reconcile(apply_outcome, routed, sha, review_outcome, fresh_reason, run_url)
    if job == "gated-apply-reconcile":
        if apply_outcome == "success":
            return (
                f"Reconcile's gated apply converged GREEN for {sha} (IAM/trust/destroy change applied after reviewer "
                "approval); convergence record updated."
            )
        return f"Reconcile's gated apply did NOT converge (RED) for {sha}. Inspect this run's logs directly."
    if job == "apply-sandbox":
        if routed == "true":
            return (
                f"sandbox terraform-apply guard routed {head_sha} to the tf-gated-apply job (out-of-budget IAM / trust / "
                "destroy diff); pending reviewer approval -- review this run's apply-sandbox plan + guard step logs, then "
                "approve at: Actions -> gated-apply job. The convergence record is unchanged until the gated apply "
                f"completes (commit_sha becomes {head_sha} on success). (Best-effort wake.)"
            )
        if apply_outcome == "success":
            return (
                f"sandbox terraform-apply converged GREEN for {sha}; convergence record updated. "
                "(Best-effort wake; next planning session re-checks the record.)"
            )
        return (
            f"sandbox terraform-apply did NOT converge (RED) for {sha}; ci-rca will file a source=ci_rca rec. "
            f"{_ACK_RETRY} (Best-effort wake.)"
        )
    if apply_outcome == "refused":
        return (
            f"gated-apply applied {head_sha} after reviewer approval but the drift guard REFUSED the green convergence "
            "write: a drift red postdates the route (or the guard read failed), so the record was NOT overwritten and "
            "stays as it was (red, or a stale green). Review the drift, then use the workflow_dispatch "
            "acknowledge-and-retry path (Decision 183). (Best-effort wake.)"
        )
    if apply_outcome == "success":
        return (
            f"gated-apply converged GREEN for {head_sha} (IAM/trust/destroy change applied in CD after reviewer "
            "approval); convergence record updated with commit_sha and plan_sha. "
            "(Best-effort wake; next planning session re-checks the record.)"
        )
    return (
        f"gated-apply did NOT converge (RED) for {head_sha}; ci-rca will file a source=ci_rca rec. "
        f"{_ACK_RETRY} (Best-effort wake.)"
    )


def _resolve_pr(repo: str, sha: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{repo}/commits/{sha}/pulls", "--jq", ".[0].number"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return None
    number = result.stdout.strip() if result.returncode == 0 else ""
    return number if number and number != "null" else None


def main(argv: Optional[list[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or argv[0] != "--job" or argv[1] not in _JOBS:
        print(f"usage: python3 -m scripts.ci.apply_wake --job {'|'.join(_JOBS)}", file=sys.stderr)
        return 2
    env = os.environ.get
    repo, sha = env("REPO", ""), env("SHA", "")
    pr_number = _resolve_pr(repo, sha)
    if pr_number is None:
        print(f"No originating PR resolved for {sha}; next-planning-session baseline applies.")
        return 0
    message = render_message(
        argv[1],
        env("APPLY_OUTCOME", ""),
        env("ROUTED", ""),
        sha,
        env("HEAD_SHA", ""),
        env("REVIEW_OUTCOME", ""),
        env("FRESH_REASON", ""),
        env("RUN_URL", ""),
    )
    try:
        subprocess.run(
            ["gh", "pr", "comment", pr_number, "--repo", repo, "--body", message],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        print(f"wake comment not posted: {exc}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
