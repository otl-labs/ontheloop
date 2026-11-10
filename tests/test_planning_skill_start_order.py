"""rec-5037 acceptance: eligibility wording and behaviour agree with typed start edges."""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts.roadmap.platform_roadmap import PlatformRoadmapState, load
from tests.fixtures.platform_roadmap_state import _LIVE_ROADMAP

_ROOT = Path(__file__).resolve().parents[1]
_PLANNING = _ROOT / ".claude" / "skills" / "planning" / "SKILL.md"
_ORIENT = _ROOT / ".claude" / "skills" / "orient" / "SKILL.md"
_OLD_PHRASES = (
    'all depends_on entries have status == "complete"',
    "depends_on are not satisfied",
    "with all depends_on satisfied",
    "(depends_on not satisfied)",
)


def _texts() -> dict[str, str]:
    instructions = yaml.safe_load(_LIVE_ROADMAP.read_text(encoding="utf-8"))["document"]["agent_instructions"]
    return {
        "roadmap agent_instructions": instructions,
        "planning skill": _PLANNING.read_text(encoding="utf-8"),
        "orient skill": _ORIENT.read_text(encoding="utf-8"),
    }


def test_each_text_names_start_edges() -> None:
    for name, text in _texts().items():
        assert "start_edges" in text, name


def test_agent_instructions_name_in_progress_beside_start_edges() -> None:
    text = _texts()["roadmap agent_instructions"]
    assert "in_progress" in text
    assert "named by one of the item's start_edges" in text


def test_no_text_keeps_an_old_phrase() -> None:
    for name, text in _texts().items():
        for phrase in _OLD_PHRASES:
            assert phrase not in text, f"{name}: {phrase}"


def _live_copy():
    doc = load(_LIVE_ROADMAP).model_copy(deep=True)
    by_id = {i.id: i for i in doc.tier_items}
    by_id["T4.32"].status = "not_started"
    by_id["T4.32"].strategic = False
    return doc, by_id


def test_t4_32_eligible_once_t4_10a_in_progress_and_blocked_before() -> None:
    doc, by_id = _live_copy()
    by_id["T4.10a"].status = "not_started"
    assert "T4.32" in {i.id for i in PlatformRoadmapState(doc).compute_blocked()}
    by_id["T4.10a"].status = "in_progress"
    state = PlatformRoadmapState(doc)
    assert "T4.32" in {i.id for i in state.eligible_items()}
    row = next(r for r in state.to_preflight_dict()["next_eligible"] if r["id"] == "T4.32")
    assert row["start_edges"] == ["T4.10a"]
