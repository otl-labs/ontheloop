"""validate_platform_roadmap parses the roadmap and every plan through parse_yaml (rec-5372)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from scripts.checks.roadmap import validate_platform_roadmap as vpr_module
from scripts.contract_rules import parse_yaml
from tests.fixtures.subprocess_stubs import _mock_completed

_ROADMAP = "document:\n  id: t\n  version: 1\n  status: draft\n  filed_via: pending_log_decision_lambda\n"


def _setup(tmp_path: Path) -> None:
    (tmp_path / "docs" / "plans").mkdir(parents=True)
    (tmp_path / "docs" / "ROADMAP-PLATFORM.yaml").write_text(_ROADMAP, encoding="utf-8")


def _run(tmp_path: Path, spy) -> list[str]:
    failed: list[str] = []
    done = _mock_completed(returncode=0, stdout="")
    with (
        patch("scripts.checks._common.ROOT", tmp_path),
        patch.object(vpr_module.subprocess, "run", return_value=done),
        patch.object(vpr_module, "parse_yaml", spy),
    ):
        vpr_module.validate_platform_roadmap(failed)
    return failed


def test_plan_parses_go_through_parse_yaml(tmp_path: Path) -> None:
    _setup(tmp_path)
    (tmp_path / "docs" / "plans" / "PLAN-a.yaml").write_text("slug: a\n", encoding="utf-8")
    seen: list[str] = []

    def spy(text: str):
        seen.append(text)
        return parse_yaml(text)

    _run(tmp_path, spy)
    assert "slug: a\n" in seen


def test_malformed_plan_still_fails(tmp_path: Path) -> None:
    _setup(tmp_path)
    (tmp_path / "docs" / "plans" / "PLAN-bad.yaml").write_text("slug: [unclosed\n", encoding="utf-8")
    failed = _run(tmp_path, parse_yaml)
    assert failed
