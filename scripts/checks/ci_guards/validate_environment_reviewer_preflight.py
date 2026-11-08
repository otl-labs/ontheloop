"""Reviewer-gated jobs fail closed without their Environment's reviewer approval (rec-4979, rec-4961).

GitHub creates a missing Environment with no protection the first time a job names it, so a gated job could run
unattended. Every reviewer-gated job therefore proves, in the job, that the run holds a recorded approval for its
Environment by a listed reviewer (scripts/ci/environment_reviewer_preflight.py). Decision 92 enforcement note; no new
Decision. Predicates take parsed workflows ({filename: parsed yaml}), terraform/github text ({filename: text}) and
CODEOWNERS text so the red cases run on synthetic data; the registered check loads the real tree.

  (a) every job's environment: is a literal declared by a github_repository_environment resource or in UI_MANAGED
  (b) REVIEWER_ENVS = declared resources with a reviewers block plus UI_MANAGED, minus EXEMPT; a UI_MANAGED name also
      declared in HCL fails, so the build that brings it into HCL drops it
  (c) every job naming a REVIEWER_ENVS name runs the approvals check before any step other than actions/checkout, with
      the token and reviewer variable, under actions read or write; in a job that takes a ref: checkout AND a
      .dispatch-tooling side checkout, that step and every one-line `python3 -m scripts.ci.` delegate run from
      .dispatch-tooling
  (d) UPSTREAM jobs check the gated job's Environment has reviewers (and no admin bypass where HCL declares none)
  (e) the script imports only the standard library and never names the runner's env, path or output files
  (f) .github/CODEOWNERS covers the script and this check
"""

from __future__ import annotations

import ast
import re
import sys
from typing import Any

import yaml

from scripts.checks import _common, registry
from scripts.checks.ci_guards.validate_trusted_plan_environment import _arg, _blocks, _env_name, _jobs, _owned, _steps
from scripts.verify_ci_workflow import _load

SCRIPT = "scripts/ci/environment_reviewer_preflight.py"
GUARD = "scripts/checks/ci_guards/validate_environment_reviewer_preflight.py"
UI_MANAGED = {"pypi": "rec-4549"}
EXEMPT = frozenset({"deployment-approval-probe"})
UPSTREAM = {"approved-text-approval.yml": ("render", "approve"), "terraform-github-apply.yml": ("preview", "apply")}
SIDE_CHECKOUT = ".dispatch-tooling"
TOKEN_EXPR = "${{ github.token }}"
LOGINS_EXPR = "${{ vars.TF_GITHUB_GATED_APPLY_REVIEWER_LOGINS }}"
_FORBIDDEN = ("GITHUB_" + "ENV", "GITHUB_" + "PATH", "GITHUB_" + "OUTPUT")
_DELEGATE_PREFIX = "python3 -m scripts.ci."


def _approvals_cmd(name: str) -> str:
    return f"python3 -I {SCRIPT} approvals --environment {name}"


def _environment_cmd(name: str, no_bypass: bool) -> str:
    return f"python3 -I {SCRIPT} environment --environment {name}" + (" --require-no-admin-bypass" if no_bypass else "")


def declared_environments(terraform: dict[str, str]) -> dict[str, dict[str, bool]]:
    """{environment name: {"reviewers": bool, "no_bypass": bool}} for every github_repository_environment resource."""
    out: dict[str, dict[str, bool]] = {}
    for block in _blocks("\n".join(terraform.values()), "github_repository_environment").values():
        raw = _arg(block, "environment")
        if raw is None:
            continue
        out[raw.strip('"')] = {
            "reviewers": bool(re.search(r"^\s*reviewers\s*\{", block, re.M)),
            "no_bypass": _arg(block, "can_admins_bypass") == "false",
        }
    return out


def reviewer_envs(declared: dict[str, dict[str, bool]]) -> set[str]:
    return ({n for n, d in declared.items() if d["reviewers"]} | set(UI_MANAGED)) - EXEMPT


def _all_jobs(workflows: dict[str, dict[str, Any]]):
    for fname, wf in sorted(workflows.items()):
        for jname, job in _jobs(wf).items():
            yield fname, wf, jname, job


def check_environment_names(workflows: dict[str, dict[str, Any]], declared: dict[str, dict[str, bool]]) -> list[str]:
    out = [
        f"b: {n} is UI_MANAGED ({UI_MANAGED[n]}) but also declared in terraform/github; drop it"
        for n in UI_MANAGED
        if n in declared
    ]
    for fname, _wf, jname, job in _all_jobs(workflows):
        name = _env_name(job)
        if name is None:
            continue
        if "${{" in name:
            out.append(f"a: {fname}:{jname} environment is an expression, not a literal")
        elif name not in declared and name not in UI_MANAGED:
            out.append(f"a: {fname}:{jname} environment {name} is not declared in terraform/github or UI_MANAGED")
    return out


def _grants_actions(wf: dict[str, Any], job: dict[str, Any]) -> bool:
    perms = job.get("permissions", wf.get("permissions"))
    return isinstance(perms, dict) and perms.get("actions") in ("read", "write")


def _is_checkout(step: dict[str, Any]) -> bool:
    return str(step.get("uses", "")).startswith("actions/checkout@")


def _ref_pinned_with_side_checkout(steps: list[dict[str, Any]]) -> bool:
    checkouts = [s for s in steps if _is_checkout(s)]
    if not checkouts or not isinstance((checkouts[0].get("with") or {}).get("ref"), str):
        return False
    return any((s.get("with") or {}).get("path") == SIDE_CHECKOUT for s in checkouts)


def _unconditional(step: dict[str, Any]) -> bool:
    return "if" not in step and not step.get("continue-on-error")


def _first_checkout_takes_ref(steps: list[dict[str, Any]]) -> bool:
    checkouts = [s for s in steps if _is_checkout(s)]
    return bool(checkouts) and isinstance((checkouts[0].get("with") or {}).get("ref"), str)


def _step_env_ok(step: dict[str, Any]) -> bool:
    return step.get("env") == {"GH_TOKEN": TOKEN_EXPR, "REVIEWER_LOGINS": LOGINS_EXPR}


def check_gated_jobs(workflows: dict[str, dict[str, Any]], declared: dict[str, dict[str, bool]]) -> tuple[list[str], int]:
    envs = reviewer_envs(declared)
    out: list[str] = []
    examined = 0
    for fname, wf, jname, job in _all_jobs(workflows):
        steps = _steps(job)
        where = f"{fname}:{jname}"
        name = _env_name(job)
        if name in envs:
            examined += 1
            first = next((s for s in steps if not _is_checkout(s)), None)
            if first is None or str(first.get("run", "")).strip() != _approvals_cmd(name):
                out.append(f"c: {where} must run `{_approvals_cmd(name)}` before any step other than actions/checkout")
            elif not _unconditional(first):
                out.append(f"c: {where} approvals step must carry no if: and no continue-on-error")
            elif not _step_env_ok(first):
                out.append(
                    f"c: {where} approvals step must map GH_TOKEN to github.token and REVIEWER_LOGINS to the repo variable"
                )
            if not _grants_actions(wf, job):
                out.append(f"c: {where} must grant actions read or write for the approvals check")
            if _first_checkout_takes_ref(steps) and not _ref_pinned_with_side_checkout(steps):
                out.append(f"c: {where} takes a ref: checkout, so it needs a {SIDE_CHECKOUT} side checkout for the script")
        if _ref_pinned_with_side_checkout(steps):
            for step in steps:
                body = str(step.get("run", "")).strip()
                approvals = body.startswith(f"python3 -I {SCRIPT} approvals")
                delegate = body.startswith(_DELEGATE_PREFIX) and "\n" not in body
                if (approvals or delegate) and step.get("working-directory") != SIDE_CHECKOUT:
                    out.append(f"c: {where} step `{step.get('name', body)}` must run with working-directory: {SIDE_CHECKOUT}")
    return out, examined


def check_upstream(workflows: dict[str, dict[str, Any]], declared: dict[str, dict[str, bool]]) -> list[str]:
    out: list[str] = []
    for fname, (upstream, gated) in UPSTREAM.items():
        wf = workflows.get(fname)
        jobs = _jobs(wf) if wf else {}
        wf = wf or {}
        if upstream not in jobs or gated not in jobs:
            out.append(f"d: {fname} must define jobs {upstream} and {gated}")
            continue
        name = _env_name(jobs[gated])
        if name is None or name not in reviewer_envs(declared):
            continue
        want = _environment_cmd(name, declared.get(name, {}).get("no_bypass", False))
        job = jobs[upstream]
        found = [s for s in _steps(job) if str(s.get("run", "")).strip() == want]
        if not found:
            out.append(f"d: {fname}:{upstream} must run `{want}`")
        elif found[0].get("continue-on-error"):
            out.append(f"d: {fname}:{upstream} environment check must not set continue-on-error")
        if not _grants_actions(wf, job):
            out.append(f"d: {fname}:{upstream} must grant actions read for the environment check")
    return out


def check_script(text: str) -> list[str]:
    out = [f"e: the script must not name {n}" for n in _FORBIDDEN if n in text]
    stdlib = sys.stdlib_module_names
    for node in ast.walk(ast.parse(text)):
        mods = [a.name for a in node.names] if isinstance(node, ast.Import) else []
        if isinstance(node, ast.ImportFrom):
            mods = [("." * node.level) + (node.module or "")]
        out += [f"e: the script imports non-stdlib {m}" for m in mods if m.split(".")[0] not in stdlib]
    return out


def check_codeowners(codeowners: str) -> list[str]:
    return [f"f: .github/CODEOWNERS does not cover {p}" for p in (SCRIPT, GUARD) if not _owned(p, codeowners)]


def run_all(
    workflows: dict[str, dict[str, Any]], terraform: dict[str, str], codeowners: str, script_text: str
) -> tuple[list[str], int]:
    declared = declared_environments(terraform)
    gated, examined = check_gated_jobs(workflows, declared)
    out = check_environment_names(workflows, declared) + gated + check_upstream(workflows, declared)
    return out + check_script(script_text) + check_codeowners(codeowners), examined


@registry.register("validate_environment_reviewer_preflight", owner="platform")
def validate_environment_reviewer_preflight(failed: list[str]) -> None:
    """Assert every reviewer-gated job checks its approval in-job. See module docstring."""
    print("\n=== reviewer-gate preflight ===")
    try:
        workflows = {
            p.name: _load(str(p))
            for p in sorted((_common.ROOT / ".github" / "workflows").glob("*.y*ml"))
            if p.suffix in (".yml", ".yaml")
        }
        terraform = {
            p.name: p.read_text(encoding="utf-8") for p in sorted((_common.ROOT / "terraform" / "github").glob("*.tf"))
        }
        codeowners = (_common.ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8")
        script_text = (_common.ROOT / SCRIPT).read_text(encoding="utf-8")
        violations, examined = run_all(workflows, terraform, codeowners, script_text)
    except (OSError, UnicodeDecodeError, SyntaxError, yaml.YAMLError, TypeError, AttributeError) as exc:
        print(f"  FAIL: could not load or parse the inputs: {exc}")
        failed.append("reviewer-gate-preflight: inputs unreadable, so no predicate ran")
        registry.examined(0, unit="gated_jobs")
        return
    for violation in violations:
        print(f"  FAIL: {violation}")
        failed.append(f"reviewer-gate-preflight: {violation}")
    registry.examined(examined, unit="gated_jobs")
    if not violations:
        print(f"  PASS: {examined} gated jobs, predicates (a)-(f)")
