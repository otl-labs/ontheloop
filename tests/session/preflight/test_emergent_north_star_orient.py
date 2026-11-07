"""Orient contract pins for the emergent-north-star Section 7 (hermetic text reads), plus the conftest stub node."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _text(rel: str) -> str:
    return re.sub(r"\s+", " ", (ROOT / rel).read_text(encoding="utf-8"))


COMMAND = _text(".claude/commands/orient.md")
SKILL = _text(".claude/skills/orient/SKILL.md")


def test_command_intent_names_the_advisory_section() -> None:
    assert "and advisory emergent north stars. Writes nothing." in COMMAND


def test_command_step2_names_the_bucket() -> None:
    assert "For Section 7: `emergent_north_star`, the advisory bucket preflight computes" in COMMAND


def test_command_step3_lists_seven_sections() -> None:
    assert "7. Emergent North Stars (advisory)" in COMMAND
    assert "seven-section chat deliverable" in COMMAND


def test_command_section7_rule_carries_its_clauses() -> None:
    for phrase in ("DEGRADED", "UNCOVERED", "cleared by serves", "+N more clusters", "CD.47-r3", "do not re-sort"):
        assert phrase in COMMAND, phrase


def test_skill_points_to_the_command_and_keeps_one_home() -> None:
    assert "seven sections" in SKILL
    assert "### 7. Emergent North Stars" in SKILL
    assert "orient command" in SKILL
    assert "cleared by serves" not in SKILL


def test_conftest_stub_is_live_outside_exempt_modules() -> None:
    from scripts.preflight import emergent_north_star  # noqa: PLC0415

    assert emergent_north_star.preflight_bucket() == {"detector_version": 1, "stubbed": True}
