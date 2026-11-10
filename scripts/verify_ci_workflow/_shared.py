"""VP helper: workflow-YAML access helpers shared across the verify_ci_workflow guard package."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def _load(path: str) -> dict[str, Any]:
    data = yaml.safe_load(Path(path).read_text())
    # PyYAML 1.1 quirk: the bare key `on:` may parse as Python True.
    # Normalise the key so callers can always use data["on"].
    if True in data and "on" not in data:
        data["on"] = data.pop(True)
    return data


def _get_steps_text(job: dict[str, Any]) -> str:
    """Flatten all 'run' values in a job's steps into a single string for substring search."""
    parts = []
    for step in job.get("steps", []):
        if run := step.get("run"):
            parts.append(run)
        if uses := step.get("uses"):
            parts.append(uses)
    return "\n".join(parts)


def _get_step_run_text(job: dict[str, Any], step_name: str) -> str:
    """Return the `run:` block text of one specific named step within a job."""
    for step in job.get("steps", []):
        if step.get("name") == step_name:
            return step.get("run") or ""
    raise AssertionError(f"step {step_name!r} not found in job")


FULL_STEP_IF = "steps.scope.outputs.plans_only != 'true'"
FAST_STEP_IF = "steps.scope.outputs.plans_only == 'true'"


def _runs_validate_full(step: dict[str, Any]) -> bool:
    run = str(step.get("run") or "")
    return "scripts.validate" in run and "--pre" not in run and "--terraform-only" not in run


def _runs_validate_pre(step: dict[str, Any]) -> bool:
    run = str(step.get("run") or "")
    return "scripts.validate" in run and "--pre" in run


def _on_full_path(step: dict[str, Any]) -> bool:
    return "if" not in step or str(step["if"]).strip() == FULL_STEP_IF


def _assert_runtime_lock(job: dict[str, Any], job_name: str) -> None:
    full_steps = [step for step in job.get("steps", []) if _on_full_path(step)]
    steps_text = _get_steps_text({"steps": full_steps})
    cache_steps = [step for step in full_steps if str(step.get("uses", "")).startswith("actions/cache")]
    cache_keys = "\n".join(str(step.get("with", {}).get("key", "")) for step in cache_steps)
    for compiled in ("requirements.txt", "requirements-dev.txt"):
        assert f"pip install -r {compiled}" in steps_text, f"{job_name} does not install compiled {compiled}"
        assert compiled in cache_keys, f"{job_name} dependency cache key omits {compiled}"


def _full_tier_jobs(data: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """The full-tier job set: jobs where any one step's run invokes scripts.validate with neither --pre
    nor --terraform-only (today: ci.yml's main-validate, main-canary.yml's single job). Classified per
    step so main-validate, which also carries a plans-only --pre step, stays in the set.

    Sole derivation (Decision 104) for "does this job run the full validate tier" -- distinct
    from the runtime-lock (_assert_runtime_lock) and canary-shape (_ci_rca._check_canary)
    derivations, which assert different properties over an already-known job.
    """
    return [
        (job_name, job)
        for job_name, job in data.get("jobs", {}).items()
        if any(_runs_validate_full(step) for step in job.get("steps", []))
    ]
