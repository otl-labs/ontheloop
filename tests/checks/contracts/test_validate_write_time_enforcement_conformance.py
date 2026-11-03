"""Mirror test for validate_write_time_enforcement_conformance: a fixture registry passes, then one mutation per failure
arm fails closed naming the table, plus manifest registration, accounting and the roadmap-owner lookup."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

from scripts.checks import registry
from scripts.checks.contracts import validate_write_time_enforcement_conformance as check
from scripts.checks.contracts._manifest import ENTRIES

_ROADMAP = (
    "  - id: T4.3\n    name: x\n    exit_criteria:\n      - id: c1\n      - id: c6\n"
    "  - id: T4.4\n    exit_criteria:\n      - id: c1\n"
)
_WHY = {"class": "temporary", "reason": "staged", "owner": "rec-1", "review_by": "2027-01-31"}


def _col(**over: Any) -> dict[str, Any]:
    return {"role": "input", "sql_type": "VARCHAR", "nullable": True, **over}


def _world() -> dict[str, Any]:
    contracts = {
        "demo_scd2": {
            "governance": {"table_class": "SCD2"},
            "fields": {
                "id": {"nullable": False, "dq_intent": {"not_null": True}},
                "title": {
                    "nullable": True,
                    "dq_intent": {"not_null": {"exclude_before": "2026-06-01"}, "min_length": {"value": 3}},
                },
                "kind": {"nullable": True, "dq_intent": {"accepted_values": {"values": ["a", "b"]}}},
                "staged": {
                    "nullable": True,
                    "dq_intent": {"min_length": {"value": 2}, "write_time_exemptions": {"min_length": _WHY}},
                },
                "lint": {
                    "nullable": True,
                    "dq_intent": {
                        "acceptance_lint": {"require_discrimination": True},
                        "write_time_exemptions": {
                            "acceptance_lint": {"class": "repository_state", "reason": "r", "owner": "x.py"}
                        },
                    },
                },
            },
        },
        "demo_events": {
            "governance": {"table_class": "event"},
            "fields": {
                "event_id": {"nullable": False, "dq_intent": {"not_null": True}},
                "kind": {"nullable": True, "dq_intent": {"accepted_values": {"values": ["open", "close"]}}},
                "note": {"nullable": True, "dq_intent": {"required_when": {"kind": ["close"]}}},
                "ts": {"nullable": True, "dq_intent": {"not_before": "start"}},
                "start": {"nullable": True, "dq_intent": {}},
                "uri": {"nullable": True, "dq_intent": {}},
                "sha": {"nullable": True, "dq_intent": {}},
                "size": {"nullable": True, "dq_intent": {}},
                "base_key": {"$ref": "baseline.yaml#/fields/key", "dq_intent_local": {}},
                "body": {
                    "nullable": True,
                    "dq_intent": {
                        "content_inline_threshold_bytes": 10,
                        "full_output_cap_bytes": 100,
                        "integrity": True,
                        "write_time_exemptions": {"inline_within_threshold": _WHY},
                    },
                },
                "tokens": {"nullable": True, "derivation": {"timing": "read"}, "dq_intent": {"at_most": "start"}},
            },
        },
        "baseline": {"fields": {"key": {"nullable": False, "dq_intent": {}}}},
        "ops_entity_counters": {
            "governance": {"table_class": "control"},
            "fields": {
                "counter_name": {"nullable": False, "dq_intent": {"not_null": True}},
                "current_value": {"nullable": False, "dq_intent": {"not_null": True}},
            },
        },
    }
    fs = {
        "tables": {"history": {}, "current": {}},
        "ops_tables": {
            "demo_scd2": {
                "columns": {
                    "id": _col(nullable=False),
                    "title": _col(not_null=True, min_length=3, exclude_before={"not_null": "2026-06-01"}),
                    "kind": _col(accepted_values=["a", "b"]),
                    "staged": _col(write_time_exemptions={"min_length": _WHY}),
                    "lint": _col(
                        write_time_exemptions={
                            "acceptance_lint": {"class": "repository_state", "reason": "r", "owner": "x.py"}
                        }
                    ),
                }
            },
            "demo_events": {
                "table_class": "event",
                "columns": {
                    "event_id": _col(nullable=False),
                    "kind": _col(accepted_values=["open", "close"]),
                    "note": _col(required_when={"kind": ["close"]}),
                    "ts": _col(not_before="start"),
                    "start": _col(),
                    "uri": _col(),
                    "sha": _col(),
                    "size": _col(sql_type="BIGINT"),
                    "base_key": _col(nullable=False),
                    "body": _col(write_time_exemptions={"inline_within_threshold": _WHY}),
                },
                "table_rules": {
                    "payload": {
                        "inline": "body",
                        "uri": "uri",
                        "sha": "sha",
                        "size": "size",
                        "threshold": 10,
                        "cap": 100,
                        "integrity": True,
                    }
                },
            },
            "ops_entity_counters": {
                "write_mode": "control",
                "columns": {"counter_name": _col(nullable=False), "current_value": _col(nullable=False)},
            },
            "ops_priority_queue": {"write_boundary": "dormant", "status": "dormant", "columns": {"rank": _col()}},
            "ops_smoke_events": {"status": "smoke", "columns": {"event_id": _col(nullable=False)}},
        },
    }
    static = {
        "dormant_ops_tables": {"ops_priority_queue": {}},
        "smoke_ops_tables": {"ops_smoke_events": {}},
        "contract_table_ops": {
            "ops_priority_queue": {"row_rule_source": {"disposition": "pending_contract", "owner": "T4.3:c6", "reason": "r"}},
            "ops_smoke_events": {"row_rule_source": {"disposition": "smoke_harness", "reason": "r"}},
        },
    }
    return {
        "contracts": contracts,
        "fs": fs,
        "static": static,
        "controls": ("ops_entity_counters",),
        "ops_yaml": {"demo_scd2"},
    }


def _run(tmp_path: Path, world: dict[str, Any]) -> tuple[list[str], int]:
    contracts = tmp_path / "contracts"
    contracts.mkdir(parents=True, exist_ok=True)
    for name, doc in world["contracts"].items():
        (contracts / f"{name}.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    return check.conformance_errors(
        world["fs"],
        world["static"],
        contracts_dir=contracts,
        repo_root=tmp_path,
        control_names=world["controls"],
        roadmap_text=world.get("roadmap", _ROADMAP),
        ops_yaml_tables=world["ops_yaml"],
    )


def _scd2(world: dict[str, Any]) -> dict[str, Any]:
    return world["fs"]["ops_tables"]["demo_scd2"]


def _events(world: dict[str, Any]) -> dict[str, Any]:
    return world["fs"]["ops_tables"]["demo_events"]


def _dormant_without_disposition(w: dict[str, Any]) -> None:
    del w["static"]["contract_table_ops"]["ops_priority_queue"]["row_rule_source"]


# (arm, mutation, expected message substring)
_ARMS: list[tuple[str, Callable[[dict[str, Any]], None], str]] = [
    (
        "no-arm-matched",
        lambda w: w["fs"]["ops_tables"].update(ops_orphan={"status": "live", "columns": {}}),
        "ops_orphan: matches no conformance arm",
    ),
    (
        "declared-rule-missing-from-projection",
        lambda w: _scd2(w)["columns"]["title"].pop("min_length"),
        "demo_scd2.title: min_length declared but not enforced",
    ),
    (
        "undeclared-projected-rule",
        lambda w: _scd2(w)["columns"]["kind"].update(min_length=4),
        "demo_scd2.kind: min_length enforced but not declared",
    ),
    ("changed-values", lambda w: _scd2(w)["columns"]["kind"].update(accepted_values=["a"]), "demo_scd2.kind: accepted_values"),
    (
        "missing-not-null",
        lambda w: [_scd2(w)["columns"]["title"].pop(k) for k in ("not_null", "exclude_before")],
        "not_null differs",
    ),
    ("dated-not-null-without-its-rule", lambda w: _scd2(w)["columns"]["title"].pop("not_null"), "does not build an engine"),
    ("lost-date", lambda w: _scd2(w)["columns"]["title"].pop("exclude_before"), "exclude_before"),
    ("lost-exemption", lambda w: _scd2(w)["columns"]["staged"].pop("write_time_exemptions"), "waived exemption"),
    ("exempt-rule-also-ships", lambda w: _scd2(w)["columns"]["staged"].update(min_length=2), "does not build an engine"),
    ("event-rule-missing", lambda w: _events(w)["columns"]["note"].pop("required_when"), "required_when"),
    (
        "scd2-with-a-table-rule",
        lambda w: _scd2(w).update(table_rules={"exactly_one_of": [["id", "title"]]}),
        "SCD2 entry carries a table-level rule",
    ),
    (
        "declared-on-a-missing-column",
        lambda w: _scd2(w)["columns"].pop("kind"),
        "names a column the projection does not carry",
    ),
    (
        "unsupported-kind",
        lambda w: w["contracts"]["demo_scd2"]["fields"]["kind"]["dq_intent"].update(mystery={"v": 1}),
        "has no comparison arm",
    ),
    (
        "control-declares-accepted-values",
        lambda w: w["contracts"]["ops_entity_counters"]["fields"]["counter_name"]["dq_intent"].update(
            accepted_values={"values": ["a"]}
        ),
        "only not_null",
    ),
    (
        "control-not-null-differs",
        lambda w: w["contracts"]["ops_entity_counters"]["fields"]["current_value"]["dq_intent"].clear(),
        "differ from the control registry",
    ),
    ("control-without-contract", lambda w: w["contracts"].pop("ops_entity_counters"), "control table without a contract"),
    (
        "control-registry-table-missing-from-ops-tables",
        lambda w: w.update(controls=("ops_entity_counters", "ops_phantom")),
        "ops_phantom: a control-registry table missing",
    ),
    ("dormant-without-disposition", _dormant_without_disposition, "needs a pending_contract row_rule_source"),
    (
        "dormant-owner-unresolved",
        lambda w: w.update(roadmap="  - id: T4.3\n    exit_criteria:\n      - id: c1\n"),
        "needs a pending_contract row_rule_source",
    ),
    (
        "smoke-outside-the-frozen-set",
        lambda w: w["fs"]["ops_tables"].update(ops_other_smoke=w["fs"]["ops_tables"].pop("ops_smoke_events")),
        "frozen set",
    ),
    (
        "sidecar-table-also-has-a-contract",
        lambda w: w["contracts"].update(ops_priority_queue={"governance": {}, "fields": {}}),
        "two rule sources",
    ),
    (
        "contract-less-entry-carries-a-rule",
        lambda w: w["fs"]["ops_tables"]["ops_priority_queue"]["columns"]["rank"].update(min_length=2),
        "carries rule key(s) ['min_length']",
    ),
    ("ops-yaml-table-missing-from-the-registry", lambda w: w.update(ops_yaml={"demo_scd2", "ghost"}), "names table 'ghost'"),
    ("tables-block-widened", lambda w: w["fs"]["tables"].update(extra={}), "tables: block holds"),
]


def test_the_fixture_registry_passes(tmp_path: Path) -> None:
    errors, examined = _run(tmp_path, _world())
    assert errors == []
    assert examined >= 10


@pytest.mark.parametrize(("arm", "mutate", "message"), _ARMS, ids=[a[0] for a in _ARMS])
def test_each_arm_fails_closed(tmp_path: Path, arm: str, mutate: Callable[[dict[str, Any]], None], message: str) -> None:
    world = copy.deepcopy(_world())
    mutate(world)
    errors, _ = _run(tmp_path, world)
    assert any(message in e for e in errors), (arm, errors)


def test_the_roadmap_owner_lookup_is_targeted() -> None:
    assert check.owner_resolves(_ROADMAP, "T4.3:c6")
    assert not check.owner_resolves(_ROADMAP, "T4.4:c6")
    assert not check.owner_resolves(_ROADMAP, "T9.9:c1") and not check.owner_resolves(_ROADMAP, "T4.3")
    assert not check.owner_resolves(_ROADMAP, "bogus:c6") and not check.owner_resolves(_ROADMAP, "T4.3:6")
    assert check.owner_resolves("  - id: T4.3\n    exit_criteria:\n      - id: c6\n", "T4.3:c6")  # the last item in the file


def test_registered_in_the_contracts_manifest() -> None:
    entry = next(e for e in ENTRIES if e.name == "validate_write_time_enforcement_conformance")
    assert (entry.module, entry.attr, entry.pre, entry.full_segment) == (
        "scripts.checks.contracts.validate_write_time_enforcement_conformance",
        "validate_write_time_enforcement_conformance",
        True,
        "full_after_lint",
    )
    globs = set(entry.pre_globs or ())
    for required in (
        "docs/contracts/*.yaml",
        "config/lambda/ducklake/field_semantics.yaml",
        "config/lambda/ducklake/field_semantics.static.yaml",
        "config/agent/data_quality/ops.yaml",
        "config/agent/data_quality/source_registry.yaml",
        "docs/ROADMAP-PLATFORM.yaml",
        "scripts/contract_rules.py",
        "src/row_rules/**",
        "src/common/ducklake_control_tables.py",
        "scripts/checks/contracts/**",
    ):
        assert required in globs, required


def test_the_registered_check_passes_on_the_real_tree(capsys: pytest.CaptureFixture[str]) -> None:
    failed: list[str] = []
    check.validate_write_time_enforcement_conformance(failed)
    assert failed == [] and "each match one arm" in capsys.readouterr().out


def test_the_registered_check_reports_its_accounting_and_unreadable_inputs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    registry.pop_declaration()
    failed: list[str] = []
    check.validate_write_time_enforcement_conformance(failed, root=tmp_path)
    declaration = registry.pop_declaration()
    assert any("inputs unreadable" in f for f in failed)
    assert declaration is not None and declaration.count == 0
    assert "FAIL" in capsys.readouterr().out
