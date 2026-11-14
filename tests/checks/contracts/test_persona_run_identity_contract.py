"""rec-4433 plan 1 acceptance: the persona key and definition hash are declared, mapped, registered and write-enforced.

Pure file reads plus src.row_rules; no network. The contract paths below are string literals so the fast tier's
affected-test selection picks this module up.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.checks.ops_governance.validate_reconcile_pending_gate import _pending_reconcile_map, _telemetry_hold_tables
from scripts.contracts import load_contract
from src.row_rules.rules import RowRuleError, RowRules, check_row

ROOT = Path(__file__).resolve().parents[3]
CONTRACTS = ROOT / "docs" / "contracts"
AGENTS = CONTRACTS / "telemetry_agents.yaml"
OBSERVATIONS = CONTRACTS / "telemetry_observations.yaml"
PERSONAS = CONTRACTS / "executor-personas.yaml"
PROJECTION = ROOT / "config" / "lambda" / "ducklake" / "field_semantics.yaml"
STATIC = ROOT / "config" / "lambda" / "ducklake" / "field_semantics.static.yaml"
REGISTRY = ROOT / "config" / "agent" / "data_quality" / "source_registry.yaml"
CREATED = datetime(2026, 11, 12, tzinfo=UTC)
GOOD_HASH = hashlib.sha256(b"persona definition").hexdigest()
VERDICT_KEYS = {"code-review", "plan-critique", "decision-scout"}


def _yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _projection(table: str) -> dict[str, Any]:
    return _yaml(PROJECTION)["ops_tables"][table]


def _row(**over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {"trigger": "subagent_sync", "definition_sha256": GOOD_HASH}
    row.update(over)
    return row


def _check(row: dict[str, Any]) -> None:
    columns = _projection("telemetry_agents")["columns"]
    rules = RowRules.from_projection({"definition_sha256": columns["definition_sha256"]})
    check_row("telemetry_agents", {"definition_sha256": row["definition_sha256"]}, rules, CREATED)


def test_definition_sha256_is_declared_and_write_enforced() -> None:
    field = load_contract(AGENTS).fields["definition_sha256"]
    assert field.nullable is True
    assert field.dq_intent_local is None or "not_null" not in field.dq_intent_local
    projected = _projection("telemetry_agents")["columns"]["definition_sha256"]
    assert projected["nullable"] is True
    assert "pattern" in projected
    _check(_row())
    _check(_row(definition_sha256=None))
    for bad in (GOOD_HASH.upper(), GOOD_HASH[:63], "sha256:" + GOOD_HASH):
        with pytest.raises(RowRuleError):
            _check(_row(definition_sha256=bad))


def test_persona_registry_keys_are_registered_and_tethered() -> None:
    rows = _yaml(PERSONAS)["personas"]
    keys = [r["registry_key"] for r in rows]
    assert len(keys) == len(set(keys))
    registered = {e["canonical_id"] for e in _yaml(REGISTRY)["entries"]}
    assert set(keys) <= registered
    for row in rows:
        if row["kind"] != "verdict":
            continue
        contract = _yaml(ROOT / row["contract"])
        assert row["registry_key"] == contract["skill_tether"]["skill"]
    agent_type = _projection("telemetry_agents")["columns"]["agent_type"]["accepted_values"]
    assert VERDICT_KEYS <= set(agent_type)
    source = _projection("ops_recommendations")["columns"]["source"]["accepted_values"]
    assert {"plan-critique", "decision-scout"} <= set(source)


def test_persona_key_contract_text() -> None:
    agents = load_contract(AGENTS)
    agent_type = agents.fields["agent_type"]
    text = yaml.safe_dump(
        {"notes": agent_type.governance_notes_local, "shapes": {k: v.agent_type for k, v in agents.run_shapes.items()}}
    )
    assert text.count("registry_key") >= 3
    parity = load_contract(OBSERVATIONS).fields["persona_backend"].semantics
    assert "(telemetry_agents.agent_type, persona_backend)" in parity
    for path in (AGENTS, OBSERVATIONS):
        assert "telemetry_agents.agent_name, persona_backend" not in path.read_text(encoding="utf-8")
    assert "prompt_hash" not in load_contract(OBSERVATIONS).fields["metadata"].semantics


def test_definition_sha256_is_pending_or_reconciled() -> None:
    sidecar = _yaml(STATIC)
    ops = sidecar["contract_table_ops"]["telemetry_agents"]
    pending = "definition_sha256" in ops["pending_reconcile"]["history"]
    reconciled = "definition_sha256" in ops["reconciled"]["history"]
    assert pending != reconciled
    if pending:
        assert "telemetry_agents" in _telemetry_hold_tables(sidecar)
        assert "definition_sha256" in _pending_reconcile_map(sidecar)["telemetry_agents"]["history"]
    else:
        evidence = ops["reconciled"]["history"]["definition_sha256"]["evidence"]
        assert "telemetry_agents.definition_sha256" in evidence and "added" in evidence
