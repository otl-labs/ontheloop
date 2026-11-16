"""dedup-probe.yml runs its probe from /tmp, so the checkout must be on PYTHONPATH for the scripts.* imports."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "dedup-probe.yml"
_SCRIPT = "/tmp/dedup_probe.py"


def _invocation_problems(workflow: dict) -> list[str]:
    problems = []
    for job_id, job in workflow["jobs"].items():
        for step in job.get("steps", []):
            for line in str(step.get("run", "")).splitlines():
                stripped = line.strip()
                if stripped.endswith(_SCRIPT) and "python" in stripped:
                    if not stripped.startswith('PYTHONPATH="$GITHUB_WORKSPACE" '):
                        problems.append(f"{job_id}: {stripped}")
    return problems


def _invocations(workflow: dict) -> int:
    runs = (str(s.get("run", "")) for j in workflow["jobs"].values() for s in j.get("steps", []))
    return sum(1 for run in runs for line in run.splitlines() if line.strip().endswith(_SCRIPT) and "python" in line)


def test_probe_runs_with_the_checkout_on_pythonpath() -> None:
    workflow = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    assert _invocations(workflow) == 1  # count-coupling-ok: one probe invocation in this workflow
    assert _invocation_problems(workflow) == []


@pytest.mark.parametrize(
    "run",
    [".venv/bin/python /tmp/dedup_probe.py", "PYTHONPATH=/tmp .venv/bin/python /tmp/dedup_probe.py"],
)
def test_red_case_without_the_checkout_on_pythonpath(run: str) -> None:
    workflow = {"jobs": {"probe": {"steps": [{"run": run}]}}}
    assert _invocation_problems(workflow) == ["probe: " + run]
