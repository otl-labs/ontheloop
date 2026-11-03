"""Public-log AWS account-ID masking guard (rec-4211; Decision 101 public-content boundary).

This repository is public, so its GitHub Actions job logs are public. The AWS sign-in action prints
its role-to-assume input, and the assumed role's unique ID, whose 8 characters after AROA encode the
account ID. The runner masks only exact registered secret values, so every sign-in job must (a) build
role-to-assume from the AWS_ACCOUNT_ID *secret* (a configuration variable is never masked) and (b)
reference the AWS_ROLE_ID_ACCOUNT_PART secret so the encoded copy is masked too. Each value must be its
own secret: a secret holding a whole ARN would not mask the bare ID where sts output or an AWS error
prints it, and the role-ID part is a different string from the account ID.

Rules (filesystem-only, no network):
  R1  any regex match of `vars.AWS_ACCOUNT_ID` (grep semantics, the dot matches any character) in any
      file under .github/workflows or .github/actions -- at least as strong as rec-4211's original grep.
  R2  any YAML string scalar under either tree holding both `arn:aws` and a `${{ }}` expression that uses
      the `vars` context as a whole token (not a substring such as steps.tfvars.outcome).
  R3  any workflow step using aws-actions/configure-aws-credentials whose with.role-to-assume does not
      reference secrets.AWS_ACCOUNT_ID, or with no reference to secrets.AWS_ROLE_ID_ACCOUNT_PART in its
      step env, its job env, or the workflow's top-level env; the top-level env counts only when every
      job in the workflow has a sign-in step, so a hoist never puts a secret into a deliberately
      secretless job (Decision 143 / Decision 201 cl.4).
  R4  any composite action under .github/actions using configure-aws-credentials: a composite cannot
      reference secrets, so job-wide masking could not be guaranteed.
  R5  any workflow top-level env value referencing either secret in a workflow where some job has no
      sign-in step, even if every sign-in step also carries its own reference.

Decision 170: calls registry.examined() with the number of sign-in steps scanned.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

from scripts.checks import _common, registry
from scripts.checks.ci_guards import _workflow_shell_bodies

_CREDENTIALS_ACTION_PREFIX = "aws-actions/configure-aws-credentials"
_ACCOUNT_REF = "secrets.AWS_ACCOUNT_ID"
_ROLE_PART_REF = "secrets.AWS_ROLE_ID_ACCOUNT_PART"
_VARS_LINE = re.compile(r"vars.AWS_ACCOUNT_ID")
_VARS_CONTEXT_EXPR = re.compile(r"\$\{\{(?:(?!\}\}).)*(?<![\w.-])vars(?![\w-])", re.DOTALL)
_TREES = (".github/workflows", ".github/actions")
_YAML_SUFFIXES = {".yml", ".yaml"}


def _rel(path: Path) -> str:
    return str(path.relative_to(_common.ROOT)).replace("\\", "/")


def _tree_files() -> list[Path]:
    files: list[Path] = []
    for tree in _TREES:
        base = _common.ROOT / tree
        if base.is_dir():
            files.extend(sorted(p for p in base.rglob("*") if p.is_file()))
    return files


def _load_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _strings(node: Any) -> Iterator[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)


def _is_signin(step: Any) -> bool:
    uses = step.get("uses") if isinstance(step, dict) else None
    return isinstance(uses, str) and uses.startswith(_CREDENTIALS_ACTION_PREFIX)


def _steps(container: Any) -> list[Any]:
    steps = container.get("steps") if isinstance(container, dict) else None
    return steps if isinstance(steps, list) else []


def _refs_part(env: Any) -> bool:
    return isinstance(env, dict) and any(_ROLE_PART_REF in s for s in _strings(env))


def _scan_text(rel: str, text: str) -> list[str]:
    return [
        f"{rel}:{number}: R1 references vars.AWS_ACCOUNT_ID (an unmasked configuration variable); use secrets.AWS_ACCOUNT_ID."
        for number, line in enumerate(text.splitlines(), start=1)
        if _VARS_LINE.search(line)
    ]


def _scan_expressions(rel: str, data: Any) -> list[str]:
    return [
        f"{rel}: R2 builds an ARN from the vars context; use secrets.AWS_ACCOUNT_ID."
        for s in _strings(data)
        if "arn:aws" in s and _VARS_CONTEXT_EXPR.search(s)
    ]


def _scan_composite(rel: str, data: Any) -> tuple[list[str], int]:
    runs = data.get("runs") if isinstance(data, dict) else None
    count = sum(1 for step in _steps(runs) if _is_signin(step))
    if not count:
        return [], 0
    message = (
        f"{rel}: R4 composite action uses {_CREDENTIALS_ACTION_PREFIX}; a composite cannot reference secrets, "
        "so sign in from the calling workflow."
    )
    return [message], count


def _scan_workflow(rel: str, data: Any) -> tuple[list[str], int]:
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, dict):
        return [], 0
    top_env = data.get("env")
    job_dicts = {job_id: job for job_id, job in jobs.items() if isinstance(job, dict)}
    every_job_signs_in = len(job_dicts) == len(jobs) and all(
        any(_is_signin(s) for s in _steps(job)) for job in job_dicts.values()
    )
    violations: list[str] = []
    top_refs = any(_ACCOUNT_REF in s or _ROLE_PART_REF in s for s in _strings(top_env))
    if top_refs and not every_job_signs_in:
        violations.append(
            f"{rel}: R5 workflow-level env references an AWS account secret but some job does not sign in; "
            "keep secretless jobs secretless (Decision 143)."
        )
    examined = 0
    for job_id, job in job_dicts.items():
        for index, step in enumerate(_steps(job)):
            if not _is_signin(step):
                continue
            examined += 1
            identity = f"{rel}::{job_id}::{step.get('id') or step.get('name') or f'#{index}'}"
            with_block = step.get("with")
            role = with_block.get("role-to-assume") if isinstance(with_block, dict) else None
            if not (isinstance(role, str) and _ACCOUNT_REF in role):
                violations.append(f"{identity}: R3 role-to-assume does not reference {_ACCOUNT_REF}.")
            masked = _refs_part(step.get("env")) or _refs_part(job.get("env")) or (every_job_signs_in and _refs_part(top_env))
            if not masked:
                violations.append(f"{identity}: R3 no reference to {_ROLE_PART_REF} in step, job or workflow env.")
    return violations, examined


def scan_repository() -> tuple[list[str], int]:
    """(violations, sign-in steps examined) over .github/workflows and .github/actions."""
    violations: list[str] = []
    examined = 0
    workflows = set(_workflow_shell_bodies._iter_workflows())
    for path in _tree_files():
        rel = _rel(path)
        violations.extend(_scan_text(rel, path.read_text(encoding="utf-8", errors="replace")))
        if path.suffix not in _YAML_SUFFIXES:
            continue
        data = _load_yaml(path)
        violations.extend(_scan_expressions(rel, data))
        if path in workflows:
            found, count = _scan_workflow(rel, data)
        elif rel.startswith(".github/actions/"):
            found, count = _scan_composite(rel, data)
        else:
            continue
        violations.extend(found)
        examined += count
    return violations, examined


@registry.register("validate_actions_account_id_masking", owner="platform")
def validate_actions_account_id_masking(failed: list[str]) -> None:
    """Fail when an AWS sign-in job could print the account ID or its role-ID encoding in a public log."""
    print("\n=== Actions account-ID masking guard ===")
    violations, examined = scan_repository()
    registry.examined(examined, unit="aws_signin_steps")

    if violations:
        for v in violations:
            print(f"  FAIL: {v}")
            failed.append(f"actions-account-id-masking: {v}")
    else:
        print(f"  PASS: {examined} sign-in step(s) read the account ID and role-ID part from secrets.")
