#!/usr/bin/env python3
"""Best-effort post-merge SHA->PR wake for terraform-apply-sandbox.yml (CD.35 5.3 / Decision 76).

Stdlib-only: the apply jobs install no Python dependencies. Extracted from the two inline wake step
bodies so the workflow stays under its structural-size budget (Decision 128/166). Every gh error or
unresolved PR prints a line and exits 0 -- the wake is best-effort and nothing depends on it.

Environment: REPO, SHA (commit whose PR is resolved), HEAD_SHA (github.sha), APPLY_OUTCOME, ROUTED.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Optional

_ACK_RETRY = "After review, unlatch via the workflow_dispatch acknowledge-and-retry path."


def render_message(job: str, apply_outcome: str, routed: str, sha: str, head_sha: str) -> str:
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
    if len(argv) != 2 or argv[0] != "--job" or argv[1] not in ("apply-sandbox", "gated-apply"):
        print("usage: python3 -m scripts.ci.apply_wake --job apply-sandbox|gated-apply", file=sys.stderr)
        return 2
    env = os.environ.get
    repo, sha = env("REPO", ""), env("SHA", "")
    pr_number = _resolve_pr(repo, sha)
    if pr_number is None:
        print(f"No originating PR resolved for {sha}; next-planning-session baseline applies.")
        return 0
    message = render_message(argv[1], env("APPLY_OUTCOME", ""), env("ROUTED", ""), sha, env("HEAD_SHA", ""))
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
