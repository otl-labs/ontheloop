"""Tests for scripts/ci/apply_wake.py -- the extracted best-effort post-merge SHA->PR wake.

Each message literal below is the text the workflow body carried before the move (byte-parity).
"""

from __future__ import annotations

import os
import subprocess
from unittest.mock import MagicMock, patch

from scripts.ci.apply_wake import main, render_message

HEAD = "headsha"
SHA = "ackedsha"

ROUTED_MSG = (
    "sandbox terraform-apply guard routed headsha to the tf-gated-apply job (out-of-budget IAM / trust / destroy diff); "
    "pending reviewer approval -- review this run's apply-sandbox plan + guard step logs, then approve at: Actions -> "
    "gated-apply job. The convergence record is unchanged until the gated apply completes (commit_sha becomes headsha "
    "on success). (Best-effort wake.)"
)
SANDBOX_GREEN = (
    "sandbox terraform-apply converged GREEN for ackedsha; convergence record updated. "
    "(Best-effort wake; next planning session re-checks the record.)"
)
SANDBOX_RED = (
    "sandbox terraform-apply did NOT converge (RED) for ackedsha; ci-rca will file a source=ci_rca rec. After review, "
    "unlatch via the workflow_dispatch acknowledge-and-retry path. (Best-effort wake.)"
)
GATED_GREEN = (
    "gated-apply converged GREEN for headsha (IAM/trust/destroy change applied in CD after reviewer approval); "
    "convergence record updated with commit_sha and plan_sha. (Best-effort wake; next planning session re-checks the record.)"
)
GATED_RED = (
    "gated-apply did NOT converge (RED) for headsha; ci-rca will file a source=ci_rca rec. After review, unlatch via "
    "the workflow_dispatch acknowledge-and-retry path. (Best-effort wake.)"
)


def test_apply_sandbox_routed_message() -> None:
    assert render_message("apply-sandbox", "skipped", "true", SHA, HEAD) == ROUTED_MSG


def test_apply_sandbox_green_message() -> None:
    assert render_message("apply-sandbox", "success", "", SHA, HEAD) == SANDBOX_GREEN


def test_apply_sandbox_red_message() -> None:
    assert render_message("apply-sandbox", "failure", "false", SHA, HEAD) == SANDBOX_RED


def test_gated_apply_green_message() -> None:
    assert render_message("gated-apply", "success", "", SHA, HEAD) == GATED_GREEN


def test_gated_apply_red_message() -> None:
    assert render_message("gated-apply", "failure", "", SHA, HEAD) == GATED_RED


def test_gated_apply_refused_message_names_ack_retry() -> None:
    message = render_message("gated-apply", "refused", "", SHA, HEAD)
    assert "converged GREEN" not in message
    assert "acknowledge-and-retry" in message
    assert HEAD in message


def _env(**extra: str) -> dict[str, str]:
    return {"REPO": "o/r", "SHA": SHA, "HEAD_SHA": HEAD, "APPLY_OUTCOME": "success", "ROUTED": "", **extra}


def _completed(stdout: str = "", returncode: int = 0) -> MagicMock:
    return MagicMock(stdout=stdout, returncode=returncode)


def test_pr_resolved_posts_comment() -> None:
    with (
        patch.dict(os.environ, _env(), clear=False),
        patch("subprocess.Popen"),
        patch("subprocess.run", side_effect=[_completed("12\n"), _completed()]) as run,
    ):
        assert main(["--job", "gated-apply"]) == 0
    comment = run.call_args_list[1].args[0]
    assert comment[:4] == ["gh", "pr", "comment", "12"]
    assert comment[-1] == GATED_GREEN


def test_no_pr_resolved_posts_nothing(capsys) -> None:
    with (
        patch.dict(os.environ, _env(), clear=False),
        patch("subprocess.Popen"),
        patch("subprocess.run", return_value=_completed("null\n")) as run,
    ):
        assert main(["--job", "apply-sandbox"]) == 0
    assert run.call_count == 1
    assert "No originating PR resolved" in capsys.readouterr().out


def test_gh_failure_exits_zero() -> None:
    with (
        patch.dict(os.environ, _env(), clear=False),
        patch("subprocess.Popen"),
        patch("subprocess.run", return_value=_completed("", 1)),
    ):
        assert main(["--job", "apply-sandbox"]) == 0


def test_gh_missing_exits_zero() -> None:
    with (
        patch.dict(os.environ, _env(), clear=False),
        patch("subprocess.Popen"),
        patch("subprocess.run", side_effect=[_completed("5\n"), OSError("gh gone")]),
    ):
        assert main(["--job", "apply-sandbox"]) == 0
    with patch.dict(os.environ, _env(), clear=False), patch("subprocess.Popen"), patch("subprocess.run", side_effect=OSError):
        assert main(["--job", "gated-apply"]) == 0


def test_bad_usage_exits_two() -> None:
    assert main([]) == 2
    assert main(["--job", "nope"]) == 2


def test_module_is_stdlib_only() -> None:
    source = open("scripts/ci/apply_wake.py", encoding="utf-8").read()
    assert "import yaml" not in source and "import boto3" not in source
    assert subprocess  # module imported at top


_RED = "redsha"
_URL = "https://github.com/o/r/actions/runs/1"
_WHY_BLOCKED = (
    "the saved plan.bin for redsha was guard-BLOCKed (so it was never applied and could not report staleness), "
    "and Reconcile re-planned fresh in this same run"
)
_WHY_STALE = (
    "the saved plan.bin for redsha was stale (STALE_PLAN_FRESH_REPLAN), and Reconcile re-planned fresh in this same run"
)


def _reconcile(apply_outcome: str = "", routed: str = "", review: str = "", fresh: str = "") -> str:
    return render_message("apply-reconcile", apply_outcome, routed, _RED, "", review, fresh, _URL)


def test_apply_reconcile_routed_message_names_the_run_steps() -> None:
    for fresh, why in (("guard_routed", _WHY_BLOCKED), ("stale", _WHY_STALE)):
        assert _reconcile(routed="true", fresh=fresh) == (
            f"Reconcile: {why}, then routed the FRESH plan to the tf-gated-apply job in reconcile.yml (out-of-budget IAM / "
            "trust / destroy). Pending reviewer approval in GitHub Actions -- the convergence record keeps its pending_gated "
            "marker until the gated apply completes. BEFORE APPROVING, review the fresh plan in this run's apply-reconcile "
            "job -- steps `Fresh re-plan (STALE_PLAN_FRESH_REPLAN fallthrough)` and `Deterministic apply guard (fail-closed) "
            f"-- fresh re-plan` at {_URL} -- because the gated job itself shows nothing of it at approval time."
        )


def test_apply_reconcile_starved_green_and_red_messages() -> None:
    assert _reconcile(review="starved") == (
        "Reconcile's subagent reviewer was STARVED for redsha (max-turns/no-verdict after retry); apply was blocked and "
        "the convergence record was NOT touched. Re-dispatch Reconcile once the reviewer is healthy."
    )
    assert _reconcile(apply_outcome="success", fresh="guard_routed") == (
        f"Reconcile: {_WHY_BLOCKED}, re-guarded it, and converged GREEN; convergence record updated. The fresh plan "
        "guard-PASSed, so it was applied here behind the digest-fed subagent review rather than the tf-gated-apply "
        "Environment (Decision 158 route (i))."
    )
    assert _reconcile(apply_outcome="success") == "Reconcile converged GREEN for redsha; convergence record updated."
    assert _reconcile(apply_outcome="failure") == (
        "Reconcile did NOT converge (RED) for redsha. Inspect this run's logs directly (ci-rca does not watch reconcile.yml)."
    )


def test_gated_apply_reconcile_messages() -> None:
    green = render_message("gated-apply-reconcile", "success", "", _RED, "")
    red = render_message("gated-apply-reconcile", "failure", "", _RED, "")
    assert green == (
        "Reconcile's gated apply converged GREEN for redsha (IAM/trust/destroy change applied after reviewer approval); "
        "convergence record updated."
    )
    assert red == "Reconcile's gated apply did NOT converge (RED) for redsha. Inspect this run's logs directly."


def test_reconcile_jobs_post_through_main_and_read_env() -> None:
    env = {"REPO": "o/r", "SHA": _RED, "APPLY_OUTCOME": "success", "REVIEW_OUTCOME": "", "FRESH_REASON": "", "RUN_URL": _URL}
    with (
        patch.dict(os.environ, env),
        patch("subprocess.run", side_effect=[_completed("12\n"), _completed()]) as run,
    ):
        assert main(["--job", "apply-reconcile"]) == 0
    assert run.call_args_list[1].args[0][-1] == "Reconcile converged GREEN for redsha; convergence record updated."
