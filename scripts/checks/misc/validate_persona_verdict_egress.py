"""Static check for the verdict shell's egress filter (PLAN-persona-verdict-shell-deny; T4.32 c7 shell leg).

A job in .github/workflows/persona-verdict*.yml is covered when it holds id-token: write, references a secret other than
its GITHUB_TOKEN, inherits the caller's secrets, or has a row in config/persona_verdict_egress.yaml. Every covered job
starts with the SHA-pinned block-mode filter whose list equals its row, then a behavioural self-check before any step that
can reach a credential. Reads YAML and text only; imports nothing from scripts/personas/.
"""

from __future__ import annotations

import ipaddress
import json
import re
from pathlib import Path
from typing import Any

import yaml

from scripts.checks import _common, registry

LIST_FILE = "config/persona_verdict_egress.yaml"
WORKFLOW_GLOB = ".github/workflows/persona-verdict*.yml"
PROBE = ".github/workflows/persona-verdict-egress-probe.yml"
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
ENDPOINT = re.compile(r"^[A-Za-z0-9*.-]+:\d+$")
EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}", re.S)
SECRET_REF = re.compile(r"(?<![\w.])secrets\b(?!\.GITHUB_TOKEN\b)", re.I)
CHECKOUT = re.compile(r"^actions/checkout@[0-9a-f]{40}$")
MKDIR = 'mkdir -p "$RUNNER_TEMP/work"'
RUN_PREFIX = 'python3 -I "$GITHUB_WORKSPACE/main/scripts/personas/run.py" '
PROBE_PREFIX = "python3 -I scripts/personas/run.py "
FILTER_WITH = {"egress-policy", "allowed-endpoints", "disable-telemetry", "disable-sudo-and-containers"}
PROBE_TRIGGER = {
    "pull_request": {
        "paths": [
            "tests/fixtures/persona_verdict/fixture_pr/**",
            "config/persona_verdict_egress.yaml",
            "scripts/personas/egress_*.py",
            "scripts/personas/run.py",
            ".github/workflows/persona-verdict-egress-probe.yml",
        ]
    }
}
PROBE_STEPS = {
    "egress-probe": [("probe-egress", PROBE_PREFIX + "egress-probe")],
    "egress-control": [
        ("control-install", "pip install pyyaml -c requirements.txt"),
        ("control-egress", PROBE_PREFIX + "egress-control"),
    ],
}


def _flag(value: Any, want: str) -> bool:
    return str(value).lower() == want


def _has_secret(node: Any) -> bool:
    text = json.dumps(node, default=str)
    return any(SECRET_REF.search(body) for body in EXPRESSION.findall(text))


def _id_token_write(perms: Any) -> bool:
    if perms == "write-all":
        return True
    return isinstance(perms, dict) and _flag(perms.get("id-token"), "write")


def _covered(workflow: dict[str, Any], job: dict[str, Any], has_row: bool) -> bool:
    perms = job["permissions"] if "permissions" in job else workflow.get("permissions")
    shared = {"env": workflow.get("env"), "defaults": workflow.get("defaults")}
    return has_row or _id_token_write(perms) or str(job.get("secrets")) == "inherit" or _has_secret(job) or _has_secret(shared)


def _load_list(root: Path, out: list[str]) -> dict[str, Any] | None:
    try:
        data = yaml.safe_load((root / LIST_FILE).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        out.append(f"{LIST_FILE} does not parse: {type(exc).__name__}")
        return None
    filt = data.get("filter") if isinstance(data, dict) else None
    ok = isinstance(filt, dict) and all(isinstance(filt.get(k), str) for k in ("action", "sha", "version", "agent_sha256"))
    if not ok:
        out.append(f"{LIST_FILE}: filter needs action, sha, version and agent_sha256")
        return None
    if not HEX40.match(filt["sha"]):
        out.append(f"{LIST_FILE}: filter.sha is not a 40-hex commit")
    if not HEX64.match(filt["agent_sha256"]):
        out.append(f"{LIST_FILE}: filter.agent_sha256 is missing or not a 64-hex digest")
    if not data.get("filter_supplied"):
        out.append(f"{LIST_FILE}: filter_supplied is empty")
    if not isinstance(data.get("wildcard_residuals"), list):
        out.append(f"{LIST_FILE}: wildcard_residuals must be a list")
        return None
    if not isinstance(data.get("jobs"), dict):
        out.append(f"{LIST_FILE}: jobs must be a mapping")
        return None
    return data


def _endpoint_problems(where: str, hosts: list[Any], residuals: list[Any], uses_artifact: bool) -> list[str]:
    out: list[str] = []
    for endpoint in map(str, hosts):
        host = endpoint.rsplit(":", 1)[0]
        if not ENDPOINT.match(endpoint):
            out.append(f"{where}: endpoint {endpoint} is not host:port with a numeric port")
            continue
        try:
            ipaddress.ip_address(host)
            out.append(f"{where}: endpoint {endpoint} is an IP literal")
        except ValueError:
            pass
        if "execute-api" in host or "lambda-url" in host:
            out.append(f"{where}: endpoint {endpoint} is an execute-api or lambda-url host")
        if "*" in host and not (endpoint in residuals and uses_artifact):
            out.append(f"{where}: wildcard endpoint {endpoint} is outside wildcard_residuals or the job moves no artifact")
    return out


def _uses_comment_ok(text: str, uses: str, version: str) -> bool:
    lines = [ln.strip().removeprefix("- ").strip() for ln in text.splitlines() if f"uses: {uses}" in ln]
    return bool(lines) and all(re.fullmatch(rf"uses: {re.escape(uses)}\s+#\s*{re.escape(version)}", ln) for ln in lines)


def _step0_problems(where: str, step: dict[str, Any], filt: dict[str, Any], row: list[Any], text: str) -> list[str]:
    out: list[str] = []
    uses = f"{filt['action']}@{filt['sha']}"
    if step.get("uses") != uses:
        return [f"{where}: step 0 must use exactly {uses}"]
    if not _uses_comment_ok(text, uses, filt["version"]):
        out.append(f"{where}: the filter's uses line must carry the version comment # {filt['version']}")
    if "if" in step or "continue-on-error" in step:
        out.append(f"{where}: the filter step has an if: or continue-on-error")
    with_ = step.get("with") or {}
    if set(with_) != FILTER_WITH:
        out.append(f"{where}: the filter's with: keys must be exactly {sorted(FILTER_WITH)}")
        return out
    if with_["egress-policy"] != "block":
        out.append(f"{where}: egress-policy must be block")
    if not _flag(with_["disable-telemetry"], "true"):
        out.append(f"{where}: disable-telemetry must be true")
    if not _flag(with_["disable-sudo-and-containers"], "true"):
        out.append(f"{where}: disable-sudo-and-containers must be true")
    if any("${{" in str(v) for v in with_.values()):
        out.append(f"{where}: a filter with: value holds an expression")
    if set(str(with_["allowed-endpoints"]).split()) != set(map(str, row)):
        out.append(f"{where}: allowed-endpoints differ from the job's row in {LIST_FILE}")
    return out


def _checkout_problems(where: str, step: dict[str, Any], probe: bool) -> list[str]:
    with_ = step.get("with") or {}
    keys = set(with_)
    want = {"persist-credentials"} if probe else {"persist-credentials", "path"}
    out: list[str] = []
    if not CHECKOUT.match(str(step.get("uses"))):
        out.append(f"{where}: a checkout before the self-check is not SHA-pinned actions/checkout")
    if not _flag(with_.get("persist-credentials"), "false"):
        out.append(f"{where}: a checkout before the self-check must set persist-credentials false")
    if not probe and with_.get("path") != "main":
        out.append(f"{where}: a checkout before the self-check must use path main")
    extra = keys - want - ({"ref"} if with_.get("ref") == "${{ github.sha }}" else set())
    if extra:
        out.append(f"{where}: a checkout before the self-check has with: inputs {sorted(extra)}")
    return out


def _self_check_ok(step: dict[str, Any], probe: bool, seen_main: bool) -> bool:
    run = str(step.get("run", "")).strip()
    if {"if", "continue-on-error", "shell", "env", "working-directory"} & set(step):
        return False
    if probe:
        return run in (PROBE_PREFIX + "egress-probe", PROBE_PREFIX + "egress-self-check")
    return run == RUN_PREFIX + "egress-self-check" and seen_main


def _order_problems(where: str, steps: list[Any], probe: bool) -> list[str]:
    out: list[str] = []
    seen_main = False
    for step in steps[1:]:
        if not isinstance(step, dict):
            out.append(f"{where}: a step is not a mapping")
            continue
        if _has_secret({"env": step.get("env"), "with": step.get("with")}):
            out.append(f"{where}: a step before the self-check references a secret")
        if str(step.get("uses", "")).startswith("actions/checkout@"):
            out.extend(_checkout_problems(where, step, probe))
            seen_main = seen_main or (step.get("with") or {}).get("path") == "main"
        elif str(step.get("run", "")).strip() != MKDIR:
            if not _self_check_ok(step, probe, seen_main):
                out.append(
                    f"{where}: the first step after the filter that is not a checkout or the work-directory mkdir "
                    "must be the self-check"
                )
            return out
    out.append(f"{where}: no self-check step")
    return out


def _job_problems(rel: str, name: str, workflow: dict[str, Any], data: dict[str, Any], text: str) -> list[str]:
    out: list[str] = []
    where, probe, job, row = f"{rel}::{name}", rel == PROBE, workflow["jobs"][name], data["jobs"][rel][name]
    steps = job.get("steps") or []
    if "defaults" in job or "defaults" in workflow:
        out.append(f"{where}: a covered job or its workflow sets defaults:, which can change how the self-check runs")
    if "uses" in job or "container" in job or "services" in job:
        out.append(f"{where}: a covered job has a job-level uses:, container: or services:")
    if job.get("runs-on") != "ubuntu-latest":
        out.append(f"{where}: runs-on must be exactly ubuntu-latest")
    if re.search(r"\bdocker\b", json.dumps([s.get("run", "") for s in steps if isinstance(s, dict)])):
        out.append(f"{where}: a covered job runs a docker command")
    if _has_secret({"job_env": job.get("env"), "workflow_env": workflow.get("env")}):
        out.append(f"{where}: the job's or the workflow's env: holds a secrets expression")
    if not steps or not isinstance(steps[0], dict):
        return [*out, f"{where}: no steps"]
    out.extend(_step0_problems(where, steps[0], data["filter"], row, text))
    out.extend(_order_problems(where, steps, probe))
    uses_artifact = any(
        isinstance(s, dict) and str(s.get("uses", "")).startswith(("actions/upload-artifact@", "actions/download-artifact@"))
        for s in steps
    )
    out.extend(_endpoint_problems(where, row, data["wildcard_residuals"], uses_artifact))
    return out


def _probe_problems(workflow: dict[str, Any]) -> list[str]:
    out: list[str] = []
    triggers = workflow.get("on", workflow.get(True))
    if workflow.get("name") != "persona-verdict-egress-probe":
        out.append(f"{PROBE}: name must be persona-verdict-egress-probe")
    if triggers != PROBE_TRIGGER:
        out.append(f"{PROBE}: the trigger or its paths drift from the scope row")
    if workflow.get("permissions") != {}:
        out.append(f"{PROBE}: top-level permissions must be {{}}")
    jobs = workflow.get("jobs") or {}
    if set(jobs) != set(PROBE_STEPS):
        out.append(f"{PROBE}: jobs must be exactly {sorted(PROBE_STEPS)}")
    for name, want in PROBE_STEPS.items():
        job = jobs.get(name) or {}
        if set(job) - {"runs-on", "timeout-minutes", "permissions", "steps"}:
            out.append(f"{PROBE}: job {name} has keys beyond runs-on, timeout-minutes, permissions and steps")
        if job.get("timeout-minutes") != 5 or job.get("permissions") != {"contents": "read"}:
            out.append(f"{PROBE}: job {name} must have timeout-minutes 5 and permissions {{contents: read}}")
        runs = [(s.get("id"), str(s.get("run", "")).strip()) for s in job.get("steps") or [] if s.get("run")]
        if runs != want:
            out.append(f"{PROBE}: job {name} run lines drift from the scope row")
        if name == "egress-control" and any("harden-runner" in str(s.get("uses", "")) for s in job.get("steps") or []):
            out.append(f"{PROBE}: egress-control must run without the filter")
    return out


def check_tree(root: Path) -> tuple[list[str], int]:
    out: list[str] = []
    files = sorted(root.glob(WORKFLOW_GLOB))
    data = _load_list(root, out)
    if data is None:
        return out, len(files)
    rows: dict[str, dict[str, Any]] = data["jobs"]
    docs: dict[str, tuple[dict[str, Any], str]] = {}
    for path in files:
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        loaded = yaml.safe_load(text)
        if isinstance(loaded, dict):
            docs[rel] = (loaded, text)
    if PROBE not in docs:
        out.append(f"{PROBE} is missing")
    else:
        out.extend(_probe_problems(docs[PROBE][0]))
    for rel, jobs in rows.items():
        for name in jobs:
            declared = (docs.get(rel, ({}, ""))[0].get("jobs") or {}).get(name)
            if declared is None or not _covered(docs[rel][0], declared, True):
                out.append(f"{LIST_FILE}: row {rel}::{name} names no existing covered job")
    for rel, (workflow, text) in docs.items():
        for name, job in (workflow.get("jobs") or {}).items():
            has_row = name in (rows.get(rel) or {})
            if not _covered(workflow, job, has_row):
                continue
            if not has_row:
                out.append(f"{rel}::{name}: a covered job has no row in {LIST_FILE}")
                continue
            out.extend(_job_problems(rel, name, workflow, data, text))
    return out, len(files)


@registry.register("validate_persona_verdict_egress", owner="platform")
def validate_persona_verdict_egress(failed: list[str]) -> None:
    """Refuse a covered verdict job without the pinned egress filter, its row's list and the self-check.

    Declares examined(workflow files read, unit="workflows") (Decision 170).
    """
    print("\n=== persona verdict egress filter (T4.32 c7 shell leg) ===")
    problems, examined = check_tree(_common.ROOT)
    registry.examined(examined, unit="workflows")
    for problem in problems:
        print(f"  FAIL: {problem}")
        failed.append(f"persona verdict egress: {problem}")
    if not problems:
        print(f"  PASS: {examined} workflow(s) satisfy the egress filter rules")
