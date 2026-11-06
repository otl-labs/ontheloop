"""Anti-drift tests for the decision scout's north-star lane (PLAN-scout-north-star-lane, T4.36 c5, Decision 243 clause 8).

Phrases are matched case-sensitively after collapsing whitespace runs to one space: the skills wrap lines and the
contract is read through yaml.safe_load.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCOUT = ROOT / ".claude" / "skills" / "decision-scout" / "SKILL.md"
CRITIQUE = ROOT / ".claude" / "skills" / "plan-critique" / "SKILL.md"
CONTRACT = ROOT / "docs" / "contracts" / "instruction-architecture.yaml"

RELOCATED = (
    "worse than not running",
    "bloat dilutes the signal",
    "hard against noise",
    "parsing logic never has to branch",
    "cannot self-verify",
    "live-file header count",
    "terse-historical",
    "widened for bounded retrieval",
)


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _flatten(node: Any) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for v in node.values() for s in _flatten(v)]
    if isinstance(node, list):
        return [s for v in node for s in _flatten(v)]
    return []


@pytest.fixture(scope="module")
def scout() -> str:
    return _collapse(SCOUT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def critique() -> str:
    return _collapse(CRITIQUE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def contract() -> dict[str, Any]:
    return yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def retrieval(contract: dict[str, Any]) -> str:
    return _collapse(" ".join(_flatten(contract["gate_subagent_retrieval"])))


class TestScoutSteps:
    def test_step_zero_precedes_step_one_and_names_the_north_star_file(self, scout: str) -> None:
        zero = scout.index("0. **North stars first.**")
        assert zero < scout.index("1. **Triage source.**")
        assert "docs/contracts/north-star.yaml" in scout[zero : scout.index("1. **Triage source.**")]

    def test_step_three_runs_the_shortlist_cli(self, scout: str) -> None:
        assert "scripts.north_star_shortlist" in scout
        assert "serves_only: RELATED at most" in scout

    @pytest.mark.parametrize(
        "phrase", ["never CONTRADICT or BLOCK", "failure_signal or principle", "shares the cap", "carries NS.n"]
    )
    def test_step_eight_north_star_rule(self, scout: str, phrase: str) -> None:
        assert phrase in scout


class TestScoutTemplate:
    def test_north_star_section_sits_inside_the_fenced_template_before_verdict(self) -> None:
        raw = SCOUT.read_text(encoding="utf-8")
        fences = [m.start() for m in re.finditer(r"^```", raw, re.MULTILINE)]
        heading = raw.index("### North-Star Alignment (NORTH STAR)")
        serves = raw.index("- Serves: [NS.n | mission | none]")
        engaged = raw.index("- Engaged: [ids passed in step 3")
        verdict = raw.index("### Verdict")
        assert heading < serves < engaged < verdict
        opens = [f for f in fences if f < heading]
        assert len(opens) % 2 == 1
        assert not [f for f in fences if heading < f < verdict]

    def test_serves_copy_instruction_follows_count_lines(self, scout: str) -> None:
        a = scout.index("copy both count lines verbatim")
        assert a < scout.index("Copy the Serves line too.")

    @pytest.mark.parametrize("phrase", ["no north star in CONTRADICT", "NORTH STAR carries a score"])
    def test_quality_gate_lines(self, scout: str, phrase: str) -> None:
        assert phrase in scout

    def test_standing_cite_pin_survives(self, scout: str) -> None:
        assert "CITE only when omission would meaningfully harm the plan" in scout


class TestRelocatedRationale:
    @pytest.mark.parametrize("phrase", RELOCATED)
    def test_phrase_lives_in_the_contract_not_the_skill(self, scout: str, retrieval: str, phrase: str) -> None:
        assert phrase in retrieval
        assert phrase not in scout

    def test_north_star_lane_names_the_cli_and_decision(self, contract: dict[str, Any]) -> None:
        lane = _collapse(contract["gate_subagent_retrieval"]["north_star_lane"])
        assert "scripts.north_star_shortlist" in lane
        assert "Decision 243" in lane

    def test_layer_two_names_the_north_star_file(self, contract: dict[str, Any]) -> None:
        layer2 = next(layer for layer in contract["layers"] if layer["layer"] == 2)
        assert "docs/contracts/north-star.yaml" in layer2["contains"]


class TestPlanCritique:
    def test_no_stale_score_references(self, critique: str) -> None:
        assert "NS.1-NS.5" not in critique
        assert "Score North Star" not in critique

    def test_step_seven_quotes_the_serves_line(self, critique: str) -> None:
        start = critique.index("7. **North Star alignment is the decision scout's")
        assert "Serves line" in critique[start : start + 250]

    def test_step_two_names_the_north_star_file(self, critique: str) -> None:
        start = critique.index("2. Read `docs/PROJECT_CONTEXT.md`")
        assert "docs/contracts/north-star.yaml" in critique[start : start + 250]
