"""VP helper: ci.yml-shape structural guards for the verify_ci_workflow package."""

from __future__ import annotations

import re
from typing import Any

from scripts.verify_ci_workflow._shared import (
    FAST_STEP_IF,
    FULL_STEP_IF,
    _assert_runtime_lock,
    _full_tier_jobs,
    _get_steps_text,
    _load,
    _runs_validate_full,
    _runs_validate_pre,
)


def _check_jobs_and_flags() -> None:
    data = _load(".github/workflows/ci.yml")
    jobs = data.get("jobs", {})

    assert "validate-python" not in jobs, "Old validate-python job still present in ci.yml"
    assert "pr-validate" in jobs, "pr-validate job missing from ci.yml"
    assert "main-validate" in jobs, "main-validate job missing from ci.yml"

    pr_job = jobs["pr-validate"]
    main_job = jobs["main-validate"]

    assert pr_job.get("if") == "github.event_name == 'pull_request'", f"pr-validate.if is wrong: {pr_job.get('if')!r}"
    assert main_job.get("if") == "github.event_name == 'push'", f"main-validate.if is wrong: {main_job.get('if')!r}"

    pr_steps = _get_steps_text(pr_job)

    assert "--pre" in pr_steps, "pr-validate steps do not contain --pre"
    main_list = main_job.get("steps", [])
    full_steps = [i for i, s in enumerate(main_list) if _runs_validate_full(s)]
    pre_steps = [i for i, s in enumerate(main_list) if _runs_validate_pre(s)]
    scope_steps = [i for i, s in enumerate(main_list) if s.get("id") == "scope"]
    assert len(full_steps) == 1, f"main-validate must have exactly one full-tier validate step, found {len(full_steps)}"
    assert str(main_list[full_steps[0]].get("if", "")).strip() == FULL_STEP_IF, (
        f"main-validate full-tier step if must be {FULL_STEP_IF!r}, got {main_list[full_steps[0]].get('if')!r}"
    )
    assert len(pre_steps) == 1, f"main-validate must have exactly one fast-tier (--pre) step, found {len(pre_steps)}"
    assert str(main_list[pre_steps[0]].get("if", "")).strip() == FAST_STEP_IF, (
        f"main-validate fast-tier step if must be {FAST_STEP_IF!r}, got {main_list[pre_steps[0]].get('if')!r}"
    )
    assert len(scope_steps) == 1, "main-validate has no classify step (id scope) running scripts/ci/push_scope.py classify"
    scope = main_list[scope_steps[0]]
    assert "if" not in scope and "scripts/ci/push_scope.py classify" in str(scope.get("run", "")), (
        "main-validate classify step (id scope) must have no if and run scripts/ci/push_scope.py classify"
    )
    assert scope_steps[0] < min(full_steps[0], pre_steps[0]), "main-validate classify step must precede both validate steps"
    _assert_runtime_lock(main_job, "main-validate")


def _is_truthy_concurrency_flag(value: Any) -> bool:
    """Accept a YAML bool True or a templated/string truthy value ("true", "${{ true }}").

    GitHub Actions `cancel-in-progress` is normally a bare YAML boolean (PyYAML loads it as
    Python True), but may also appear quoted or as a `${{ ... }}` expression -- both of which
    PyYAML loads as plain strings.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    normalized = str(value).strip().lower()
    if normalized == "true":
        return True
    return bool(re.fullmatch(r"\$\{\{\s*true\s*\}\}", normalized))


def _check_concurrency() -> None:
    """VTS-11: pr-validate must cancel superseded runs on a per-PR key (so a force-pushed PR
    doesn't leave a stale run occupying a required check); main-validate -- the merge-to-main
    gate -- must NOT cancel in-flight runs (drift-canary + per-commit RCA role, dec-73 L8). Also
    retains the CD.21 ci-runner-serialisation-group-absence anti-regression guard for both jobs.
    """
    data = _load(".github/workflows/ci.yml")
    jobs = data.get("jobs", {})

    # CD.21: the self-hosted runner is retired; each job runs on its own
    # isolated GitHub-hosted runner, so the ci-runner serialisation group
    # is obsolete. Assert it is absent as an anti-regression guard.
    for job_name in ("pr-validate", "main-validate"):
        job = jobs.get(job_name)
        assert job is not None, f"Job {job_name!r} not found in ci.yml"
        concurrency = job.get("concurrency") or {}
        assert concurrency.get("group") != "ci-runner", (
            f"{job_name} still declares obsolete concurrency.group 'ci-runner' (retired by CD.21)"
        )

    # pr-validate: a superseded run (e.g. after a force-push) must be cancelled, keyed
    # per-PR so distinct PRs never cross-cancel each other.
    pr_job = jobs.get("pr-validate") or {}
    pr_concurrency = pr_job.get("concurrency") or {}
    pr_group = str(pr_concurrency.get("group", ""))
    assert any(key in pr_group for key in ("github.ref", "head_ref", "pull_request")), (
        f"pr-validate concurrency.group is not per-PR keyed (expected github.ref / head_ref / "
        f"pull_request in the group): {pr_group!r}"
    )
    assert _is_truthy_concurrency_flag(pr_concurrency.get("cancel-in-progress")), (
        f"pr-validate concurrency.cancel-in-progress is not truthy: {pr_concurrency.get('cancel-in-progress')!r}"
    )

    # main-validate: the merge-to-main gate must run to completion even if superseded --
    # cancel-in-progress must stay absent/false.
    main_job = jobs.get("main-validate") or {}
    main_concurrency = main_job.get("concurrency") or {}
    assert not _is_truthy_concurrency_flag(main_concurrency.get("cancel-in-progress")), (
        f"main-validate concurrency.cancel-in-progress is truthy (must not cancel in-flight main "
        f"runs): {main_concurrency.get('cancel-in-progress')!r}"
    )


def _checkout_step(job: dict[str, Any]) -> dict[str, Any] | None:
    for step in job.get("steps", []):
        if str(step.get("uses", "")).startswith("actions/checkout"):
            return step
    return None


def _check_fetch_depth() -> None:
    """A dated correction on Decision 159 clause 1 (amends/subsumes it; mints no new Decision
    number, D177 post-lock dialect): every full-tier job -- the jobs
    _full_tier_jobs() derives, canary included -- must check out full history (fetch-depth: 0),
    so history-dependent tests (e.g. the fast-tier corpus harness) never starve on a bounded
    depth. This rule subsumes the retired main-validate == 2 pin outright.

    pr-validate is NOT a full-tier job (it runs --pre only) -- Decision 181: that leg is
    RETAINED alongside the rule below, never replaced by it.
    """
    ci_data = _load(".github/workflows/ci.yml")
    canary_data = _load(".github/workflows/main-canary.yml")

    pr_checkout = _checkout_step(ci_data.get("jobs", {}).get("pr-validate", {}))
    assert pr_checkout is not None, "pr-validate has no checkout step"
    pr_with = pr_checkout.get("with", {}) or {}
    assert pr_with.get("fetch-depth") == 0, f"pr-validate checkout fetch-depth is {pr_with.get('fetch-depth')!r}, expected 0"

    full_tier_jobs = _full_tier_jobs(ci_data) + _full_tier_jobs(canary_data)
    # Fail closed (Decision 170): a zero-match derivation here is an authoring/regression bug in
    # the derivation itself, never a silently-skipped guard.
    assert full_tier_jobs, "no full-tier job found in ci.yml or main-canary.yml"

    for job_name, job in full_tier_jobs:
        checkout = _checkout_step(job)
        assert checkout is not None, f"{job_name} has no checkout step"
        with_block = checkout.get("with", {}) or {}
        assert with_block.get("fetch-depth") == 0, (
            f"{job_name} checkout fetch-depth is {with_block.get('fetch-depth')!r}, expected 0 "
            "(every full-tier job must check out full history -- Decision 159 clause 1, as corrected)"
        )


def _check_full_tier_runtime_lock() -> None:
    """Single-enumeration (Decision 104): both legs derive their job(s) via _full_tier_jobs(),
    the package-wide sole full-tier-job derivation, rather than re-deriving by job name."""
    ci_full_tier = _full_tier_jobs(_load(".github/workflows/ci.yml"))
    assert ci_full_tier, "no full-tier job found in ci.yml"
    for job_name, job in ci_full_tier:
        _assert_runtime_lock(job, job_name)

    canary_full_tier = _full_tier_jobs(_load(".github/workflows/main-canary.yml"))
    assert len(canary_full_tier) == 1, "main-canary.yml must contain exactly one full-tier job"
    for job_name, job in canary_full_tier:
        _assert_runtime_lock(job, job_name)


_MODULE_INVOCATION_RE = re.compile(
    r"(?:python[0-9.]*\s+-m\s+scripts\.([A-Za-z_][A-Za-z0-9_]*)|scripts/([A-Za-z_][A-Za-z0-9_]*)\.py)"
)
_CHECK_LIKE_RE = re.compile(r"^(validate|verify|check)")


def _check_validate_single_source() -> None:
    """Decision 80: ci.yml's only validation entrypoint is `scripts.validate`.

    Every check-like `python -m scripts.<mod>` / `scripts/<mod>.py` invocation in
    ci.yml (module name matching ^(validate|verify|check)) must resolve to the
    scripts.validate registry runner -- not a bespoke module bypassing it.
    """
    data = _load(".github/workflows/ci.yml")
    jobs = data.get("jobs", {})

    violations = []
    for job_name, job in jobs.items():
        steps_text = _get_steps_text(job)
        for match in _MODULE_INVOCATION_RE.finditer(steps_text):
            module = match.group(1) or match.group(2)
            if _CHECK_LIKE_RE.match(module) and module != "validate":
                violations.append(f"{job_name}: scripts.{module}")

    assert not violations, f"ci.yml invokes check-like module(s) other than scripts.validate: {violations}"


def _admits_pull_request(if_expr: Any) -> bool:
    """A job is PR-gating unless its `if` is a push-only guard.

    A job with NO `if` key runs on every triggering event, including pull_request --
    that makes it PR-gating too (e.g. terraform-validate). A job whose `if` mentions
    `push` but not `pull_request` is treated as a push-only guard and excluded. An
    `if` mentioning neither (e.g. a schedule/workflow_dispatch-only guard) defaults
    to PR-gating -- the conservative direction, since excluding a job that actually
    can run on pull_request would silently create an ungated merge path.
    """
    if if_expr is None:
        return True
    if_str = str(if_expr)
    if "pull_request" in if_str:
        return True
    if "push" in if_str:
        return False
    return True


def _check_signal_green_needs() -> None:
    """Every PR-gating job in ci.yml must be listed in signal-green.needs."""
    data = _load(".github/workflows/ci.yml")
    jobs = data.get("jobs", {})

    signal_green = jobs.get("signal-green")
    assert signal_green is not None, "signal-green job missing from ci.yml"

    needs = signal_green.get("needs") or []
    if isinstance(needs, str):
        needs = [needs]

    missing = [
        job_name
        for job_name, job in jobs.items()
        if job_name != "signal-green" and _admits_pull_request(job.get("if")) and job_name not in needs
    ]

    assert not missing, f"PR-gating job(s) missing from signal-green.needs: {missing}"
