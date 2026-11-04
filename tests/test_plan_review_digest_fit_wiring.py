"""The trusted plan comment step carries the reviewer-digest fit report without ever failing the step."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
TRUSTED = WORKFLOWS / "terraform-plan-trusted.yml"
APPLY = WORKFLOWS / "terraform-apply-sandbox.yml"


def _jobs(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"]


def _comment_step() -> dict:
    steps = _jobs(TRUSTED)["plan"]["steps"]
    matches = [s for s in steps if s.get("name") == "Post the plan comment"]
    assert len(matches) == 1
    return matches[0]


def _fit_line() -> str:
    lines = [ln for ln in _comment_step()["run"].splitlines() if "--fit-report" in ln]
    assert len(lines) == 1
    return lines[0].strip()


def test_fit_report_is_wired_once_and_feeds_the_report_script() -> None:
    body = _comment_step()["run"]
    assert body.count("scripts/ci/terraform_plan_digest.py") == 1
    line = _fit_line()
    assert '--fit-report "$RUNNER_TEMP/plan.json"' in line and line.rstrip().endswith("|| true")
    assert '--fit-file "$RUNNER_TEMP/fit.txt"' in body


def test_apply_job_never_references_fit_report() -> None:
    for job in _jobs(APPLY).values():
        for step in job.get("steps", []):
            assert "--fit-report" not in str(step.get("run", ""))


@pytest.mark.parametrize(
    ("shim", "expected"),
    [("echo 'Reviewer digest: 3 actionable'", "Reviewer digest: 3 actionable"), ("exit 7", "")],
)
def test_substitution_never_fails_the_step(tmp_path: Path, shim: str, expected: str) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    py = bin_dir / "python3"
    py.write_text(f"#!/usr/bin/env bash\n{shim}\n", encoding="utf-8")
    py.chmod(0o755)
    script = f"""set -euo pipefail
{_fit_line()}
cat "$RUNNER_TEMP/fit.txt"
"""
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "RUNNER_TEMP": str(tmp_path)}
    result = subprocess.run(["bash", "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected
