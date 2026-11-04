"""Pins CD.40's ratification onto Decision 199 and the clause map on Decision 199's dated update line."""

import re
from pathlib import Path

import yaml

from scripts.decisions_md import iter_decision_sections

ROOT = Path(__file__).resolve().parents[2]
DECISIONS = ROOT / "docs" / "DECISIONS.md"
ARCHIVE = ROOT / "docs" / "DECISIONS_ARCHIVE.md"
ROADMAP = ROOT / "docs" / "ROADMAP-PLATFORM.yaml"

REQUIRED_PHRASES = (
    "not a clause-1 or clause-5 note",
    "Decision 78's 2026-09-22 amendment",
    "Decisions 127 and 174",
    "imported-row flag",
    "the audit's ops_sessions, not telemetry_sessions",
    "Decision 213",
    "derived-read-too-costly condition above",
    "rec-4833",
    "snapshot retention",
)
NAMED_DECISIONS = (78, 127, 174, 213)


def _norm(text: str) -> str:
    return " ".join(text.split())


def _find_cd40() -> dict:
    data = yaml.safe_load(ROADMAP.read_text(encoding="utf-8"))
    found: list[dict] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if node.get("id") == "CD.40":
                found.append(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    assert len(found) == 1, found
    return found[0]


def _decision_199_block() -> str:
    for path in (DECISIONS, ARCHIVE):
        if not path.exists():
            continue
        for match, block in iter_decision_sections(path.read_text(encoding="utf-8")):
            if match.group(1) == "199":
                return block
    raise AssertionError("Decision 199 not found")


def test_cd40_ratified_onto_decision_199() -> None:
    cd40 = _find_cd40()
    assert cd40["state"] == "ratified"
    assert cd40["ratified_as"] == "dec-199"
    assert cd40["filed_via"] == "ops_decisions:dec-199"
    assert cd40["gates"] == ["T3.20"]
    assert cd40["affects"] == ["T2.26"]
    assert "detail" not in cd40
    points = cd40["discipline_points"]
    assert len(points) == 1
    assert "clause 1 realized" in points[0]
    assert "Decision 199" in points[0]


def test_decision_199_maps_cd40_clauses() -> None:
    block = _decision_199_block()
    lines = [ln for ln in block.splitlines() if ln.startswith("> **Update (")]
    cd40_lines = [ln for ln in lines if "CD.40" in ln]
    assert len(cd40_lines) == 1, cd40_lines
    line = _norm(cd40_lines[0])
    for phrase in REQUIRED_PHRASES:
        assert phrase in line, phrase
    corpus = DECISIONS.read_text(encoding="utf-8")
    if ARCHIVE.exists():
        corpus += ARCHIVE.read_text(encoding="utf-8")
    for number in NAMED_DECISIONS:
        assert re.search(rf"^## Decision {number}:", corpus, re.MULTILINE), number
