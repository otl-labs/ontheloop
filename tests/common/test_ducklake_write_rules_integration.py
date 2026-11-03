"""Real-DuckLake integration for the writer's row rules (rec-4158 plan B).

pytest.mark.integration: write_scd2 and file_scd2 run against a genuine local DuckLake file catalog ATTACHed as
ops_catalog on the pinned duckdb/ducklake, with the real field_semantics projection. This tier runs only under
-m integration, so a skip would be invisible to the unit CI tier -- the fixture FAILS when the extension cannot load
(rec-4071). It is the only real-catalog proof that a rejection rolls back, leaves the counter where it was, and keeps
an older row with a pre-date violation updatable.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

duckdb = pytest.importorskip("duckdb")

import scripts.contract_rule_census as census  # noqa: E402
import scripts.field_semantics_rule_projection as projection  # noqa: E402
from scripts.contract_rules import declared_rules  # noqa: E402
from src.common import ducklake_dq_checks as dq  # noqa: E402
from src.common import ducklake_runtime as rt  # noqa: E402
from src.common.ducklake_write_rules import RowRuleViolationError  # noqa: E402
from src.row_rules.rules import RowRuleError, RowRules, check_row  # noqa: E402
from tests.fixtures.ducklake_fakes import ops_rec_fields, ops_rec_record  # noqa: E402

pytestmark = pytest.mark.integration

TABLE = "ops_recommendations"
CURRENT = f"{rt.CATALOG_ALIAS}.ops_recommendations_current"
HISTORY = f"{rt.CATALOG_ALIAS}.ops_recommendations_history"
COUNTER = f"{rt.CATALOG_ALIAS}.{rt.ENTITY_COUNTERS_TABLE}"
OLD = datetime(2026, 4, 1, tzinfo=timezone.utc)
MID = datetime(2026, 6, 1, tzinfo=timezone.utc)
NEW = datetime(2026, 10, 1, tzinfo=timezone.utc)


@pytest.fixture
def con(tmp_path: Path, _allow_network_for_integration: None) -> Any:
    connection = duckdb.connect()
    try:
        connection.execute("INSTALL ducklake; LOAD ducklake")
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"ducklake extension could not load -- a skip is a FAIL (rec-4071): {exc}")
    connection.execute("SET ducklake_default_data_inlining_row_limit=0")
    connection.execute("SET TimeZone='UTC'")
    connection.execute(
        f"ATTACH 'ducklake:{tmp_path / 'catalog.ducklake'}' AS {rt.CATALOG_ALIAS} (DATA_PATH '{tmp_path / 'data'}')"
    )
    rt.create_scd2_tables(connection, table=TABLE)
    rt.bootstrap_entity_counter(connection, rt.resolve_table_spec(TABLE))
    yield connection
    connection.close()


def _state(con: Any) -> tuple[int, int, int]:
    count = lambda t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: E731
    return count(HISTORY), count(CURRENT), con.execute(f"SELECT current_value FROM {COUNTER}").fetchone()[0]


def _seed(con: Any, ulid: str, created: datetime, **fields: Any) -> None:
    """Insert a row straight into history and current, bypassing the writer (how a pre-rule row came to exist)."""
    row = {"ulid": ulid, **ops_rec_record(**fields), "created_timestamp": created, "last_updated_timestamp": created}
    columns = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    for table in (HISTORY, CURRENT):
        con.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", list(row.values()))


def test_a_rule_clean_file_scd2_lands(con: Any) -> None:
    before = _state(con)
    result = rt.file_scd2(con, ops_rec_fields(tags=["good-tag"], dependencies=["rec-7"]), table=TABLE)
    assert result.rec_id == "rec-001"
    assert _state(con) == (before[0] + 1, before[1] + 1, before[2] + 1)
    assert con.execute(f"SELECT title FROM {CURRENT} WHERE id = ?", [result.rec_id]).fetchone()[0] == "Test recommendation"


@pytest.mark.parametrize(
    ("override", "rule", "column"),
    [
        ({"title": "short"}, "min_length", "title"),
        ({"context": "c" * 50}, "min_length", "context"),
        ({"effort": "XXL"}, "accepted_values", "effort"),
        ({"source": "not-a-registered-source"}, "accepted_values", "source"),
        ({"source": None}, "not_null", "source"),
        ({"file": "/absolute/path.py"}, "pattern", "file"),
        ({"dependencies": ["rec-1", "not-a-rec"]}, "array_element_format", "dependencies"),
        ({"tags": ["Bad_Tag"]}, "array_element_format", "tags"),
    ],
)
def test_each_rule_kind_rejects_and_leaves_nothing(con: Any, override: dict, rule: str, column: str) -> None:
    rt.file_scd2(con, ops_rec_fields(), table=TABLE)  # a first row, so the counter sits at a non-zero value
    before = _state(con)
    with pytest.raises(RowRuleViolationError) as raised:
        rt.file_scd2(con, ops_rec_fields(**override), table=TABLE)
    assert (raised.value.rule, raised.value.column) == (rule, column)
    assert _state(con) == before  # history, current and the counter are unchanged
    with pytest.raises(RowRuleViolationError):
        rt.write_scd2(con, ops_rec_record(id="rec-900", **override), table=TABLE)
    assert _state(con) == before


def test_an_older_row_with_a_pre_date_violation_stays_updatable(con: Any) -> None:
    _seed(con, "01ULIDOLD", OLD, id="rec-50", context="c" * 50, tags=["Bad_Tag"])
    before = _state(con)
    unchanged = ops_rec_record(id="rec-50", status="closed", context="c" * 50, tags=["Bad_Tag"])
    result = rt.write_scd2(con, unchanged, table=TABLE, require_exists=True)
    assert result.created_timestamp == OLD
    assert con.execute(f"SELECT status FROM {CURRENT} WHERE id = 'rec-50'").fetchone()[0] == "closed"
    after = _state(con)
    assert after[0] == before[0] + 1 and after[1] == before[1]
    for changed in ({"context": "d" * 50}, {"tags": ["Worse_Tag"]}):
        state = _state(con)
        with pytest.raises(RowRuleViolationError):
            rt.write_scd2(con, {**unchanged, **changed}, table=TABLE, require_exists=True)
        assert _state(con) == state
    rt.write_scd2(con, {**unchanged, "context": "e" * 120, "tags": ["good-tag"]}, table=TABLE, require_exists=True)
    assert con.execute(f"SELECT context, tags FROM {CURRENT} WHERE id = 'rec-50'").fetchone() == ("e" * 120, ["good-tag"])


def _engine_ids(con: Any, rule: Any) -> set[str]:
    spec = rt.resolve_table_spec(TABLE)
    columns = {name: {"nullable": True} for name in spec.fields}
    with patch.object(projection, "declared_rules", return_value=(rule,)):
        rules = RowRules.from_projection(projection.project_row_rules(TABLE, columns))
    cursor = con.execute(f"SELECT * FROM {CURRENT}")
    names = [d[0] for d in cursor.description]
    rejected: set[str] = set()
    for values in cursor.fetchall():
        row = dict(zip(names, values, strict=True))
        try:
            check_row(TABLE, row, rules, row["created_timestamp"], prior=row)
        except RowRuleError:
            rejected.add(row["id"])
    return rejected


def test_the_census_sql_and_the_engine_agree_row_for_row(con: Any) -> None:
    seeds = [
        ("A", OLD, {"id": "rec-61", "title": "short", "context": "c" * 50, "file": "/abs", "effort": "XXL"}),
        ("B", MID, {"id": "rec-62", "title": "short", "context": "c" * 50, "tags": ["Bad_Tag"], "source": None}),
        ("C", NEW, {"id": "rec-63", "title": "short", "context": "c" * 50, "tags": ["Bad_Tag"], "dependencies": ["x"]}),
        ("D", NEW, {"id": "rec-64"}),
        ("E", MID, {"id": "rec-65", "priority": "Urgent", "risk": "extreme", "acceptance": "   "}),
    ]
    for ulid, created, fields in seeds:
        _seed(con, f"01ULID{ulid}", created, **fields)
    compared = 0
    for rule in declared_rules(TABLE):
        if rule.exemption is not None:
            continue
        params = census.compile_rule(rule).params
        fragment, binds = dq.row_predicate(rule.kind, rule.table, rule.column, params)
        gate = " AND created_timestamp >= CAST(? AS DATE)" if rule.exclude_before else ""
        sql = f"SELECT id FROM {CURRENT} WHERE ({fragment}){gate}"
        census_ids = {r[0] for r in con.execute(sql, [*binds, *([rule.exclude_before] if gate else [])]).fetchall()}
        assert census_ids == _engine_ids(con, rule), (rule.column, rule.kind)
        compared += 1
    assert compared >= 25
