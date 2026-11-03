"""Mirror test for scripts/contract_rules.py (rec-4158 plan A): the one contract-rule reader, its layering, its
fail-closed grammar, and the declared ops rule lists."""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

import scripts.contract_rules as rules_mod
import scripts.field_semantics_event_projection as proj_mod
from scripts.contract_rules import (
    DeclaredRule,
    check_rule_keys,
    declared_rules,
    layer_rules,
    load_yaml,
    not_null_declared,
    read_intents,
    registry_values,
    split_ref,
)

_D = "2026-05-01"
_ALLOWED = frozenset({"not_null", "accepted_values", "min_length", "write_time_exemptions"})


def _by_column(table: str) -> dict[str, dict[str, DeclaredRule]]:
    out: dict[str, dict[str, DeclaredRule]] = {}
    for rule in declared_rules(table):
        out.setdefault(rule.column, {})[rule.kind] = rule
    return out


def _shape(rule: DeclaredRule) -> tuple:
    return (dict(rule.params), rule.exclude_before)


def test_ops_recommendations_declares_its_rule_list() -> None:
    cols = _by_column("ops_recommendations")
    registry_doc = yaml.safe_load(
        (rules_mod.REPO_ROOT / "config/agent/data_quality/source_registry.yaml").read_text(encoding="utf-8")
    )
    registry = [entry["canonical_id"] for entry in registry_doc["entries"]]
    expected: dict[str, dict[str, tuple]] = {
        "ulid": {"not_null": ({}, None)},
        "id": {
            "not_null": ({}, None),
            "pattern": ({"value": "^(rec-[0-9]+|agent-[0-9]+|test-[a-z0-9-]+)$"}, None),
        },
        "title": {"not_null": ({}, None), "min_length": ({"value": 10}, "2026-09-01"), "glossary_lint": ({}, None)},
        "source": {"not_null": ({}, _D), "accepted_values": ({"values": registry}, None)},
        "effort": {"not_null": ({}, _D), "accepted_values": ({"values": ["XS", "S", "M", "L", "XL"]}, _D)},
        "priority": {"not_null": ({}, _D), "accepted_values": ({"values": ["Critical", "High", "Medium", "Low"]}, _D)},
        "status": {
            "not_null": ({}, None),
            "accepted_values": ({"values": ["open", "closed", "failed", "declined", "superseded"]}, None),
        },
        "automatable": {"not_null": ({}, _D)},
        "risk": {"not_null": ({}, _D), "accepted_values": ({"values": ["low", "medium", "high"]}, _D)},
        "file": {
            "not_null": ({}, _D),
            "min_length": ({"value": 1}, _D),
            "pattern": (
                {"value": r"^([^/\\A-Za-z][^\\]*|[A-Za-z]([^:\\][^\\]*)?|[A-Za-z]:([^/\\][^\\]*)?)$"},
                _D,
            ),
        },
        "context": {"not_null": ({}, _D), "min_length": ({"value": 80}, _D), "glossary_lint": ({}, None)},
        "acceptance": {
            "not_null": ({}, _D),
            "min_length": ({"value": 1}, _D),
            "acceptance_lint": ({"require_discrimination": True}, None),
        },
        "verification": {"glossary_lint": ({}, None)},
        "dependencies": {
            "array_element_format": ({"pattern": "^rec-[0-9]+$"}, None),
            "array_element_reference": ({}, None),
        },
        "tags": {"array_element_format": ({"pattern": "^[a-z][a-z0-9-]*$"}, "2026-09-08")},
        "created_timestamp": {"not_null": ({}, None)},
        "last_updated_timestamp": {"not_null": ({}, None), "not_before": ({"value": "created_timestamp"}, None)},
    }
    actual = {col: {kind: _shape(rule) for kind, rule in kinds.items()} for col, kinds in cols.items()}
    assert actual == expected
    assert cols["acceptance"]["acceptance_lint"].exemption["class"] == "repository_state"
    for column in ("title", "context", "verification"):
        assert cols[column]["glossary_lint"].exemption["class"] == "repository_state"
        assert cols[column]["glossary_lint"].exemption["owner"] == "scripts/ops_portal/write_validators.py"
    assert cols["dependencies"]["array_element_reference"].exemption["class"] == "cross_row"
    assert cols["dependencies"]["array_element_reference"].exemption["owner"] == "rec-4166"
    assert all(
        r.exemption is None
        for c in cols.values()
        for k, r in c.items()
        if k not in ("acceptance_lint", "array_element_reference", "glossary_lint")
    )


def test_ops_decisions_keeps_only_its_not_null_rules() -> None:
    rules = declared_rules("ops_decisions")
    assert rules and {r.kind for r in rules} == {"not_null"}
    assert all(r.exclude_before is None and r.exemption is None for r in rules)
    assert {r.column for r in rules} >= {"ulid", "id", "title", "status"}


def test_source_values_come_from_the_registry() -> None:
    contracts = rules_mod.CONTRACTS_DIR
    raw = load_yaml(contracts / "ops_recommendations.yaml")
    local = raw["fields"]["source"]["dq_intent_local"]
    assert "write_time" not in local["not_null"] and "accepted_values" not in local
    intents = read_intents(
        "ops_recommendations",
        {},
        contracts_dir=contracts,
        repo_root=rules_mod.REPO_ROOT,
        registry_key_ref=rules_mod.REGISTRY_KEY_REF,
    )
    registry = yaml.safe_load(
        (rules_mod.REPO_ROOT / "config/agent/data_quality/source_registry.yaml").read_text(encoding="utf-8")
    )
    assert intents["source"]["accepted_values"]["values"] == [e["canonical_id"] for e in registry["entries"]]
    assert intents["source"]["not_null"] == {"exclude_before": _D}


def test_layering_keeps_leg_subkeys() -> None:
    legacy = layer_rules(
        "t", "c", {"not_null": {"enforced": False, "note": "x"}}, {"not_null": {"enforced": True, "exclude_before": _D}}
    )
    assert legacy["not_null"] == {"exclude_before": _D}
    kept = layer_rules("t", "c", {"not_null": {"enforced": True}}, {"not_null": {"enforced": False, "exclude_before": _D}})
    assert kept["not_null"] is True
    assert "not_null" not in layer_rules("t", "c", {}, {"not_null": None})
    assert "not_null" not in layer_rules("t", "c", {"not_null": {"enforced": False}}, {})
    assert layer_rules("t", "c", {}, {"not_null": {"exclude_before": _D}})["not_null"] == {"exclude_before": _D}
    assert layer_rules("t", "c", {"not_null": True}, {"not_null": {"exclude_before": _D}})["not_null"] == {
        "exclude_before": _D
    }
    assert layer_rules("t", "c", {}, {"not_null": True})["not_null"] is True
    assert layer_rules("t", "c", {"a": 1}, {"b": 2}) == {"a": 1, "b": 2}
    assert layer_rules("t", "c", {"a": 1}, {"a": 1}) == {"a": 1}
    with pytest.raises(ValueError, match="changes the inherited rule 'a'"):
        layer_rules("t", "c", {"a": 1}, {"a": 2})


def test_rule_keys_and_exemptions_fail_closed() -> None:
    check_rule_keys("t", "c", {"not_null": {}}, _ALLOWED)
    check_rule_keys(
        "t",
        "c",
        {"not_null": {}, "write_time_exemptions": {"x": {"reason": "r", "owner": "o", "class": "temporary"}}},
        _ALLOWED,
    )
    with pytest.raises(ValueError, match=r"unknown dq_intent rule key\(s\) \['mystery'\]"):
        check_rule_keys("t", "c", {"mystery": 1}, _ALLOWED)
    with pytest.raises(ValueError, match="accepted_values must carry a values list"):
        check_rule_keys("t", "c", {"accepted_values": ["a"]}, _ALLOWED)
    with pytest.raises(ValueError, match="must be a mapping of rule kind to exemption"):
        check_rule_keys("t", "c", {"write_time_exemptions": ["x"]}, _ALLOWED)
    for bad in ({"reason": "r"}, {"owner": "o"}, "text", {"reason": "", "owner": "o"}):
        with pytest.raises(ValueError, match="needs a reason and an owner"):
            check_rule_keys("t", "c", {"write_time_exemptions": {"x": bad}}, _ALLOWED)
    with pytest.raises(ValueError, match="class must be a non-empty string"):
        check_rule_keys("t", "c", {"write_time_exemptions": {"x": {"reason": "r", "owner": "o", "class": 3}}}, _ALLOWED)


def test_reference_failures(tmp_path: Path) -> None:
    (tmp_path / "target.yaml").write_text(yaml.safe_dump({"fields": {"t": {"dq_intent": {}}}}), encoding="utf-8")
    base = {"fields": {"c": {"$ref": "target.yaml#/fields/missing"}}}
    (tmp_path / "fx.yaml").write_text(yaml.safe_dump(base), encoding="utf-8")
    with pytest.raises(ValueError, match="not found"):
        declared_rules("fx", contracts_dir=tmp_path, repo_root=tmp_path)
    own = {"fields": {"c": {"$ref": "target.yaml#/fields/t", "dq_intent_local": {"accepted_values": {"values": ["a"]}}}}}
    (tmp_path / "fx.yaml").write_text(yaml.safe_dump(own), encoding="utf-8")
    with pytest.raises(ValueError, match="declares no accepted_values of its own"):
        declared_rules("fx", contracts_dir=tmp_path, repo_root=tmp_path, registry_key_ref=("target.yaml", "t"))


def test_projection_imports_the_reader() -> None:
    for moved in ("_load_yaml", "_split_ref", "_layer_rules", "_registry_values", "_field_value"):
        assert not hasattr(proj_mod, moved), f"{moved} must live only in scripts.contract_rules"
    with patch.object(proj_mod, "read_intents", return_value={"c": {"sentinel": 1}}) as reader:
        assert proj_mod._effective_intents("t", {}) == {"c": {"sentinel": 1}}
    kwargs = reader.call_args.kwargs
    assert kwargs["contracts_dir"] == proj_mod._CONTRACTS_DIR and kwargs["repo_root"] == proj_mod._REPO_ROOT
    assert kwargs["registry_key_ref"] == proj_mod._REGISTRY_KEY_REF
    with patch.object(proj_mod, "check_rule_keys") as checker:
        proj_mod._check_rule_keys("t", "c", {})
    checker.assert_called_once_with("t", "c", {}, proj_mod._KNOWN_DQ_KEYS)
    assert "from scripts.contract_rules import" in inspect.getsource(proj_mod)


def test_reader_failure_modes_and_fixture_fallback(tmp_path: Path) -> None:
    (tmp_path / "list.yaml").write_text("- a\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a YAML mapping"):
        load_yaml(tmp_path / "list.yaml")
    with pytest.raises(ValueError, match="cannot read contract file"):
        load_yaml(tmp_path / "absent.yaml")
    assert split_ref("docs/contracts/source-lineage.yaml#/contract/fields/registry_key") == (
        "source-lineage.yaml",
        "registry_key",
    )
    with pytest.raises(ValueError, match="must name a registry and a key"):
        registry_values(tmp_path, {})
    (tmp_path / "reg.yaml").write_text("entries: [{k: a}, {k: a}]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no values or duplicate"):
        registry_values(tmp_path, {"allowed_values": {"registry": "reg.yaml", "key": "k"}})
    resolved = {"c": {"dq_intent": {"not_null": {"enforced": True}}}, "d": type("S", (), {"dq_intent": None})()}
    assert read_intents("fixture", resolved, contracts_dir=tmp_path, repo_root=tmp_path, registry_key_ref=("x", "y")) == {
        "c": {"not_null": {"enforced": True}},
        "d": {},
    }
    with pytest.raises(ValueError, match="no contract for table"):
        declared_rules("fixture", contracts_dir=tmp_path, repo_root=tmp_path)


def test_declared_rules_semantics(tmp_path: Path) -> None:
    contract = {
        "fields": {
            "a": {
                "dq_intent": {
                    "not_null": {"enforced": False},
                    "min_length": {"value": 3, "exclude_before": "2026-06-01", "enforced": False},
                }
            },
            "b": {
                "dq_intent": {
                    "not_null": {"enforced": True},
                    "not_before": "a",
                    "acceptance_lint": {"require_discrimination": True},
                    "write_time_exemptions": {"acceptance_lint": {"class": "temporary", "reason": "r", "owner": "rec-1"}},
                }
            },
            "ref": {"$ref": "target.yaml#/fields/t", "dq_intent_local": {"not_null": {"enforced": True}}},
        }
    }
    target = {"fields": {"t": {"dq_intent": {"not_null": {"enforced": False}}}}}
    (tmp_path / "fx.yaml").write_text(yaml.safe_dump(contract), encoding="utf-8")
    (tmp_path / "target.yaml").write_text(yaml.safe_dump(target), encoding="utf-8")
    got = {(r.column, r.kind): r for r in declared_rules("fx", contracts_dir=tmp_path, repo_root=tmp_path)}
    assert set(got) == {
        ("a", "min_length"),
        ("b", "not_null"),
        ("b", "not_before"),
        ("b", "acceptance_lint"),
        ("ref", "not_null"),
    }
    assert got[("a", "min_length")].exclude_before == "2026-06-01" and dict(got[("a", "min_length")].params) == {"value": 3}
    assert got[("b", "not_before")].params == {"value": "a"}
    assert got[("b", "acceptance_lint")].exemption["owner"] == "rec-1"
    assert got[("b", "not_null")].exemption is None


def test_scalar_not_null_leg_fails_with_a_message(tmp_path: Path) -> None:
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    (contracts / "demo.yaml").write_text(
        yaml.safe_dump({"fields": {"name": {"dq_intent": {"not_null": False}}}}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match=r"demo\.name: rule kind 'not_null' must be true or a mapping, got False"):
        declared_rules("demo", contracts_dir=contracts, repo_root=tmp_path)
    with pytest.raises(ValueError, match=r"demo\.name: rule kind 'not_null' must be true or a mapping, got 'yes'"):
        layer_rules("demo", "name", {}, {"not_null": "yes"})


def test_not_null_presence_and_legacy_spelling_read_alike(tmp_path: Path) -> None:
    def rules_of(not_null: object) -> dict:
        intent = {} if not_null is None else {"not_null": not_null}
        (tmp_path / "fx.yaml").write_text(yaml.safe_dump({"fields": {"c": {"dq_intent": intent}}}), encoding="utf-8")
        return {r.kind: r for r in declared_rules("fx", contracts_dir=tmp_path, repo_root=tmp_path)}

    assert rules_of(None) == {}
    assert rules_of(None) == {} and not_null_declared(False) is False and not_null_declared(None) is False
    assert rules_of({"enforced": False}) == {}
    for spelling in (True, {"enforced": True}):
        got = rules_of(spelling)["not_null"]
        assert dict(got.params) == {} and got.exclude_before is None
    for spelling in ({"exclude_before": _D}, {"enforced": True, "exclude_before": _D}):
        got = rules_of(spelling)["not_null"]
        assert dict(got.params) == {} and got.exclude_before == _D
    (tmp_path / "fx.yaml").write_text(
        yaml.safe_dump(
            {"fields": {"c": {"dq_intent": {"accepted_values": {"values": ["a"], "note": "n", "enforced": False}}}}}
        ),
        encoding="utf-8",
    )
    (rule,) = declared_rules("fx", contracts_dir=tmp_path, repo_root=tmp_path)
    assert dict(rule.params) == {"values": ["a"]}
