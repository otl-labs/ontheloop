"""dq_check kinds on a real in-memory DuckDB: planted violations, bound values, the row engine and the replaced SQL."""

from __future__ import annotations

import dataclasses
import itertools
from datetime import datetime
from typing import Any
from unittest.mock import patch

import pytest

duckdb = pytest.importorskip("duckdb", reason="env: duckdb is not installed in the fast tier")

import scripts.field_semantics_rule_projection as projection  # noqa: E402
from scripts.contract_rules import DeclaredRule, declared_rules  # noqa: E402
from src.common import ducklake_dq_checks as dq  # noqa: E402
from src.common.ducklake_scd2_schema import resolve_table_spec  # noqa: E402
from src.row_rules.rules import RowRuleError, RowRules, check_row  # noqa: E402

_TEXT = [
    None,
    "",
    " ",
    "abc",
    "  abc  ",
    "\t\tabcdefghij\t\t",
    "\u00a0\u00a0abc\u00a0",
    "\u3000" * 12,
    "abcdefghij",
    " abcdefghi ",
    "\x85ab\x85",
    "\x1cabcdefghij\x1f",
    "ab\u200bcdefghij",
    "ab\u2003",
    "x" * 80,
    "\u2003" + "y" * 79,
]
_PATHS = [None, "", "a/b.py", "/abs/p", "C:/x", "C:\\x", "a\\b", "c:", "C:x", "1:/x", "ab", ".", "..\\a", "z:\\", "Q:"]
_ARRAYS = [None, [], ["rec-1"], ["rec-1", "rec-22"], ["rec-1", None], [None], ["rec-x"], ["rec-1", ""], ["REC-1"]]
_STAMPS = [
    ("2026-01-01 00:00:00+00", "2026-01-02 00:00:00+00"),
    ("2026-09-01 00:00:00+00", "2026-08-31 00:00:00+00"),
    ("2026-09-10 00:00:00+00", "2026-09-10 00:00:00+00"),
    (None, "2026-09-10 00:00:00+00"),
    ("2026-09-10 00:00:00+00", None),
]


def _rule(kind: str, column: str, params: dict, gate: str | None = None) -> DeclaredRule:
    return DeclaredRule("ops_recommendations", column, kind, params, gate, None)


def _contract_rule(column: str, kind: str) -> DeclaredRule:
    return next(r for r in declared_rules("ops_recommendations") if r.column == column and r.kind == kind)


def _dq_params(rule: DeclaredRule) -> dict[str, Any]:
    if rule.kind == "array_element_format":
        return {"pattern": rule.params["pattern"]}
    return dict(rule.params)


@pytest.fixture()
def conn():
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE t (n INTEGER, txt VARCHAR, path VARCHAR, arr VARCHAR[], "
        "created_timestamp TIMESTAMPTZ, ts_a TIMESTAMPTZ, ts_b TIMESTAMPTZ)"
    )
    rows = []
    for i in range(max(len(_TEXT), len(_PATHS), len(_ARRAYS), len(_STAMPS))):
        a, b = _STAMPS[i % len(_STAMPS)]
        created = ("2026-04-01 00:00:00+00", "2026-05-01 00:00:00+00", "2026-09-07 00:00:00+00")[i % 3]
        rows.append((i, _TEXT[i % len(_TEXT)], _PATHS[i % len(_PATHS)], _ARRAYS[i % len(_ARRAYS)], created, a, b))
    con.executemany("INSERT INTO t VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    yield con, rows
    con.close()


def _violating_ids(con, rule: DeclaredRule) -> set[int]:
    fragment, binds = dq.row_predicate(rule.kind, rule.table, rule.column, _dq_params(rule))
    return {r[0] for r in con.execute(f"SELECT n FROM t WHERE ({fragment})", binds).fetchall()}


def _gated_count(con, rule: DeclaredRule) -> int:
    fragment, binds = dq.row_predicate(rule.kind, rule.table, rule.column, _dq_params(rule))
    sql = f"SELECT COUNT(*) FROM t WHERE ({fragment}) AND created_timestamp >= CAST(? AS DATE)"
    return con.execute(sql, [*binds, rule.exclude_before]).fetchone()[0]


def _stamp(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


def _engine_ids(rows, rule: DeclaredRule) -> set[int]:
    names = ("txt", "path", "arr", "ts_a", "ts_b")
    spec = {name: {"nullable": True} for name in names}
    with patch.object(projection, "declared_rules", return_value=(rule,)):
        rules = RowRules.from_projection(projection.project_row_rules("t", spec))
    rejected: set[int] = set()
    for n, txt, path, arr, created, ts_a, ts_b in rows:
        row = {"txt": txt, "path": path, "arr": arr, "ts_a": _stamp(ts_a), "ts_b": _stamp(ts_b)}
        try:
            check_row("t", row, rules, _stamp(created), prior=row)
        except RowRuleError:
            rejected.add(n)
    return rejected


def test_contract_kinds_agree_with_the_row_engine(conn) -> None:
    con, rows = conn
    rule = _rule("not_null", "txt", {})
    assert _violating_ids(con, rule) == _engine_ids(rows, rule)
    gated = _rule("not_null", "txt", {}, "2026-05-01")
    assert _gated_count(con, gated) == len(_engine_ids(rows, gated))
    values = _rule("accepted_values", "txt", {"values": ["abc", "it's"]})
    assert _violating_ids(con, values) == _engine_ids(rows, values)

    for minimum in (1, 10, 80):
        rule = _rule("min_length", "txt", {"value": minimum})
        assert _violating_ids(con, rule) == _engine_ids(rows, rule)
        assert _violating_ids(con, rule) or minimum == 1
        dated = dataclasses.replace(rule, exclude_before="2026-05-01")
        assert _gated_count(con, dated) == len(_engine_ids(rows, dated))

    contract_rule = _contract_rule("file", "pattern")
    pattern = contract_rule.params["value"]
    rule = dataclasses.replace(contract_rule, column="path", exclude_before=None)
    assert _violating_ids(con, rule) == _engine_ids(rows, rule)
    engine_pattern = RowRules.from_projection(resolve_table_spec("ops_recommendations").fields).patterns["file"]
    con.execute("CREATE TABLE p (n INTEGER, path VARCHAR)")
    alphabet = ["a", "Z", "1", "/", "\\", ":", "."]
    corpus = ["".join(chars) for length in range(1, 5) for chars in itertools.product(alphabet, repeat=length)]
    con.executemany("INSERT INTO p VALUES (?, ?)", list(enumerate(corpus)))
    fragment, binds = dq.row_predicate("pattern", "t", "path", {"value": pattern})
    flagged = {corpus[i] for (i,) in con.execute(f"SELECT n FROM p WHERE {fragment}", binds).fetchall()}
    assert flagged == {path for path in corpus if engine_pattern.fullmatch(path) is None}

    for array_pattern in ("^rec-[0-9]+$", "^[a-z][a-z0-9-]*$"):
        rule = _rule("array_element_format", "arr", {"pattern": array_pattern})
        assert _violating_ids(con, rule) == _engine_ids(rows, rule)
        dated = dataclasses.replace(rule, exclude_before="2026-09-08")
        assert _gated_count(con, dated) == len(_engine_ids(rows, dated))
    flagged_ids = _violating_ids(con, _rule("array_element_format", "arr", {"pattern": "^rec-[0-9]+$"}))
    assert {i for i, r in enumerate(rows) if r[3] == []}.isdisjoint(flagged_ids)
    assert {r[0] for r in rows if r[3] and None in r[3]} <= flagged_ids

    rule = _rule("not_before", "ts_a", {"value": "ts_b"})
    assert _violating_ids(con, rule) == _engine_ids(rows, rule)
    assert _violating_ids(con, rule)


# ---- the ops_catalog fixture the real render() runs against ----

_REC_COLS = (
    "ulid VARCHAR, id VARCHAR, title VARCHAR, status VARCHAR, context VARCHAR, tags VARCHAR[], "
    "created_timestamp TIMESTAMPTZ, last_updated_timestamp TIMESTAMPTZ"
)


@pytest.fixture()
def catalog():
    con = duckdb.connect(":memory:")
    con.execute("CREATE SCHEMA ops_catalog")
    for name in ("ops_recommendations_current", "ops_recommendations_history"):
        con.execute(f"CREATE TABLE ops_catalog.{name} ({_REC_COLS})")
    con.execute("CREATE TABLE ops_catalog.ops_decisions_current (id VARCHAR, created_timestamp TIMESTAMPTZ)")
    con.execute("CREATE TABLE ops_catalog.ops_priority_queue_current (rank INTEGER, created_timestamp TIMESTAMPTZ)")
    plan_cols = "ulid VARCHAR, rec_id VARCHAR, revision INTEGER, created_timestamp TIMESTAMPTZ"
    con.execute(f"CREATE TABLE ops_catalog.ops_execution_plans_current ({plan_cols})")
    con.execute(f"CREATE TABLE ops_catalog.ops_execution_plans_history ({plan_cols})")
    yield con
    con.close()


def _count(con, kind: str, table: str, **params: Any) -> int:
    return dq.run_check(con, dq.validate({"check": kind, "table": table, "params": params}))


def _recs(con, table: str, rows: list[tuple]) -> None:
    con.executemany(f"INSERT INTO ops_catalog.{table} VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)


_OLD, _NEW = "2026-04-01 00:00:00+00", "2026-09-01 00:00:00+00"


def test_every_kind_counts_planted_violations(catalog) -> None:
    _recs(
        catalog,
        "ops_recommendations_current",
        [
            ("u1", "rec-1", None, "open", "a", ["rec-1"], _NEW, _NEW),
            ("u2", "rec-2", "t", "weird", "   ", ["BAD"], _NEW, _OLD),
            ("u3", "rec-3", "t", "open", "x" * 90, [], _OLD, _NEW),
            ("u4", "rec-3", "t", None, None, None, _NEW, _NEW),
        ],
    )
    rec = "ops_recommendations"
    assert _count(catalog, "row_count_min", rec, min=5) == 1 and _count(catalog, "row_count_min", rec, min=4) == 0
    assert _count(catalog, "recency_hours", rec, column="last_updated_timestamp", hours=1) == 1
    assert _count(catalog, "recency_hours", rec, column="last_updated_timestamp", hours=10**7) == 0
    assert _count(catalog, "not_null", rec, column="title") == 1
    assert _count(catalog, "not_null", rec, column="title", exclude_before="2026-05-01") == 1
    assert _count(catalog, "accepted_values", rec, column="status", values=["open"]) == 1
    assert _count(catalog, "min_length", rec, column="context", value=10) == 2
    assert _count(catalog, "pattern", rec, column="id", value="^rec-[0-2]$") == 2
    assert _count(catalog, "array_element_format", rec, column="tags", pattern="^rec-[0-9]+$") == 1
    assert _count(catalog, "not_before", rec, column="last_updated_timestamp", value="created_timestamp") == 1
    assert _count(catalog, "unique", rec, columns=["id"]) == 1
    assert _count(catalog, "unique", rec, columns=["id"], exclude_before="2026-06-01") == 0
    assert _count(catalog, "key_absent", rec, column="id", value="rec-3") == 2
    catalog.execute("INSERT INTO ops_catalog.ops_priority_queue_current VALUES (0, ?), (5, ?), (NULL, ?)", [_NEW] * 3)
    assert _count(catalog, "min_value", "ops_priority_queue", column="rank", value=1) == 1
    catalog.execute("INSERT INTO ops_catalog.ops_execution_plans_current VALUES ('a', 'r1', 1, ?)", [_NEW])
    catalog.execute(
        "INSERT INTO ops_catalog.ops_execution_plans_history VALUES ('a', 'r1', 1, ?), ('b', 'r1', 2, ?)", [_NEW, _NEW]
    )
    plan = "ops_execution_plans"
    assert _count(catalog, "history_not_ahead_of_current", plan, column="revision") == 1
    assert _count(catalog, "unique", plan, columns=["rec_id", "revision"], projection="history") == 0
    assert _count(catalog, "unique", plan, columns=["rec_id"], projection="history") == 1


def test_values_are_bound_never_spliced(catalog) -> None:
    hostile = "x'); DROP TABLE ops_catalog.ops_recommendations_current; --"
    _recs(catalog, "ops_recommendations_current", [("u1", hostile, "t", "open", "c", [], _NEW, _NEW)])
    assert _count(catalog, "accepted_values", "ops_recommendations", column="id", values=[hostile]) == 0
    assert _count(catalog, "key_absent", "ops_recommendations", column="id", value=hostile) == 1
    assert _count(catalog, "pattern", "ops_recommendations", column="id", value="^x'.*--$") == 0
    assert _count(catalog, "row_count_min", "ops_recommendations", min=1) == 0


# ---- the SQL each runner kind replaces, frozen as literals so the proof survives PR B ----

_ORACLE = {
    "not_null": "SELECT COUNT(*) AS violation FROM {tbl} WHERE title IS NULL AND created_timestamp >= DATE('2026-05-01')",
    "accepted_values": (
        "SELECT COUNT(*) AS violation FROM {tbl} WHERE status IS NOT NULL AND status NOT IN ('open', 'closed') "
        "AND created_timestamp >= DATE('2026-05-01')"
    ),
    "unique_gated": (
        "SELECT COUNT(*) AS violation FROM (SELECT id, COUNT(*) AS n FROM {tbl} "
        "WHERE created_timestamp >= DATE('2026-05-01') GROUP BY id HAVING COUNT(*) > 1)"
    ),
    "unique": "SELECT COUNT(*) AS violation FROM (SELECT id, COUNT(*) AS n FROM {tbl} GROUP BY id HAVING COUNT(*) > 1)",
    "ulid_history": (
        "SELECT COUNT(*) AS violation FROM (SELECT ulid, COUNT(*) n FROM {hist} GROUP BY ulid HAVING COUNT(*) > 1) d"
    ),
    "key_absent": "SELECT COUNT(*) AS violation FROM {tbl} WHERE id = 'rec-3'",
    "pattern": "SELECT COUNT(*) AS violation FROM {tbl} WHERE NOT (regexp_matches(id, '^rec-[0-9]+$'))",
    "row_count": "SELECT CASE WHEN cnt < 9 THEN 1 ELSE 0 END AS violation FROM (SELECT COUNT(*) AS cnt FROM {tbl})",
    "recency": (
        "SELECT CASE WHEN date_diff('hour', MAX(last_updated_timestamp), CURRENT_TIMESTAMP) > 168 "
        "THEN 1 ELSE 0 END AS violation FROM {tbl}"
    ),
    "min_value": "SELECT COUNT(*) AS violation FROM {tbl} WHERE NOT (rank >= 1)",
    "composite_unique": (
        "SELECT COUNT(*) AS violation FROM {tbl} WHERE NOT (NOT EXISTS "
        "(SELECT 1 FROM {hist} GROUP BY rec_id, revision HAVING COUNT(*) > 1))"
    ),
    "ahead": (
        "SELECT COUNT(*) AS violation FROM {tbl} WHERE NOT (NOT EXISTS "
        "(SELECT 1 FROM {tbl} cur JOIN {hist} h ON h.rec_id = cur.rec_id WHERE h.revision > cur.revision))"
    ),
}


def _old(con, name: str, table: str) -> int:
    sql = _ORACLE[name].replace("{tbl}", f"ops_catalog.{table}_current").replace("{hist}", f"ops_catalog.{table}_history")
    return con.execute(sql).fetchone()[0]


def _seed_recs(con) -> None:
    rows = [
        ("u1", "rec-1", None, "open", "a", [], _NEW, _NEW),
        ("u2", "rec-2", "t", "weird", "b", [], _OLD, _OLD),
        ("u3", "rec-2", "t", None, "c", [], _NEW, _NEW),
        ("u3", "rec-x", "t", "closed", "d", [], _NEW, _NEW),
        ("u5", None, None, "open", None, [], _OLD, _NEW),
    ]
    _recs(con, "ops_recommendations_current", rows)
    _recs(con, "ops_recommendations_history", rows)


def test_kinds_match_the_sql_they_replace(catalog) -> None:
    _seed_recs(catalog)
    rec = "ops_recommendations"
    gate = "2026-05-01"
    assert _count(catalog, "not_null", rec, column="title", exclude_before=gate) == _old(catalog, "not_null", rec)
    assert _count(catalog, "accepted_values", rec, column="status", values=["open", "closed"], exclude_before=gate) == _old(
        catalog, "accepted_values", rec
    )
    assert _count(catalog, "unique", rec, columns=["id"], exclude_before=gate) == _old(catalog, "unique_gated", rec)
    assert _count(catalog, "unique", rec, columns=["id"]) == _old(catalog, "unique", rec)
    assert _count(catalog, "unique", rec, columns=["ulid"], projection="history") == _old(catalog, "ulid_history", rec) == 1
    assert _count(catalog, "key_absent", rec, column="id", value="rec-3") == _old(catalog, "key_absent", rec)
    assert _count(catalog, "pattern", rec, column="id", value="^rec-[0-9]+$") == _old(catalog, "pattern", rec) == 1
    assert _count(catalog, "row_count_min", rec, min=9) == _old(catalog, "row_count", rec) == 1
    assert _count(catalog, "recency_hours", rec, column="last_updated_timestamp", hours=168) == _old(catalog, "recency", rec)
    catalog.execute("INSERT INTO ops_catalog.ops_priority_queue_current VALUES (0, ?), (3, ?), (NULL, ?)", [_NEW] * 3)
    assert _count(catalog, "min_value", "ops_priority_queue", column="rank", value=1) == _old(
        catalog, "min_value", "ops_priority_queue"
    )


def _plans(con, current: list[tuple], history: list[tuple]) -> None:
    con.execute("DELETE FROM ops_catalog.ops_execution_plans_current")
    con.execute("DELETE FROM ops_catalog.ops_execution_plans_history")
    for table, rows in (("current", current), ("history", history)):
        for row in rows:
            con.execute(f"INSERT INTO ops_catalog.ops_execution_plans_{table} VALUES (?, ?, ?, ?)", list(row))


def test_not_exists_replacements_agree_on_zero_and_nonzero(catalog) -> None:
    plan = "ops_execution_plans"
    clean = [("a", "r1", 1, _NEW), ("b", "r2", 1, _NEW)]
    fixtures = [
        (clean, clean),
        (clean, clean + [("c", "r1", 1, _NEW)]),
        (clean, clean + [("c", "r1", 2, _NEW)]),
        ([("a", "r1", 3, _NEW)], [("a", "r1", 3, _NEW), ("b", "r1", 2, _NEW)]),
    ]
    for current, history in fixtures:
        _plans(catalog, current, history)
        unique = _count(catalog, "unique", plan, columns=["rec_id", "revision"], projection="history")
        ahead = _count(catalog, "history_not_ahead_of_current", plan, column="revision")
        assert (unique > 0) == (_old(catalog, "composite_unique", plan) > 0)
        assert (ahead > 0) == (_old(catalog, "ahead", plan) > 0)


def test_the_stricter_case_current_empty_with_a_history_duplicate(catalog) -> None:
    _plans(catalog, [], [("a", "r1", 1, _NEW), ("b", "r1", 1, _NEW)])
    assert _old(catalog, "composite_unique", "ops_execution_plans") == 0
    assert _count(catalog, "unique", "ops_execution_plans", columns=["rec_id", "revision"], projection="history") == 1
