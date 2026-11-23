"""Verb egress-fail-closed-verdict: reads the drill jobs' recorded outcome (PLAN-persona-verdict-egress-floor).

Keyless (GITHUB_TOKEN actions: read). The hooked drill job passes when its runner was cut off mid-step; the control passes
when it kept reporting and its check failed visibly. Prints the observed shape of each job whatever the outcome.
"""

from __future__ import annotations

import json
import os
import urllib.request
from collections.abc import Sequence
from typing import Any

HOOKED = "egress-fail-closed"
CONTROL = "egress-fail-closed-control"
SETUP = ("drill-floor", "drill-self-check")
CHECK = "drill-fail-closed"
FINAL = "drill-final-check"


def fetch_jobs() -> list[dict[str, Any]]:
    repo, run, attempt = (os.environ[k] for k in ("GITHUB_REPOSITORY", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"))
    url = f"https://api.github.com/repos/{repo}/actions/runs/{run}/attempts/{attempt}/jobs?per_page=100"
    request = urllib.request.Request(  # noqa: S310
        url, headers={"Authorization": f"Bearer {os.environ['GH_TOKEN']}", "Accept": "application/vnd.github+json"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return [j for j in json.load(response)["jobs"] if isinstance(j, dict)]


def _steps(job: dict[str, Any]) -> dict[str, str | None]:
    return {str(s.get("name")): s.get("conclusion") for s in job.get("steps") or [] if isinstance(s, dict)}


def _observed(name: str, job: dict[str, Any] | None) -> str:
    if job is None:
        return f"EGRESS-FAIL-CLOSED-VERDICT observed job={name} missing"
    steps = " ".join(f"{k}={v}" for k, v in _steps(job).items())
    return f"EGRESS-FAIL-CLOSED-VERDICT observed job={name} conclusion={job.get('conclusion')} {steps}"


def hooked_problems(job: dict[str, Any] | None) -> list[str]:
    if job is None:
        return [f"{HOOKED} missing"]
    steps, out = _steps(job), []
    out += [f"{HOOKED} {s} not success" for s in SETUP if steps.get(s) != "success"]
    if steps.get(CHECK) in ("success", "failure"):
        out.append(f"{HOOKED} {CHECK} concluded {steps.get(CHECK)}, so the runner was not cut off")
    if steps.get(FINAL) is not None:
        out.append(f"{HOOKED} {FINAL} concluded {steps.get(FINAL)}, so the runner kept reporting")
    if job.get("conclusion") == "success":
        out.append(f"{HOOKED} concluded success")
    return out


def control_problems(job: dict[str, Any] | None) -> list[str]:
    if job is None:
        return [f"{CONTROL} missing"]
    steps, out = _steps(job), []
    out += [f"{CONTROL} {s} not success" for s in SETUP if steps.get(s) != "success"]
    if steps.get(CHECK) != "failure":
        out.append(f"{CONTROL} {CHECK} did not fail visibly ({steps.get(CHECK)})")
    if steps.get(FINAL) is None:
        out.append(f"{CONTROL} {FINAL} never concluded")
    return out


def main(argv: Sequence[str]) -> int:
    jobs = {str(j.get("name")): j for j in fetch_jobs()}
    for name in (HOOKED, CONTROL):
        print(_observed(name, jobs.get(name)))
    problems = hooked_problems(jobs.get(HOOKED)) + control_problems(jobs.get(CONTROL))
    print("EGRESS-FAIL-CLOSED-VERDICT PASS" if not problems else "EGRESS-FAIL-CLOSED-VERDICT FAIL " + "; ".join(problems))
    return 0 if not problems else 1
