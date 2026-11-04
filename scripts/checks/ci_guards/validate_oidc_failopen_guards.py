"""Fail-open assume-role guard (Decision 172 PR-2 / OIDC immutable-subject-recovery outage).

Standing guard closing the exact defect class this plan's incident review found: of 26
assume-role steps in .github/workflows, only 7 declare `continue-on-error: true`, and of those
only 4 carried a NEGATIVE-branch classifier that actually surfaces a failed assume -- the other
3 (terraform-apply-sandbox's advisory-status and ducklake-artifacts, formerly speculative-plan, terraform-drift's assume)
either had no `id` at all or gated every downstream step on a POSITIVE `== 'success'` check, so a
failed assume silently skipped every substantive step and the job still reported success. An
assume-role failure and "nothing to do" are indistinguishable without an explicit negative-branch
classifier -- this guard makes that classifier mandatory wherever the job can otherwise mask it.

Predicate: for every step under .github/workflows/**  that (a) `uses:` an
aws-actions/configure-aws-credentials action AND (b) declares `continue-on-error: true`, the
guard requires:
  1. A non-empty `id:` on that step.
  2. At least one reference to `steps.<id>.outcome` in a NEGATIVE branch (a `!=` comparison
     against `success`) ANYWHERE in the same job -- a step-level `if:` expression, an `env:`
     value, or an inline `run:` body.

Matches `.outcome` EXCLUSIVELY, never `.conclusion` -- GitHub Actions forces `conclusion` to
`success` for a `continue-on-error: true` step regardless of its actual `outcome`, so accepting a
`.conclusion` reference as a valid classifier would admit a permanently-dead check (it can never
observe a failure). The negative-branch match is quoting-agnostic and tolerates the
expression-split form ci.yml's sanctioned `mirror_oidc` classifier uses:
`"${{ steps.mirror_oidc.outcome }}" != "success"`, where the `!=` is separated from the word
`outcome` by the closing `}}` expression braces and a quote character -- a naive
`outcome.{0,3}!=` proximity match would miss this shape, so `_NEGATIVE_BRANCH_TEMPLATE` explicitly
threads an optional `}}`/quote run between `outcome` and `!=`.

No allowlist, no frozen baseline (Decision 163: the negative-branch predicate removes the
exception structurally rather than formalising a list of sanctioned sites) -- every
continue-on-error assume-role step in the repository is scanned on every run.

Decision 170 (MANDATORY declaration obligation): calls registry.examined()/skipped() -- this is a
NEW check, so it cannot be grandfathered into config/check_accounting_baseline.yaml (frozen
_BASELINE_SEED; validate_check_accounting rejects an undeclared new check outright).

Filesystem-only: no subprocess, no network, matching every sibling ci_guards module.
"""

from __future__ import annotations

import re
from typing import Any

import yaml

from scripts.checks import _common, registry
from scripts.checks.ci_guards import _workflow_shell_bodies

_CREDENTIALS_ACTION_PREFIX = "aws-actions/configure-aws-credentials"

# Threads an optional `}}` / quote run between the `.outcome` reference and the `!=` comparator
# so both the bare GH-Actions-expression form (`steps.x.outcome != 'success'`) and the
# expression-split shell form (`"${{ steps.x.outcome }}" != "success"`) match identically.
_NEGATIVE_BRANCH_TEMPLATE = r"steps\.{id}\.outcome\s*\}}*\s*[\"']?\s*!=\s*[\"']?success[\"']?"


def _uses_credentials_action(step: dict[str, Any]) -> bool:
    uses = step.get("uses")
    return isinstance(uses, str) and uses.startswith(_CREDENTIALS_ACTION_PREFIX)


def _is_continue_on_error(step: dict[str, Any]) -> bool:
    return step.get("continue-on-error") is True


def _job_search_text(steps: list[Any]) -> str:
    """Every string value from every step's `if`, `run`, and `env` mapping in the job, joined --
    the corpus the negative-branch reference is searched across (Decision spec: "anywhere in the
    same job", not only the candidate step itself)."""
    chunks: list[str] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        if_expr = step.get("if")
        if isinstance(if_expr, str):
            chunks.append(if_expr)
        run_body = step.get("run")
        if isinstance(run_body, str):
            chunks.append(run_body)
        env = step.get("env")
        if isinstance(env, dict):
            chunks.extend(v for v in env.values() if isinstance(v, str))
    return "\n".join(chunks)


def _has_negative_branch(search_text: str, step_id: str) -> bool:
    pattern = re.compile(_NEGATIVE_BRANCH_TEMPLATE.format(id=re.escape(step_id)))
    return pattern.search(search_text) is not None


def _scan_job(rel_path: str, job_id: str, job: dict[str, Any]) -> list[str]:
    steps = job.get("steps")
    if not isinstance(steps, list):
        return []
    search_text = _job_search_text(steps)
    violations: list[str] = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        if not (_uses_credentials_action(step) and _is_continue_on_error(step)):
            continue
        step_id = step.get("id")
        identity = step_id if isinstance(step_id, str) and step_id else f"#{index}"
        if not (isinstance(step_id, str) and step_id):
            violations.append(
                f"{rel_path}::{job_id}::{identity}: continue-on-error assume-role step declares "
                "no id -- a failed assume cannot be classified downstream at all."
            )
            continue
        if not _has_negative_branch(search_text, step_id):
            violations.append(
                f"{rel_path}::{job_id}::{identity}: continue-on-error assume-role step has no "
                f"negative-branch classifier referencing steps.{step_id}.outcome anywhere in the "
                "job (only .outcome counts -- .conclusion is forced to success under "
                "continue-on-error and can never classify a failure)."
            )
    return violations


def scan_repository() -> tuple[list[str], int]:
    """(violations, candidate_count) over every .github/workflows/*.yml|yaml file."""
    violations: list[str] = []
    candidate_count = 0
    for path in _workflow_shell_bodies._iter_workflows():
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        rel_path = str(path.relative_to(_common.ROOT)).replace("\\", "/")
        jobs = data.get("jobs")
        if not isinstance(jobs, dict):
            continue
        for job_id, job in jobs.items():
            if not isinstance(job, dict):
                continue
            steps = job.get("steps")
            if isinstance(steps, list):
                candidate_count += sum(
                    1
                    for step in steps
                    if isinstance(step, dict) and _uses_credentials_action(step) and _is_continue_on_error(step)
                )
            violations.extend(_scan_job(rel_path, job_id, job))
    return violations, candidate_count


@registry.register("validate_oidc_failopen_guards", owner="platform")
def validate_oidc_failopen_guards(failed: list[str]) -> None:
    """Fail on a continue-on-error assume-role step with no id, or one whose id is referenced
    only in positive `== 'success'` gates (or not referenced at all). See module docstring."""
    print("\n=== OIDC fail-open assume-role guard ===")
    violations, candidate_count = scan_repository()
    registry.examined(candidate_count, unit="continue_on_error_assume_role_steps")

    if violations:
        for v in violations:
            print(f"  FAIL: {v}")
            failed.append(f"oidc-failopen-guard: {v}")
    else:
        print(
            f"  PASS: {candidate_count} continue-on-error assume-role step(s) all declare an id "
            "and a negative-branch classifier."
        )
