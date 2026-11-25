"""The keyed-job pins validate_persona_verdict_chain calls (PLAN-persona-verdict-first-key-read and -fixture-run).

keyprep (keyless), smoke and run (persona-verdict Environment) and refusal (no Environment) exist together or not at all. Each
carries exactly its pinned condition, needs, permissions and steps; the only secret is AWS_ACCOUNT_ID, as the env key of an
inference-smoke, key-refusal or run-persona step after the self-check (and, in run, after the first container probe);
packages reach the keyed jobs only as the hash-locked wheelhouse. The run job's whole step list is closed: each step equals its
canonical mapping. No keyed job and no workflow carries env or defaults at job or top level. Imports nothing from
scripts/personas/; the docker rule is validate_persona_verdict_egress's command-token rule, imported.
"""

from __future__ import annotations

import json
import re
from typing import Any

from scripts.checks.ci_guards._persona_verdict_fallback import ROLES as RUN_JOBS
from scripts.checks.ci_guards._persona_verdict_fallback import fallback_problems, run_fallback_condition
from scripts.checks.ci_guards._shared import is_sha_pinned
from scripts.checks.misc.validate_persona_verdict_egress import DOCKER_TOKEN, _logical_lines

VERDICT = ".github/workflows/persona-verdict.yml"
KEYED_JOBS = ("keyprep", "smoke", "refusal", "run", "run-fallback")
TIER_ROLES = ("primary", "warm_fetched_escape_hatch")
KEYED_IF = (
    "!cancelled() && needs.prepare.result == 'success' && github.event.workflow_run.event == 'pull_request' && "
    "needs.prepare.outputs.allowlisted == 'true' && needs.prepare.outputs.fixture == 'true'"
)
KEYPREP_OK = "needs.keyprep.result == 'success'"
KEYPREP_IF = "!cancelled() && needs.prepare.result == 'success'"
KEYED_CONDITION = f"{KEYED_IF} && {KEYPREP_OK}"
CONDITIONS = {
    "keyprep": KEYPREP_IF,
    "smoke": KEYED_CONDITION,
    "refusal": KEYED_CONDITION,
    "run": KEYED_CONDITION,
    "run-fallback": run_fallback_condition(KEYED_IF, KEYPREP_OK),
}
NEEDS = {
    "keyprep": ["prepare"],
    "smoke": ["prepare", "keyprep"],
    "refusal": ["prepare", "keyprep"],
    "run": ["prepare", "keyprep"],
    "run-fallback": ["prepare", "keyprep", "run"],
}
PERMISSIONS = {
    "keyprep": {"contents": "read"},
    "smoke": {"contents": "read", "id-token": "write"},
    "refusal": {"contents": "read", "id-token": "write"},
    "run": {"contents": "read", "id-token": "write"},
    "run-fallback": {"contents": "read", "id-token": "write"},
}
ENVIRONMENT_JOBS = ("smoke", "run", "run-fallback")
RUN_PREFIX = 'python3 -I "$GITHUB_WORKSPACE/main/scripts/personas/run.py" '
SELF_CHECK = RUN_PREFIX + "egress-self-check"
FLOOR = "sudo " + RUN_PREFIX + "egress-floor"
FLOOR_CONTAINER = FLOOR + "-container"
LAUNCHER = "sudo /usr/bin/python3 -I -S /usr/local/lib/persona-egress/egress_launcher.py "
PROBE = RUN_PREFIX + "egress-container-request | " + LAUNCHER + "probe"
PERSONA = RUN_PREFIX + "persona-request | " + LAUNCHER + "run | " + RUN_PREFIX + "persona-record"
MKDIR = 'mkdir -p "$RUNNER_TEMP/work"'
LITERAL_VERBS = {
    FLOOR: "egress-floor",
    FLOOR_CONTAINER: "egress-floor-container",
    PROBE: "container-probe",
    PERSONA: "persona-run",
}
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
PREPARED = "persona-verdict-prepared"
RESULTS = "persona-verdict-results"
UPLOAD_NAME = "Upload verdict results"
RESULTS_FALLBACK = "persona-verdict-results-fallback"
UPLOAD_NAME_FALLBACK = "Upload fallback verdict results"
WORKDIR = "${{ runner.temp }}/work"
SNAPSHOT_ENV = "${{ runner.temp }}/snapshot"
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
    if run in LITERAL_VERBS:
        return LITERAL_VERBS[run]
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
    if name in ENVIRONMENT_JOBS and job.get("environment") != "persona-verdict":
        out.append(f"{w} does not name exactly the persona-verdict Environment")
    if name not in ENVIRONMENT_JOBS and "environment" in job:
        out.append(f"{w} names an Environment")
    for key in ("continue-on-error", "container", "services", "uses", "env", "defaults"):
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
    want = {
        "keyprep": [PIP_DOWNLOAD],
        "smoke": [PIP_INSTALL],
        "refusal": [],
        "run": [PIP_INSTALL],
        "run-fallback": [PIP_INSTALL],
    }.get(name)
    if want is None:
        return [f"{VERDICT}: keyed job {name} has no pinned pip set"]
    if pips != want:
        return [f"{VERDICT}: keyed job {name} pip lines are not exactly {want}"]
    return []


DOWNLOADS = {"smoke": [WHEELS], "run": [PREPARED, WHEELS], "run-fallback": [PREPARED, WHEELS]}
UPLOADS = {
    "keyprep": [(WHEELS, None)],
    "run": [(RESULTS, UPLOAD_NAME)],
    "run-fallback": [(RESULTS_FALLBACK, UPLOAD_NAME_FALLBACK)],
}


def _artifact_rules(name: str, job: dict[str, Any]) -> list[str]:
    ups: list[tuple[str, Any]] = []
    downs: list[str] = []
    for step in _steps(job):
        uses = str(step.get("uses", "")).split("@", 1)[0]
        artifact = str(_map(step.get("with")).get("name"))
        if uses == "actions/upload-artifact":
            ups.append((artifact, step.get("name") if name in RUN_JOBS else None))
        if uses == "actions/download-artifact":
            downs.append(artifact)
    out: list[str] = []
    if ups != UPLOADS.get(name, []):
        out.append(f"{VERDICT}: keyed job {name} uploads {ups}, not exactly {UPLOADS.get(name, [])}")
    if sorted(downs) != sorted(DOWNLOADS.get(name, [])):
        out.append(f"{VERDICT}: keyed job {name} downloads {downs}, not exactly {DOWNLOADS.get(name, [])}")
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
    if name in RUN_JOBS:
        return _run_job_rules(name, w, steps, run_py)
    want = ["egress-floor", "egress-self-check"] + (
        ["inference-smoke"] * len(TIER_ROLES) if name == "smoke" else ["key-refusal"]
    )
    want.append("egress-self-check")
    if [_verb(s) for s in run_py] != want:
        out.append(f"{w} run.py steps are not exactly {want}")
        return out
    if set(run_py[0]) - {"name", "id", "run"} or set(run_py[1]) - {"name", "id", "run"}:
        out.append(f"{w} floor or first self-check step has keys beyond name, id and run")
    final = run_py[-1]
    if str(final.get("if", "")).strip() != "always()" or set(final) - {"name", "id", "if", "run"}:
        out.append(f"{w} final self-check must carry if: always() and no env, shell or working-directory")
    keys = run_py[2:-1]
    for step in keys:
        env = _map(step.get("env"))
        if env.get("PERSONA_AWS_REGION") != REGION_ENV:
            out.append(f"{w} PERSONA_AWS_REGION is not exactly {REGION_ENV}")
        if env.get("AWS_ACCOUNT_ID") != ACCOUNT_ENV:
            out.append(f"{w} AWS_ACCOUNT_ID is not exactly {ACCOUNT_ENV}")
    if name == "smoke":
        roles = tuple(_map(s.get("env")).get("PERSONA_SMOKE_ROLE") for s in keys)
        if roles != TIER_ROLES:
            out.append(f"{w} PERSONA_SMOKE_ROLE values are not exactly {TIER_ROLES}")
    return out


CHECKOUT_WITH = {"ref": "${{ github.sha }}", "path": "main", "persist-credentials": False}
PIN_ENDPOINTS = "allowed-endpoints"


def _canonical_run_steps(job: str = "run") -> list[dict[str, Any]]:
    """The run job's steps in order (run-fallback's differ in step ids and the upload); uses steps list their action and with
    inputs, run steps their whole mapping."""
    pre = "run-" if job == "run" else "run-fallback-"
    upload = (UPLOAD_NAME, RESULTS) if job == "run" else (UPLOAD_NAME_FALLBACK, RESULTS_FALLBACK)

    def run(name: str, id_: str, text: str, **extra: Any) -> dict[str, Any]:
        return {"name": name, "id": id_, "run": text, **extra}

    return [
        {
            "name": "Harden the runner",
            "uses": "step-security/harden-runner",
            "with": {
                "egress-policy": "block",
                "disable-telemetry": True,
                "disable-sudo-and-containers": False,
                PIN_ENDPOINTS: None,
            },
        },
        {"name": "Check out main", "uses": "actions/checkout", "with": CHECKOUT_WITH},
        run("Lay the container egress floor", pre + "floor", FLOOR_CONTAINER),
        run("Create the work directory", pre + "mkdir", MKDIR),
        run("Check the egress filter", pre + "self-check", SELF_CHECK),
        {
            "name": "Download prepared inputs",
            "uses": "actions/download-artifact",
            "with": {"name": PREPARED, "path": WORKDIR + "/prepared"},
        },
        run("Probe from the container", pre + "probe", PROBE),
        {"name": "Set up Python 3.12", "uses": "actions/setup-python", "with": {"python-version": "3.12"}},
        {
            "name": "Download the wheelhouse",
            "uses": "actions/download-artifact",
            "with": {"name": WHEELS, "path": WORKDIR + "/wheels"},
        },
        run("Install the wheelhouse offline", pre + "install", PIP_INSTALL),
        run("Run the fixture persona", pre + "persona", PERSONA, env=None, **{"working-directory": WORKDIR}),
        {
            "name": upload[0],
            "uses": "actions/upload-artifact",
            "with": {"name": upload[1], "path": WORKDIR + "/results", "retention-days": 1, "if-no-files-found": "ignore"},
        },
        run("Probe from the container again", pre + "probe-final", PROBE),
        run("Check the egress filter again", pre + "final-check", SELF_CHECK, **{"if": "always()"}),
    ]


def _step_matches(step: dict[str, Any], canon: dict[str, Any]) -> bool:
    """Key for key and value; uses compare by action name plus the SHA-pin rule; env of the persona step is checked apart."""
    if set(step) != set(canon):
        return False
    for key, want in canon.items():
        got = step[key]
        if key == "uses":
            if str(got).split("@", 1)[0] != want or not is_sha_pinned(str(got)):
                return False
        elif key == "run":
            if str(got).strip() != want:
                return False
        elif key == "env":
            continue
        elif key == "with":
            if set(got or {}) != set(want):
                return False
            for k, v in want.items():
                if v is not None and str(_map(got)[k]).lower() != str(v).lower():
                    return False
        elif got != want:
            return False
    return True


def _run_job_rules(job_name: str, w: str, steps: list[dict[str, Any]], run_py: list[dict[str, Any]]) -> list[str]:
    """The run job's run.py sequence (k3) and its closed step list: every step equals its canonical mapping."""
    out: list[str] = []
    want = [
        "egress-floor-container",
        "egress-self-check",
        "container-probe",
        "persona-run",
        "container-probe",
        "egress-self-check",
    ]
    if [_verb(s) for s in run_py] != want:
        return [f"{w} run.py steps are not exactly {want}"]
    for step in (run_py[0], run_py[1], run_py[2], run_py[4]):
        if set(step) - {"name", "id", "run"} or not {"name", "id", "run"} <= set(step):
            out.append(f"{w} floor, self-check or probe step has keys beyond name, id and run")
    persona = run_py[3]
    env = _map(persona.get("env"))
    if set(persona) != {"name", "id", "run", "env", "working-directory"} or persona.get("working-directory") != WORKDIR:
        out.append(f"{w} persona step keys are not exactly name, id, run, env and working-directory in the work directory")
    want_env = {
        "PERSONA_AWS_REGION": REGION_ENV,
        "AWS_ACCOUNT_ID": ACCOUNT_ENV,
        "PERSONA_SNAPSHOT": SNAPSHOT_ENV,
        "PERSONA_ROLE": RUN_JOBS[job_name],
    }
    if env != want_env:
        out.append(f"{w} persona step env is not exactly {sorted(want_env)}, pinned (PERSONA_ROLE {RUN_JOBS[job_name]})")
    final = run_py[-1]
    if str(final.get("if", "")).strip() != "always()" or set(final) - {"name", "id", "if", "run"}:
        out.append(f"{w} final self-check must carry if: always() and no env, shell or working-directory")
    expected = _canonical_run_steps(job_name)
    if len(steps) != len(expected):
        return out + [f"{w} steps are not exactly the {len(expected)} canonical steps in order"]
    for index, (step, canon) in enumerate(zip(steps, expected, strict=True)):
        if not _step_matches(step, canon):
            out.append(f"{w} step {index} ({step.get('name', step.get('id', '?'))!r}) differs from its canonical mapping")
    return out


def _secret_rules(jobs: dict[str, Any], doc_text: str) -> list[str]:
    """Every secrets reference in the file is AWS_ACCOUNT_ID as the env key of a key-reading step after the self-check.

    In run it is the persona step only, after the first self-check and after the first container probe.
    """
    allowed = 0
    for name in ("smoke", "refusal", "post", "run", "run-fallback"):
        steps = _steps(_map(jobs.get(name)))
        check = next((i for i, s in enumerate(steps) if _run(s) == SELF_CHECK), None)
        probe = next((i for i, s in enumerate(steps) if _run(s) == PROBE), None)
        verbs = ("persona-run",) if name in RUN_JOBS else ("inference-smoke", "key-refusal", "post")
        after = check if name not in RUN_JOBS else (None if check is None or probe is None else max(check, probe))
        for i, step in enumerate(steps):
            if _verb(step) in verbs and after is not None and i > after:
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
        return [f"{VERDICT}: keyprep, smoke, refusal, run and run-fallback exist together or not at all (found {present})"]
    out: list[str] = []
    for key in ("env", "defaults"):
        if key in doc:
            out.append(f"{VERDICT}: a top-level {key}: reaches every keyed step's shell")
    for name in KEYED_JOBS:
        job = _map(jobs[name])
        out.extend(_job_shape(name, job) + _step_rules(name, job) + _pip_rules(name, job))
        out.extend(_artifact_rules(name, job) + _run_py_rules(name, job))
    out.extend(_secret_rules(jobs, json.dumps(doc, default=str)))
    return out + fallback_problems(jobs)
