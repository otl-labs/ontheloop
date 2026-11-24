"""Fast-tier gate: config/agent/ci_rca/coverage.yaml and .github/workflows/ci-rca.yml stay in step (rec-5537)."""

from __future__ import annotations

from scripts.checks import registry
from scripts.ci_rca import coverage


@registry.register("validate_ci_rca_coverage", owner="platform")
def validate_ci_rca_coverage(failed: list[str]) -> None:
    cov = coverage.load()
    problems = cov.check()
    for problem in problems:
        print(f"  - {problem}")
    registry.examined(len(cov.steps), unit="ci_rca_steps")
    if problems:
        failed.append("CI RCA coverage table")
    else:
        print(f"  PASS: {len(cov.steps)} ci-rca.yml steps each have exactly one row.")
