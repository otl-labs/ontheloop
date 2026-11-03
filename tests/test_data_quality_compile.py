from pathlib import Path

import pytest
import yaml

from scripts.data_quality_compile import _compile_column_test, load_checks


@pytest.fixture
def sample_yaml(tmp_path):
    d = tmp_path / "config" / "data_quality"
    d.mkdir(parents=True)
    f = d / "test.yaml"
    content = {
        "database": "test_db",
        "tables": {
            "table1": {
                "view_suffix": "_v",
                "row_count": {"min": 10, "severity": "error"},
                "recency": {"column": "ts", "error_after_hours": 24},
                "columns": {
                    "col1": {
                        "tests": [
                            "not_null",
                            "unique",
                            {"accepted_values": {"values": ["A", "B"], "severity": "warn"}},
                            {"relationships": {"to_table": "table2", "to_column": "id"}},
                            {"not_null": {"severity": "warn"}},
                            {"unique": {"severity": "error"}},
                        ]
                    }
                },
            }
        },
    }
    f.write_text(yaml.dump(content))
    return f


def test_load_checks(sample_yaml):
    checks, metadata = load_checks(sample_yaml)
    assert metadata["database"] == "test_db"
    assert len(checks) == 8
    recency = [c for c in checks if c.test_type == "recency"][0]
    assert (recency.kind, recency.params) == ("recency_hours", {"column": "ts", "hours": 24})
    row_count = [c for c in checks if c.test_type == "row_count"][0]
    assert (row_count.kind, row_count.params) == ("row_count_min", {"min": 10})


def test_load_checks_with_filter(sample_yaml):
    checks, _ = load_checks(sample_yaml, table_filter="table1")
    assert len(checks) == 8
    checks, _ = load_checks(sample_yaml, table_filter="nonexistent")
    assert len(checks) == 0


def test_unrecognised_test_type_raises():
    """An unrecognised test type is neither SQL-compilable nor declared write-time-only -- it must
    raise rather than silently compiling to nothing (rec-3308, Decision 55 silent-drop class)."""
    with pytest.raises(ValueError, match="unknown"):
        _compile_column_test("db.t", "t", "c", "unknown")
    with pytest.raises(ValueError, match="unknown"):
        _compile_column_test("db.t", "t", "c", {"unknown": {}})


@pytest.mark.parametrize(
    "test_type",
    ["path_syntax", "acceptance_lint", "array_element_format", "min_length", "array_element_reference"],
)
def test_former_write_time_only_types_now_raise(test_type):
    """No write-time test can return to a DQ file: each former write-time-only type is an unrecognised test type (the
    portal enforces these through the writer's own gate over the Class A contract, rec-4158)."""
    with pytest.raises(ValueError, match=f"unrecognised test type '{test_type}'"):
        _compile_column_test("db.t", "t", "c", {test_type: {}})
    with pytest.raises(ValueError, match=f"unrecognised test type '{test_type}'"):
        _compile_column_test("db.t", "t", "c", test_type)


def test_compile_column_test_non_str_non_dict_returns_none():
    """A test entry that is neither a string nor a dict (malformed YAML) falls through to None
    rather than raising -- distinct from the loud-fail path, which only fires for a recognised
    shape (str or dict) carrying an unrecognised name."""
    assert _compile_column_test("db.t", "t", "c", 123) is None


def test_load_checks_enforced_from_yaml(tmp_path):
    """Loader reads enforced: false from dict-form YAML; bare-string defaults to True."""
    d = tmp_path / "config" / "data_quality"
    d.mkdir(parents=True)
    f = d / "test.yaml"
    content = {
        "database": "db",
        "tables": {
            "tbl": {
                "row_count": {"min": 1, "enforced": False},
                "recency": {"column": "ts", "error_after_hours": 24, "enforced": True},
                "columns": {
                    "col": {
                        "tests": [
                            "not_null",
                            {"accepted_values": {"values": ["A"], "enforced": False}},
                            {"not_null": {"enforced": False}},
                        ]
                    }
                },
            }
        },
    }
    f.write_text(yaml.dump(content))
    checks, _ = load_checks(f)

    row_count_check = next(c for c in checks if c.test_type == "row_count")
    recency_check = next(c for c in checks if c.test_type == "recency")
    bare_not_null = next(c for c in checks if c.test_type == "not_null" and c.enforced is True)
    accepted_check = next(c for c in checks if c.test_type == "accepted_values")
    dict_not_null = next(c for c in checks if c.test_type == "not_null" and c.enforced is False)

    assert row_count_check.enforced is False
    assert recency_check.enforced is True
    assert bare_not_null.enforced is True
    assert accepted_check.enforced is False
    assert dict_not_null.enforced is False


def test_load_checks_reads_exclude_before(tmp_path):
    d = tmp_path / "config" / "data_quality"
    d.mkdir(parents=True)
    f = d / "test.yaml"
    content = {
        "database": "db",
        "tables": {
            "tbl": {
                "columns": {
                    "col": {
                        "tests": [
                            {"not_null": {"enforced": False, "exclude_before": "2026-01-01"}},
                        ]
                    }
                }
            }
        },
    }
    f.write_text(yaml.dump(content))
    checks, _ = load_checks(f)
    assert len(checks) == 1
    assert checks[0].exclude_before == "2026-01-01"


def _compile(test):
    return _compile_column_test("db.tbl", "tbl", "col", test)


def test_yaml_tests_compile_to_dq_kinds():
    gate = "2026-01-01"
    cases = [
        ("not_null", "not_null", {"column": "col"}),
        ({"not_null": {"enforced": False, "exclude_before": gate}}, "not_null", {"column": "col", "exclude_before": gate}),
        ("unique", "unique", {"columns": ["col"], "projection": "current"}),
        (
            {"unique": {"enforced": False, "exclude_before": gate}},
            "unique",
            {"columns": ["col"], "projection": "current", "exclude_before": gate},
        ),
        (
            {"unique": {"columns": ["rec_id", "revision"], "projection": "history"}},
            "unique",
            {"columns": ["rec_id", "revision"], "projection": "history"},
        ),
        ({"accepted_values": ["A", "B"]}, "accepted_values", {"column": "col", "values": ["A", "B"]}),
        (
            {"accepted_values": {"values": ["A"], "exclude_before": gate}},
            "accepted_values",
            {"column": "col", "values": ["A"], "exclude_before": gate},
        ),
        ({"pattern": {"value": "^x$"}}, "pattern", {"column": "col", "value": "^x$"}),
        ({"min_value": {"value": 1}}, "min_value", {"column": "col", "value": 1}),
        ({"history_not_ahead_of_current": {"column": "revision"}}, "history_not_ahead_of_current", {"column": "revision"}),
        ("history_not_ahead_of_current", "history_not_ahead_of_current", {"column": "col"}),
    ]
    for test, kind, params in cases:
        check = _compile(test)
        assert (check.kind, check.params) == (kind, params), test
        assert not hasattr(check, "sql")
    assert _compile({"not_null": {"enforced": False, "exclude_before": gate}}).enforced is False
    assert _compile({"accepted_values": ["A"]}).severity == "error"
    rel = _compile({"relationships": {"to_table": "p", "to_column": "id"}})
    assert rel.kind is None and rel.test_type == "relationships"


def test_expression_tests_are_refused():
    for test in ({"expression": {"sql": "col > 0"}}, {"expression": "not_a_dict"}, "expression"):
        with pytest.raises(ValueError, match="unrecognised test type 'expression'"):
            _compile(test)


def test_ops_yaml_declares_no_expression_test():
    spec = yaml.safe_load(open("config/agent/data_quality/ops.yaml", encoding="utf-8"))
    names = {
        next(iter(t)) if isinstance(t, dict) else t
        for table in spec["tables"].values()
        for col in table.get("columns", {}).values()
        for t in col.get("tests", []) or []
    }
    assert "expression" not in names
    checks, _ = load_checks(Path("config/agent/data_quality/ops.yaml"))
    assert checks and all(c.kind is not None or c.test_type == "relationships" for c in checks)


def test_ops_yaml_recommendations_tests_are_judged_one_way():
    """validate_ops_rule_source judges only not_null and accepted_values on ops_recommendations, so no other kind (a
    contract write-time kind such as pattern) may sit there unjudged."""
    spec = yaml.safe_load(open("config/agent/data_quality/ops.yaml", encoding="utf-8"))
    cols = spec["tables"]["ops_recommendations"]["columns"]
    names = {next(iter(t)) if isinstance(t, dict) else t for c in cols.values() for t in c.get("tests", []) or []}
    assert names <= {"not_null", "accepted_values"}


def test_compile_relationships_non_dict_returns_none():
    assert _compile({"relationships": ["invalid"]}) is None


def test_row_count_carries_exclude_before_but_not_in_params(tmp_path):
    f = tmp_path / "test.yaml"
    content = {
        "database": "db",
        "tables": {"tbl": {"row_count": {"min": 1, "enforced": True, "exclude_before": "2026-01-01"}}},
    }
    f.write_text(yaml.dump(content))
    checks, _ = load_checks(f)
    rc = next(c for c in checks if c.test_type == "row_count")
    assert rc.params == {"min": 1} and rc.exclude_before == "2026-01-01"


def test_build_clause8_checks_generates_uniqueness():
    import yaml

    import scripts.data_quality_runner as dq

    spec = yaml.safe_load(open("config/agent/data_quality/ops.yaml", encoding="utf-8"))
    checks = dq.build_clause8_checks(spec, "agent_platform")
    types_ = {c.test_type for c in checks}
    assert "ulid_history_unique" in types_ and "current_merge_key_unique" in types_
    assert all(c.backend == "ducklake" for c in checks)


def test_build_clause8_checks_table_filter():
    import yaml

    import scripts.data_quality_runner as dq

    spec = yaml.safe_load(open("config/agent/data_quality/ops.yaml", encoding="utf-8"))
    # Recs-first slice: only ops_recommendations is clause-8-checked (decisions deferred to Iceberg).
    checks = dq.build_clause8_checks(spec, "agent_platform", table_filter="ops_recommendations")
    assert {c.table for c in checks} == {"ops_recommendations"}
    # A deferred table filter yields no clause-8 checks (it is not on DuckLake this slice).
    assert dq.build_clause8_checks(spec, "agent_platform", table_filter="ops_decisions") == []


def test_tombstone_check_is_a_key_absent_request():
    import scripts.data_quality_runner as dq

    checks = dq.build_tombstone_checks([{"table": "ops_recommendations", "id": "rec-9"}], database="agent_platform")
    assert checks and checks[0].test_type == "tombstone_resurrection"
    assert (checks[0].kind, checks[0].params) == ("key_absent", {"column": "id", "value": "rec-9"})
    assert checks[0].table in dq._OPS_TABLES  # so main() flips it to backend=ducklake
    assert dq.build_tombstone_checks([{"table": "ops_recommendations", "id": "rec-9"}], table_filter="other") == []
    assert dq.build_tombstone_checks([{"table": "", "id": "x"}]) == []
