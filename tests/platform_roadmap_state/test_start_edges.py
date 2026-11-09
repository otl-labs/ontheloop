"""Tests for typed roadmap start edges (rec-5037): model validation, eligibility, projection, live T4.32."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from scripts.roadmap.platform_roadmap import PlatformRoadmapState, RoadmapDocument, StartEdge, load
from tests.fixtures.platform_roadmap_state import _LIVE_ROADMAP, _doc, _item, _make_state


def _edge(ref: str, once: str = "in_progress") -> dict:
    return {"ref": ref, "once": once}


def _with_edge(dep_status: str, *, edges: list[dict] | None = None, extra_deps: list | None = None) -> PlatformRoadmapState:
    edged = _item("T0.2", depends_on=["T0.1", *(extra_deps or [])])
    edged["start_edges"] = [_edge("T0.1")] if edges is None else edges
    items = [_item("T0.1", status=dep_status), edged]
    if extra_deps:
        items.append(_item(extra_deps[0]))
    return _make_state(items)


def _ids(items) -> set[str]:
    return {i.id for i in items}


def test_start_edge_makes_in_progress_dependency_eligible() -> None:
    without = _with_edge("in_progress", edges=[])
    assert "T0.2" not in _ids(without.eligible_items())
    assert "T0.2" in _ids(without.compute_blocked())
    with_edge = _with_edge("in_progress")
    assert "T0.2" in _ids(with_edge.eligible_items())
    assert "T0.2" not in _ids(with_edge.compute_blocked())


def test_blocked_on_names_dependency_only_while_not_started() -> None:
    state = _with_edge("not_started")
    item = next(i for i in state.compute_blocked() if i.id == "T0.2")
    assert state._blocked_on(item) == ["T0.1"]
    state = _with_edge("in_progress")
    item = next(i for i in state.eligible_items() if i.id == "T0.2")
    assert state._blocked_on(item) == []


def test_start_edge_never_relaxes_not_started_or_edgeless_dependencies() -> None:
    assert "T0.2" not in _ids(_with_edge("not_started").eligible_items())
    state = _with_edge("in_progress", extra_deps=["T0.3"])
    assert "T0.2" not in _ids(state.eligible_items())
    assert "T0.2" in _ids(state.compute_blocked())


@pytest.mark.parametrize(
    ("edges", "message"),
    [
        ([_edge("T0")], "must not be a tier shortcut"),
        ([_edge("T0.9")], "is not in its own depends_on"),
        ([_edge("T0.1"), _edge("T0.1")], "duplicate start_edges ref"),
        ([_edge("T0.1", once="complete")], "once"),
    ],
)
def test_model_rejects_bad_start_edges(edges: list[dict], message: str) -> None:
    edged = _item("T0.2", depends_on=["T0.1"])
    edged["start_edges"] = edges
    with pytest.raises(ValidationError, match=message):
        RoadmapDocument.model_validate(_doc(tier_items=[_item("T0.1"), edged]))


def test_model_rejects_complete_item_with_unfinished_dependency() -> None:
    edged = _item("T0.2", depends_on=["T0.1"], status="complete")
    edged["start_edges"] = [_edge("T0.1")]
    with pytest.raises(ValidationError, match="is complete while depends_on 'T0.1' is not complete"):
        RoadmapDocument.model_validate(_doc(tier_items=[_item("T0.1", status="in_progress"), edged]))
    RoadmapDocument.model_validate(_doc(tier_items=[_item("T0.1", status="complete"), edged]))


def test_completion_guard_covers_tier_shortcut_dependency() -> None:
    edged = _item("T1.2", tier="T1", depends_on=["T0.1", "T0"], status="complete")
    edged["start_edges"] = [_edge("T0.1")]
    items = [_item("T0.1", status="complete"), _item("T0.5"), edged]
    with pytest.raises(ValidationError, match="depends_on 'T0' is not complete"):
        RoadmapDocument.model_validate(_doc(tier_items=items))
    items[1] = _item("T0.5", status="reserved")
    RoadmapDocument.model_validate(_doc(tier_items=items))


def test_projection_carries_start_edges() -> None:
    from scripts.platform_roadmap_state import _item_dict

    state = _with_edge("in_progress")
    d = state.to_preflight_dict()
    assert next(r for r in d["next_eligible"] if r["id"] == "T0.2")["start_edges"] == ["T0.1"]
    blocked = _with_edge("not_started").to_preflight_dict()
    assert next(r for r in blocked["blocked"] if r["id"] == "T0.2")["start_edges"] == ["T0.1"]
    assert _item_dict(state._by_id["T0.1"])["start_edges"] == []


def test_start_edge_model_shape() -> None:
    edge = StartEdge(ref="T0.1", once="in_progress")
    assert edge.note is None


def test_live_t4_32_start_edge_and_eligibility() -> None:
    doc = load(_LIVE_ROADMAP)
    t432 = next(i for i in doc.tier_items if i.id == "T4.32")
    assert [(e.ref, e.once) for e in t432.start_edges] == [("T4.10a", "in_progress")]
    copy = doc.model_copy(deep=True)
    by_id = {i.id: i for i in copy.tier_items}
    by_id["T4.32"].status = "not_started"
    by_id["T4.10a"].status = "not_started"
    state = PlatformRoadmapState(copy)
    assert "T4.32" in _ids(state.compute_blocked())
    by_id["T4.10a"].status = "in_progress"
    state = PlatformRoadmapState(copy)
    assert "T4.32" in _ids(state.eligible_items())
