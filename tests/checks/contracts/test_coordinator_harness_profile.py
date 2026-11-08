"""Content tests for the Claude projects harness profile and the contract's pointers to it."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

import yaml

from scripts.session import coordinator_copy

_ROOT = Path(__file__).parents[3]
_PROFILE_REL = "docs/contracts/coordinator-harness-claude-projects.yaml"
_CONTRACT_TEXT = (_ROOT / "docs" / "contracts" / "coordinator.yaml").read_text(encoding="utf-8")
_PROFILE_TEXT = (_ROOT / _PROFILE_REL).read_text(encoding="utf-8")
_CONTRACT = yaml.safe_load(_CONTRACT_TEXT)
_PROFILE = yaml.safe_load(_PROFILE_TEXT)
_ID_SHAPES = [
    r"cmsg_[A-Za-z0-9]{6,}",
    r"session_[A-Za-z0-9]{10,}",
    r"trig_[A-Za-z0-9]{6,}",
    r"chan_[A-Za-z0-9]{6,}",
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
]
_KEBAB = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def test_harness_profile_shape() -> None:
    header = _PROFILE["contract"]
    assert header["class"] == "D"
    assert header["id"] == "coordinator-harness-claude-projects"
    assert header["subject"] == "coordinator-harness-claude-projects"
    assert header["evaluator"]["agent_surface"] == "docs/contracts/coordinator.yaml"
    assert set(_CONTRACT["terms"]) <= set(_PROFILE["terms"])
    facts = _PROFILE["facts"]
    assert facts
    ids = [fact["id"] for fact in facts]
    assert len(ids) == len(set(ids))
    for fact in facts:
        assert set(fact) == {"id", "verified", "fact"}
        assert _KEBAB.match(fact["id"]), fact["id"]
        assert _DATE.match(str(fact["verified"])), fact["id"]
    for line in _PROFILE["day_one"]:
        for group in re.findall(r"\bfacts? ([a-z0-9, -]+)\)", line):
            for cited in re.split(r",\s*", group):
                assert cited.strip() in ids, cited
    for shape in _ID_SHAPES:
        assert not re.search(shape, _PROFILE_TEXT), shape
    assert not re.search(r"\b\d{12}\b", _PROFILE_TEXT)


def test_contract_points_at_the_profile() -> None:
    assert "coordinator-harness-claude-projects.yaml" in _CONTRACT_TEXT
    assert "docs/contracts/coordinator-harness-*.yaml" in str(_CONTRACT["surfaces"]["delivery"])
    assert "[contract], [harness] or [repo:<path>]" in _CONTRACT_TEXT.split("Generic rulings land in batches")[1]
    assert "[contract], [harness] or [repo:<path>], not landed" in _CONTRACT_TEXT
    assert "Harness facts: <shared_store>/coordinator/" in str(_CONTRACT["templates"]["standing_instructions"])
    last = _CONTRACT["amendment_log"][-1]
    assert "PLAN-coordinator-harness-profile" in last["summary"]
    assert last["semantic_break"] is False
    assert fnmatch.fnmatchcase(_PROFILE_REL, coordinator_copy.PROFILE_GLOB)
