"""Tests for scripts/roadmap/platform_roadmap.py: facade re-export completeness and CLI entrypoint."""

from __future__ import annotations

import runpy
import sys
from unittest.mock import patch

import pytest

import scripts.platform_roadmap_gate_rules as gate_rules
import scripts.platform_roadmap_models as models
import scripts.platform_roadmap_state as state
import scripts.roadmap.platform_roadmap as facade

# ---------------------------------------------------------------------------
# TestFacadeReexports -- every public symbol + _GATE_HELPERS is importable from the
# facade and identity-equal to its submodule definition (PLAN-sloc-platform-roadmap).
# ---------------------------------------------------------------------------

_GATE_RULES_SYMBOLS = ("GateRuleEvaluator", "GateRuleParser")
_MODELS_SYMBOLS = (
    "GateHelper",
    "DocumentMeta",
    "ExitCriterion",
    "TierItem",
    "CandidateDecision",
    "CrossTierGate",
    "OpenQuestion",
    "KnownGap",
    "NorthStar",
    "FoundationItem",
    "RoadmapDocument",
    "_GATE_HELPERS",
)
_STATE_SYMBOLS = ("PlatformRoadmapState", "load", "compute_followon_state", "compute_state_dict")


class TestFacadeReexports:
    def test_gate_rules_symbols_identity_equal(self) -> None:
        for name in _GATE_RULES_SYMBOLS:
            assert hasattr(facade, name), f"facade missing re-export: {name}"
            assert getattr(facade, name) is getattr(gate_rules, name), f"{name}: facade object is not the gate_rules object"

    def test_models_symbols_identity_equal(self) -> None:
        for name in _MODELS_SYMBOLS:
            assert hasattr(facade, name), f"facade missing re-export: {name}"
            assert getattr(facade, name) is getattr(models, name), f"{name}: facade object is not the models object"

    def test_state_symbols_identity_equal(self) -> None:
        for name in _STATE_SYMBOLS:
            assert hasattr(facade, name), f"facade missing re-export: {name}"
            assert getattr(facade, name) is getattr(state, name), f"{name}: facade object is not the state object"

    def test_north_star_principle_retired(self) -> None:
        assert not hasattr(facade, "NorthStarPrinciple")
        assert not hasattr(models, "NorthStarPrinciple")

    def test_all_declares_every_public_symbol(self) -> None:
        expected = set(_GATE_RULES_SYMBOLS) | set(_MODELS_SYMBOLS) | set(_STATE_SYMBOLS)
        assert expected <= set(facade.__all__), f"missing from __all__: {expected - set(facade.__all__)}"


# ---------------------------------------------------------------------------
# TestComputeStateDictPatchInterception -- proves patch("scripts.roadmap.platform_roadmap.
# compute_state_dict") is observed via facade attribute access: the mechanism every external
# patch site of this attribute (the package conftest, test_main_branches, and the two
# root-level harnesses) depends on.
# ---------------------------------------------------------------------------


class TestComputeStateDictPatchInterception:
    def test_patched_facade_attribute_is_observed_via_module_access(self) -> None:
        sentinel = {"patched": True}
        with patch("scripts.roadmap.platform_roadmap.compute_state_dict", return_value=sentinel):
            result = facade.compute_state_dict("dummy/path.yaml")
        assert result == sentinel


# ---------------------------------------------------------------------------
# TestCliMain -- runpy exercise of the `if __name__ == "__main__":` CLI body
# ---------------------------------------------------------------------------


class TestCliMain:
    # This module imports scripts.roadmap.platform_roadmap at the top (for the re-export identity
    # tests above), so it is already in sys.modules by the time runpy re-executes it below --
    # the standard, benign runpy caveat for this pattern (docs.python.org/3/library/runpy.html).
    @pytest.mark.filterwarnings("ignore:.*found in sys.modules.*:RuntimeWarning")
    def test_runpy_main_prints_pass_and_exits_zero(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["platform_roadmap", "docs/ROADMAP-PLATFORM.yaml"])
        with pytest.raises(SystemExit) as exc_info:
            runpy.run_module("scripts.roadmap.platform_roadmap", run_name="__main__")
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out.strip() == "PASS: docs/ROADMAP-PLATFORM.yaml validates against RoadmapDocument schema."

    @pytest.mark.filterwarnings("ignore:.*found in sys.modules.*:RuntimeWarning")
    def test_runpy_main_prints_fail_and_exits_one(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        # Nonexistent path -> load() raises FileNotFoundError -> the CLI's
        # broad except prints "FAIL: {exc}" and exits 1.
        monkeypatch.setattr(sys, "argv", ["platform_roadmap", "/nonexistent/path/ROADMAP-BOGUS.yaml"])
        with pytest.raises(SystemExit) as exc_info:
            runpy.run_module("scripts.roadmap.platform_roadmap", run_name="__main__")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert captured.out.startswith("FAIL:")
