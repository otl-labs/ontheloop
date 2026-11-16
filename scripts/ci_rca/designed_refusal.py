"""Designed-refusal fail-closed skip predicate for ci-rca.yml (PLAN-ci-rca-ops-plane-coverage).

Some watched workflows can fail via a DESIGNED refusal -- a gate that deliberately blocks a step
under a known, self-documenting condition (e.g. deploy-ducklake-lambdas' reconcile-gate job, the
Decision 88 reconcile-pending predicate) rather than a genuine defect. Filing a fresh
priority=critical ci_rca rec for every such refusal would mint a rec storm and a planning-queue
wedge (Decision 73 L5/L6) for a condition that already self-documents and already blocks the
deploy -- see PLAN-ci-rca-ops-plane-coverage's WATCHED-SET ADJUDICATION context, and rec-2854 for
the alarm-not-gate monitor lane that covers the STANDING refusal instead.

Fail-closed is the point (Decision 55): this predicate returns skip=true ONLY when the resolved
failing-job set for the run is non-empty AND every member is a DECLARED designed-refusal job for
that workflow slug. Any uncertainty -- an empty failing-job set, an unparseable/`{}` jobs file, or
any undeclared failing job (even alongside a declared one) -- returns skip=false, so the ci-rca
agent still runs and files.

Mirrors scripts/ci_rca/convergence_dedup.py exactly: a thin invocation-only shell, no network call
of its own -- it consumes the /tmp/ci-rca-jobs.json the existing "Fetch jobs JSON" step already
writes (continue-on-error, `{}` on failure -- a concrete producer for the fail-closed input).

CLI usage (invoked by ci-rca.yml's designed-refusal skip step):
    bin/venv-python -m scripts.ci_rca.designed_refusal \\
        --jobs-json /tmp/ci-rca-jobs.json --workflow-slug deploy-ducklake-lambdas
Prints skip=true|false.

PLAN-persona-trigger-policy adds a step-granular refusal for any watched slug: a run whose only failure is the
persona dispatch gate step (Decision 205 cl.1), with the gate's refusal marker in the failed log, is a designed
refusal. The new --failed-log argument defaults to /tmp/ci-rca-failed.log so the existing command is unchanged
and an older checkout (which lacks the argument) simply files, fail-closed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, cast

PERSONA_GATE_STEP_PREFIX = "Persona dispatch gate"
PERSONA_GATE_REFUSAL_MARKER = "PERSONA_DISPATCH_REFUSED:"

# Declared workflow slug -> designed-refusal job NAME set. The job NAME is checked (not a step
# name) because the failing-job granularity is what the jobs JSON exposes; none of
# deploy-ducklake-lambdas.yml's jobs set a `name:` key, so the API display name equals the job id.
DESIGNED_REFUSAL_JOBS: dict[str, frozenset[str]] = {
    "deploy-ducklake-lambdas": frozenset({"reconcile-gate"}),
}


def resolve_failing_jobs(jobs_payload: dict) -> set[str]:
    """Extract the set of job NAMES whose conclusion is 'failure' from a parsed jobs JSON.

    `jobs_payload` is the parsed `gh run view --json jobs` response shape: {"jobs": [{"name":
    ..., "conclusion": ...}, ...]}. Any other shape (empty dict, missing/non-list "jobs" key)
    resolves to an empty set -- the fail-closed default, never an exception.
    """
    if not isinstance(jobs_payload, dict):
        return set()
    jobs = jobs_payload.get("jobs")
    if not isinstance(jobs, list):
        return set()
    # The trailing `and j.get("name")` predicate already drops every falsy name, so the surviving
    # elements are exactly the truthy job names -- str by the jobs-JSON shape documented above.
    return cast(
        "set[str]",
        {j.get("name") for j in jobs if isinstance(j, dict) and j.get("conclusion") == "failure" and j.get("name")},
    )


def is_designed_refusal(workflow_slug: str, jobs_payload: dict) -> bool:
    """True iff every failing job in this run is a DECLARED designed-refusal job for the slug.

    Fail-closed: an empty failing-job set (nothing resolved -- uncertain, never treated as "no
    genuine failure") and any undeclared failing job co-occurring with a declared one both return
    False.
    """
    failing = resolve_failing_jobs(jobs_payload)
    if not failing:
        return False
    declared = DESIGNED_REFUSAL_JOBS.get(workflow_slug, frozenset())
    return failing.issubset(declared)


def _is_failed(entry: object) -> bool:
    return isinstance(entry, dict) and entry.get("conclusion") == "failure"


def is_persona_gate_refusal(jobs_payload: dict, failed_log_text: str) -> bool:
    """True iff the only failure of every failing job is one persona dispatch gate step and the log shows its marker.

    A failing job is one with conclusion failure or, while still in progress, a failed step. Fail-closed: no
    marker, a job with no steps array, any other failed step, or an empty failing set returns False.
    """
    if PERSONA_GATE_REFUSAL_MARKER not in failed_log_text or not isinstance(jobs_payload, dict):
        return False
    jobs = jobs_payload.get("jobs")
    if not isinstance(jobs, list):
        return False
    failing = 0
    for job in jobs:
        if not isinstance(job, dict):
            continue
        steps = job.get("steps")
        in_progress_failed = job.get("status") == "in_progress" and isinstance(steps, list) and any(map(_is_failed, steps))
        if not (_is_failed(job) or in_progress_failed):
            continue
        if not isinstance(steps, list):
            return False
        failed_steps = [s for s in steps if _is_failed(s)]
        if len(failed_steps) != 1 or not str(failed_steps[0].get("name", "")).startswith(PERSONA_GATE_STEP_PREFIX):
            return False
        failing += 1
    return failing > 0


def _load_failed_log(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _load_jobs_payload(jobs_json: Path) -> dict:
    """Read + parse the jobs JSON file. Any read/parse failure resolves to {} (fail-closed)."""
    try:
        text = jobs_json.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Designed-refusal fail-closed skip predicate for ci-rca.yml.")
    parser.add_argument(
        "--jobs-json",
        type=Path,
        default=Path("/tmp/ci-rca-jobs.json"),
        help="Path to the jobs JSON written by ci-rca.yml's Fetch jobs JSON step.",
    )
    parser.add_argument(
        "--workflow-slug",
        required=True,
        help="The slugified workflow name (ci-rca.yml's Resolve run metadata workflow_slug output).",
    )
    parser.add_argument(
        "--failed-log",
        type=Path,
        default=Path("/tmp/ci-rca-failed.log"),
        help="Path to the failed-step log written before this step; read for the persona gate refusal marker.",
    )
    args = parser.parse_args(argv)

    jobs_payload = _load_jobs_payload(args.jobs_json)
    skip = is_designed_refusal(args.workflow_slug, jobs_payload) or is_persona_gate_refusal(
        jobs_payload, _load_failed_log(args.failed_log)
    )
    print(f"skip={'true' if skip else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
