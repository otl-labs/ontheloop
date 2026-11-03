"""Mirror test for src/row_rules/rules.py: every rule kind rejects with table, rule and column; exemptions; the module
holds no table literal. Stdlib only (fast tier)."""

from __future__ import annotations

import ast
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import src.row_rules.rules as rules_module
from src.row_rules.rules import WAIVABLE_KINDS, RowRuleError, RowRules, RuleProjectionError, check_row

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
CREATED = T0 + timedelta(seconds=10)
TABLE = "demo"

COLUMNS = {
    "kind": {"role": "input", "sql_type": "VARCHAR", "nullable": False, "accepted_values": ["a", "b"]},
    "mode": {"role": "input", "sql_type": "VARCHAR", "nullable": True},
    "needed": {"role": "input", "sql_type": "VARCHAR", "nullable": True, "required_when": {"kind": ["a"], "mode": ["x"]}},
    "digest": {"role": "input", "sql_type": "VARCHAR", "nullable": True, "pattern": "^[0-9a-f]{4}$"},
    "started": {"role": "input", "sql_type": "TIMESTAMP WITH TIME ZONE", "nullable": True},
    "happened": {
        "role": "input",
        "sql_type": "TIMESTAMP WITH TIME ZONE",
        "nullable": True,
        "not_before": "started",
        "max_after_write_seconds": 300,
    },
    "total": {"role": "input", "sql_type": "BIGINT", "nullable": True},
    "part": {
        "role": "input",
        "sql_type": "BIGINT",
        "nullable": True,
        "at_most": "total",
        "null_or_zero_when": {"mode": ["none"]},
    },
    "inline": {"role": "input", "sql_type": "VARCHAR", "nullable": True, "representation_of": ["sha", "size"]},
    "uri": {"role": "input", "sql_type": "VARCHAR", "nullable": True, "representation_of": ["sha", "size"]},
    "why": {"role": "input", "sql_type": "VARCHAR", "nullable": True},
    "sha": {"role": "input", "sql_type": "VARCHAR", "nullable": True},
    "size": {"role": "input", "sql_type": "BIGINT", "nullable": True},
}
TABLE_RULES = {
    "exactly_one_of": [["inline", "uri", "why"]],
    "payload": {
        "inline": "inline",
        "uri": "uri",
        "sha": "sha",
        "size": "size",
        "threshold": 10,
        "cap": 100,
        "integrity": True,
    },
}


def make_rules(**table_overrides) -> RowRules:
    return RowRules.from_projection(COLUMNS, {**TABLE_RULES, **table_overrides})


def good(**over):
    row = {
        "kind": "b", "started": T0, "happened": T0 + timedelta(seconds=1), "total": 5, "part": 3, "mode": "y",
        "inline": "hello", "sha": hashlib.sha256(b"hello").hexdigest(), "size": 5,
    }  # fmt: skip
    row.update(over)
    return row


def violation(row, rules=None, created=CREATED) -> RowRuleError:
    with pytest.raises(RowRuleError) as raised:
        check_row(TABLE, row, rules or make_rules(), created)
    return raised.value


def test_a_conforming_row_passes() -> None:
    check_row(TABLE, good(), make_rules(), CREATED)


def test_each_rule_kind_rejects_with_table_rule_and_column() -> None:
    cases = [
        ({"kind": None}, "not_null", "kind"),
        ({"kind": "z"}, "accepted_values", "kind"),
        ({"kind": "a", "mode": "x"}, "required_when", "needed"),
        ({"digest": "XYZ1"}, "pattern", "digest"),
        ({"happened": T0 - timedelta(seconds=1)}, "not_before", "happened"),
        ({"part": 6}, "at_most", "part"),
        ({"mode": "none", "part": 1}, "null_or_zero_when", "part"),
        ({"happened": CREATED + timedelta(seconds=301)}, "max_after_write_seconds", "happened"),
        ({"inline": None, "sha": None}, "exactly_one_of", "inline"),
    ]
    for override, rule, column in cases:
        error = violation(good(**override))
        assert (error.table, error.rule, error.column) == (TABLE, rule, column), override
        assert TABLE in str(error) and rule in str(error) and column in str(error)


def test_error_message_never_carries_a_value() -> None:
    leaked = "unexpected-value-xyz"
    error = violation(good(kind=leaked))
    assert leaked not in str(error) and error.rule == "accepted_values"


def test_required_when_and_exactly_one_of_with_omission() -> None:
    check_row(TABLE, good(kind="a", mode="y"), make_rules(), CREATED)
    check_row(TABLE, good(kind="a", mode="x", needed="present"), make_rules(), CREATED)
    check_row(TABLE, good(inline=None, why="oversize", sha="0" * 64, size=999999), make_rules(), CREATED)
    two = violation(good(uri="k/1", size=50))
    assert two.rule == "exactly_one_of"
    none = violation(good(inline=None, why=None))
    assert none.rule == "exactly_one_of"


def test_ordering_and_write_time_skew_are_monotone_on_retry() -> None:
    rules = make_rules()
    edge = good(happened=CREATED + timedelta(seconds=300))
    check_row(TABLE, edge, rules, CREATED)
    assert violation(good(happened=CREATED + timedelta(seconds=300, milliseconds=1))).rule == "max_after_write_seconds"
    for later in (CREATED + timedelta(seconds=1), CREATED + timedelta(hours=3)):
        check_row(TABLE, edge, rules, later)
    check_row(TABLE, good(happened=T0), rules, CREATED)
    check_row(TABLE, good(happened=None, started=None), rules, CREATED)


def test_content_integrity_threshold_legs_and_cap() -> None:
    rules = make_rules()
    assert violation(good(sha="0" * 64)).rule == "integrity"
    assert violation(good(size=6)).rule == "integrity"
    assert violation(good(inline="hello world!", sha=hashlib.sha256(b"hello world!").hexdigest(), size=12)).rule == (
        "inline_within_threshold"
    )
    assert violation(good(inline=None, uri="t/p/s/x", size=10)).rule == "uri_above_threshold"
    check_row(TABLE, good(inline=None, uri="t/p/s/x", size=11), rules, CREATED)
    check_row(TABLE, good(inline=None, uri="t/p/s/x", size=100), rules, CREATED)
    assert violation(good(inline=None, uri="t/p/s/x", size=101)).rule == "within_cap"
    check_row(TABLE, good(inline=None, why="oversize", sha="f" * 64, size=5_000), rules, CREATED)
    check_row(TABLE, good(size=None), make_rules(payload={**TABLE_RULES["payload"], "integrity": False}), CREATED)
    uncapped = make_rules(payload={**TABLE_RULES["payload"], "cap": None})
    check_row(TABLE, good(inline=None, uri="t/p/s/x", size=10**9), uncapped, CREATED)
    plain = make_rules(payload={**TABLE_RULES["payload"], "integrity": False})
    check_row(TABLE, good(sha="any", size=5), plain, CREATED)
    assert violation(good(inline="x" * 101, sha="any", size=101), plain).rule == "inline_within_threshold"


def test_declared_exemption_is_honoured_and_named() -> None:
    long_text = "x" * 40
    row = good(inline=long_text, sha=hashlib.sha256(long_text.encode()).hexdigest(), size=40)
    assert violation(row).rule == "inline_within_threshold"
    exempt = {k: dict(v) for k, v in COLUMNS.items()}
    exempt["inline"]["write_time_exemptions"] = {"inline_within_threshold": {"reason": "route lands later", "owner": "rec-1"}}
    rules = RowRules.from_projection(exempt, TABLE_RULES)
    assert rules.exemptions == {"inline_within_threshold": ("route lands later", "rec-1")}
    check_row(TABLE, row, rules, CREATED)
    assert violation(good(sha="0" * 64), rules).rule == "integrity"
    over = good(inline="y" * 200, sha=hashlib.sha256(b"y" * 200).hexdigest(), size=200)
    assert violation(over, rules).rule == "within_cap"
    every = {k: dict(v) for k, v in COLUMNS.items()}
    every["inline"]["write_time_exemptions"] = {
        leg: {"reason": "r", "owner": "o"} for leg in ("inline_within_threshold", "within_cap", "integrity")
    }
    every["uri"]["write_time_exemptions"] = {"uri_above_threshold": {"reason": "r", "owner": "o"}}
    open_rules = RowRules.from_projection(every, TABLE_RULES)
    check_row(TABLE, good(sha="0" * 64, size=1), open_rules, CREATED)
    check_row(TABLE, good(inline=None, uri="k", size=1), open_rules, CREATED)
    check_row(TABLE, good(inline="z" * 200, size=200), open_rules, CREATED)
    check_row(TABLE, good(inline=None, uri="k", size=500), open_rules, CREATED)


def test_column_bound_and_conditional_zero() -> None:
    check_row(TABLE, good(part=5, total=5), make_rules(), CREATED)
    check_row(TABLE, good(part=9, total=None), make_rules(), CREATED)
    check_row(TABLE, good(part=None, total=1), make_rules(), CREATED)
    assert violation(good(part=6, total=5)).rule == "at_most"
    check_row(TABLE, good(mode="none", part=None), make_rules(), CREATED)
    check_row(TABLE, good(mode="none", part=0), make_rules(), CREATED)
    check_row(TABLE, good(mode="y", part=4), make_rules(), CREATED)
    assert violation(good(mode="none", part=2)).rule == "null_or_zero_when"


def test_from_projection_fails_closed_on_malformed_input() -> None:
    def project(columns=None, table_rules=None):
        return RowRules.from_projection(columns or COLUMNS, table_rules)

    with pytest.raises(RuleProjectionError, match="unknown table rule"):
        project(table_rules={"nonsense": 1})
    with pytest.raises(RuleProjectionError, match="unknown rule key"):
        project({"c": {"role": "input", "sql_type": "VARCHAR", "nullable": True, "mystery": 1}})
    for bad in (-1, "300", 1.5):
        with pytest.raises(RuleProjectionError, match="max_after_write_seconds"):
            project({"c": {"nullable": True, "max_after_write_seconds": bad}})
    with pytest.raises(RuleProjectionError, match="unknown or duplicate leg"):
        project({"c": {"nullable": True, "write_time_exemptions": {"made_up": {"reason": "r", "owner": "o"}}}})
    with pytest.raises(RuleProjectionError, match="needs a reason and an owner"):
        project({"c": {"nullable": True, "write_time_exemptions": {"integrity": {"reason": "r"}}}})
    both = {
        "a": {"nullable": True, "write_time_exemptions": {"integrity": {"reason": "r", "owner": "o"}}},
        "b": {"nullable": True, "write_time_exemptions": {"integrity": {"reason": "r", "owner": "o"}}},
    }
    with pytest.raises(RuleProjectionError, match="unknown or duplicate leg"):
        project(both)
    with pytest.raises(RuleProjectionError, match="exactly_one_of names unknown"):
        project(table_rules={"exactly_one_of": [["kind", "ghost"]]})
    for bad_condition in ({}, "kind", {"kind": []}, {"kind": "a"}):
        with pytest.raises(RuleProjectionError, match="required_when"):
            project({"c": {"nullable": True, "required_when": bad_condition}})
    with pytest.raises(RuleProjectionError, match="pattern"):
        project({"c": {"nullable": True, "pattern": "("}})
    with pytest.raises(RuleProjectionError, match="pattern"):
        project({"c": {"nullable": True, "pattern": 5}})
    with pytest.raises(RuleProjectionError, match="not_before"):
        project({"c": {"nullable": True, "not_before": "ghost"}})
    with pytest.raises(RuleProjectionError, match="at_most"):
        project({"c": {"nullable": True, "at_most": 3}})


def test_content_rule_projection_fails_closed_on_malformed_input() -> None:
    def project(table_rules):
        return RowRules.from_projection(COLUMNS, table_rules)

    base = TABLE_RULES["payload"]
    with pytest.raises(RuleProjectionError, match="unknown content rule key"):
        project({"payload": {**base, "extra": 1}})
    with pytest.raises(RuleProjectionError, match="names an unknown column"):
        project({"payload": {**base, "sha": "ghost"}})
    with pytest.raises(RuleProjectionError, match="integers"):
        project({"payload": {**base, "threshold": "10"}})
    with pytest.raises(RuleProjectionError, match="integers"):
        project({"payload": {**base, "cap": 1.5}})
    with pytest.raises(RuleProjectionError, match="below the threshold"):
        project({"payload": {**base, "cap": 5}})
    assert project({}).content is None
    assert RowRules.from_projection(COLUMNS).representation_only == {"inline", "uri"}


def test_rules_module_holds_no_table_literal() -> None:
    from tests.fixtures.turn_capture_ducklake import load_specs

    specs = load_specs()
    names = set()
    for spec in specs.values():
        names |= set(spec.columns) | {spec.table}
    distinctive_values = {
        "telemetry_sessions", "telemetry_observations", "telemetry_transcripts", "telemetry_agents", "tool_call",
        "model_call", "claude_code", "tool_output", "tool_result", "fixed_non_rollover_allowance", "summarized",
        "omitted", "oversize",
    }  # fmt: skip
    tree = ast.parse(Path(rules_module.__file__).read_text(encoding="utf-8"))
    literals = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    hits = literals & (names | distinctive_values)
    assert not hits, hits
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not any(m and m.startswith("src.telemetry") for m in modules) and "gate" not in imported


def _extra_rules(columns: dict, **table_overrides) -> RowRules:
    return RowRules.from_projection({**COLUMNS, **columns}, {**TABLE_RULES, **table_overrides})


_NEW_KINDS = {
    "title": {"role": "input", "sql_type": "VARCHAR", "nullable": True, "not_null": True, "min_length": 5},
    "labels": {"role": "input", "sql_type": "VARCHAR[]", "nullable": True, "array_element_format": "^[a-z]+$"},
}


def test_min_length_and_element_format_reject() -> None:
    rules = _extra_rules(_NEW_KINDS)
    base = good(title="a valid title", labels=["ab", "cd"])
    check_row(TABLE, base, rules, CREATED)
    check_row(TABLE, good(title="a valid title", labels=[]), rules, CREATED)
    check_row(TABLE, good(title="a valid title", labels=None), rules, CREATED)
    cases = [
        ({"title": None}, "not_null", "title"),
        ({"title": "   ab  "}, "min_length", "title"),
        ({"title": 12345}, "min_length", "title"),
        ({"labels": "ab"}, "array_element_format", "labels"),
        ({"labels": ["ab", None]}, "array_element_format", "labels"),
        ({"labels": ["ab", 3]}, "array_element_format", "labels"),
        ({"labels": ["ab", "Bad"]}, "array_element_format", "labels"),
    ]
    for override, rule, column in cases:
        error = violation({**base, **override}, rules)
        assert (error.table, error.rule, error.column) == (TABLE, rule, column), override
        assert "valid title" not in str(error)
    nullable_rules = _extra_rules({"note": {"nullable": True, "min_length": 3, "pattern": "^[a-z]+$"}})
    check_row(TABLE, good(note=None), nullable_rules, CREATED)


def test_exclude_before_binds_from_utc_midnight() -> None:
    columns = {"title": {**_NEW_KINDS["title"], "exclude_before": {"min_length": "2026-09-01"}}}
    rules = _extra_rules(columns)
    row = good(title="tiny")
    prior = {"title": "tiny"}
    day = datetime(2026, 9, 1, tzinfo=timezone.utc)
    check_row(TABLE, row, rules, day - timedelta(microseconds=1), prior=prior)
    for at in (day, day + timedelta(days=30)):
        with pytest.raises(RowRuleError) as raised:
            check_row(TABLE, row, rules, at, prior=prior)
        assert raised.value.rule == "min_length"
    assert violation(row, rules, day - timedelta(days=30)).rule == "min_length"  # prior None: an insert is always bound
    dated_null = _extra_rules({"title": {**_NEW_KINDS["title"], "exclude_before": {"not_null": "2026-09-01"}}})
    check_row(TABLE, good(title=None), dated_null, day - timedelta(seconds=1), prior={"title": None})
    with pytest.raises(RowRuleError) as raised:
        check_row(TABLE, good(title=None), dated_null, day, prior={"title": None})
    assert raised.value.rule == "not_null"
    west = timezone(timedelta(hours=-5))  # 2026-08-31 20:00 local is 2026-09-01 01:00 UTC: already bound
    with pytest.raises(RowRuleError):
        check_row(TABLE, row, rules, datetime(2026, 8, 31, 20, 0, tzinfo=west), prior=prior)
    check_row(TABLE, row, rules, datetime(2026, 8, 31, 18, 0, tzinfo=west), prior=prior)  # 23:00 UTC on 08-31


def test_changed_column_binds_older_rows() -> None:
    columns = {
        "title": {**_NEW_KINDS["title"], "exclude_before": {"min_length": "2026-09-01"}},
        "labels": {**_NEW_KINDS["labels"], "exclude_before": {"array_element_format": "2026-09-01"}},
    }
    rules = _extra_rules(columns)
    older = datetime(2026, 4, 1, tzinfo=timezone.utc)
    prior = {"title": "short", "labels": ["Bad"]}
    check_row(TABLE, good(title="short", labels=["Bad"]), rules, older, prior=prior)
    with pytest.raises(RowRuleError) as raised:
        check_row(TABLE, good(title="tiny", labels=["Bad"]), rules, older, prior=prior)
    assert (raised.value.rule, raised.value.column) == ("min_length", "title")
    with pytest.raises(RowRuleError) as raised:
        check_row(TABLE, good(title="short", labels=["Worse"]), rules, older, prior=prior)
    assert (raised.value.rule, raised.value.column) == ("array_element_format", "labels")
    check_row(TABLE, good(title="a valid title", labels=["ok"]), rules, older, prior=prior)
    two_column = _extra_rules({"happened": {**COLUMNS["happened"], "exclude_before": {"not_before": "2026-09-01"}}})
    backwards = good(happened=T0 - timedelta(seconds=1))
    prior_row = {"happened": T0 - timedelta(seconds=1), "started": T0}
    check_row(TABLE, backwards, two_column, older, prior=prior_row)
    with pytest.raises(RowRuleError):
        check_row(TABLE, {**backwards, "started": T0 + timedelta(seconds=5)}, two_column, older, prior=prior_row)


def test_producer_side_exemptions_are_recorded_not_evaluated() -> None:
    why = {"class": "cross_row", "reason": "a lookup no row-local evaluator can run", "owner": "rec-1"}
    columns = {
        "a": {"nullable": True, "write_time_exemptions": {"acceptance_lint": why}},
        "b": {"nullable": True, "write_time_exemptions": {"acceptance_lint": why, "array_element_reference": why}},
    }
    rules = _extra_rules(columns)
    expected = ("a lookup no row-local evaluator can run", "rec-1")
    assert rules.producer_exemptions == {
        ("a", "acceptance_lint"): expected,
        ("b", "acceptance_lint"): expected,
        ("b", "array_element_reference"): expected,
    }
    assert rules.exemptions == {}
    check_row(TABLE, good(a="anything", b=["x"]), rules, CREATED)


def test_glossary_lint_is_a_producer_side_kind() -> None:
    why = {
        "class": "repository_state",
        "reason": "reads the repository tree",
        "owner": "scripts/ops_portal/write_validators.py",
    }
    rules = _extra_rules({"a": {"nullable": True, "write_time_exemptions": {"glossary_lint": why}}})
    assert list(rules.producer_exemptions) == [("a", "glossary_lint")]
    check_row(TABLE, good(a="the platform"), rules, CREATED)


def test_unknown_keys_and_bad_dates_fail_closed() -> None:
    def project(column):
        return _extra_rules({"c": {"nullable": True, **column}})

    for bad in (False, "yes", 1):
        with pytest.raises(RuleProjectionError, match="not_null"):
            project({"not_null": bad})
    for bad in (-1, "3", 1.5, True):
        with pytest.raises(RuleProjectionError, match="min_length"):
            project({"min_length": bad})
    with pytest.raises(RuleProjectionError, match="array_element_format|pattern"):
        project({"array_element_format": "("})
    for bad in ({}, "2026-09-01", None):
        with pytest.raises(RuleProjectionError, match="exclude_before"):
            project({"min_length": 3, "exclude_before": bad})
    for bad in ("2026-13-45", "yesterday", 20260901, datetime(2026, 9, 1)):
        with pytest.raises(RuleProjectionError, match="ISO date"):
            project({"min_length": 3, "exclude_before": {"min_length": bad}})
    with pytest.raises(RuleProjectionError, match="not a dateable rule"):
        project({"min_length": 3, "exclude_before": {"pattern": "2026-09-01"}})  # the column carries no pattern
    with pytest.raises(RuleProjectionError, match="not a dateable rule"):
        project({"required_when": {"kind": ["a"]}, "exclude_before": {"required_when": "2026-09-01"}})
    with pytest.raises(RuleProjectionError, match="silently drop"):
        _extra_rules({"c": {"nullable": False, "not_null": True, "exclude_before": {"not_null": "2026-09-01"}}})
    with pytest.raises(RuleProjectionError, match="unknown or duplicate leg"):
        project({"write_time_exemptions": {"made_up": {"reason": "r", "owner": "o"}}})
    with pytest.raises(RuleProjectionError, match="needs a reason and an owner"):
        project({"write_time_exemptions": {"acceptance_lint": {"reason": "r"}}})
    assert _extra_rules(
        {"c": {"nullable": True, "exclude_before": {"not_null": "2026-09-01"}, "not_null": True}}
    ).exclude_before["c"]


def test_package_is_a_stdlib_leaf() -> None:
    import configparser
    import sys

    root = Path(rules_module.__file__).resolve().parent
    init = ast.parse((root / "__init__.py").read_text(encoding="utf-8"))
    assert not any(isinstance(n, (ast.Import, ast.ImportFrom, ast.Assign, ast.FunctionDef, ast.ClassDef)) for n in init.body)
    for path in sorted(root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            for name in names:
                assert name.split(".")[0] in sys.stdlib_module_names, f"{path.name}: non-stdlib import {name!r}"
    config = configparser.ConfigParser()
    config.read(root.parents[1] / ".importlinter", encoding="utf-8")
    section = config["importlinter:contract:src-row-rules-is-a-leaf"]
    assert section["type"] == "forbidden" and section["source_modules"].split() == ["src.row_rules"]
    for forbidden in ("scripts", "src.common", "src.data", "src.lambdas", "src.schemas", "src.telemetry", "src.turn_capture"):
        assert forbidden in section["forbidden_modules"].split()


def test_a_naive_created_timestamp_is_read_as_utc() -> None:
    rules = _extra_rules({"title": {**_NEW_KINDS["title"], "exclude_before": {"min_length": "2026-09-01"}}})
    row, prior = good(title="tiny", happened=None), {"title": "tiny"}
    check_row(TABLE, row, rules, datetime(2026, 8, 31, 23, 0), prior=prior)
    with pytest.raises(RowRuleError):
        check_row(TABLE, row, rules, datetime(2026, 9, 1, 0, 0), prior=prior)


WHEN_COLUMNS = {
    "kind": {"role": "input", "sql_type": "VARCHAR", "nullable": False, "accepted_values": ["a", "b", "c"]},
    "label": {
        "role": "input",
        "sql_type": "VARCHAR",
        "nullable": True,
        "pattern_when": [{"when": {"kind": ["a"]}, "pattern": "^x:[a-z]+$"}],
    },
}


def _when_rules(**label) -> RowRules:
    columns = {**WHEN_COLUMNS, "label": {**WHEN_COLUMNS["label"], **label}}
    return RowRules.from_projection(columns)


def test_pattern_when_binds_only_where_its_condition_matches() -> None:
    rules = _when_rules()
    check_row(TABLE, {"kind": "a", "label": "x:ok"}, rules, CREATED)
    check_row(TABLE, {"kind": "a", "label": None}, rules, CREATED)
    check_row(TABLE, {"kind": "b", "label": "anything at all"}, rules, CREATED)
    check_row(TABLE, {"kind": "b", "label": 7}, rules, CREATED)
    for bad in ("nope", "x:", "x:ok\n", "X:ok", 7, b"x:ok"):
        with pytest.raises(RowRuleError) as raised:
            check_row(TABLE, {"kind": "a", "label": bad}, rules, CREATED)
        assert (raised.value.table, raised.value.rule, raised.value.column) == (TABLE, "pattern_when", "label")
        assert "nope" not in str(raised.value)
    two = _when_rules(
        pattern_when=[
            {"when": {"kind": ["a"]}, "pattern": "^x:[a-z]+$"},
            {"when": {"kind": ["a", "b"]}, "pattern": "^.{1,4}$"},
        ]
    )
    check_row(TABLE, {"kind": "b", "label": "abcd"}, two, CREATED)
    with pytest.raises(RowRuleError, match="pattern_when"):
        check_row(TABLE, {"kind": "a", "label": "x:toolong"}, two, CREATED)


def test_pattern_when_fails_closed_on_malformed_projection() -> None:
    good_entry = {"when": {"kind": ["a"]}, "pattern": "^x$"}
    malformed = [
        None,
        "x",
        {},
        [],
        ["x"],
        [{"when": {"kind": ["a"]}}],
        [{"pattern": "^x$"}],
        [{**good_entry, "extra": 1}],
        [{"when": {}, "pattern": "^x$"}],
        [{"when": {"kind": "a"}, "pattern": "^x$"}],
        [{"when": {"kind": ["a"]}, "pattern": "("}],
        [{"when": {"kind": ["a"]}, "pattern": 5}],
        [{"when": {"ghost": ["a"]}, "pattern": "^x$"}],
        [{"when": {"kind": ["zzz"]}, "pattern": "^x$"}],
    ]
    for bad in malformed:
        with pytest.raises(RuleProjectionError, match="pattern_when"):
            _when_rules(pattern_when=bad)
    with pytest.raises(RuleProjectionError, match="not a dateable rule"):
        _when_rules(exclude_before={"pattern_when": "2026-09-01"})


def test_conditions_must_name_known_columns_and_values() -> None:
    def project(rule: str, condition) -> RowRules:
        columns = {**WHEN_COLUMNS, "other": {"nullable": True, rule: condition}}
        return RowRules.from_projection(columns)

    for rule in ("required_when", "null_or_zero_when"):
        project(rule, {"kind": ["a", "b"]})
        with pytest.raises(RuleProjectionError, match=rule):
            project(rule, {"ghost": ["a"]})
        with pytest.raises(RuleProjectionError, match=rule):
            project(rule, {"kind": ["a", "typo"]})
    open_values = {"free": {"nullable": True}, "other": {"nullable": True, "required_when": {"free": ["anything"]}}}
    assert RowRules.from_projection(open_values).required_when["other"] == {"free": frozenset({"anything"})}


def test_patterns_are_compiled_once(monkeypatch: pytest.MonkeyPatch) -> None:
    columns = {
        "kind": {"nullable": False, "accepted_values": ["a"]},
        "p": {"nullable": True, "pattern": "^p$"},
        "arr": {"nullable": True, "array_element_format": "^e$"},
        "w": {"nullable": True, "pattern_when": [{"when": {"kind": ["a"]}, "pattern": "^w$"}]},
    }
    rules = RowRules.from_projection(columns)

    class Untouchable:
        def __getattr__(self, name: str):
            raise AssertionError(f"check_row used re.{name}")

    monkeypatch.setattr(rules_module, "re", Untouchable())
    check_row(TABLE, {"kind": "a", "p": "p", "arr": ["e", "e"], "w": "w"}, rules, CREATED)
    for bad in ({"p": "q"}, {"arr": ["e", "z"]}, {"w": "z"}):
        with pytest.raises(RowRuleError):
            check_row(TABLE, {"kind": "a", **bad}, rules, CREATED)


def test_pattern_when_always_binds_older_rows_and_unhashable_conditions_fail_closed() -> None:
    rules = _when_rules()
    with pytest.raises(RowRuleError, match="pattern_when"):
        check_row(TABLE, {"kind": "a", "label": "bad"}, rules, CREATED, prior={"kind": "a", "label": "bad"})
    with pytest.raises(RuleProjectionError, match="required_when"):
        RowRules.from_projection({**WHEN_COLUMNS, "other": {"nullable": True, "required_when": {"kind": [["a"]]}}})


_WAIVED_RULES = {
    "not_null": True,
    "accepted_values": ["a"],
    "min_length": 3,
    "pattern": "^a$",
    "pattern_when": [{"when": {"k": ["a"]}, "pattern": "^a$"}],
    "array_element_format": "^a$",
    "required_when": {"k": ["a"]},
    "not_before": "k",
    "at_most": "k",
    "null_or_zero_when": {"k": ["a"]},
    "max_after_write_seconds": 5,
}
_OWNED = {"reason": "staged", "owner": "rec-1"}


def _waived_columns(kind: str, *, ships: bool, nullable: bool = True) -> dict:
    column = {"nullable": nullable, "write_time_exemptions": {kind: _OWNED}}
    if ships:
        column[kind] = _WAIVED_RULES[kind]
    return {"k": {"nullable": True, "accepted_values": ["a", "b"]}, "c": column}


def test_waived_rules_cover_every_exemptable_kind() -> None:
    assert set(_WAIVED_RULES) == WAIVABLE_KINDS


@pytest.mark.parametrize("kind", sorted(WAIVABLE_KINDS))
def test_row_local_exemption_is_recorded_and_unenforced(kind: str) -> None:
    rules = RowRules.from_projection(_waived_columns(kind, ships=False))
    assert rules.waived == {("c", kind): ("staged", "rec-1")}
    assert not rules.not_null - {"c"} and "c" not in rules.not_null
    assert "c" not in rules.accepted_values and "c" not in rules.min_length and "c" not in rules.patterns
    check_row(TABLE, {"k": "a", "c": None}, rules, CREATED)


@pytest.mark.parametrize("kind", sorted(WAIVABLE_KINDS))
def test_an_exempt_rule_that_also_ships_raises(kind: str) -> None:
    with pytest.raises(RuleProjectionError, match="exempt and must not also ship"):
        RowRules.from_projection(_waived_columns(kind, ships=True))


def test_not_null_cannot_be_exempted_on_a_nullable_false_column() -> None:
    with pytest.raises(RuleProjectionError, match="nullable:false"):
        RowRules.from_projection(_waived_columns("not_null", ships=False, nullable=False))
