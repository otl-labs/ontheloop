"""Predicates (j) and (k) of the tf-trusted-plan fence (PLAN-trusted-plan-github-root PR 4, rec-4638).

Pure functions over parsed workflow mappings: no file reads. Each returns failure lines naming the file, job and rule only.

  (j) role partition: terraform-github-plan.yml assumes only the trusted github plan role; terraform-plan-trusted.yml
      never assumes it; no other workflow assumes either trusted role. A role is "named" by the role-to-assume value of a
      configure-aws-credentials step, never by raw file text.
  (k) a job that reads TF_GITHUB_PLAN_APP_PRIVATE_KEY (any secrets reference in the job mapping, or in the workflow-level
      env that every job inherits) declares the tf-trusted-plan environment.
"""

from __future__ import annotations

import json
import re
from typing import Any

ENV = "tf-trusted-plan"
GITHUB_ROLE = "agent-platform-github-ci-trusted-github-plan"
SHARED_ROLE = "agent-platform-github-ci-trusted-plan"
GITHUB_WORKFLOW = "terraform-github-plan.yml"
PERSONAL_WORKFLOW = "terraform-plan-trusted.yml"
_KEY_RE = re.compile(r"secrets\.TF_GITHUB_PLAN_APP_PRIVATE_KEY\b|secrets\[\s*['\"]TF_GITHUB_PLAN_APP_PRIVATE_KEY['\"]\s*\]")


def _jobs(wf: dict[str, Any]) -> dict[str, dict[str, Any]]:
    jobs = wf.get("jobs")
    return {k: v for k, v in jobs.items() if isinstance(v, dict)} if isinstance(jobs, dict) else {}


def _roles(job: dict[str, Any]) -> list[str]:
    steps = job.get("steps")
    found: list[str] = []
    for step in steps if isinstance(steps, list) else []:
        if not isinstance(step, dict) or not str(step.get("uses", "")).startswith("aws-actions/configure-aws-credentials"):
            continue
        with_ = step.get("with")
        value = with_.get("role-to-assume") if isinstance(with_, dict) else None
        if isinstance(value, str):
            found.append(value.rsplit("/", 1)[-1].strip())
    return found


def check_role_partition(workflows: dict[str, dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for name, wf in workflows.items():
        for jid, job in _jobs(wf).items():
            for role in _roles(job):
                if name == GITHUB_WORKFLOW and role != GITHUB_ROLE:
                    out.append(f"j: {name}:{jid} assumes a role other than the trusted github plan role")
                elif name == PERSONAL_WORKFLOW and role == GITHUB_ROLE:
                    out.append(f"j: {name}:{jid} assumes the trusted github plan role")
                elif name not in (GITHUB_WORKFLOW, PERSONAL_WORKFLOW) and role in (GITHUB_ROLE, SHARED_ROLE):
                    out.append(f"j: {name}:{jid} assumes a trusted plan role")
    return out


def _env_name(job: dict[str, Any]) -> str | None:
    env = job.get("environment")
    if isinstance(env, dict):
        env = env.get("name")
    return str(env) if env is not None else None


def check_key_location(workflows: dict[str, dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for name, wf in workflows.items():
        inherited = bool(_KEY_RE.search(json.dumps(wf.get("env", {}), default=str)))
        for jid, job in _jobs(wf).items():
            if (inherited or _KEY_RE.search(json.dumps(job, default=str))) and _env_name(job) != ENV:
                out.append(f"k: {name}:{jid} reads the plan App key without environment {ENV}")
    return out
