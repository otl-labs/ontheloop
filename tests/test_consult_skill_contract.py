"""rec-5644: the consult skill holds the protocol and triggers; live surfaces point at it."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
SKILL = REPO / ".claude/skills/consult/SKILL.md"
POINTER = ".claude/skills/consult/SKILL.md"
SURFACES = [
    ".claude/skills/overseer/SKILL.md",
    ".claude/commands/overseer.md",
    ".claude/skills/planning/SKILL.md",
    ".claude/skills/implement/SKILL.md",
    "docs/contracts/data-modeling-standard.yaml",
    "docs/contracts/implement-scope-boundary.yaml",
    "config/agent/read_sets.yaml",
    "docs/contracts/file-router.yaml",
]


def _norm(path: str) -> str:
    return " ".join((REPO / path).read_text(encoding="utf-8").split()).casefold()


def test_consult_skill_holds_protocol_and_triggers() -> None:
    raw = SKILL.read_text(encoding="utf-8")
    _, front, _body = raw.split("---", 2)
    meta = yaml.safe_load(front)
    assert meta["name"] == "consult"
    desc = " ".join(meta["description"].split())
    for phrase in (
        "Use when: you were dispatched as a consult",
        "the consult-class model",
        "first action",
        "never invokes this skill in its own session",
        "merge_key",
        "rec synthesis",
    ):
        assert phrase in desc, phrase
    body = _norm(".claude/skills/consult/SKILL.md")
    for phrase in (
        "edits nothing",
        "decides nothing",
        "settled consensus",
        "contested",
        "adopt",
        "adapt",
        "reject",
        "design fork",
        "disputed blocking critique finding",
        "second blocking critique finding on one plan row",
        "merge_key",
        "deviation_trigger",
        "workflow adaptation",
        "rec synthesis",
    ):
        assert phrase in body, phrase


def test_live_surfaces_point_at_the_consult_skill() -> None:
    for path in SURFACES:
        assert "advice-consult protocol" not in _norm(path), path
    planning = _norm(".claude/skills/planning/SKILL.md")
    assert (
        "per the `consult` skill (dispatched: an `open_questions` item of the same gate_request); "
        "in session only, also on a second blocking finding on one plan row"
    ) in planning
    for path in (
        ".claude/skills/implement/SKILL.md",
        ".claude/commands/overseer.md",
        "docs/contracts/data-modeling-standard.yaml",
        "docs/contracts/implement-scope-boundary.yaml",
    ):
        assert POINTER.casefold() in _norm(path), path
    runs = yaml.safe_load((REPO / "config/agent/read_sets.yaml").read_text(encoding="utf-8"))["runs"]
    for name in ("planning", "implementing"):
        assert POINTER in [row["path"] for row in runs[name]["files"]], name


def test_no_skill_or_contract_hard_codes_the_consult_model() -> None:
    pattern = re.compile(r'(?<![\w])model:\s*"?fable')
    paths = [*(REPO / ".claude/skills").rglob("*.md"), *(REPO / "docs/contracts").rglob("*.yaml")]
    hits = [str(p.relative_to(REPO)) for p in paths if pattern.search(" ".join(p.read_text(encoding="utf-8").split()))]
    assert hits == []
    assert "project.capability_class.classes" in SKILL.read_text(encoding="utf-8")
