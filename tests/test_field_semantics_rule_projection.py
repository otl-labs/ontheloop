"""Mirror test for scripts/field_semantics_rule_projection.py (rec-4158 plan B): every declared row rule of an SCD2
contract reaches the generated projection exactly once, exemptions ship only as exemptions, an unknown kind fails
closed, and the rule projection moves nothing but rule keys under ops_tables.*.columns.

The set of rule tables and each table's expected (column, key) pairs are derived from the contracts and
scripts.contract_rules.declared_rules by a private oracle, never from a literal table list and never from the
generator's own dispatch (Decision 191). A table classed event is excluded (its projection owns those keys). Every other
table with a contract is expected to carry exactly its declared rules: SCD2 contracts (ops_recommendations,
ops_decisions, ops_tenants and ops_projects once they declare rules) and control contracts alike. The generator never runs
project_row_rules for a control table, so a projectable rule on a control contract, or a contract authored for a still
dormant sidecar table, fails test_other_tables_project_no_rule_key by design (Decision 210: a declared rule is enforced at
write or it is a gap). The remedy is to wire the projection or register the table, never to exclude it here."""

from __future__ import annotations

import copy
from typing import Any, Callable
from unittest.mock import patch

import pytest
import yaml

import scripts.field_semantics_rule_projection as projection
import scripts.schema_to_field_semantics as generator
from scripts.contract_rules import CONTRACTS_DIR, DeclaredRule, declared_rules
from scripts.schema_to_field_semantics import generate
from src.row_rules.rules import RuleProjectionError

_BASE_COLUMN_KEYS = frozenset({"role", "sql_type", "nullable", "description", "semantics"})

_Reader = Callable[[str], "tuple[DeclaredRule, ...]"]


def _columns(table: str) -> dict:
    return generate()["ops_tables"][table]["columns"]


def _expected_value(rule: DeclaredRule):
    if rule.kind == "accepted_values":
        return list(rule.params["values"])
    if rule.kind == "array_element_format":
        return rule.params["pattern"]
    return rule.params["value"]


def _plain_doc() -> dict:
    with patch.object(projection, "project_row_rules", side_effect=lambda table_id, columns: columns):
        return generate()


def _table_class(table: str) -> str | None:
    path = CONTRACTS_DIR / f"{table}.yaml"
    if not path.is_file():
        return None
    governance = yaml.safe_load(path.read_text(encoding="utf-8")).get("governance") or {}
    return str(governance.get("table_class") or "scd2").lower()


def _expected_pairs(table: str, plain_columns: dict, reader: _Reader) -> set[tuple[str, str]]:
    if _table_class(table) in (None, "event"):
        return set()
    pairs: set[tuple[str, str]] = set()
    for rule in reader(table):
        if rule.exemption is not None:
            pairs.add((rule.column, "write_time_exemptions"))
            continue
        if rule.kind == "not_null" and plain_columns[rule.column]["nullable"] is False:
            continue
        pairs.add((rule.column, rule.kind))
        if rule.exclude_before is not None:
            pairs.add((rule.column, "exclude_before"))
    return pairs


def _projected_pairs(columns: dict) -> set[tuple[str, str]]:
    return {(name, key) for name, spec in columns.items() for key in set(spec) - _BASE_COLUMN_KEYS}


def _strip_rule_keys(doc: dict) -> dict:
    out = copy.deepcopy(doc)
    for entry in out["ops_tables"].values():
        for spec in (entry.get("columns") or {}).values():
            for key in set(spec) - _BASE_COLUMN_KEYS:
                spec.pop(key)
    return out


def _rule_invariants(real: dict, plain: dict, reader: _Reader = declared_rules) -> set[str]:
    """Assert the projection invariants over generate() output and return the rule tables (non-event, expected pairs)."""
    assert real != plain
    assert _strip_rule_keys(real) == _strip_rule_keys(plain)
    rule_tables: set[str] = set()
    for table, entry in real["ops_tables"].items():
        if _table_class(table) == "event":
            continue
        plain_columns = plain["ops_tables"][table].get("columns") or {}
        assert not _projected_pairs(plain_columns), (table, "plain projection carries a rule key")
        expected = _expected_pairs(table, plain_columns, reader)
        if expected:
            rule_tables.add(table)
        assert _projected_pairs(entry.get("columns") or {}) == expected, table
    changed = {t for t in real["ops_tables"] if real["ops_tables"][t] != plain["ops_tables"][t]}
    assert changed == rule_tables
    return rule_tables


def test_projects_every_declared_rule_once() -> None:
    plain = _plain_doc()
    real = generate()
    rule_tables = _rule_invariants(real, plain)
    assert "ops_recommendations" in rule_tables
    for table in sorted(rule_tables):
        columns = real["ops_tables"][table]["columns"]
        seen: set[tuple[str, str]] = set()
        for rule in declared_rules(table):
            spec = columns[rule.column]
            key = (rule.column, rule.kind)
            assert key not in seen
            seen.add(key)
            if rule.exemption is not None:
                assert rule.kind not in spec, key
                assert spec["write_time_exemptions"][rule.kind]["owner"] == rule.exemption["owner"]
                continue
            if rule.kind == "not_null":
                if spec["nullable"] is False:
                    assert "not_null" not in spec, key
                    assert rule.exclude_before is None
                    continue
                assert spec["not_null"] is True
            else:
                assert spec[rule.kind] == _expected_value(rule), key
            if rule.exclude_before is not None:
                assert spec["exclude_before"][rule.kind] == rule.exclude_before, key
            else:
                assert rule.kind not in spec.get("exclude_before", {}), key
    columns = real["ops_tables"]["ops_recommendations"]["columns"]
    assert columns["title"]["nullable"] is False and columns["title"]["min_length"] == 10


def test_exemptions_and_unknown_kinds() -> None:
    columns = _columns("ops_recommendations")
    assert set(columns["acceptance"]["write_time_exemptions"]) == {"acceptance_lint"}
    assert set(columns["dependencies"]["write_time_exemptions"]) == {"array_element_reference"}
    assert columns["dependencies"]["array_element_format"] == "^rec-[0-9]+$"
    base = {"id": {"role": "input", "sql_type": "VARCHAR", "nullable": False}}
    unknown = DeclaredRule("t", "id", "mystery", {"value": 1})
    with patch.object(projection, "declared_rules", return_value=(unknown,)):
        with pytest.raises(ValueError, match="unknown rule kind 'mystery'"):
            projection.project_row_rules("t", base)
    dated_not_null = DeclaredRule("t", "id", "not_null", {}, "2026-09-01")
    with patch.object(projection, "declared_rules", return_value=(dated_not_null,)):
        with pytest.raises(ValueError, match="dated not_null on a nullable:false column"):
            projection.project_row_rules("t", base)
    ghost = DeclaredRule("t", "ghost", "not_null", {})
    with patch.object(projection, "declared_rules", return_value=(ghost,)):
        with pytest.raises(ValueError, match="names a column the projection does not carry"):
            projection.project_row_rules("t", base)
    before = copy.deepcopy(base)
    with patch.object(projection, "declared_rules", return_value=()):
        projection.project_row_rules("t", base)
    assert base == before


def test_other_tables_project_no_rule_key() -> None:
    doc = generate()
    plain = _plain_doc()
    for table, entry in doc["ops_tables"].items():
        if _table_class(table) == "event":
            continue
        plain_columns = plain["ops_tables"][table].get("columns") or {}
        expected = _expected_pairs(table, plain_columns, declared_rules)
        assert _projected_pairs(entry.get("columns") or {}) == expected, table
    for table in ("ops_decisions", "ops_entity_counters"):
        assert not _projected_pairs(_columns(table)), table


def test_rule_projection_touches_only_rule_keys() -> None:
    plain = _plain_doc()
    real = generate()
    rule_tables = _rule_invariants(real, plain)
    changed = {t for t in real["ops_tables"] if real["ops_tables"][t] != plain["ops_tables"][t]}
    assert changed == rule_tables
    assert "ops_recommendations" in changed


def test_rule_invariants_follow_a_new_rule_table() -> None:
    extra = DeclaredRule("ops_decisions", "title", "min_length", {"value": 3})

    def reader(table_id: str) -> tuple[DeclaredRule, ...]:
        return declared_rules(table_id) + ((extra,) if table_id == "ops_decisions" else ())

    plain = _plain_doc()
    with patch.object(projection, "declared_rules", reader):
        real = generate()
    assert "ops_decisions" not in _rule_invariants(generate(), plain)
    assert "ops_decisions" in _rule_invariants(real, plain, reader)


def _wrapped(mutate: Callable[[str, dict[str, Any]], dict[str, Any]]) -> dict:
    original = projection.project_row_rules
    with patch.object(projection, "project_row_rules", side_effect=lambda t, c: mutate(t, original(t, c))):
        return generate()


def test_rule_invariants_catch_undeclared_and_dropped_rules() -> None:
    plain = _plain_doc()
    real = generate()
    recs_pairs = _expected_pairs("ops_recommendations", plain["ops_tables"]["ops_recommendations"]["columns"], declared_rules)
    declared_kinds = {kind for _, kind in recs_pairs}
    assert "pattern" in declared_kinds
    other = next(c for c in plain["ops_tables"]["ops_recommendations"]["columns"] if (c, "pattern") not in recs_pairs)

    def undeclared_on_ruleless(table: str, columns: dict[str, Any]) -> dict[str, Any]:
        if table == "ops_decisions":
            first = next(iter(columns))
            columns[first] = {**columns[first], "pattern": "^x$"}
        return columns

    def undeclared_kind_on_rule_table(table: str, columns: dict[str, Any]) -> dict[str, Any]:
        if table == "ops_recommendations":
            columns[other] = {**columns[other], "pattern": "^x$"}
        return columns

    def dropped_dispatch(table: str, columns: dict[str, Any]) -> dict[str, Any]:
        return (
            {n: {k: v for k, v in s.items() if k in _BASE_COLUMN_KEYS} for n, s in columns.items()}
            if table == "ops_recommendations"
            else columns
        )

    _rule_invariants(real, plain)
    for mutate in (undeclared_on_ruleless, undeclared_kind_on_rule_table, dropped_dispatch):
        with pytest.raises(AssertionError):
            _rule_invariants(_wrapped(mutate), plain)


def test_sidecar_table_with_a_contract_fails_the_generator() -> None:
    projection.refuse_contract_overlap(("ops_priority_queue", "ops_execution_plans", "ops_smoke_events"), CONTRACTS_DIR)
    with patch.object(generator, "_DORMANT_TABLE_IDS", ("ops_priority_queue", "ops_decisions")):
        with pytest.raises(ValueError, match=r"ops_decisions: a sidecar \(dormant or smoke\) table also has docs/contracts/"):
            generate()


def test_malformed_sidecar_rule_fails_the_generator(tmp_path) -> None:
    sidecar = yaml.safe_load(generator._SIDECAR_PATH.read_text(encoding="utf-8"))
    projection.validate_sidecar_rules(sidecar["dormant_ops_tables"])
    projection.validate_sidecar_rules(sidecar["smoke_ops_tables"])
    sidecar["dormant_ops_tables"]["ops_priority_queue"]["columns"]["rank"]["min_length"] = -1
    broken = tmp_path / "sidecar.yaml"
    broken.write_text(yaml.safe_dump(sidecar), encoding="utf-8")
    with patch.object(generator, "_SIDECAR_PATH", broken):
        with pytest.raises(RuleProjectionError, match="min_length must be a non-negative integer"):
            generate()
