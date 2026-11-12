"""Growth-safe pins for the required-checks Decision and the four dated notes that cite it."""

from __future__ import annotations

import re

from tests.fixtures.merge_overlap_repos import REAL_ROOT

_TEXT = (REAL_ROOT / "docs" / "DECISIONS.md").read_text(encoding="utf-8")
_HEAD = re.compile(r"^## Decision (\d+):.*$", re.M)
_TITLE = "An agent merge is refused unless every required check"


def _spans() -> dict[int, str]:
    marks = list(_HEAD.finditer(_TEXT))
    return {
        int(m.group(1)): _TEXT[m.start() : marks[i + 1].start() if i + 1 < len(marks) else len(_TEXT)]
        for i, m in enumerate(marks)
    }


def _new() -> tuple[int, str]:
    found = [(n, body) for n, body in _spans().items() if body.startswith(f"## Decision {n}: {_TITLE}")]
    assert len(found) == 1
    return found[0]


def test_one_entry_with_envelope_and_citations() -> None:
    _, body = _new()
    assert re.search(r"amends\W+\[?83\]?", body) and "direction" in body
    assert "rec-4080" in body and "expectedHeadSha" in body


def test_four_decisions_hold_one_note_each_naming_the_plan_and_the_decision() -> None:
    number, _ = _new()
    spans = _spans()
    for target in (83, 229, 245, 246):
        notes = [line for line in spans[target].splitlines() if "PLAN-merge-requires-green-checks" in line]
        assert len(notes) == 1, target
        assert re.search(rf"\bDecision {number}\b", notes[0]), target
