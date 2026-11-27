"""The full-tier step alone runs with PYTHONUNBUFFERED=1 so the log shows real per-check timing (rec-5727)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from scripts.ci.deploy_gate import FULL_TIER_STEP

CI_YML = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"


def _workflow() -> dict[str, Any]:
    return yaml.safe_load(CI_YML.read_text(encoding="utf-8"))


def test_full_tier_step_sets_pythonunbuffered() -> None:
    steps = _workflow()["jobs"]["main-validate"]["steps"]
    matches = [s for s in steps if s.get("name") == FULL_TIER_STEP]
    assert len(matches) == 1
    assert matches[0].get("env", {}).get("PYTHONUNBUFFERED") == "1"
    assert matches[0]["run"] == "python -m scripts.validate"


def test_unbuffered_is_scoped_to_the_full_tier_step() -> None:
    wf = _workflow()
    assert "PYTHONUNBUFFERED" not in (wf.get("env") or {})
    for job_name in ("main-validate", "pr-validate"):
        job = wf["jobs"][job_name]
        assert "PYTHONUNBUFFERED" not in (job.get("env") or {})
        for step in job["steps"]:
            if step.get("name") == FULL_TIER_STEP:
                continue
            assert "PYTHONUNBUFFERED" not in (step.get("env") or {}), step.get("name")
