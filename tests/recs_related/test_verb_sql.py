"""Executes recs_related's REGISTERED SQL in in-memory DuckDB (Decision 84 I-3, Decision 215 cl.3).

The table is built from resolve_table_spec("ops_recommendations") and {tbl} is substituted as the
VERB_FIELDS parity test does. duckdb is absent from requirements-fast.txt, hence importorskip.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

duckdb = pytest.importorskip("duckdb")

from src.common.ducklake_named_reads import NAMED_READS  # noqa: E402
from src.common.ducklake_scd2_schema import resolve_table_spec  # noqa: E402

pytestmark = pytest.mark.unit

_COLS = ("ulid", "id", "title", "source", "effort", "priority", "status", "file", "context", "created_timestamp")


def _con() -> Any:
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")
    spec = resolve_table_spec("ops_recommendations")
    con.execute("CREATE TABLE t (" + ", ".join(f"{n} {ty}" for n, ty in spec.ordered_columns) + ")")
    return con


def _add(con: Any, rid: str, **kw: Any) -> None:
    row = {
        "ulid": rid,
        "id": rid,
        "title": kw.pop("title", f"title of {rid}"),
        "source": "manual",
        "effort": "S",
        "priority": kw.pop("priority", "medium"),
        "status": kw.pop("status", "open"),
        "file": kw.pop("file", None),
        "context": kw.pop("context", ""),
        "created_timestamp": kw.pop("ts", "2026-01-01T00:00:00+00:00"),
    }
    assert not kw, kw
    con.execute(f"INSERT INTO t ({', '.join(_COLS)}) VALUES ({', '.join('?' for _ in _COLS)})", [row[c] for c in _COLS])


def _run(con: Any, files: str = "", near: str = "", terms: str = "", exclude: str = "", max_rows: str = "20") -> list[dict]:
    sql = NAMED_READS["recs_related"].sql.replace("{tbl}", "t")
    cur = con.execute(sql, [files, near, terms, exclude, max_rows])
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def _ids(rows: list[dict]) -> list[str]:
    return [r["id"] for r in rows]


def _filler(con: Any, n: int, start: int = 1000) -> None:
    for i in range(n):
        _add(con, f"rec-{start + i}", file="unrelated/x.py")


class TestRecsRelatedSql:
    def test_direct_tier_first_then_priority_score_recency(self):
        con = _con()
        _add(con, "rec-1", file="a.py", priority="low")
        _add(con, "rec-2", file="a.py", priority="critical")
        _add(con, "rec-3", file="b.py", priority="critical")
        _add(con, "rec-4", file="a.py", priority="high", ts="2026-01-01T00:00:00+00:00")
        _add(con, "rec-5", file="a.py", priority="high", ts="2026-02-01T00:00:00+00:00")
        _add(con, "rec-6", file="c.py", priority="high")
        _add(con, "rec-7", file="d.py", priority="high")
        rows = _run(con, files="a.py", near="b.py|2,c.py|3,d.py|1")
        # direct first (priority then recency), then near by priority then score
        assert _ids(rows) == ["rec-2", "rec-5", "rec-4", "rec-1", "rec-3", "rec-6", "rec-7"]
        assert [r["direct"] for r in rows] == [True] * 4 + [False] * 3
        assert rows[5]["score"] == 3 and rows[6]["score"] == 1

    def test_score_orders_within_same_priority(self):
        con = _con()
        _add(con, "rec-1", file="a.py")
        _add(con, "rec-2", file="b.py")
        rows = _run(con, near="a.py|1,b.py|4")
        assert _ids(rows) == ["rec-2", "rec-1"]

    def test_directory_anchor_is_never_direct_with_or_without_trailing_slash(self):
        con = _con()
        _add(con, "rec-1", file="docs/contracts")
        _add(con, "rec-2", file="docs/contracts/")
        _add(con, "rec-3", file="docs/contracts/x.yaml")
        assert _ids(_run(con, files="docs/contracts/x.yaml,docs/contracts/y.yaml")) == ["rec-3"]
        assert _ids(_run(con, files="docs/contracts/x.yaml")) == ["rec-3"]
        reached = _run(con, near="docs/contracts|0.5,docs/contracts/|0.5")
        assert sorted(_ids(reached)) == ["rec-1", "rec-2"]
        assert not any(r["direct"] for r in reached)
        assert _ids(_run(con, near="docs/contracts|0.5")) == ["rec-1"]

    def test_empty_and_null_anchor_rows_are_never_returned_for_empty_binds(self):
        con = _con()
        _add(con, "rec-1", file="")
        _add(con, "rec-2", file=None)
        _add(con, "rec-3", file="a.py", status="closed")
        _add(con, "rec-4", file="a.py")
        assert _run(con) == []
        assert _run(con, files=",,") == []
        assert _ids(_run(con, files="a.py")) == ["rec-4"]
        assert _run(con, near="|1") == []

    def test_non_open_rows_are_never_candidates(self):
        con = _con()
        for i, st in enumerate(("closed", "in_progress", "superseded")):
            _add(con, f"rec-{i}", file="a.py", status=st, title="needle thing")
        assert _run(con, files="a.py", near="a.py|1", terms="needle|1") == []

    def test_whole_token_matching_has_no_substring_hit(self):
        con = _con()
        _add(con, "rec-1", title="the planner module", context="")
        _add(con, "rec-2", title="plan", context="")
        _add(con, "rec-3", title="x", context="a plan_runner thing")
        _add(con, "rec-4", title="x", context="see (plan), done")
        _filler(con, 60)
        rows = _run(con, terms="plan|1")
        assert sorted(_ids(rows)) == ["rec-2", "rec-4"]
        assert all(r["matched_terms"] == "plan" for r in rows)

    def test_regexp_escape_makes_a_dot_literal(self):
        con = _con()
        _add(con, "rec-1", title="edit scripts/foo.py now")
        _add(con, "rec-2", title="edit scripts/fooxpy now")
        _filler(con, 60)
        rows = _run(con, terms="scripts/foo.py|1")
        assert _ids(rows) == ["rec-1"]

    def test_term_in_context_matches_and_snippet_is_truncated(self):
        con = _con()
        _add(con, "rec-1", title="t", context="needle " + "z" * 400)
        _filler(con, 30)
        (row,) = _run(con, terms="needle|2")
        assert len(row["snippet"]) == 200 and row["snippet"].startswith("needle")

    def test_common_term_reranks_but_never_admits_alone(self):
        con = _con()
        _add(con, "rec-1", file="a.py", title="alpha common")
        _add(con, "rec-2", file="a.py", title="alpha plain")
        _add(con, "rec-3", file="z.py", title="only common here")
        for i in range(10, 30):
            _add(con, f"rec-{i}", file="q.py", title="filler common")
        rows = _run(con, files="a.py", terms="common|1")
        assert _ids(rows) == ["rec-1", "rec-2"]  # reranked above rec-2; rec-3 and the fillers never admitted
        assert rows[0]["score"] > 0 and rows[1]["score"] == 0

    def test_rare_term_admits_and_is_scored_by_idf(self):
        con = _con()
        _add(con, "rec-1", title="rareword here")
        _filler(con, 29)
        (row,) = _run(con, terms="rareword|2")
        assert row["id"] == "rec-1" and row["direct"] is False
        assert row["score"] == pytest.approx(2 * math.log(1 + 30 / (1 + 1)))
        assert row["matched_terms"] == "rareword"

    def test_exclude_filter_and_post_exclusion_totals(self):
        con = _con()
        for i in range(250):
            _add(con, f"rec-{i:04d}", file="a.py" if i < 200 else "b.py")
        first = _run(con, files="a.py", near="b.py|1", max_rows="100")
        assert len(first) == 100
        assert first[0]["total"] == 250 and first[0]["direct_total"] == 200
        second = _run(con, files="a.py", near="b.py|1", exclude=",".join(_ids(first)), max_rows="100")
        assert not set(_ids(first)) & set(_ids(second))
        assert second[0]["total"] == 150 and second[0]["direct_total"] == 100
        seen = _ids(first) + _ids(second)
        third = _run(con, files="a.py", near="b.py|1", exclude=",".join(seen[:200]), max_rows="100")
        assert third[0]["total"] == 50 and third[0]["direct_total"] == 0

    def test_over_200_excluded_ids_raises_loudly(self):
        con = _con()
        _add(con, "rec-1", file="a.py")
        ok = ",".join(f"rec-x{i}" for i in range(200))
        assert _ids(_run(con, files="a.py", exclude=ok)) == ["rec-1"]
        too_many = ",".join(f"rec-x{i}" for i in range(201))
        with pytest.raises(duckdb.Error, match="exclude_ids over 200"):
            _run(con, files="a.py", exclude=too_many)

    def test_page_is_capped_at_100_rows(self):
        con = _con()
        for i in range(130):
            _add(con, f"rec-{i:04d}", file="a.py")
        assert len(_run(con, files="a.py", max_rows="500")) == 100
        assert len(_run(con, files="a.py", max_rows="7")) == 7

    def test_malformed_near_weight_raises(self):
        con = _con()
        _add(con, "rec-1", file="a.py")
        with pytest.raises(duckdb.Error):
            _run(con, near="a.py|heavy")

    def test_projection_columns_match_the_declared_order(self):
        from src.common.ducklake_reader_client import VERB_FIELDS

        con = _con()
        _add(con, "rec-1", file="a.py")
        (row,) = _run(con, files="a.py")
        assert tuple(row) == VERB_FIELDS["recs_related"]
