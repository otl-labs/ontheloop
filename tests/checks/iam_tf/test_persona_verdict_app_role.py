"""Standing guard for PLAN-persona-verdict-app-grant (T4.32 egress E4): the verdict App key's role and Environment.

Three PRs share this file: PR 1 (bootstrap pre-stage and boundary Deny), PR 2 (the persona-verdict-app Environment and
the workflow fence) and PR 3 (the role in terraform/personal). Each case is a pure function of HCL or workflow text.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from scripts.checks.ci_guards.validate_trusted_plan_environment import _arg, _blocks
from scripts.verify_ci_workflow import _load

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TF_GITHUB_DIR = _REPO_ROOT / "terraform" / "github"
_WORKFLOWS_DIR = _REPO_ROOT / ".github" / "workflows"
_ENV = "persona-verdict-app"
_ENV_WORKFLOW = "persona-verdict.yml"
_ENV_JOB = "post"
_ROLE_NAME = "agent-platform-github-ci-persona-verdict-app"
_SECRET_TYPES = ("github_actions_environment_secret", "github_actions_environment_variable", "github_actions_secret")


def _tf_github_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(_TF_GITHUB_DIR.glob("*.tf")))


def environment_problems(text: str) -> list[str]:
    envs = {n: b for n, b in _blocks(text, "github_repository_environment").items() if _arg(b, "environment") == f'"{_ENV}"'}
    if len(envs) != 1:
        return [f"expected exactly one {_ENV} environment, found {len(envs)}"]
    name, block = next(iter(envs.items()))
    problems = [
        f"{arg} must be {want}"
        for arg, want in (
            ("can_admins_bypass", "false"),
            ("protected_branches", "false"),
            ("custom_branch_policies", "true"),
            ("prevent_destroy", "true"),
        )
        if _arg(block, arg) != want
    ]
    if re.search(r"^\s*(reviewers|wait_timer)\b", block, re.M):
        problems.append("environment declares reviewers or a wait timer")
    refs = {f"github_repository_environment.{name}.environment", f'"{_ENV}"'}
    policies = [
        b for b in _blocks(text, "github_repository_environment_deployment_policy").values() if _arg(b, "environment") in refs
    ]
    if len(policies) != 1:
        problems.append(f"expected exactly one deployment policy, found {len(policies)}")
    for b in policies:
        if _arg(b, "branch_pattern") != '"main"' or _arg(b, "tag_pattern") is not None:
            problems.append('deployment policy must be branch_pattern = "main" with no tag_pattern')
    return problems


def secret_problems(text: str) -> list[str]:
    return [
        f"{rtype}.{name} names {token}"
        for rtype in _SECRET_TYPES
        for name, block in _blocks(text, rtype).items()
        for token in (_ENV, "persona_verdict_app")
        if token in block or token in name
    ]


def _workflow_env(job: dict[str, Any]) -> str | None:
    env = job.get("environment")
    if isinstance(env, dict):
        env = env.get("name")
    return None if env is None else str(env)


def _jobs(wf: dict[str, Any]) -> dict[str, dict[str, Any]]:
    jobs = wf.get("jobs")
    return {k: v for k, v in jobs.items() if isinstance(v, dict)} if isinstance(jobs, dict) else {}


def _assumes_role(job: dict[str, Any]) -> bool:
    steps = job.get("steps") if isinstance(job.get("steps"), list) else []
    return any(
        isinstance(s, dict)
        and "configure-aws-credentials" in str(s.get("uses", ""))
        and str((s.get("with") or {}).get("role-to-assume", "")).endswith(f"/{_ROLE_NAME}")
        for s in steps
    )


def workflow_problems(workflows: dict[str, dict[str, Any]]) -> list[str]:
    problems: list[str] = []
    for fname, wf in workflows.items():
        for jname, job in _jobs(wf).items():
            env = _workflow_env(job)
            allowed = fname == _ENV_WORKFLOW and jname == _ENV_JOB
            if env is not None and "${{" in env:
                problems.append(f"{fname}:{jname} names an environment by expression")
            if env == _ENV and not allowed:
                problems.append(f"{fname}:{jname} names the {_ENV} environment")
            if _assumes_role(job) and not allowed:
                problems.append(f"{fname}:{jname} assumes the verdict App key role")
    return problems


def _real_workflows() -> dict[str, dict[str, Any]]:
    return {p.name: _load(str(p)) for p in sorted(_WORKFLOWS_DIR.glob("*.yml"))}


def test_persona_verdict_app_environment_shape() -> None:
    assert environment_problems(_tf_github_text()) == []


def test_no_secret_or_variable_names_the_app_environment() -> None:
    assert secret_problems(_tf_github_text()) == []


def test_workflow_fence() -> None:
    assert workflow_problems(_real_workflows()) == []


def test_red_environment_shape() -> None:
    base = _tf_github_text()
    env = _blocks(base, "github_repository_environment")["persona_verdict_app"]
    policy = _blocks(base, "github_repository_environment_deployment_policy")["persona_verdict_app_main"]
    for broken in (
        env.replace("can_admins_bypass = false", "can_admins_bypass = true"),
        env.replace("  deployment_branch_policy {", "  reviewers {\n    users = [1]\n  }\n\n  deployment_branch_policy {"),
        env.replace("  deployment_branch_policy {", "  wait_timer = 5\n\n  deployment_branch_policy {"),
        env.replace("protected_branches     = false", "protected_branches     = true"),
        env.replace("custom_branch_policies = true", "custom_branch_policies = false"),
        env.replace("prevent_destroy = true", "prevent_destroy = false"),
    ):
        assert environment_problems(base.replace(env, broken)), broken
    assert environment_problems(base.replace(policy, policy.replace('branch_pattern = "main"', 'branch_pattern = "*"')))
    second = (
        '\nresource "github_repository_environment_deployment_policy" "persona_verdict_app_extra" {\n'
        "  environment    = github_repository_environment.persona_verdict_app.environment\n"
        '  branch_pattern = "main"\n}\n'
    )
    assert any("exactly one deployment policy" in p for p in environment_problems(base + second))
    assert environment_problems("")


def test_red_environment_secret() -> None:
    extra = (
        f'\nresource "github_actions_environment_secret" "x" {{\n  environment     = "{_ENV}"\n  secret_name     = "X"\n}}\n'
    )
    assert secret_problems(_tf_github_text() + extra)


def test_red_workflow_fence() -> None:
    job = {"runs-on": "ubuntu-latest", "environment": _ENV, "steps": []}
    assert workflow_problems({"other.yml": {"on": "push", "jobs": {"j": job}}})
    assert workflow_problems({_ENV_WORKFLOW: {"on": "push", "jobs": {"other": job}}})
    assert workflow_problems({"other.yml": {"on": "push", "jobs": {"j": {"environment": {"name": _ENV}}}}})
    assert workflow_problems({"other.yml": {"on": "push", "jobs": {"j": {"environment": "${{ inputs.env }}"}}}})
    assume = {
        "steps": [
            {
                "uses": "aws-actions/configure-aws-credentials@v4",
                "with": {"role-to-assume": f"arn:aws:iam::123:role/{_ROLE_NAME}"},  # pragma: allowlist secret
            }
        ]
    }
    assert workflow_problems({"other.yml": {"on": "push", "jobs": {"j": assume}}})
    assert workflow_problems({_ENV_WORKFLOW: {"on": "push", "jobs": {"other": assume}}})
    assert workflow_problems({_ENV_WORKFLOW: {"on": "push", "jobs": {_ENV_JOB: {**job, **assume}}}}) == []
