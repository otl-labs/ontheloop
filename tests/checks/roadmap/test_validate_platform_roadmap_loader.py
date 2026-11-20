"""validate_platform_roadmap parses the roadmap and plans through the shared libyaml loader (rec-5372)."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from scripts.checks._common import ROOT
from scripts.checks.roadmap import validate_platform_roadmap as mod


def test_roadmap_and_plan_parses_go_through_parse_yaml() -> None:
    failed: list[str] = []
    with mock.patch.object(mod, "parse_yaml", wraps=mod.parse_yaml) as spy:
        mod.validate_platform_roadmap(failed)
    plans = len(list((Path(ROOT) / "docs" / "plans").glob("PLAN-*.yaml")))
    assert spy.call_count >= plans
    assert failed == []
