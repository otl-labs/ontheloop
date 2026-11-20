"""Workflow-YAML acceptance test for Decision 154 / rec-2862 (PLAN-convergence-red-artifact-fetch).

Sibling of tests/test_terraform_drift_acceptance.py: covers five structural invariants the
runtime cannot check pre-deploy (a GitHub Actions expression only actually evaluates in CI).
(a) the pre-apply-failure derivation's closed allow-list + no-apply-step-ran conjunction,
computed inline rather than via an aggregator; (b) each of the four jobs' artifact mode;
(c) write-convergence-record's default/branch-ordering/read-back contract, and that all four
call sites pass the input; (d) the Reconcile dispatch-ref side-checkout wiring (ordering, `uses`,
working-directory, and that the side-checkout carries no RED_SHA ref); (e) Decision 154's
compacted stub and its superseder's reversal conditions in docs/DECISIONS.md, each scoped to
its own block (the phrase "Decision 154" appears dozens of times elsewhere in the file, so an unscoped
file-wide grep would pass before any work is done).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

SANDBOX_WORKFLOW = Path(".github/workflows/terraform-apply-sandbox.yml")
RECONCILE_WORKFLOW = Path(".github/workflows/reconcile.yml")
WRITE_RECORD_ACTION = Path(".github/actions/write-convergence-record/action.yml")
DECISIONS_MD = Path("docs/DECISIONS.md")

# The CLOSED allow-list (AGENTS.md / this plan's constraints): under-inclusive on purpose --
# `plan`, `guard`, `review`, `replan`, `guard_fresh`, `review_fresh` must NEVER appear in a
# pre-apply-failure failure-outcome check, since a plan failure / guard BLOCK / explicit REVISE
# is not an infra error and must keep latching red.
ALLOW_LIST = {"artifact_sha", "tfvars", "tf_init", "build_ducklake", "fetch_plan"}
DISALLOWED_IDS = {"plan", "guard", "review", "replan", "guard_fresh", "review_fresh"}

# (job_name, workflow_path, {apply-step ids that must ALL report 'skipped'})
JOBS = [
    ("apply-sandbox", SANDBOX_WORKFLOW, frozenset({"apply"})),
    ("gated-apply", SANDBOX_WORKFLOW, frozenset({"apply"})),
    ("apply-reconcile", RECONCILE_WORKFLOW, frozenset({"apply", "apply_fresh"})),
    ("gated-apply-reconcile", RECONCILE_WORKFLOW, frozenset({"apply"})),
]

_STEP_OUTCOME_RE = re.compile(r"steps\.(\w+)\.outcome\s*==\s*'(skipped|failure)'")


def _load_jobs(path: Path) -> dict:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    return doc["jobs"]


def _find_step(steps: list, *, uses: str) -> dict:
    for step in steps:
        if step.get("uses") == uses:
            return step
    raise AssertionError(f"no step found with uses={uses!r}")


def _record_write_step(job: dict) -> dict:
    return _find_step(job["steps"], uses="./.github/actions/write-convergence-record")


def _parse_outcome_checks(expr: str) -> tuple[set[str], set[str]]:
    """Return (skipped_ids, failure_ids) referenced in a pre-apply-failure expression string."""
    skipped_ids: set[str] = set()
    failure_ids: set[str] = set()
    for step_id, outcome in _STEP_OUTCOME_RE.findall(expr):
        (skipped_ids if outcome == "skipped" else failure_ids).add(step_id)
    return skipped_ids, failure_ids


# Module-level (not class-nested) functions: the plan's Verification Plan and
# validate_graduation_completeness both select these by bare `file.py::test_name`, which does not
# resolve into a class-nested method.


def test_pre_apply_failure_derivation_bounded() -> None:
    for job_name, wf_path, apply_ids in JOBS:
        job = _load_jobs(wf_path)[job_name]
        record_step = _record_write_step(job)
        expr = record_step["with"]["pre-apply-failure"]
        assert isinstance(expr, str), f"{job_name}: pre-apply-failure must be a string expression"

        skipped_ids, failure_ids = _parse_outcome_checks(expr)
        assert skipped_ids == set(apply_ids), (
            f"{job_name}: expected the 'skipped' clause to check exactly {set(apply_ids)}, got {skipped_ids}"
        )
        assert failure_ids, f"{job_name}: expected at least one 'failure' outcome check"
        assert failure_ids <= ALLOW_LIST, (
            f"{job_name}: failure-outcome step ids {failure_ids} are not a subset of the closed allow-list {ALLOW_LIST}"
        )
        assert not (failure_ids & DISALLOWED_IDS), (
            f"{job_name}: disallowed step id present in pre-apply-failure's failure clause: {failure_ids & DISALLOWED_IDS}"
        )
        # Computed inline at the write step -- never routed through a job-level aggregator
        # (e.g. apply-reconcile's `resolved` step, which is defined AFTER both apply steps).
        assert "steps.resolved" not in expr, (
            f"{job_name}: pre-apply-failure must not reference steps.resolved (the aggregator)"
        )


def test_pre_apply_failure_input_defaults_false() -> None:
    doc = yaml.safe_load(WRITE_RECORD_ACTION.read_text(encoding="utf-8"))
    assert doc["inputs"]["pre-apply-failure"]["default"] == "false"


def test_per_job_artifact_modes() -> None:
    sandbox_jobs = _load_jobs(SANDBOX_WORKFLOW)
    reconcile_jobs = _load_jobs(RECONCILE_WORKFLOW)

    apply_sandbox_build = _find_step(sandbox_jobs["apply-sandbox"]["steps"], uses="./.github/actions/build-ducklake-artifacts")
    mode_expr = apply_sandbox_build["with"]["mode"]
    assert isinstance(mode_expr, str) and "'assert'" in mode_expr and "fetch" not in mode_expr, (
        f"apply-sandbox: push-path build mode must resolve to 'assert' (reproducibility canary), got {mode_expr!r}"
    )

    gated_apply_build = _find_step(sandbox_jobs["gated-apply"]["steps"], uses="./.github/actions/build-ducklake-artifacts")
    assert gated_apply_build["with"]["mode"] == "fetch"

    apply_reconcile_build = _find_step(
        reconcile_jobs["apply-reconcile"]["steps"],
        uses="./.dispatch-tooling/.github/actions/build-ducklake-artifacts",
    )
    assert apply_reconcile_build["with"]["mode"] == "fetch"

    gated_apply_reconcile_build = _find_step(
        reconcile_jobs["gated-apply-reconcile"]["steps"],
        uses="./.dispatch-tooling/.github/actions/build-ducklake-artifacts",
    )
    assert gated_apply_reconcile_build["with"]["mode"] == "fetch"


def test_write_record_infra_error_branch_contract() -> None:
    doc = yaml.safe_load(WRITE_RECORD_ACTION.read_text(encoding="utf-8"))
    assert doc["inputs"]["pre-apply-failure"]["default"] == "false"

    run_script = doc["runs"]["steps"][0]["run"]
    routed_idx = run_script.index('if [ "$ROUTED" = "true" ]; then')
    pre_apply_idx = run_script.index('if [ "$PRE_APPLY_FAILURE" = "true" ]; then')
    starved_idx = run_script.index('if [ "$REVIEW_OUTCOME" = "starved" ]; then')
    assert routed_idx < pre_apply_idx < starved_idx, (
        "branch ordering must be: routed -> pre-apply-failure (infra_error) -> STARVED"
    )

    branch_src = run_script[pre_apply_idx:starved_idx]
    assert "PRIOR_STATUS" in branch_src, "infra_error branch must read PRIOR_STATUS before overwriting"
    assert 'READBACK_STATUS" != "$PRIOR_STATUS"' in branch_src, (
        "infra_error branch's read-back verify must compare against PRIOR_STATUS, not a literal"
    )

    for job_name, wf_path, _ in JOBS:
        job = _load_jobs(wf_path)[job_name]
        record_step = _record_write_step(job)
        assert "pre-apply-failure" in record_step["with"], (
            f"{job_name}: write-convergence-record call site is missing the pre-apply-failure input"
        )


def test_dispatch_ref_side_checkout_wiring() -> None:
    reconcile_jobs = _load_jobs(RECONCILE_WORKFLOW)
    for job_name in ("apply-reconcile", "gated-apply-reconcile"):
        steps = reconcile_jobs[job_name]["steps"]
        checkout_indices = [i for i, s in enumerate(steps) if str(s.get("uses", "")).startswith("actions/checkout@")]
        assert len(checkout_indices) == 2, (
            f"{job_name}: expected exactly 2 actions/checkout steps, found {len(checkout_indices)}"
        )
        root_idx, side_idx = checkout_indices
        assert root_idx < side_idx, f"{job_name}: the dispatch-ref side-checkout must appear AFTER the root checkout"

        root_step = steps[root_idx]
        side_step = steps[side_idx]
        assert root_step["with"]["ref"] == "${{ env.RED_SHA }}", f"{job_name}: root checkout must pin ref: env.RED_SHA"
        assert side_step.get("with", {}).get("path") == ".dispatch-tooling", (
            f"{job_name}: side-checkout must declare path: .dispatch-tooling"
        )
        assert side_step.get("with", {}).get("ref") != "${{ env.RED_SHA }}", (
            f"{job_name}: side-checkout must NOT carry ref: env.RED_SHA -- a copy-pasted ref would still "
            "load the old (RED_SHA) scripts.ci.ducklake_artifacts module"
        )

        build_step = _find_step(steps, uses="./.dispatch-tooling/.github/actions/build-ducklake-artifacts")
        assert build_step["with"]["working-directory"] == ".dispatch-tooling", (
            f"{job_name}: build step must pass working-directory: .dispatch-tooling, else the composite "
            "action's `python3 -m scripts.ci.ducklake_artifacts` resolves the OLD RED_SHA module"
        )


def test_decision_154_present_with_reversal_conditions() -> None:
    content = DECISIONS_MD.read_text(encoding="utf-8")
    stub_match = re.search(r"^## Decision 154:.*?(?=^## Decision \d+:)", content, re.MULTILINE | re.DOTALL)
    assert stub_match, "Decision 154 header not found in docs/DECISIONS.md"
    superseder = re.search(r"\*\*Superseded by: Decision (\d+)\*\*", stub_match.group(0))
    assert superseder, "Decision 154 must be a compacted stub naming its superseder"
    number = superseder.group(1)
    entry_match = re.search(rf"^## Decision {number}:.*?(?=^## Decision \d+:)", content, re.MULTILINE | re.DOTALL)
    assert entry_match, f"Decision {number} not found in docs/DECISIONS.md"
    entry = entry_match.group(0)
    assert "```yaml reversal-conditions" in entry, f"Decision {number} has no reversal-conditions stanza"
    stanza = entry[entry.index("```yaml reversal-conditions") :]
    for condition in ("marker-hid-mutation", "ci-rca-independence-lapses", "markers-unactioned"):
        assert f"id: {condition}" in stanza, f"Decision 154's reversal condition {condition} is not live in Decision {number}"
