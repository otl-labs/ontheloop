"""Tests for typed roadmap start edges (rec-5037): model validation, eligibility, projection, live T4.32."""

from __future__ import annotations

from scripts.platform_roadmap_state import _item_dict
from scripts.roadmap.platform_roadmap import PlatformRoadmapState, StartEdge, load
from tests.fixtures.platform_roadmap_state import _LIVE_ROADMAP, _item, _make_state


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


def test_projection_carries_start_edges() -> None:

    state = _with_edge("in_progress")
    d = state.to_preflight_dict()
    assert next(r for r in d["next_eligible"] if r["id"] == "T0.2")["start_edges"] == ["T0.1"]
    blocked = _with_edge("not_started").to_preflight_dict()
    assert next(r for r in blocked["blocked"] if r["id"] == "T0.2")["start_edges"] == ["T0.1"]
    assert _item_dict(state._by_id["T0.1"])["start_edges"] == []


def test_facade_exports_start_edge_and_live_edge_type() -> None:
    t432 = next(i for i in load(_LIVE_ROADMAP).tier_items if i.id == "T4.32")
    assert all(isinstance(e, StartEdge) for e in t432.start_edges)


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
