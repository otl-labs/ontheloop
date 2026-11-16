"""terraform-apply-sandbox.yml dispatch-gated-apply topology guard (Decision 183; rec-2918 part (c)).

rec-2918 identified a dispatch-vs-push deadlock: the workflow_dispatch acknowledge-and-retry path
(the ONLY heal verb that plans fresh at main HEAD) could hit a guard-ROUTED verdict (out-of-budget
IAM / trust / destroy) and have nowhere to go -- gated-apply's job-level `if` admitted a push event
only, so a routed dispatch's fresh plan was silently discarded and the red convergence record
stayed latched. Decision 183 makes gated-apply DISPATCH-REACHABLE: reachable from EITHER heal verb,
with the fresh plan.bin handed over as a run-scoped SANDBOX_FRESH_PLAN_ARTIFACT (mirrors
reconcile.yml's RECONCILE_FRESH_PLAN_ARTIFACT hand-off verbatim) instead of being fetched from S3.

Thirteen invariants, asserted over the PARSED workflow (never a whole-file grep, matching the sibling
scripts.verify_ci_workflow._check_recovery_workflow_topology shape this guard is modelled on):

  (i)   gated-apply's `if` contains the routed signal and NO LONGER contains the push conjunct --
        the exact pre-fix shape this guard exists to reject.
  (ii)  gated-apply still declares `environment: tf-gated-apply` (the sole authorization boundary,
        Decision 92) and its `apply` step applies plan.bin verbatim; no step in the job re-plans
        (Decision 77 no-TOCTOU -- a `terraform plan` smuggled into gated-apply would defeat the
        guard's inspection of the applied plan).
  (iii) apply-sandbox declares the three job outputs the hand-off depends on: fresh_plan_pending
        (derived from the EMIT step's outcome), fresh_plan_sha256, and artifact_sha.
  (iv)  the upload-artifact step and the sha256 EMIT step are gated on BOTH a workflow_dispatch
        event and a guard-routed verdict -- the fresh plan.bin must never upload on a push (it
        already has a saved plan in S3) or on an un-routed dispatch (nothing to hand off).
  (v)   gated-apply's download-artifact and sha256 VERIFY steps are gated on fresh_plan_pending;
        fetch_plan is gated on ITS NEGATION plus push -- the two plan sources stay mutually
        exclusive and a saved plan.bin is never fetched on a dispatch.
  (vi)  the artifact-sha chain survives the widened reach: gated-apply's artifact_sha step echoes
        needs.apply-sandbox.outputs.artifact_sha (it can no longer re-resolve a PR from a dispatch
        commit) and its DuckLake fetch step still reads steps.artifact_sha.outputs.value.
  (vii) both best-effort wake steps derive their PR-lookup SHA from the acknowledge input on a
        dispatch, so the wake targets the incident PR being watched, not an unrelated HEAD commit.
  (viii) apply-sandbox's own job `if` admits ONLY push and workflow_dispatch. This is the belt
        invariant (i) removed: with no event conjunct left on gated-apply, its exclusion from the
        workflow's pull_request trigger rests solely on apply-sandbox skipping (and thus failing
        gated-apply's `needs` condition). Widening that `if` would make the tf-gated-apply
        Environment PR-reachable with zero signal (Decision 83 / T2.22: NOT a PR required check).

Declares registry.examined(8, unit="topology_invariants") (Decision 170) -- one count per
invariant above, not per individual assertion within one. Loads the workflow via
scripts.verify_ci_workflow._load (never re-implements YAML parsing); registry.skipped(reason) only
if the workflow file itself cannot be read.

Own module because scripts/verify_ci_workflow.py sits at exactly 500 SLOC (Decision 128: never
raise without a marker; a new module beats a facade decomposition for one guard).

Filesystem-only: no subprocess, no network, matching every sibling ci_guards module.
"""

from __future__ import annotations

from typing import Any

from scripts.checks import _common, registry
from scripts.verify_ci_workflow import _load

_WORKFLOW_PATH = ".github/workflows/terraform-apply-sandbox.yml"
_PREFIX = "dispatch-gated-apply-topology"

_ROUTED_SIGNAL = "needs.apply-sandbox.outputs.routed == 'true'"
_PUSH_CONJUNCT = "github.event_name == 'push'"
_DISPATCH_EVENT = "github.event_name == 'workflow_dispatch'"
_FRESH_PLAN_PENDING = "needs.apply-sandbox.outputs.fresh_plan_pending == 'true'"
_FRESH_PLAN_NOT_PENDING = "needs.apply-sandbox.outputs.fresh_plan_pending != 'true'"
_ACK_INPUT = "acknowledge_red_commit"
_ARTIFACT_MARKER = "SANDBOX_FRESH_PLAN_ARTIFACT"
_REVISE_DISJUNCT = (
    "github.event_name == 'push' && steps.review.outcome == 'success' && steps.review.outputs.outcome == 'revise'"
)
_REVIEW_ACTION = "./.github/actions/subagent-plan-review"
_RECONCILE_PATH = ".github/workflows/reconcile.yml"
_ACTION_PATH = ".github/actions/subagent-plan-review/action.yml"


def _report(failed: list[str], label: str, ok: bool, detail: str = "") -> None:
    """One PASS/FAIL line plus, on failure, one DISTINCT `dispatch-gated-apply-topology: <label>` entry."""
    if ok:
        print(f"  PASS: {label}")
        return
    print(f"  FAIL: {label}{f' ({detail})' if detail else ''}")
    failed.append(f"{_PREFIX}: {label}")


def _steps(job: Any) -> list[dict[str, Any]]:
    steps = job.get("steps") if isinstance(job, dict) else None
    return [step for step in steps if isinstance(step, dict)] if isinstance(steps, list) else []


def _find_step(steps: list[dict[str, Any]], step_id: str) -> dict[str, Any] | None:
    """A step by its `id`. Steps carrying no id are selected by their name marker at the call
    site instead (invariants (iv)/(v)) -- never positionally."""
    return next((step for step in steps if step.get("id") == step_id), None)


def _check_invariant_i(failed: list[str], gated_if: str) -> None:
    _report(
        failed,
        "(i) gated-apply if carries the routed signal and no push conjunct",
        _ROUTED_SIGNAL in gated_if and _PUSH_CONJUNCT not in gated_if,
        repr(gated_if),
    )


def _check_invariant_ii(failed: list[str], gated_job: dict[str, Any], gated_steps: list[dict[str, Any]]) -> None:
    _report(
        failed,
        "(ii) gated-apply declares environment: tf-gated-apply",
        gated_job.get("environment") == "tf-gated-apply",
        repr(gated_job.get("environment")),
    )
    apply_step = _find_step(gated_steps, "apply")
    apply_body = str(apply_step.get("run", "")) if apply_step else ""
    _report(
        failed,
        "(ii) gated-apply's apply step runs terraform apply ... plan.bin",
        apply_step is not None and "terraform apply" in apply_body and "plan.bin" in apply_body,
        repr(apply_body),
    )
    replan_steps = [str(step.get("run", "")) for step in gated_steps if "terraform plan" in str(step.get("run", ""))]
    _report(
        failed,
        "(ii) no step in gated-apply runs terraform plan",
        not replan_steps,
        f"re-plan step(s) found: {replan_steps}",
    )


def _check_invariant_iii(failed: list[str], apply_sandbox_outputs: dict[str, Any]) -> None:
    _report(
        failed,
        "(iii) apply-sandbox declares fresh_plan_pending from the EMIT step's outcome",
        "steps.upload_fresh_plan_sha256.outcome" in str(apply_sandbox_outputs.get("fresh_plan_pending", "")),
        repr(apply_sandbox_outputs.get("fresh_plan_pending")),
    )
    _report(
        failed,
        "(iii) apply-sandbox declares fresh_plan_sha256",
        "steps.upload_fresh_plan_sha256.outputs.sha256" in str(apply_sandbox_outputs.get("fresh_plan_sha256", "")),
        repr(apply_sandbox_outputs.get("fresh_plan_sha256")),
    )
    _report(
        failed,
        "(iii) apply-sandbox declares artifact_sha",
        "steps.artifact_sha.outputs.value" in str(apply_sandbox_outputs.get("artifact_sha", "")),
        repr(apply_sandbox_outputs.get("artifact_sha")),
    )


def _check_invariant_iv(failed: list[str], apply_sandbox_steps: list[dict[str, Any]]) -> None:
    emit_step = _find_step(apply_sandbox_steps, "upload_fresh_plan_sha256")
    emit_if = str(emit_step.get("if", "")) if emit_step else ""
    _report(
        failed,
        "(iv) the sha256 EMIT step is gated on workflow_dispatch AND guard routed",
        emit_step is not None and _DISPATCH_EVENT in emit_if and "steps.guard.outputs.routed == 'true'" in emit_if,
        repr(emit_if),
    )
    # Identity-anchored on the artifact marker, never positional: a first-match `next()` over
    # every actions/upload-artifact step would silently validate an UNRELATED upload added earlier
    # in the job and let an ungated fresh-plan upload through.
    uploads = [step for step in apply_sandbox_steps if str(step.get("uses", "")).startswith("actions/upload-artifact")]
    marked = [step for step in uploads if _ARTIFACT_MARKER in str(step.get("name", ""))]
    upload_step = marked[0] if len(marked) == 1 else None
    upload_if = str(upload_step.get("if", "")) if upload_step else ""
    _report(
        failed,
        "(iv) the upload-artifact step is gated on workflow_dispatch AND guard routed",
        upload_step is not None and _DISPATCH_EVENT in upload_if and "steps.guard.outputs.routed == 'true'" in upload_if,
        f"{len(marked)} of {len(uploads)} upload step(s) name {_ARTIFACT_MARKER}; if={upload_if!r}",
    )


def _check_invariant_v(failed: list[str], gated_steps: list[dict[str, Any]]) -> None:
    # Identity-anchored for the same reason invariant (iv) is: an unrelated download-artifact
    # step added earlier in the job must not be validated in the fresh plan's place.
    downloads = [step for step in gated_steps if str(step.get("uses", "")).startswith("actions/download-artifact")]
    marked = [step for step in downloads if _ARTIFACT_MARKER in str(step.get("name", ""))]
    download_step = marked[0] if len(marked) == 1 else None
    download_if = str(download_step.get("if", "")) if download_step else ""
    _report(
        failed,
        "(v) gated-apply's download-artifact step is gated on fresh_plan_pending",
        download_step is not None and _FRESH_PLAN_PENDING in download_if,
        f"{len(marked)} of {len(downloads)} download step(s) name {_ARTIFACT_MARKER}; if={download_if!r}",
    )
    verify_step = _find_step(gated_steps, "verify_fresh_plan_sha256")
    verify_if = str(verify_step.get("if", "")) if verify_step else ""
    _report(
        failed,
        "(v) gated-apply's sha256 VERIFY step is gated on fresh_plan_pending",
        verify_step is not None and _FRESH_PLAN_PENDING in verify_if,
        repr(verify_if),
    )
    fetch_plan_step = _find_step(gated_steps, "fetch_plan")
    fetch_plan_if = str(fetch_plan_step.get("if", "")) if fetch_plan_step else ""
    _report(
        failed,
        "(v) gated-apply's fetch_plan step is gated on NOT fresh_plan_pending AND push",
        fetch_plan_step is not None and _FRESH_PLAN_NOT_PENDING in fetch_plan_if and _PUSH_CONJUNCT in fetch_plan_if,
        repr(fetch_plan_if),
    )


def _check_invariant_vi(failed: list[str], gated_steps: list[dict[str, Any]]) -> None:
    artifact_sha_step = _find_step(gated_steps, "artifact_sha")
    artifact_sha_body = str(artifact_sha_step.get("run", "")) if artifact_sha_step else ""
    _report(
        failed,
        "(vi) gated-apply's artifact_sha step echoes needs.apply-sandbox.outputs.artifact_sha",
        artifact_sha_step is not None and "needs.apply-sandbox.outputs.artifact_sha" in artifact_sha_body,
        repr(artifact_sha_body),
    )
    builds = [step for step in gated_steps if str(step.get("uses", "")).endswith("build-ducklake-artifacts")]
    build_step = builds[0] if len(builds) == 1 else None
    build_with = build_step.get("with", {}) if build_step else {}
    _report(
        failed,
        "(vi) gated-apply's build-ducklake-artifacts step keeps mode fetch reading steps.artifact_sha.outputs.value",
        build_step is not None
        and build_with.get("mode") == "fetch"
        and "steps.artifact_sha.outputs.value" in str(build_with.get("artifact-sha", "")),
        repr(build_with),
    )


def _check_invariant_vii(
    failed: list[str], apply_sandbox_steps: list[dict[str, Any]], gated_steps: list[dict[str, Any]]
) -> None:
    wake_steps = [step for step in apply_sandbox_steps + gated_steps if "wake" in str(step.get("name", "")).lower()]
    bad = [step.get("name") for step in wake_steps if _ACK_INPUT not in str((step.get("env") or {}).get("SHA", ""))]
    _report(
        failed,
        "(vii) both best-effort wake steps derive SHA from the acknowledge input",
        len(wake_steps) == 2 and not bad,
        f"wake step count={len(wake_steps)}, not deriving from {_ACK_INPUT}: {bad}",
    )


def _check_invariant_viii(failed: list[str], apply_sandbox_job: dict[str, Any]) -> None:
    """The belt invariant (i) removed. gated-apply's `if` no longer carries ANY event conjunct, so
    its exclusion from the workflow's `pull_request` trigger now rests SOLELY on apply-sandbox's
    own `if` -- a pull_request run skips apply-sandbox, whose non-success result then fails
    gated-apply's `needs` condition. Nothing else asserts that. If apply-sandbox's `if` were ever
    widened to admit pull_request, the tf-gated-apply Environment would become PR-reachable with
    zero signal, breaking Decision 83 / T2.22's "NOT a PR required check" constraint."""
    condition = str(apply_sandbox_job.get("if", ""))
    admits_only_push_and_dispatch = (
        _PUSH_CONJUNCT in condition and _DISPATCH_EVENT in condition and "pull_request" not in condition
    )
    _report(
        failed,
        "(viii) apply-sandbox's job `if` admits only push and workflow_dispatch, never pull_request",
        admits_only_push_and_dispatch,
        repr(condition),
    )


def _check_invariants_ix_to_xii(failed: list[str], apply_sandbox: dict[str, Any], steps: list[dict[str, Any]]) -> None:
    review = _find_step(steps, "review")
    revise_routes = str((review or {}).get("with", {}).get("revise-routes", ""))
    _report(
        failed,
        "(ix) apply-sandbox's review step passes revise-routes only on a push",
        revise_routes == "${{ github.event_name == 'push' }}",
        repr(revise_routes),
    )
    apply_if = str((_find_step(steps, "apply") or {}).get("if", ""))
    _report(
        failed,
        "(x) apply-sandbox's apply step runs only on an explicit PROCEED",
        "steps.review.outputs.outcome == 'proceed'" in apply_if,
        repr(apply_if),
    )
    sites = {
        "job output routed": str((apply_sandbox.get("outputs") or {}).get("routed", "")),
        "record_write routed input": str((_find_step(steps, "record_write") or {}).get("with", {}).get("routed", "")),
        "wake ROUTED env": str(
            next((s for s in steps if "wake" in str(s.get("name", "")).lower()), {}).get("env", {}).get("ROUTED", "")
        ),
    }
    bad = [name for name, text in sites.items() if _REVISE_DISJUNCT not in text or "steps.guard.outputs.routed" not in text]
    _report(failed, "(xi) the routed signal carries the push REVISE disjunct at all three sites", not bad, f"{bad}")
    handoff = [str((_find_step(steps, "upload_fresh_plan_sha256") or {}).get("if", ""))]
    handoff += [str(s.get("if", "")) for s in steps if _ARTIFACT_MARKER in str(s.get("name", ""))]
    _report(
        failed,
        "(xii) the dispatch fresh-plan hand-off steps do not depend on the review",
        not any("steps.review" in text for text in handoff),
        repr(handoff),
    )


def _check_invariant_xiii(failed: list[str]) -> None:
    label = "(xiii) reconcile.yml passes no revise-routes and the action defaults it to false"
    try:
        reconcile = _load(str(_common.ROOT / _RECONCILE_PATH))
        action = _load(str(_common.ROOT / _ACTION_PATH))
    except Exception as exc:
        _report(failed, label, False, f"unreadable: {exc}")
        return
    uses = [
        step
        for job in (reconcile.get("jobs") or {}).values()
        for step in _steps(job)
        if str(step.get("uses", "")) == _REVIEW_ACTION
    ]
    passing = [step.get("id") for step in uses if "revise-routes" in (step.get("with") or {})]
    default = ((action.get("inputs") or {}).get("revise-routes") or {}).get("default")
    _report(failed, label, bool(uses) and not passing and default == "false", f"passing={passing}, default={default!r}")


@registry.register("validate_dispatch_gated_apply_topology", owner="platform")
def validate_dispatch_gated_apply_topology(failed: list[str]) -> None:
    """Assert terraform-apply-sandbox.yml's dispatch-reachable gated-apply topology (Decision 183;
    rec-2918 part (c)). See module docstring for the thirteen invariants."""
    print("\n=== dispatch-gated-apply topology guard ===")
    try:
        data = _load(str(_common.ROOT / _WORKFLOW_PATH))
    except Exception as exc:
        print(f"  FAIL: could not load {_WORKFLOW_PATH}: {exc}")
        registry.skipped(f"workflow file unreadable: {exc}")
        return

    jobs = data.get("jobs") or {}
    apply_sandbox = jobs.get("apply-sandbox") or {}
    gated_apply = jobs.get("gated-apply") or {}

    if not apply_sandbox or not gated_apply:
        print("  FAIL: apply-sandbox or gated-apply job missing from the workflow")
        failed.append(f"{_PREFIX}: apply-sandbox or gated-apply job missing")
        registry.examined(0, unit="topology_invariants")
        return

    apply_sandbox_steps = _steps(apply_sandbox)
    gated_steps = _steps(gated_apply)
    gated_if = str(gated_apply.get("if", ""))

    _check_invariant_i(failed, gated_if)
    _check_invariant_ii(failed, gated_apply, gated_steps)
    _check_invariant_iii(failed, apply_sandbox.get("outputs") or {})
    _check_invariant_iv(failed, apply_sandbox_steps)
    _check_invariant_v(failed, gated_steps)
    _check_invariant_vi(failed, gated_steps)
    _check_invariant_vii(failed, apply_sandbox_steps, gated_steps)
    _check_invariant_viii(failed, apply_sandbox)
    _check_invariants_ix_to_xii(failed, apply_sandbox, apply_sandbox_steps)
    _check_invariant_xiii(failed)

    registry.examined(13, unit="topology_invariants")
