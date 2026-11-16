"""Tests for scripts/roadmap/plan_edges.py (THE single plan-edge extractor, T4.32 c4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.roadmap.plan_edges import PlanEdgesError, extract_edges, load_roadmap, roadmap_edges

_ROADMAP = {
    "T4.32": {"depends_on": ["T4.10a", "T1.1"], "start_edges": [["T4.10a", "in_progress"]]},
    "T1.1": {"depends_on": [], "start_edges": []},
}
_HEADER = (
    "document:\n  id: ROADMAP-PLATFORM\n  version: 1\n  status: ratified\n  filed_via: ops_decisions:dec-108\ntier_items:\n"
)


def _plan(**fields) -> dict:
    return {"intent": "x", "scope": [{"file": "a"}], **fields}


def test_cites_and_amends_tokens_and_numbers() -> None:
    edges = extract_edges(_plan(cites=["dec:229#4", "dec:217"], amends=["dec:217#6", "dec:5"]), _ROADMAP)
    assert edges.cites == ("dec:217", "dec:229#4")
    assert edges.amends == ("dec:217#6", "dec:5")
    assert edges.decision_numbers == (5, 217, 229)


def test_extra_cites_merge_and_dedupe() -> None:
    edges = extract_edges(_plan(cites=["dec:217"]), _ROADMAP, extra_cites=["dec:217", "dec:9"])
    assert edges.cites == ("dec:217", "dec:9")


def test_introduces_sorted_and_deduplicated() -> None:
    assert extract_edges(_plan(introduces=["b", "a", "b"]), _ROADMAP).introduces == ("a", "b")


@pytest.mark.parametrize(
    ("fields", "expected_items"),
    [
        ({"phase": "T4.32"}, ("T4.32",)),
        ({"phase": "T4.32, c4"}, ("T4.32",)),
        ({"phase": "persona foundation"}, ()),
        ({"closes_criteria": ["T1.1:c2"]}, ("T1.1",)),
        ({"phase": "T4.32", "closes_criteria": ["T4.32:c1", "T4.32:c2"]}, ("T4.32",)),
    ],
)
def test_items_from_closes_criteria_and_phase(fields: dict, expected_items: tuple) -> None:
    assert extract_edges(_plan(**fields), _ROADMAP).items == expected_items


def test_edges_read_from_roadmap_and_none_for_unknown_item() -> None:
    edges = extract_edges(_plan(phase="T4.32", closes_criteria=["T9.9:c1"]), _ROADMAP).edges
    assert edges["T4.32"] == {"depends_on": ["T1.1", "T4.10a"], "start_edges": [["T4.10a", "in_progress"]]}
    assert edges["T9.9"] is None
    assert extract_edges(_plan(phase="T8.8"), _ROADMAP).edges == {"T8.8": None}


@pytest.mark.parametrize(
    ("fields", "field"),
    [
        ({"cites": ["dec-217"]}, "cites"),
        ({"amends": ["217"]}, "amends"),
        ({"cites": "dec:217"}, "cites"),
        ({"amends": "dec:217"}, "amends"),
        ({"introduces": "a"}, "introduces"),
        ({"closes_criteria": "T1.1:c1"}, "closes_criteria"),
        ({"closes_criteria": ["T1.1"]}, "closes_criteria"),
        ({"closes_criteria": ["T1.1:c1:x"]}, "closes_criteria"),
    ],
)
def test_malformed_typed_fields_raise_naming_the_field(fields: dict, field: str) -> None:
    with pytest.raises(PlanEdgesError, match=field):
        extract_edges(_plan(**fields), _ROADMAP)


def test_free_text_is_never_read() -> None:
    plan = _plan(
        context=["Decision 999 and dec:999"],
        execution_steps=["cite dec:999"],
        constraints=["Decision 999"],
        scope=[{"file": "a", "purpose": "Decision 999"}],
    )
    edges = extract_edges(plan, _ROADMAP)
    assert edges.cites == () and edges.amends == () and edges.decision_numbers == ()


def test_load_roadmap_hydrates_and_roadmap_edges_maps_items(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "ROADMAP-PLATFORM.yaml").write_text(
        _HEADER
        + "  - {id: T1.1, tier: T1, name: a}\n"
        + "  - {id: T1.2, tier: T1, name: b, depends_on: [T1.1], start_edges: [{ref: T1.1, once: in_progress}]}\n",
        encoding="utf-8",
    )
    mapping = roadmap_edges(load_roadmap(tmp_path))
    assert mapping["T1.2"] == {"depends_on": ["T1.1"], "start_edges": [["T1.1", "in_progress"]]}
    assert mapping["T1.1"] == {"depends_on": [], "start_edges": []}


def test_missing_roadmap_raises(tmp_path: Path) -> None:
    with pytest.raises(PlanEdgesError, match="cannot read roadmap"):
        load_roadmap(tmp_path)


def test_unparsable_roadmap_raises(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "ROADMAP-PLATFORM.yaml").write_text("document: [unclosed\n", encoding="utf-8")
    with pytest.raises(PlanEdgesError, match="cannot read roadmap"):
        load_roadmap(tmp_path)


def test_schema_invalid_roadmap_raises(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "ROADMAP-PLATFORM.yaml").write_text("document: {}\n", encoding="utf-8")
    with pytest.raises(PlanEdgesError, match="cannot read roadmap"):
        load_roadmap(tmp_path)


def test_an_empty_typed_field_reads_as_no_edges() -> None:
    edges = extract_edges(_plan(cites=None, amends=None, introduces=None, closes_criteria=None), _ROADMAP)
    assert edges.cites == () and edges.items == () and edges.introduces == ()
