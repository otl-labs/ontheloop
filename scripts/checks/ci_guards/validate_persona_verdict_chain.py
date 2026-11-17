"""Shape check for the verdict chain workflows (PLAN-persona-verdict-chain; T4.2 c8, c9 and c12; Decision 205 cl.2).

Refuses an unsafe shape of .github/workflows/persona-verdict-trigger.yml (a secretless pull_request trigger) or
persona-verdict.yml (the workflow_run verdict workflow on main's definition): any widened trigger, permission, secret,
Environment or OIDC grant, any unpinned action, any python step off the one pinned argv, working directory or snapshot
variable, any token in the steps that handle untrusted data, an unpinned or unclassified inter-job artifact. It reads the
two workflow files and the evidence contract as YAML only and imports nothing from scripts/personas/.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from scripts.checks import _common, registry

TRIGGER = ".github/workflows/persona-verdict-trigger.yml"
VERDICT = ".github/workflows/persona-verdict.yml"
EVIDENCE = "docs/contracts/github-actions-evidence.yaml"
PR_TYPES = ["opened", "ready_for_review", "reopened", "synchronize"]
PINNED = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
MKDIR = 'mkdir -p "$RUNNER_TEMP/work"'
RUN_PREFIX = 'python3 -I "$GITHUB_WORKSPACE/main/scripts/personas/run.py" '
VERBS = frozenset({"resolve", "route", "fetch", "materialise", "execute", "post"})
SNAPSHOT = "${{ runner.temp }}/snapshot"
WORKDIR = "${{ runner.temp }}/work"
PIP_SETS = {"prepare": "pyyaml", "run": "jsonschema", "post": "pyyaml jsonschema"}
JOB_PERMISSIONS: dict[str, dict[str, str]] = {
    "prepare": {"contents": "read", "pull-requests": "read"},
    "run": {"contents": "read"},
    "post": {"checks": "write", "contents": "read", "actions": "read"},
}
ARTIFACT_DIRS = {"persona-verdict-prepared": "prepared", "persona-verdict-results": "results"}
RUN_CONDITION = "!cancelled() && needs.prepare.result == 'success'"
POST_CONDITION = "!cancelled()"
PYTHON_CMD = re.compile(r"(?:^|[\s;&|(`])(?:\S*/)?python[0-9.]*(?=$|[\s;&|)`])")
TOKEN_REF = re.compile(
    r"github\s*(?:\.\s*token|\[\s*['\"]\s*token)|secrets\s*(?:\.\s*github_token|\[\s*['\"]\s*github_token)|"
    r"tojson\s*\(\s*(?:github|secrets)\s*\)|gh_token|github_token",
    re.IGNORECASE,
)
SECRETS_CTX = re.compile(r"secrets\s*(?:\.\s*([A-Za-z0-9_]+)|\[)|tojson\s*\(\s*secrets\s*\)", re.IGNORECASE)
BARE_VAR = re.compile(r"\$\{?(?:RUNNER_TEMP|GITHUB_WORKSPACE)\b")


def _unwrap(expr: str) -> str:
    expr = " ".join(str(expr).split())
    if expr.startswith("${{") and expr.endswith("}}"):
        expr = expr[3:-2].strip()
    return expr


def _on(doc: dict[str, Any]) -> Any:
    keyed: dict[Any, Any] = doc
    return keyed.get("on", keyed.get(True))


def _map(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _jobs(doc: dict[str, Any]) -> dict[str, Any]:
    jobs = doc.get("jobs")
    return jobs if isinstance(jobs, dict) else {}


def _text(value: Any) -> str:
    return json.dumps(value, default=str)


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    steps = job.get("steps")
    return [s for s in steps if isinstance(s, dict)] if isinstance(steps, list) else []


def _action(step: dict[str, Any]) -> str:
    return str(step.get("uses", "")).split("@", 1)[0]


def _load(root: Path, rel: str) -> tuple[dict[str, Any] | None, str | None]:
    path = root / rel
    if not path.is_file():
        return None, f"{rel} is missing"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return None, f"{rel} does not parse: {exc}"
    return (data, None) if isinstance(data, dict) else (None, f"{rel} is not a mapping")


def _common_problems(rel: str, doc: dict[str, Any]) -> list[str]:
    out: list[str] = []
    jobs = _jobs(doc)
    for name, job in jobs.items():
        for step in _steps(job):
            uses = step.get("uses")
            if uses is not None and not PINNED.match(str(uses)):
                out.append(f"{rel}: job {name} uses {uses!r}, which is not pinned to a 40-hex SHA")
        if "environment" in job:
            out.append(f"{rel}: job {name} names an Environment")
        perms = job.get("permissions")
        if isinstance(perms, dict) and perms.get("id-token") == "write":
            out.append(f"{rel}: job {name} holds id-token: write")
    return out


def trigger_problems(doc: dict[str, Any]) -> list[str]:
    out = _common_problems(TRIGGER, doc)
    on = _on(doc)
    if not (isinstance(on, dict) and set(on) == {"pull_request"}):
        out.append(f"{TRIGGER}: the trigger is not exactly pull_request")
    elif sorted((on["pull_request"] or {}).get("types") or []) != PR_TYPES:
        out.append(f"{TRIGGER}: pull_request types are not exactly opened, synchronize, reopened, ready_for_review")
    if doc.get("permissions") != {}:
        out.append(f"{TRIGGER}: top-level permissions are not {{}}")
    for name, job in _jobs(doc).items():
        if job.get("permissions") != {}:
            out.append(f"{TRIGGER}: job {name} permissions are not {{}}")
        if any("uses" in s for s in _steps(job)):
            out.append(f"{TRIGGER}: job {name} has a uses: step (no checkout or action in the trigger)")
    if re.search(r"secrets\b", _text(doc), re.IGNORECASE):
        out.append(f"{TRIGGER}: a secrets. reference")
    return out


def _python_steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in _steps(job) if PYTHON_CMD.search(str(s.get("run", ""))) and "pip install" not in str(s.get("run", ""))]


def _job_problems(name: str, job: dict[str, Any]) -> list[str]:
    out: list[str] = []
    steps = _steps(job)
    if job.get("permissions") != JOB_PERMISSIONS[name]:
        out.append(f"{VERDICT}: job {name} permissions differ from {JOB_PERMISSIONS[name]}")
    if name != "post" and isinstance(job.get("permissions"), dict) and job["permissions"].get("checks") == "write":
        out.append(f"{VERDICT}: job {name} holds checks: write")
    for step in steps:
        run = str(step.get("run", ""))
        if "pip install" in run:
            want = f'pip install {PIP_SETS[name]} -c "$GITHUB_WORKSPACE/main/requirements.txt"'
            if run.strip() != want:
                out.append(f"{VERDICT}: job {name} pip line is not exactly {want!r}")
    py = _python_steps(job)
    for step in py:
        run = str(step.get("run", "")).strip()
        verb = run[len(RUN_PREFIX) :] if run.startswith(RUN_PREFIX) else ""
        env = _map(step.get("env"))
        if verb not in VERBS:
            out.append(f"{VERDICT}: job {name} python step {run!r} is not the pinned run.py argv")
        if env.get("PERSONA_SNAPSHOT") != SNAPSHOT:
            out.append(f"{VERDICT}: job {name} python step {run!r} lacks PERSONA_SNAPSHOT: {SNAPSHOT}")
        if step.get("working-directory") != WORKDIR:
            out.append(f"{VERDICT}: job {name} python step {run!r} working-directory is not {WORKDIR}")
        if name == "run" and verb != "execute":
            out.append(f"{VERDICT}: the run job runs {verb or run!r}, not only the execute verb")
    if py:
        first_python = steps.index(py[0])
        if not any(str(s.get("run", "")).strip() == MKDIR for s in steps[:first_python]):
            out.append(f"{VERDICT}: job {name} has python steps but no {MKDIR} step before them")
    for step in steps:
        for field in ("env", "with"):
            if BARE_VAR.search(_text(step.get(field, {}))):
                out.append(f"{VERDICT}: job {name} has a bare $RUNNER_TEMP or $GITHUB_WORKSPACE in a {field}: value")
        if BARE_VAR.search(str(step.get("working-directory", ""))):
            out.append(f"{VERDICT}: job {name} has a bare variable in working-directory")
    return out


def _artifact_problems(jobs: dict[str, Any], evidence: dict[str, Any] | None) -> list[str]:
    out: list[str] = []
    rows = [r for r in (evidence or {}).get("artifact_uploads", []) if isinstance(r, dict) and r.get("workflow") == VERDICT]
    seen: set[tuple[str, str, str]] = set()
    for name, job in jobs.items():
        for step in _steps(job):
            action, with_ = _action(step), _map(step.get("with"))
            artifact = str(with_.get("name", ""))
            if action == "actions/upload-artifact":
                seen.add((name, str(step.get("name", "")), artifact))
                if with_.get("retention-days") != 1 or isinstance(with_.get("retention-days"), bool):
                    out.append(f"{VERDICT}: upload {artifact} retention-days is not exactly 1")
                if with_.get("path") != f"{WORKDIR}/{ARTIFACT_DIRS.get(artifact, '?')}":
                    out.append(f"{VERDICT}: upload {artifact} path is not its work directory")
            if action == "actions/download-artifact":
                if with_.get("path") != f"{WORKDIR}/{ARTIFACT_DIRS.get(artifact, '?')}":
                    out.append(f"{VERDICT}: download {artifact} path is not its upload directory")
                if name == "post" and step.get("continue-on-error") is not True:
                    out.append(f"{VERDICT}: post download {artifact} lacks continue-on-error: true")
    declared = {(str(r.get("job")), str(r.get("step")), str(r.get("artifact"))) for r in rows}
    if declared != seen:
        out.append(
            f"{VERDICT}: upload steps and {EVIDENCE} rows differ: "
            f"missing={sorted(seen - declared)} stale={sorted(declared - seen)}"
        )
    return out


def verdict_problems(doc: dict[str, Any], evidence: dict[str, Any] | None) -> list[str]:
    out = _common_problems(VERDICT, doc)
    on = _on(doc)
    if not (isinstance(on, dict) and set(on) == {"workflow_run"}):
        out.append(f"{VERDICT}: the trigger is not exactly workflow_run")
    else:
        wr = on["workflow_run"] or {}
        if wr.get("workflows") != ["persona-verdict-trigger"]:
            out.append(f"{VERDICT}: workflow_run does not name exactly persona-verdict-trigger")
        if wr.get("types") != ["completed"]:
            out.append(f"{VERDICT}: workflow_run types are not exactly [completed]")
    if doc.get("permissions") != {}:
        out.append(f"{VERDICT}: top-level permissions are not {{}}")
    jobs = _jobs(doc)
    if set(jobs) != set(JOB_PERMISSIONS):
        out.append(f"{VERDICT}: jobs are not exactly prepare, run and post")
    for name in sorted(set(jobs) & set(JOB_PERMISSIONS)):
        out.extend(_job_problems(name, jobs[name]))
    for name, job in jobs.items():
        if name in JOB_PERMISSIONS and not isinstance(job.get("permissions"), dict):
            out.append(f"{VERDICT}: job {name} has no permissions mapping")
    if "run" in jobs and _unwrap(jobs["run"].get("if", "")) != RUN_CONDITION:
        out.append(f"{VERDICT}: the run job condition is not {RUN_CONDITION}")
    if "post" in jobs and _unwrap(jobs["post"].get("if", "")) != POST_CONDITION:
        out.append(f"{VERDICT}: the post job condition is not {POST_CONDITION}")
    out.extend(_prepare_problems(jobs.get("prepare")))
    out.extend(_token_problems(doc, jobs))
    out.extend(_artifact_problems(jobs, evidence))
    return out


def _prepare_problems(job: Any) -> list[str]:
    if not isinstance(job, dict):
        return []
    out: list[str] = []
    names = [
        str(s.get("run", "")).strip()[len(RUN_PREFIX) :] if str(s.get("run", "")).startswith(RUN_PREFIX) else ""
        for s in _steps(job)
    ]
    if "fetch" in names and "materialise" in names and names.index("fetch") > names.index("materialise"):
        out.append(f"{VERDICT}: the fetch step comes after the materialise step")
    for verb in ("fetch", "materialise"):
        if verb in names:
            step = _steps(job)[names.index(verb)]
            if "allowlisted == 'true'" not in str(step.get("if", "")):
                out.append(f"{VERDICT}: the {verb} step lacks the allowlisted condition")
    return out


def _token_problems(doc: dict[str, Any], jobs: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if TOKEN_REF.search(_text(doc.get("env", {}))):
        out.append(f"{VERDICT}: a token reference in the workflow-level env")
    for name, job in jobs.items():
        if TOKEN_REF.search(_text(job.get("env", {}))):
            out.append(f"{VERDICT}: a token reference in job {name}'s job-level env")
        for step in _steps(job):
            run = str(step.get("run", ""))
            if run.startswith(RUN_PREFIX) and run.strip()[len(RUN_PREFIX) :] in ("materialise", "execute"):
                if TOKEN_REF.search(_text(step.get("env", {}))):
                    out.append(f"{VERDICT}: the {run.strip()[len(RUN_PREFIX) :]} step env holds a token")
    for match in SECRETS_CTX.finditer(_text(doc)):
        if (match.group(1) or "").upper() != "GITHUB_TOKEN":
            out.append(f"{VERDICT}: a secrets context reference other than secrets.GITHUB_TOKEN: {match.group(0)!r}")
    return out


def check_tree(root: Path) -> tuple[list[str], int]:
    problems: list[str] = []
    examined = 0
    evidence, err = _load(root, EVIDENCE)
    if err:
        problems.append(err)
    for rel, fn in ((TRIGGER, lambda d: trigger_problems(d)), (VERDICT, lambda d: verdict_problems(d, evidence))):
        doc, err = _load(root, rel)
        if err:
            problems.append(err)
            continue
        examined += 1
        problems.extend(fn(doc))
    return problems, examined


@registry.register("validate_persona_verdict_chain", owner="platform")
def validate_persona_verdict_chain(failed: list[str]) -> None:
    """Refuse an unsafe shape of the two verdict chain workflows.

    Declares examined(workflow files read, unit="workflows") (Decision 170 cl.3).
    """
    print("\n=== persona verdict chain shape (T4.2 c8) ===")
    problems, examined = check_tree(_common.ROOT)
    registry.examined(examined, unit="workflows")
    for problem in problems:
        print(f"  FAIL: {problem}")
        failed.append(f"persona verdict chain: {problem}")
    if not problems:
        print(f"  PASS: {examined} workflow(s) satisfy the verdict chain shape")
