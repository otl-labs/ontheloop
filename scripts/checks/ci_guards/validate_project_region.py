"""Project-region single-source guard (rec-4067, Decision 202 clause 4).

config/aws-region is the project's one AWS region. Every workflow sign-in reads it through
.github/actions/aws-region (which fails closed on a missing or invalid value and exports
AWS_REGION / AWS_DEFAULT_REGION); every Terraform S3 backend reads it from AWS_REGION or an
init-time -backend-config region. This guard is filesystem-only (no network, no git) so a
tmp-tree red case needs no git history.

Rules:
  R1  config/aws-region holds exactly one valid AWS region id (and nothing else).
  R2  no AWS region or availability-zone id appears anywhere in the raw text of any file under
      .github/ (comments included) -- the region-agnostic regex is designed so the Neon id
      aws-eu-west-2 is never matched (the region alternation must not be immediately preceded by
      a hyphen or word character).
  R3  no such id appears in the raw text (comments included) of any terraform/**/*.tf or *.hcl
      file, except the default of variable "aws_region" in terraform/bootstrap/variables.tf and
      terraform/personal/providers.tf, each of which must equal config/aws-region; no
      backend "s3" block or backend*.hcl file sets a region (literal or expression).
  R4  every configure-aws-credentials step's aws-region is either `${{ env.AWS_REGION }}` with an
      earlier ./.github/actions/aws-region step in the same job whose most recent preceding
      checkout step (actions/checkout, any version or pinned sha) has neither `ref` nor `path`
      (the workflow's own commit, never an older tree), or
      `${{ needs.<job>.outputs.aws_region }}` where <job> is in this job's `needs` and binds its
      `aws_region` output to a step using the aws-region action.
  R5  no workflow, job or step env sets AWS_REGION or AWS_DEFAULT_REGION.

Failure text names file, job and step -- never a value beyond the region id itself.
Decision 170: calls registry.examined() with the number of files and sign-in steps scanned.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from scripts.checks import _common, registry

# (?<![\w-]) / (?![\w-]) keep this from matching inside a longer token such as the Neon region id
# aws-eu-west-2 (there, "eu-west-2" is immediately preceded by a hyphen, so the lookbehind blocks
# it) while still matching a bare region or AZ id (eu-west-2, eu-west-2a) anywhere else.
_REGION_RE = re.compile(
    r"(?<![\w-])(af|ap|ca|cn|eu|il|me|mx|sa|us|us-gov)-"
    r"(central|north|south|east|west|northeast|southeast|northwest|southwest)-\d[a-z]?(?![\w-])"
)
_VALID_REGION_RE = re.compile(r"^[a-z]{2}(-gov)?-[a-z]+-[0-9]$")

_SIGNIN_PREFIX = "aws-actions/configure-aws-credentials"
_AWS_REGION_ACTION = "./.github/actions/aws-region"
_CHECKOUT_PREFIX = "actions/checkout@"

_PINNED_VAR_FILES = {
    "terraform/bootstrap/variables.tf",
    "terraform/personal/providers.tf",
}

_VAR_AWS_REGION_RE = re.compile(r'variable\s+"aws_region"\s*\{([^}]*)\}', re.DOTALL)
_DEFAULT_RE = re.compile(r'default\s*=\s*"([^"]*)"')
_BACKEND_S3_RE = re.compile(r'backend\s+"s3"\s*\{([^}]*)\}', re.DOTALL)
_REGION_KEY_RE = re.compile(r"^\s*region\s*=", re.MULTILINE)


def _rel(path: Path) -> str:
    return str(path.relative_to(_common.ROOT)).replace("\\", "/")


def _github_files() -> list[Path]:
    base = _common.ROOT / ".github"
    if not base.is_dir():
        return []
    return sorted(p for p in base.rglob("*") if p.is_file())


def _workflow_files() -> list[Path]:
    base = _common.ROOT / ".github" / "workflows"
    if not base.is_dir():
        return []
    return sorted(p for p in base.glob("*.yml")) + sorted(p for p in base.glob("*.yaml"))


def _tf_hcl_files() -> list[Path]:
    base = _common.ROOT / "terraform"
    if not base.is_dir():
        return []
    return sorted(p for p in base.rglob("*.tf")) + sorted(p for p in base.rglob("*.hcl"))


def _check_region_file(violations: list[str]) -> str | None:
    region_file = _common.ROOT / "config" / "aws-region"
    if not region_file.is_file():
        violations.append("config/aws-region: R1 file is missing.")
        return None
    value = region_file.read_text(encoding="utf-8").strip()
    if not value or not _VALID_REGION_RE.fullmatch(value):
        violations.append(f"config/aws-region: R1 '{value}' is not a single valid AWS region id.")
        return None
    return value


def _scan_github_tree(violations: list[str]) -> int:
    files = _github_files()
    for path in files:
        rel = _rel(path)
        text = path.read_text(encoding="utf-8", errors="ignore")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for m in _REGION_RE.finditer(line):
                violations.append(f"{rel}:{lineno}: R2 region id '{m.group(0)}' found under .github/.")
    return len(files)


def _find_aws_region_default_span(text: str) -> tuple[int, int, str] | None:
    m = _VAR_AWS_REGION_RE.search(text)
    if not m:
        return None
    block = m.group(1)
    dm = _DEFAULT_RE.search(block)
    if not dm:
        return None
    block_start = m.start(1)
    return block_start + dm.start(1), block_start + dm.end(1), dm.group(1)


def _check_backend_region(rel: str, text: str, violations: list[str]) -> None:
    for m in _BACKEND_S3_RE.finditer(text):
        if _REGION_KEY_RE.search(m.group(1)):
            lineno = text[: m.start()].count("\n") + 1
            violations.append(f'{rel}:{lineno}: R3 backend "s3" block sets a region.')


def _check_backend_hcl_region(rel: str, text: str, violations: list[str]) -> None:
    m = _REGION_KEY_RE.search(text)
    if m:
        lineno = text[: m.start()].count("\n") + 1
        violations.append(f"{rel}:{lineno}: R3 backend hcl file sets a region.")


def _scan_terraform_tree(pinned_region: str | None, violations: list[str]) -> int:
    files = _tf_hcl_files()
    for path in files:
        rel = _rel(path)
        text = path.read_text(encoding="utf-8", errors="ignore")

        exempt_lines: set[int] = set()
        if rel in _PINNED_VAR_FILES:
            span = _find_aws_region_default_span(text)
            if span is not None:
                start, _end, value = span
                lineno = text[:start].count("\n") + 1
                exempt_lines.add(lineno)
                if pinned_region is not None and value != pinned_region:
                    violations.append(
                        f"{rel}:{lineno}: R3 aws_region variable default '{value}' differs from "
                        f"config/aws-region ('{pinned_region}')."
                    )

        for lineno, line in enumerate(text.splitlines(), start=1):
            if lineno in exempt_lines:
                continue
            for m in _REGION_RE.finditer(line):
                violations.append(
                    f"{rel}:{lineno}: R3 region id '{m.group(0)}' found (only the pinned aws_region default may hold one)."
                )

        _check_backend_region(rel, text, violations)
        if path.name.startswith("backend") and path.suffix == ".hcl":
            _check_backend_hcl_region(rel, text, violations)
    return len(files)


def _is_checkout(step: Any) -> bool:
    uses = step.get("uses") if isinstance(step, dict) else None
    return isinstance(uses, str) and uses.startswith(_CHECKOUT_PREFIX)


def _is_region_action(step: Any) -> bool:
    uses = step.get("uses") if isinstance(step, dict) else None
    return uses == _AWS_REGION_ACTION


def _is_signin(step: Any) -> bool:
    uses = step.get("uses") if isinstance(step, dict) else None
    return isinstance(uses, str) and uses.startswith(_SIGNIN_PREFIX)


def _jobs(data: Any) -> dict[str, Any]:
    jobs = data.get("jobs") if isinstance(data, dict) else None
    return jobs if isinstance(jobs, dict) else {}


def _steps(container: Any) -> list[Any]:
    steps = container.get("steps") if isinstance(container, dict) else None
    return steps if isinstance(steps, list) else []


def _job_outputs_aws_region_via_action(job: Any) -> bool:
    outputs = job.get("outputs") if isinstance(job, dict) else None
    if not isinstance(outputs, dict) or "aws_region" not in outputs:
        return False
    m = re.fullmatch(r"\$\{\{\s*steps\.([\w-]+)\.outputs\.aws_region\s*\}\}", str(outputs["aws_region"]).strip())
    if not m:
        return False
    step_id = m.group(1)
    return any(isinstance(s, dict) and s.get("id") == step_id and _is_region_action(s) for s in _steps(job))


def _needs_list(job: Any) -> list[str]:
    needs = job.get("needs") if isinstance(job, dict) else None
    if isinstance(needs, list):
        return needs
    if isinstance(needs, str):
        return [needs]
    return []


def _signin_region_ok(jobs: dict[str, Any], job: Any, step_index: int) -> bool:
    steps = _steps(job)
    step = steps[step_index]
    with_block = step.get("with") if isinstance(step, dict) else None
    raw = with_block.get("aws-region") if isinstance(with_block, dict) else None
    if not isinstance(raw, str):
        return False
    val = raw.strip()

    if val == "${{ env.AWS_REGION }}":
        region_step_idx = None
        for i in range(step_index - 1, -1, -1):
            if isinstance(steps[i], dict) and _is_region_action(steps[i]):
                region_step_idx = i
                break
        if region_step_idx is None:
            return False
        checkout_idx = None
        for i in range(region_step_idx - 1, -1, -1):
            if isinstance(steps[i], dict) and _is_checkout(steps[i]):
                checkout_idx = i
                break
        if checkout_idx is None:
            return False
        checkout_with = steps[checkout_idx].get("with")
        checkout_with = checkout_with if isinstance(checkout_with, dict) else {}
        return "ref" not in checkout_with and "path" not in checkout_with

    m = re.fullmatch(r"\$\{\{\s*needs\.([\w-]+)\.outputs\.aws_region\s*\}\}", val)
    if m:
        needed_job_id = m.group(1)
        if needed_job_id not in _needs_list(job):
            return False
        needed_job = jobs.get(needed_job_id)
        return isinstance(needed_job, dict) and _job_outputs_aws_region_via_action(needed_job)

    return False


def _check_env_dict(rel: str, where: str, env: Any, violations: list[str]) -> None:
    if not isinstance(env, dict):
        return
    for key in ("AWS_REGION", "AWS_DEFAULT_REGION"):
        if key in env:
            violations.append(f"{rel}: R5 {where} sets {key} in env.")


def _scan_workflow_wiring(violations: list[str]) -> int:
    signin_count = 0
    for path in _workflow_files():
        rel = _rel(path)
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:
            violations.append(f"{rel}: unparseable ({exc}).")
            continue
        if not isinstance(data, dict):
            continue

        _check_env_dict(rel, "workflow", data.get("env"), violations)
        jobs = _jobs(data)
        for job_id, job in jobs.items():
            if not isinstance(job, dict):
                continue
            _check_env_dict(rel, f"job {job_id}", job.get("env"), violations)
            for idx, step in enumerate(_steps(job)):
                if not (isinstance(step, dict) and _is_signin(step)):
                    continue
                signin_count += 1
                _check_env_dict(rel, f"job {job_id} step #{idx}", step.get("env"), violations)
                if not _signin_region_ok(jobs, job, idx):
                    violations.append(
                        f"{rel}: R4 job {job_id} step #{idx}: configure-aws-credentials aws-region "
                        "is not wired to the aws-region action."
                    )
    return signin_count


@registry.register("validate_project_region", owner="platform")
def validate_project_region(failed: list[str]) -> None:
    """Enforce the single AWS-region source (config/aws-region) across .github/ and terraform/."""
    print("\n=== project-region single-source guard ===")
    violations: list[str] = []

    pinned_region = _check_region_file(violations)
    github_count = _scan_github_tree(violations)
    tf_count = _scan_terraform_tree(pinned_region, violations)
    signin_count = _scan_workflow_wiring(violations)

    registry.examined(github_count + tf_count + signin_count, unit="files_and_signin_steps")

    if violations:
        for v in violations:
            print(f"  FAIL: {v}")
        failed.extend(f"project-region: {v}" for v in violations)
    else:
        print(f"  PASS: {github_count} .github/ files, {tf_count} terraform files, {signin_count} sign-in steps")
