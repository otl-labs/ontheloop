"""main-validate's cancel ceiling is 90 minutes while pr-validate keeps 50 (rec-5726)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

CI_YML = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"


def _jobs() -> dict[str, Any]:
    return yaml.safe_load(CI_YML.read_text(encoding="utf-8"))["jobs"]


def test_main_validate_timeout_minutes_is_90() -> None:
    jobs = _jobs()
    assert jobs["main-validate"]["timeout-minutes"] == 90
    assert jobs["pr-validate"]["timeout-minutes"] == 50
