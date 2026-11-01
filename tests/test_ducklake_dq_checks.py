"""Closed dq_check kind registry: rendering, refusals, versions, contract coverage (no duckdb; a fake connection)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

import src.lambdas.ducklake_reader.handler as h
from src.common import ducklake_dq_checks as dq
from src.common import ducklake_runtime as rt
from tests.fixtures.ducklake_reader_fakes import CountCon

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_REC = "ops_recommendations"

_VALID: dict[str, tuple[str, dict[str, Any]]] = {
    "row_count_min": (_REC, {"min": 1}),
    "recency_hours": (_REC, {"column": "last_updated_timestamp", "hours": 168}),
    "not_null": (_REC, {"column": "title", "exclude_before": "2026-05-01"}),
    "accepted_values": (_REC, {"column": "status", "values": ["open", "it's"]}),
    "min_length": (_REC, {"column": "context", "value": 80, "exclude_before": "2026-05-01"}),
    "pattern": ("ops_decisions", {"column": "id", "value": "^dec-[0-9]+$"}),
    "array_element_format": (_REC, {"column": "tags", "pattern": "^[a-z][a-z0-9-]*$"}),
    "not_before": (_REC, {"column": "last_updated_timestamp", "value": "created_timestamp"}),
    "min_value": ("ops_priority_queue", {"column": "rank", "value": 1}),
    "unique": ("ops_execution_plans", {"columns": ["rec_id", "revision"], "projection": "history"}),
    "history_not_ahead_of_current": ("ops_execution_plans", {"column": "revision"}),
    "key_absent": (_REC, {"column": "id", "value": "rec-1"}),
}


def _check(kind: str, table: str | None = None, **overrides: Any) -> dict[str, Any]:
    default_table, params = _VALID[kind]
    return {"check": kind, "table": table or default_table, "params": {**params, **overrides}}


def test_every_kind_renders_one_bound_statement() -> None:
    assert set(_VALID) == set(dq.DQ_KINDS)
    for kind in dq.DQ_KINDS:
        check = dq.validate(_check(kind))
        sql, binds = dq.render(check)
        assert sql.startswith("SELECT") and ";" not in sql
        assert sql.count("?") == len(binds)
        for value in (v for b in binds for v in (b if isinstance(b, list) else [b])):
            assert not isinstance(value, str) or value not in sql
    sql, binds = dq.render(dq.validate(_check("accepted_values")))
    assert binds == ["open", "it's"] and "it's" not in sql
    sql, binds = dq.render(dq.validate(_check("not_null")))
    assert sql.endswith("created_timestamp >= CAST(? AS DATE)") and binds == ["2026-05-01"]
    sql, binds = dq.render(dq.validate(_check("unique", projection="current", exclude_before="2026-05-01")))
    assert "WHERE created_timestamp >= CAST(? AS DATE) GROUP BY" in sql and binds == ["2026-05-01"]
    assert "_history" in dq.render(dq.validate(_check("unique")))[0]


def test_run_check_counts_and_defaults_to_zero() -> None:
    con = CountCon(4)
    assert dq.run_check(con, dq.validate(_check("not_null"))) == 4
    assert con.executed[0][1] == ["2026-05-01"]
    assert dq.run_check(CountCon(0), dq.validate(_check("history_not_ahead_of_current"))) == 0
    none_con = CountCon()
    none_con.fetchone = lambda: None  # type: ignore[method-assign]
    assert dq.run_check(none_con, dq.validate(_check("row_count_min"))) == 0


@pytest.mark.parametrize(
    ("payload", "param"),
    [
        ({"check": "nope", "table": _REC}, "check"),
        ({"check": "row_count_min", "table": _REC}, "min"),
        ({"check": ["x"], "table": _REC}, "check"),
        ({"check": "not_null", "table": None}, "table"),
        ({"check": "not_null", "table": "no_such_table", "params": {"column": "title"}}, "table"),
        ({"check": "not_null", "table": _REC, "params": []}, "params"),
        ({"check": "not_null", "table": _REC, "params": {"column": "title", "sql": "1"}}, "sql"),
        ({"check": "not_null", "table": _REC, "params": {"column": "title", 7: "x"}}, "params"),
        ({"check": "not_null", "table": _REC, "params": {}}, "column"),
        ({"check": "not_null", "table": _REC, "params": {"column": "no_such"}}, "column"),
        ({"check": "not_null", "table": _REC, "params": {"column": "title; DROP"}}, "column"),
        (
            {"check": "not_null", "table": _REC, "params": {"column": "title", "exclude_before": "2026-13-45"}},
            "exclude_before",
        ),
        ({"check": "not_null", "table": _REC, "params": {"column": "title", "exclude_before": 5}}, "exclude_before"),
        ({"check": "row_count_min", "table": _REC, "params": {"min": True}}, "min"),
        ({"check": "row_count_min", "table": _REC, "params": {"min": -1}}, "min"),
        ({"check": "recency_hours", "table": _REC, "params": {"column": "last_updated_timestamp", "hours": "1"}}, "hours"),
        ({"check": "accepted_values", "table": _REC, "params": {"column": "status", "values": []}}, "values"),
        ({"check": "accepted_values", "table": _REC, "params": {"column": "status", "values": [1]}}, "values"),
        ({"check": "min_length", "table": _REC, "params": {"column": "context", "value": 1.5}}, "value"),
        ({"check": "pattern", "table": _REC, "params": {"column": "id", "value": ""}}, "value"),
        ({"check": "pattern", "table": _REC, "params": {"column": "id", "value": "x" * 501}}, "value"),
        ({"check": "array_element_format", "table": _REC, "params": {"column": "tags", "pattern": None}}, "pattern"),
        ({"check": "not_before", "table": _REC, "params": {"column": "created_timestamp", "value": "nope"}}, "value"),
        ({"check": "min_value", "table": "ops_priority_queue", "params": {"column": "rank", "value": float("nan")}}, "value"),
        ({"check": "min_value", "table": "ops_priority_queue", "params": {"column": "rank", "value": True}}, "value"),
        ({"check": "unique", "table": _REC, "params": {"columns": "id"}}, "columns"),
        ({"check": "unique", "table": _REC, "params": {"columns": ["id", "nope"]}}, "columns"),
        ({"check": "unique", "table": _REC, "params": {"columns": ["id"], "projection": "other"}}, "projection"),
    ],
)
def test_refusals_name_the_parameter_before_any_sql(payload: dict[str, Any], param: str) -> None:
    with pytest.raises(dq.DqCheckInputError) as err:
        dq.validate(payload)
    assert err.value.param == param
    assert "DROP" not in str(err.value) and "2026-13" not in str(err.value)


def test_exclude_before_needs_a_created_timestamp_column(monkeypatch: pytest.MonkeyPatch) -> None:
    import dataclasses

    spec = rt.resolve_table_spec("ops_priority_queue")
    fields = {k: v for k, v in spec.fields.items() if k != "created_timestamp"}
    monkeypatch.setattr(dq, "resolve_table_spec", lambda table: dataclasses.replace(spec, fields=fields))
    with pytest.raises(dq.DqCheckInputError) as err:
        dq.validate(
            {"check": "not_null", "table": "ops_priority_queue", "params": {"column": "rank", "exclude_before": "2026-05-01"}}
        )
    assert err.value.param == "exclude_before"


def test_refused_tables_match_the_generic_read_refusals() -> None:
    refused = []
    for table in rt.ops_table_names():
        try:
            h._require_ops_table(table)
        except rt.DuckLakeRuntimeError:
            refused.append(table)
        else:
            continue
        with pytest.raises(dq.DqCheckInputError) as err:
            dq.validate({"check": "row_count_min", "table": table, "params": {"min": 1}})
        assert err.value.param == "table"
    assert "ops_entity_counters" in refused and "telemetry_sessions" in refused
    for table in set(rt.ops_table_names()) - set(refused):
        try:
            dq.validate({"check": "row_count_min", "table": table, "params": {"min": 1}})
        except dq.DqCheckInputError as exc:
            assert "current projection" in exc.reason


def test_append_only_tables_have_no_current_checks() -> None:
    append_only = [t for t in rt.ops_table_names() if _writable(t) and rt.resolve_table_spec(t).current_table is None]
    for table in append_only:
        with pytest.raises(dq.DqCheckInputError):
            dq.validate({"check": "row_count_min", "table": table, "params": {"min": 1}})


def _writable(table: str) -> bool:
    try:
        h._require_ops_table(table)
    except rt.DuckLakeRuntimeError:
        return False
    return True


def test_check_version_moves_with_the_template_only(monkeypatch: pytest.MonkeyPatch) -> None:
    versions = {k: dq.check_version(k) for k in dq.DQ_KINDS}
    assert len(set(versions.values())) == len(versions) and all(len(v) == 16 for v in versions.values())
    assert dq.check_version("not_null") == versions["not_null"]
    monkeypatch.setitem(dq._PREDICATES, "not_null", "{col} IS NULL OR 1 = 0")
    monkeypatch.setitem(dq._TEMPLATES, "not_null", dq._TEMPLATES["not_null"] + " ")
    assert dq.check_version("not_null") != versions["not_null"]
    assert dq.check_version("pattern") == versions["pattern"]


def test_whitespace_class_equals_isspace() -> None:
    import re

    cls = dq.whitespace_class()
    assert dq.whitespace_class() is cls
    # decode the class back into code points and compare with str.isspace over every code point
    members: set[int] = set()
    for lo, hi in re.findall(r"\\x\{([0-9a-f]+)\}(?:-\\x\{([0-9a-f]+)\})?", cls):
        members.update(range(int(lo, 16), int(hi or lo, 16) + 1))
    assert members == {i for i in range(0x110000) if chr(i).isspace()}


def test_contracts_name_every_kind() -> None:
    import re

    reader = yaml.safe_load((_ROOT / "docs/contracts/ducklake_reader.yaml").read_text(encoding="utf-8"))
    engine = yaml.safe_load((_ROOT / "docs/contracts/read-engine.yaml").read_text(encoding="utf-8"))
    ref = reader["verbs"]["dq_check"]["payload_schema_ref"]
    match = re.search(r"check_kinds=\[([^\]]*)\]", ref)
    assert match is not None
    declared = {k.strip() for k in match.group(1).split(",")}
    surface = set(engine["read_surface"]["dq_check"]["check_kinds"])
    assert declared == set(dq.DQ_KINDS) == surface
