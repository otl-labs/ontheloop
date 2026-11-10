"""Model-level tests for typed roadmap start edges (rec-5037): validation rules and the completion guard."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from scripts.roadmap.platform_roadmap import RoadmapDocument, StartEdge
from tests.fixtures.platform_roadmap_state import _doc, _item


def _edge(ref: str, once: str = "in_progress") -> dict:
    return {"ref": ref, "once": once}


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


def test_start_edge_model_shape() -> None:
    assert StartEdge(ref="T0.1", once="in_progress").note is None


def test_completion_guard_treats_concrete_reserved_dependency_as_unfinished() -> None:
    edged = _item("T0.2", depends_on=["T0.1"], status="complete")
    edged["start_edges"] = [_edge("T0.1")]
    with pytest.raises(ValidationError, match="depends_on 'T0.1' is not complete"):
        RoadmapDocument.model_validate(_doc(tier_items=[_item("T0.1", status="reserved"), edged]))
