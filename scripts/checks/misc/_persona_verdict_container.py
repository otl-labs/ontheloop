"""The container-job class of the verdict egress rules (PLAN-persona-verdict-container-egress; T4.32 c7 container leg).

A covered job is in the class when its floor step runs egress-floor-container. Until rec-5550 amends this rule, a class
job must be in the egress probe workflow and hold no credential. Called by validate_persona_verdict_egress and
_persona_verdict_floor; reads parsed YAML only and imports nothing from scripts/personas/, _persona_verdict_floor.py or
validate_persona_verdict_egress.py.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

PROBE = ".github/workflows/persona-verdict-egress-probe.yml"
PROBE_PREFIX = "python3 -I scripts/personas/run.py "
RUN_PREFIX = 'python3 -I "$GITHUB_WORKSPACE/main/scripts/personas/run.py" '
CONTAINER_VERB = "egress-floor-container"
BLOB_HOST = "pkg-containers.githubusercontent.com:443"
LAUNCHER = "/usr/bin/python3 -I -S /usr/local/lib/persona-egress/egress_launcher.py probe"
LAUNCHER_LINE = PROBE_PREFIX + "egress-container-request | sudo " + LAUNCHER
LAUNCHER_KEYS = {"name", "id", "run"}
SELF_CHECK = PROBE_PREFIX + "egress-self-check"
INFERENCE_PROVIDER = "docs/contracts/inference-provider.yaml"
SECRET_REF = re.compile(r"(?<![\w.])secrets\b(?!\.GITHUB_TOKEN\b)", re.I)
EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}", re.S)


def _runs(job: dict[str, Any]) -> list[str]:
    return [str(s.get("run", "")).strip() for s in job.get("steps") or [] if isinstance(s, dict)]


def is_container_job(job: dict[str, Any]) -> bool:
    floors = {f"sudo {prefix}{CONTAINER_VERB}" for prefix in (PROBE_PREFIX, RUN_PREFIX)}
    return any(run in floors for run in _runs(job))


def is_launcher_line(run: str) -> bool:
    return run.strip() == LAUNCHER_LINE


def primary_host(root: Path, data: dict[str, Any]) -> tuple[str | None, list[str]]:
    try:
        doc = yaml.safe_load((root / INFERENCE_PROVIDER).read_text(encoding="utf-8"))
        tiers = doc["tier_model"]["tiers"]
        models = doc["model_id_formats"]["litellm_tier_models"]
    except (OSError, yaml.YAMLError, KeyError, TypeError) as exc:
        return None, [f"{INFERENCE_PROVIDER} does not give the tier models: {type(exc).__name__}"]
    for tier in tiers:
        if isinstance(tier, dict) and tier.get("role") == "primary":
            name = str(tier.get("selection_ref", "")).rsplit(".", 1)[-1]
            default = str((models.get(name) or {}).get("default_model", ""))
            prefix = default.split("/", 1)[0] + "/" if "/" in default else ""
            host = (data.get("provider_hosts") or {}).get(prefix)
            if host is None:
                return None, [
                    f"config/persona_verdict_egress.yaml: provider_hosts has no entry for the primary prefix {prefix!r}"
                ]
            return str(host), []
    return None, [f"{INFERENCE_PROVIDER}: no primary-role tier row"]


def _id_token_write(perms: Any) -> bool:
    if perms == "write-all":
        return True
    return isinstance(perms, dict) and str(perms.get("id-token")).lower() == "write"


def _has_secret(node: Any) -> bool:
    text = json.dumps(node, default=str)
    return any(SECRET_REF.search(body) for body in EXPRESSION.findall(text))


def _sudo_runs(run: str) -> bool:
    return any(token.rsplit("/", 1)[-1] == "sudo" for token in run.replace("|", " ").replace(";", " ").split())


def class_problems(
    where: str, rel: str, workflow: dict[str, Any], job: dict[str, Any], row: list[Any], primary: str | None
) -> list[str]:
    out: list[str] = []
    if rel != PROBE:
        out.append(f"{where}: a container job must be in {PROBE}")
    perms = job["permissions"] if "permissions" in job else workflow.get("permissions")
    if _id_token_write(perms):
        out.append(f"{where}: a container job holds id-token: write")
    if _has_secret(job) or _has_secret({"env": workflow.get("env")}) or str(job.get("secrets")) == "inherit":
        out.append(f"{where}: a container job references a secret other than GITHUB_TOKEN or inherits secrets")
    if "environment" in job:
        out.append(f"{where}: a container job has an environment:")
    steps = [s for s in job.get("steps") or [] if isinstance(s, dict)]
    floor = f"sudo {PROBE_PREFIX}{CONTAINER_VERB}"
    launchers = [i for i, s in enumerate(steps) if is_launcher_line(str(s.get("run", "")))]
    for step in steps:
        run = str(step.get("run", "")).strip()
        if _sudo_runs(run) and run != floor and not is_launcher_line(run):
            out.append(f"{where}: a container job's only sudo lines are the floor step and the launcher step")
    if len(launchers) != 1:
        out.append(f"{where}: a container job needs exactly one launcher step running exactly: {LAUNCHER_LINE}")
    else:
        step = steps[launchers[0]]
        if set(step) != LAUNCHER_KEYS:
            out.append(f"{where}: the launcher step's keys must be exactly name, id and run")
        first_check = next((i for i, s in enumerate(steps) if str(s.get("run", "")).strip() == SELF_CHECK), None)
        if first_check is None or launchers[0] < first_check:
            out.append(f"{where}: the launcher step comes before the first self-check")
    if not steps or str(steps[-1].get("run", "")).strip() != SELF_CHECK:
        out.append(f"{where}: the last step of a container job must be the final self-check")
    if primary is not None and sorted(map(str, row)) != sorted([primary, BLOB_HOST]):
        out.append(f"{where}: a container job's row must be exactly the routed provider host {primary} and {BLOB_HOST}")
    return out
