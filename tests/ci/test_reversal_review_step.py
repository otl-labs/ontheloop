"""The reversal-review step of convergence-health.yml (rec-5264)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "convergence-health.yml"
GATE = "${{ !cancelled() && github.ref == 'refs/heads/main' }}"
NAME = "File reversal reviews (rec-5264)"


def _steps() -> list[dict[str, Any]]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["health-check"]["steps"]


def _step() -> dict[str, Any]:
    return next(s for s in _steps() if s.get("name") == NAME)


def test_step_runs_the_sensor_before_the_capacity_steps() -> None:
    names = [s.get("name") for s in _steps()]
    assert names.index(NAME) == names.index("Assert scheduled loop liveness (LSA-02)") + 1
    assert names.index(NAME) < names.index("Measure Actions capacity (rec-5022)")


def test_step_is_gated_on_main_and_unless_cancelled() -> None:
    assert _step()["if"] == GATE


def test_step_command_and_diagnose_dry_run_mapping() -> None:
    step = _step()
    lines = step["run"].splitlines()
    assert lines[0] == "set -euo pipefail"
    assert ".venv/bin/python -m scripts.preflight.reversal_reviews" in lines[1]
    assert "${CONVERGENCE_HEALTH_DIAGNOSE:+--dry-run}" in lines[1]
    assert step["env"]["CONVERGENCE_HEALTH_DIAGNOSE"] == "${{ inputs.diagnose && '1' || '' }}"
    assert "continue-on-error" not in step


def test_header_states_the_alarm_only_invariants() -> None:
    header = " ".join(WORKFLOW.read_text(encoding="utf-8").split("\non:\n", 1)[0].replace("#", " ").split())
    assert "rec-5264" in header and "never closes or edits a rec" in header
