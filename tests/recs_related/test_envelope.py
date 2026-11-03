"""Hermetic: the Envelope schema and the post-exclusion completeness arithmetic."""

from __future__ import annotations

import json

import pytest

from scripts.recs_related.envelope import CHANNEL_OK, TIERS, Envelope, Hit, unavailable

pytestmark = pytest.mark.unit


def _hit(rid: str, tier: str = "direct") -> Hit:
    return Hit(
        id=rid,
        title="t",
        priority="high",
        status="open",
        effort="S",
        source="manual",
        file="a.py",
        tier=tier,
        score=1.5,
        why=["direct:a.py"],
        snippet="s",
    )


def _env(hits: list[Hit], total: int, direct_total: int) -> Envelope:
    return Envelope(
        query={"files": ["a.py"], "aliases": [], "terms": []},
        query_files=[{"path": "a.py", "status": "tracked"}],
        hits=hits,
        total=total,
        direct_total=direct_total,
        channels={"imports": CHANNEL_OK},
    )


class TestEnvelope:
    def test_tier_vocabulary(self):
        assert TIERS == ("direct", "near", "term")

    def test_unavailable_status_string(self):
        assert unavailable("shallow clone") == "unavailable: shallow clone"

    def test_field_set_is_the_documented_schema(self):
        out = _env([_hit("rec-1")], 1, 1).to_dict()
        assert set(out) == {
            "query",
            "query_files",
            "hits",
            "total",
            "direct_total",
            "returned",
            "complete",
            "direct_complete",
            "channels",
            "dir_anchor_hits",
            "near_truncated",
            "terms_truncated",
            "next_exclude",
            "notes",
        }
        assert set(out["hits"][0]) == {
            "id",
            "title",
            "priority",
            "status",
            "effort",
            "source",
            "file",
            "tier",
            "score",
            "why",
            "snippet",
        }
        assert set(out["query"]) == {"files", "aliases", "terms"}

    def test_to_json_round_trips(self):
        env = _env([_hit("rec-1"), _hit("rec-2", "near")], 2, 1)
        assert json.loads(env.to_json()) == env.to_dict()
        assert json.loads(env.to_json(indent=None))["returned"] == 2

    def test_complete_is_false_while_candidates_remain_after_the_page(self):
        env = _env([_hit(f"rec-{i}") for i in range(20)], total=50, direct_total=50)
        assert (env.returned, env.complete, env.direct_complete) == (20, False, False)

    def test_complete_on_a_final_page_judged_against_post_exclusion_totals(self):
        env = _env([_hit(f"rec-{i}") for i in range(10)], total=10, direct_total=10)
        assert (env.complete, env.direct_complete) == (True, True)

    def test_multi_page_sequence_where_candidates_remain_after_page_two(self):
        page1 = _env([_hit(f"rec-a{i}") for i in range(20)], total=50, direct_total=30)
        page2 = _env([_hit(f"rec-b{i}") for i in range(20)], total=30, direct_total=10)
        page3 = _env([_hit(f"rec-c{i}") for i in range(10)], total=10, direct_total=0)
        assert [p.complete for p in (page1, page2, page3)] == [False, False, True]

    def test_direct_complete_can_hold_while_complete_does_not(self):
        hits = [_hit(f"rec-d{i}") for i in range(3)] + [_hit("rec-n1", "near")]
        env = _env(hits, total=9, direct_total=3)
        assert env.direct_complete is True and env.complete is False

    def test_empty_result_is_complete(self):
        env = _env([], total=0, direct_total=0)
        assert (env.returned, env.complete, env.direct_complete) == (0, True, True)
