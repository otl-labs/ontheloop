"""The container-job class of the verdict egress rules (PLAN-persona-verdict-container-egress; T4.32 c7 container leg).

A covered job is in the class when its floor step runs egress-floor-container. A class job is in the egress probe workflow
and holds no credential, except persona-verdict.yml's run job (PLAN-persona-verdict-fixture-run; T4.32 c7 ORDER), which holds
id-token: write, the persona-verdict Environment and secrets.AWS_ACCOUNT_ID in its persona step only. Called by
validate_persona_verdict_egress and _persona_verdict_floor; reads parsed YAML only and imports nothing from
scripts/personas/, _persona_verdict_floor.py or validate_persona_verdict_egress.py.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

PROBE = ".github/workflows/persona-verdict-egress-probe.yml"
VERDICT = ".github/workflows/persona-verdict.yml"
RUN_ROLES = {f"{VERDICT}::run": "primary", f"{VERDICT}::run-fallback": "warm_fetched_escape_hatch"}
PROBE_PREFIX = "python3 -I scripts/personas/run.py "
RUN_PREFIX = 'python3 -I "$GITHUB_WORKSPACE/main/scripts/personas/run.py" '
CONTAINER_VERB = "egress-floor-container"
BLOB_HOST = "pkg-containers.githubusercontent.com:443"
LAUNCHER = "/usr/bin/python3 -I -S /usr/local/lib/persona-egress/egress_launcher.py probe"
LAUNCHER_LINE = PROBE_PREFIX + "egress-container-request | sudo " + LAUNCHER
LAUNCHER_RUN = LAUNCHER.removesuffix("probe") + "run"
RUN_PROBE_LINE = RUN_PREFIX + "egress-container-request | sudo " + LAUNCHER
RUN_PERSONA_LINE = RUN_PREFIX + "persona-request | sudo " + LAUNCHER_RUN + " | " + RUN_PREFIX + "persona-record"
LAUNCHER_KEYS = {"name", "id", "run"}
PERSONA_KEYS = {"name", "id", "run", "env", "working-directory"}
PERSONA_ENV = {"AWS_ACCOUNT_ID", "PERSONA_AWS_REGION", "PERSONA_SNAPSHOT", "PERSONA_ROLE"}
SELF_CHECK = PROBE_PREFIX + "egress-self-check"
RUN_SELF_CHECK = RUN_PREFIX + "egress-self-check"
RUN_FLOOR = f"sudo {RUN_PREFIX}{CONTAINER_VERB}"
AWS_HOSTS = ("sts.{aws_region}.amazonaws.com:443", "ssm.{aws_region}.amazonaws.com:443")
INFERENCE_PROVIDER = "docs/contracts/inference-provider.yaml"
LIST_FILE = "config/persona_verdict_egress.yaml"
SECRET_REF = re.compile(r"(?<![\w.])secrets\b(?!\.GITHUB_TOKEN\b)", re.I)
SECRET_NAME = re.compile(r"secrets\s*\.\s*([A-Za-z0-9_]+)", re.I)
EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}", re.S)


def _runs(job: dict[str, Any]) -> list[str]:
    return [str(s.get("run", "")).strip() for s in job.get("steps") or [] if isinstance(s, dict)]


def is_container_job(job: dict[str, Any]) -> bool:
    floors = {f"sudo {prefix}{CONTAINER_VERB}" for prefix in (PROBE_PREFIX, RUN_PREFIX)}
    return any(run in floors for run in _runs(job))


def is_launcher_line(run: str) -> bool:
    return run.strip() in (LAUNCHER_LINE, RUN_PROBE_LINE, RUN_PERSONA_LINE)


def role_hosts(root: Path, data: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    """Role -> provider host for each committed tier, read from inference-provider.yaml; no two roles share a host."""
    try:
        doc = yaml.safe_load((root / INFERENCE_PROVIDER).read_text(encoding="utf-8"))
        tiers = doc["tier_model"]["tiers"]
        models = doc["model_id_formats"]["litellm_tier_models"]
    except (OSError, yaml.YAMLError, KeyError, TypeError) as exc:
        return {}, [f"{INFERENCE_PROVIDER} does not give the tier models: {type(exc).__name__}"]
    hosts: dict[str, str] = {}
    out: list[str] = []
    for tier in tiers:
        if not isinstance(tier, dict) or str(tier.get("state", "")).startswith("Deferred"):
            continue
        name = str(tier.get("selection_ref", "")).rsplit(".", 1)[-1]
        default = str((models.get(name) or {}).get("default_model", ""))
        prefix = default.split("/", 1)[0] + "/" if "/" in default else ""
        host = (data.get("provider_hosts") or {}).get(prefix)
        if host is None:
            out.append(f"{LIST_FILE}: provider_hosts has no entry for the LiteLLM prefix {prefix!r} of tier row {name}")
        else:
            hosts[str(tier.get("role"))] = str(host)
    if "primary" not in hosts and not any(tier.get("role") == "primary" for tier in tiers if isinstance(tier, dict)):
        out.insert(0, f"{INFERENCE_PROVIDER}: no primary-role tier row")
    if len(set(hosts.values())) != len(hosts):
        out.append(f"{INFERENCE_PROVIDER}: two tier roles share a provider host")
    return hosts, out


def job_role(where: str) -> str:
    """The role a class job runs, from its name: run-fallback the escape hatch, every other job the primary."""
    return RUN_ROLES.get(where, "primary")


def _id_token_write(perms: Any) -> bool:
    if perms == "write-all":
        return True
    return isinstance(perms, dict) and str(perms.get("id-token")).lower() == "write"


def _has_secret(node: Any) -> bool:
    text = json.dumps(node, default=str)
    return any(SECRET_REF.search(body) for body in EXPRESSION.findall(text))


def _sudo_runs(run: str) -> bool:
    return any(token.rsplit("/", 1)[-1] == "sudo" for token in run.replace("|", " ").replace(";", " ").split())


def _persona_secret_ok(steps: list[dict[str, Any]], persona: int) -> list[str]:
    rest = [s for i, s in enumerate(steps) if i != persona]
    out = []
    if _has_secret(rest):
        out.append("a secret is referenced outside the persona step")
    env = steps[persona].get("env") if isinstance(steps[persona].get("env"), dict) else {}
    bodies = EXPRESSION.findall(json.dumps(env, default=str))
    refs = [m for body in bodies for m in SECRET_REF.findall(body)]
    names = [m for body in bodies for m in SECRET_NAME.findall(body)]
    if len(refs) != len(names) or set(names) != {"AWS_ACCOUNT_ID"}:
        out.append("the persona step must reference exactly secrets.AWS_ACCOUNT_ID")
    return out


def run_job_problems(
    where: str, workflow: dict[str, Any], job: dict[str, Any], row: list[Any], hosts: dict[str, str]
) -> list[str]:
    """The run job's class rules: credentials only in the persona step, two probes around it, the AWS hosts in its row."""
    out: list[str] = []
    if job.get("permissions") != {"contents": "read", "id-token": "write"}:
        out.append(f"{where}: the run job's permissions must be exactly contents: read and id-token: write")
    if job.get("environment") != "persona-verdict":
        out.append(f"{where}: the run job must name exactly the persona-verdict Environment")
    if str(job.get("secrets")) == "inherit" or _has_secret({"env": workflow.get("env")}):
        out.append(f"{where}: the run job inherits secrets or the workflow env references one")
    steps = [s for s in job.get("steps") or [] if isinstance(s, dict)]
    runs = [str(s.get("run", "")).strip() for s in steps]
    probes = [i for i, r in enumerate(runs) if r == RUN_PROBE_LINE]
    persona = [i for i, r in enumerate(runs) if r == RUN_PERSONA_LINE]
    for run in runs:
        if _sudo_runs(run) and run not in (RUN_FLOOR, RUN_PROBE_LINE, RUN_PERSONA_LINE):
            out.append(f"{where}: the run job's only sudo lines are the floor step, the two probes and the persona step")
    if runs.count(RUN_FLOOR) != 1 or len(probes) != 2 or len(persona) != 1:
        out.append(f"{where}: the run job needs the floor step, exactly two probe steps and one persona step")
        return out
    if any(set(steps[i]) != LAUNCHER_KEYS for i in probes):
        out.append(f"{where}: a probe step's keys must be exactly name, id and run")
    if set(steps[persona[0]]) != PERSONA_KEYS or set(steps[persona[0]].get("env") or {}) != PERSONA_ENV:
        out.append(
            f"{where}: the persona step's keys must be exactly {sorted(PERSONA_KEYS)} with env exactly {sorted(PERSONA_ENV)}"
        )
    role = job_role(where)
    if (steps[persona[0]].get("env") or {}).get("PERSONA_ROLE") != role:
        out.append(f"{where}: the persona step's PERSONA_ROLE must be exactly {role}")
    out.extend(f"{where}: {m}" for m in _persona_secret_ok(steps, persona[0]))
    return out + _run_order_problems(where, steps, runs, probes, persona[0], row, hosts.get(role))


def _run_order_problems(
    where: str,
    steps: list[dict[str, Any]],
    runs: list[str],
    probes: list[int],
    persona: int,
    row: list[Any],
    host: str | None,
) -> list[str]:
    out: list[str] = []
    first_check = next((i for i, r in enumerate(runs) if r == RUN_SELF_CHECK), None)
    if first_check is None or probes[0] < first_check or persona < probes[0]:
        out.append(f"{where}: the first probe step must follow the first self-check and precede the persona step")
    if probes[1] < persona or probes[1] != len(steps) - 2:
        out.append(f"{where}: the second probe must be the step before the final self-check and follow the persona step")
    if not steps or runs[-1] != RUN_SELF_CHECK:
        out.append(f"{where}: the last step of a container job must be the final self-check")
    want = sorted([*(AWS_HOSTS), BLOB_HOST, *([host] if host else [])])
    if host is not None and sorted(map(str, row)) != want:
        out.append(
            f"{where}: the run job's row must be exactly the role's provider host {host}, {BLOB_HOST} and the AWS hosts"
        )
    return out


def class_problems(
    where: str, rel: str, workflow: dict[str, Any], job: dict[str, Any], row: list[Any], hosts: dict[str, str]
) -> list[str]:
    if where in RUN_ROLES and rel == VERDICT:
        return run_job_problems(where, workflow, job, row, hosts)
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
    if len(launchers) != 1 or str(steps[launchers[0]].get("run", "")).strip() != LAUNCHER_LINE:
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
    host = hosts.get(job_role(where))
    if host is not None and sorted(map(str, row)) != sorted([host, BLOB_HOST]):
        out.append(f"{where}: a container job's row must be exactly the role's provider host {host} and {BLOB_HOST}")
    return out
