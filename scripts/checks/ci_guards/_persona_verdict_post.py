"""The post-job pins validate_persona_verdict_chain calls (PLAN-persona-verdict-app-post; Decision 271 cl.4, Decision 273).

post is the one job that holds the verdict App token: in the persona-verdict-app Environment, behind the block-mode filter,
it builds every check body first and then mints, posts and revokes. It carries exactly its pinned condition, needs,
permissions and step sequence; packages reach it only as the hash-locked wheelhouse; the floor, switch and final-check rules
stay in _persona_verdict_floor (applied to every covered job), so this helper pins only their places in post's sequence.
Across .github/workflows/ no other job names the App Environment and no other file holds the App role name; that fence is
advisory in --pre (the chain check's pre_globs match only persona-verdict*.yml) and binding in the full tier.
Imports nothing from scripts/personas/.
"""

from __future__ import annotations

import json
from typing import Any

from scripts.checks.ci_guards._persona_verdict_keyed import (
    ACCOUNT_ENV,
    FLOOR,
    PIP_INSTALL,
    REGION_ENV,
    RUN_PREFIX,
    SELF_CHECK,
    VERDICT,
    WHEELS,
    _map,
    _run,
    _steps,
    _unwrap,
    _verb,
)
from scripts.checks.misc.validate_persona_verdict_egress import DOCKER_TOKEN, _logical_lines

ENVIRONMENT = "persona-verdict-app"
APP_ROLE = "agent-platform-github-ci-persona-verdict-app"
W = f"{VERDICT}: post job"
CONDITION = "!cancelled()"
NEEDS = ["prepare", "run", "keyprep"]
PERMISSIONS = {"checks": "read", "contents": "read", "actions": "read", "id-token": "write"}
VERBS = ["egress-floor", "egress-self-check", "post", "egress-self-check"]
SEQUENCE = (
    "filter",
    "checkout",
    "floor",
    "mkdir",
    "self-check",
    "setup-python",
    "wheels",
    "install",
    "prepared",
    "results",
    "post",
    "final-check",
)
ARTIFACTS = {"persona-verdict-prepared", "persona-verdict-results", WHEELS}
OPTIONAL = {"persona-verdict-prepared", "persona-verdict-results"}
POST_ENV = {"GH_TOKEN": "${{ github.token }}", "AWS_ACCOUNT_ID": ACCOUNT_ENV, "PERSONA_AWS_REGION": REGION_ENV}
MKDIR = 'mkdir -p "$RUNNER_TEMP/work"'


def _kind(step: dict[str, Any]) -> str:
    uses, run = str(step.get("uses", "")), _run(step)
    if uses.startswith("step-security/harden-runner@"):
        return "filter"
    if uses.startswith("actions/checkout@"):
        return "checkout"
    if uses.startswith("actions/setup-python@"):
        return "setup-python"
    if uses.startswith("actions/download-artifact@"):
        name = _map(step.get("with")).get("name")
        return {WHEELS: "wheels", "persona-verdict-prepared": "prepared", "persona-verdict-results": "results"}.get(
            str(name), "download?"
        )
    if run == FLOOR:
        return "floor"
    if run == MKDIR:
        return "mkdir"
    if run == SELF_CHECK:
        return "final-check" if str(step.get("if", "")).strip() == "always()" else "self-check"
    if run == PIP_INSTALL:
        return "install"
    if run == RUN_PREFIX + "post":
        return "post"
    return "other"


def _job_problems(job: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if _unwrap(job.get("if", "")) != CONDITION:
        out.append(f"{W} condition is not {CONDITION}")
    if job.get("needs") != NEEDS:
        out.append(f"{W} needs are not exactly {NEEDS}")
    if job.get("environment") != ENVIRONMENT:
        out.append(f"{W} does not name exactly the {ENVIRONMENT} Environment")
    if job.get("permissions") != PERMISSIONS:
        out.append(f"{W} permissions are not exactly {PERMISSIONS}")
    for key in ("continue-on-error", "container", "services", "uses"):
        if key in job:
            out.append(f"{W} sets {key}")
    return out


def _step_problems(job: dict[str, Any]) -> list[str]:
    out: list[str] = []
    steps = _steps(job)
    kinds = [_kind(s) for s in steps]
    if tuple(kinds) != SEQUENCE:
        out.append(f"{W} steps are not exactly {list(SEQUENCE)} (found {kinds})")
    for step in steps:
        uses = str(step.get("uses", ""))
        if "configure-aws-credentials" in uses:
            out.append(f"{W} has a configure-aws-credentials step")
        if uses.startswith("./"):
            out.append(f"{W} uses the local action {uses}")
        if "run" in step and not step.get("id"):
            out.append(f"{W} has a run: step without an id")
        if any(DOCKER_TOKEN.search(line) for line in _logical_lines(str(step.get("run", "")))):
            out.append(f"{W} runs a docker command")
        action, artifact = uses.split("@", 1)[0], _map(step.get("with")).get("name")
        if action == "actions/upload-artifact":
            out.append(f"{W} uploads {artifact!r}")
        if action == "actions/download-artifact" and artifact not in ARTIFACTS:
            out.append(f"{W} downloads {artifact!r}")
        coe = "continue-on-error" in step
        if coe and not (action == "actions/download-artifact" and artifact in OPTIONAL and step["continue-on-error"] is True):
            out.append(f"{W} has a step with continue-on-error other than the prepared and results downloads")
        if not coe and artifact in OPTIONAL:
            out.append(f"{W} download of {artifact} lacks continue-on-error: true")
    pips = [_run(s) for s in steps if "pip " in str(s.get("run", ""))]
    if pips != [PIP_INSTALL]:
        out.append(f"{W} pip lines are not exactly the keyed offline install")
    verbs = [_verb(s) for s in steps if _verb(s) is not None]
    if verbs != VERBS:
        out.append(f"{W} run.py verbs are not exactly {VERBS}")
    if "post" not in kinds:
        return out
    env = _map(steps[kinds.index("post")].get("env"))
    if set(env) != set(POST_ENV) | {"PERSONA_SNAPSHOT"} or any(env.get(k) != v for k, v in POST_ENV.items()):
        out.append(
            f"{W} post step env is not exactly GH_TOKEN, AWS_ACCOUNT_ID, PERSONA_AWS_REGION and PERSONA_SNAPSHOT as pinned"
        )
    return out


def _fence_problems(workflows: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for rel, doc in sorted(workflows.items()):
        for name, job in _map(_map(doc).get("jobs")).items():
            env = _map(job).get("environment")
            env_name = env.get("name") if isinstance(env, dict) else env
            if env_name == ENVIRONMENT and not (rel == "persona-verdict.yml" and name == "post"):
                out.append(f".github/workflows/{rel}: job {name} names the {ENVIRONMENT} Environment")
        if rel != "persona-verdict.yml" and APP_ROLE in json.dumps(doc, default=str):
            out.append(f".github/workflows/{rel}: holds the verdict App role name")
    return out


def post_problems(doc: dict[str, Any], workflows: dict[str, Any] | None = None) -> list[str]:
    post = _map(_map(doc.get("jobs")).get("post"))
    if not post:
        return []
    return _job_problems(post) + _step_problems(post) + _fence_problems(workflows or {})
