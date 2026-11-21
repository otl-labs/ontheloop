"""The keyed-job pins validate_persona_verdict_chain calls (PLAN-persona-verdict-first-key-read).

keyprep (keyless), smoke (persona-verdict Environment) and refusal (no Environment) exist together or not at all. Each carries
exactly its pinned condition, needs, permissions and steps; the only secret is AWS_ACCOUNT_ID, as the env key of an
inference-smoke or key-refusal step after the self-check; packages reach the keyed jobs only as the hash-locked wheelhouse.
Imports nothing from scripts/personas/; the docker rule is validate_persona_verdict_egress's command-token rule, imported.
"""

from __future__ import annotations

import json
import re
from typing import Any

from scripts.checks.misc.validate_persona_verdict_egress import DOCKER_TOKEN, _logical_lines

VERDICT = ".github/workflows/persona-verdict.yml"
KEYED_JOBS = ("keyprep", "smoke", "refusal")
TIER_ROLES = ("primary", "warm_fetched_escape_hatch")
KEYED_IF = (
    "!cancelled() && needs.prepare.result == 'success' && github.event.workflow_run.event == 'pull_request' && "
    "needs.prepare.outputs.allowlisted == 'true' && needs.prepare.outputs.fixture == 'true'"
)
KEYPREP_OK = "needs.keyprep.result == 'success'"
CONDITIONS = {"keyprep": KEYED_IF, "smoke": f"{KEYED_IF} && {KEYPREP_OK}", "refusal": f"{KEYED_IF} && {KEYPREP_OK}"}
NEEDS = {"keyprep": ["prepare"], "smoke": ["prepare", "keyprep"], "refusal": ["prepare", "keyprep"]}
PERMISSIONS = {
    "keyprep": {"contents": "read"},
    "smoke": {"contents": "read", "id-token": "write"},
    "refusal": {"contents": "read", "id-token": "write"},
}
RUN_PREFIX = 'python3 -I "$GITHUB_WORKSPACE/main/scripts/personas/run.py" '
SELF_CHECK = RUN_PREFIX + "egress-self-check"
PIP_DOWNLOAD = (
    "pip download --require-hashes --no-deps --only-binary :all: -r docker/persona/requirements-persona.txt "
    '-d "$RUNNER_TEMP/work/wheels"'
)
PIP_INSTALL = (
    'pip install --no-index --find-links "$RUNNER_TEMP/work/wheels" --require-hashes --no-deps --only-binary :all: '
    '-r "$GITHUB_WORKSPACE/main/docker/persona/requirements-persona.txt"'
)
REGION_ACTION = "./.github/actions/aws-region"
REGION_ENV = "${{ needs.keyprep.outputs.aws_region }}"
ACCOUNT_ENV = "${{ secrets.AWS_ACCOUNT_ID }}"
WHEELS = "persona-verdict-wheels"
SECRETS = re.compile(r"secrets\s*(?:\.|\[)", re.IGNORECASE)


def _unwrap(expr: Any) -> str:
    text = " ".join(str(expr).split())
    return text[3:-2].strip() if text.startswith("${{") and text.endswith("}}") else text


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    steps = job.get("steps")
    return [s for s in steps if isinstance(s, dict)] if isinstance(steps, list) else []


def _map(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _run(step: dict[str, Any]) -> str:
    return str(step.get("run", "")).strip()


def _verb(step: dict[str, Any]) -> str | None:
    run = _run(step)
    return run[len(RUN_PREFIX) :] if run.startswith(RUN_PREFIX) else None


def _job_shape(name: str, job: dict[str, Any]) -> list[str]:
    out: list[str] = []
    w = f"{VERDICT}: keyed job {name}"
    if _unwrap(job.get("if", "")) != CONDITIONS[name]:
        out.append(f"{w} condition is not {CONDITIONS[name]}")
    if job.get("needs") not in (NEEDS[name], NEEDS[name][0] if len(NEEDS[name]) == 1 else None):
        out.append(f"{w} needs are not exactly {NEEDS[name]}")
    if job.get("permissions") != PERMISSIONS[name]:
        out.append(f"{w} permissions are not exactly {PERMISSIONS[name]}")
    if name == "smoke" and job.get("environment") != "persona-verdict":
        out.append(f"{w} does not name exactly the persona-verdict Environment")
    if name != "smoke" and "environment" in job:
        out.append(f"{w} names an Environment")
    for key in ("continue-on-error", "container", "services", "uses"):
        if key in job:
            out.append(f"{w} sets {key}")
    return out


def _step_rules(name: str, job: dict[str, Any]) -> list[str]:
    out: list[str] = []
    w = f"{VERDICT}: keyed job {name}"
    steps = _steps(job)
    for step in steps:
        uses = str(step.get("uses", ""))
        if "continue-on-error" in step:
            out.append(f"{w} has a step with continue-on-error")
        if "configure-aws-credentials" in uses:
            out.append(f"{w} has a configure-aws-credentials step")
        if uses.startswith("./") and not (name == "keyprep" and uses == REGION_ACTION):
            out.append(f"{w} uses the local action {uses}")
        if "run" in step and not step.get("id"):
            out.append(f"{w} has a run: step without an id")
        if any(DOCKER_TOKEN.search(line) for line in _logical_lines(str(step.get("run", "")))):
            out.append(f"{w} runs a docker command")
    return out


def _pip_rules(name: str, job: dict[str, Any]) -> list[str]:
    pips = [_run(s) for s in _steps(job) if "pip " in str(s.get("run", ""))]
    want = {"keyprep": [PIP_DOWNLOAD], "smoke": [PIP_INSTALL], "refusal": []}[name]
    if pips != want:
        return [f"{VERDICT}: keyed job {name} pip lines are not exactly {want}"]
    return []


def _artifact_rules(name: str, job: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for step in _steps(job):
        uses = str(step.get("uses", "")).split("@", 1)[0]
        artifact = _map(step.get("with")).get("name")
        if uses == "actions/upload-artifact" and (name != "keyprep" or artifact != WHEELS):
            out.append(f"{VERDICT}: keyed job {name} uploads {artifact!r}")
        if uses == "actions/download-artifact" and (name != "smoke" or artifact != WHEELS):
            out.append(f"{VERDICT}: keyed job {name} downloads {artifact!r}")
    return out


def _run_py_rules(name: str, job: dict[str, Any]) -> list[str]:
    out: list[str] = []
    w = f"{VERDICT}: keyed job {name}"
    steps = _steps(job)
    run_py = [s for s in steps if _verb(s) is not None]
    if name == "keyprep":
        if run_py or any("python" in str(s.get("run", "")) for s in steps):
            out.append(f"{w} runs python or a run.py verb")
        for step in steps:
            if str(step.get("uses", "")).startswith("actions/checkout@"):
                with_ = _map(step.get("with"))
                if set(with_) != {"persist-credentials"} or str(with_.get("persist-credentials")).lower() != "false":
                    out.append(f"{w} checkout is not exactly persist-credentials: false (no ref, no path)")
        return out
    want = ["egress-self-check"] + (["inference-smoke"] * len(TIER_ROLES) if name == "smoke" else ["key-refusal"])
    if [_verb(s) for s in run_py] != want:
        out.append(f"{w} run.py steps are not exactly {want}")
        return out
    for step in run_py[1:]:
        env = _map(step.get("env"))
        if env.get("PERSONA_AWS_REGION") != REGION_ENV:
            out.append(f"{w} PERSONA_AWS_REGION is not exactly {REGION_ENV}")
        if env.get("AWS_ACCOUNT_ID") != ACCOUNT_ENV:
            out.append(f"{w} AWS_ACCOUNT_ID is not exactly {ACCOUNT_ENV}")
    if name == "smoke":
        roles = tuple(_map(s.get("env")).get("PERSONA_SMOKE_ROLE") for s in run_py[1:])
        if roles != TIER_ROLES:
            out.append(f"{w} PERSONA_SMOKE_ROLE values are not exactly {TIER_ROLES}")
    return out


def _secret_rules(jobs: dict[str, Any], doc_text: str) -> list[str]:
    """Every secrets reference in the file is AWS_ACCOUNT_ID as the env key of a key-reading step after the self-check."""
    allowed = 0
    for name in ("smoke", "refusal"):
        steps = _steps(_map(jobs.get(name)))
        check = next((i for i, s in enumerate(steps) if _run(s) == SELF_CHECK), None)
        for i, step in enumerate(steps):
            if _verb(step) in ("inference-smoke", "key-refusal") and check is not None and i > check:
                allowed += json.dumps(step, default=str).count("secrets.AWS_ACCOUNT_ID")
    total = len(re.findall(r"secrets\.AWS_ACCOUNT_ID", doc_text))
    out = (
        []
        if total == allowed
        else [f"{VERDICT}: secrets.AWS_ACCOUNT_ID appears outside a keyed key-reading step after the self-check"]
    )
    for name, job in jobs.items():
        blob = json.dumps({k: v for k, v in _map(job).items() if k != "steps"}, default=str)
        if SECRETS.search(blob):
            out.append(f"{VERDICT}: job {name} holds a secrets reference outside its steps")
    return out


def keyed_problems(doc: dict[str, Any]) -> list[str]:
    jobs = _map(doc.get("jobs"))
    present = [n for n in KEYED_JOBS if n in jobs]
    if not present:
        return []
    if len(present) != len(KEYED_JOBS):
        return [f"{VERDICT}: keyprep, smoke and refusal exist together or not at all (found {present})"]
    out: list[str] = []
    for name in KEYED_JOBS:
        job = _map(jobs[name])
        out.extend(_job_shape(name, job) + _step_rules(name, job) + _pip_rules(name, job))
        out.extend(_artifact_rules(name, job) + _run_py_rules(name, job))
    out.extend(_secret_rules(jobs, json.dumps(doc, default=str)))
    return out
