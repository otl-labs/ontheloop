"""rec-4416 acceptance (T3.22 c5): the top-level agent run shape is declared and write-enforced.

Pure file reads plus src.row_rules; no network.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.contracts import load_contract
from scripts.schema_to_field_semantics import _enforce_reconcile_pending_subset
from src.row_rules.rules import RowRuleError, RowRules, check_row

ROOT = Path(__file__).resolve().parents[3]
CONTRACTS = ROOT / "docs" / "contracts"
PROJECTION = ROOT / "config" / "lambda" / "ducklake" / "field_semantics.yaml"
CREATED = datetime(2026, 10, 29, tzinfo=UTC)
EMPTY_SHA = hashlib.sha256(b"").hexdigest()

SUB_TRIGGERS = ["subagent_sync", "subagent_background"]
TOP_TRIGGERS = ["workflow_run", "workflow_dispatch", "push", "schedule", "eventbridge", "manual", "smoke_test"]


def _projection(table: str) -> dict[str, Any]:
    return yaml.safe_load(PROJECTION.read_text(encoding="utf-8"))["ops_tables"][table]


def _rules(table: str, columns: list[str]) -> RowRules:
    entry = _projection(table)["columns"]
    return RowRules.from_projection({name: entry[name] for name in columns})


def _agent_row(**over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "trigger": "workflow_run",
        "entity_ref": "0b7e5a1c-1111-4222-8333-444455556666/run",
        "host_run_ref": "gha:1/1/rca/1",
    }
    row.update(over)
    return row


def _refused(table: str, row: dict[str, Any], rules: RowRules, rule: str, column: str) -> None:
    with pytest.raises(RowRuleError) as raised:
        check_row(table, row, rules, CREATED)
    assert rule in str(raised.value) and column in str(raised.value)


def test_top_level_run_shape_is_declared_and_write_enforced() -> None:
    agents = load_contract(CONTRACTS / "telemetry_agents.yaml")
    shapes = agents.run_shapes
    assert shapes is not None and set(shapes) == {"sub_agent", "top_level"}
    sub, top = shapes["sub_agent"], shapes["top_level"]
    trigger = agents.fields["trigger"]
    allowed = trigger.dq_intent["accepted_values"]["values"]
    assert not set(sub.triggers) & set(top.triggers)
    assert set(sub.triggers) | set(top.triggers) == set(allowed)
    assert (top.session_id, top.observation_id, top.entity_ref, top.host_run_ref) == (
        "root",
        "null",
        "<root session entity_ref>/run",
        "required",
    )
    assert (sub.session_id, sub.host_run_ref) == ("child", "null")

    entity_when = agents.fields["entity_ref"].dq_intent_local["pattern_when"]
    assert set(top.triggers) <= set(entity_when[0]["when"]["trigger"])

    projected = _projection("telemetry_agents")["columns"]
    assert projected["host_run_ref"]["nullable"] is True and "pattern" in projected["host_run_ref"]
    assert projected["trigger"]["accepted_values"] == allowed
    assert projected["entity_ref"]["pattern_when"][0]["pattern"] == "^.+/run$"

    rules = _rules("telemetry_agents", ["trigger", "entity_ref", "host_run_ref"])
    table = "telemetry_agents"
    check_row(table, _agent_row(), rules, CREATED)
    check_row(table, _agent_row(trigger="subagent_sync", entity_ref="toolu_01abc", host_run_ref=None), rules, CREATED)
    _refused(table, _agent_row(trigger="cron_workflow"), rules, "accepted_values", "trigger")
    _refused(table, _agent_row(host_run_ref="k8s:x"), rules, "pattern", "host_run_ref")
    _refused(table, _agent_row(host_run_ref="gha:1"), rules, "pattern", "host_run_ref")
    _refused(table, _agent_row(entity_ref="0b7e5a1c-1111-4222-8333-444455556666"), rules, "pattern_when", "entity_ref")
    _refused(
        table,
        _agent_row(trigger="subagent_background", entity_ref="toolu_01abc"),
        rules,
        "null_or_zero_when",
        "host_run_ref",
    )
    # (g) no ARN can match; the account-shaped run is built at runtime, never typed
    arn = ":".join(["arn", "aws", "lambda", "r", "1" * 12, "function", "f"])
    _refused(table, _agent_row(host_run_ref=f"local:x/{arn}"), rules, "pattern", "host_run_ref")
    _refused(table, _agent_row(host_run_ref="gha:1/1/rca/0"), rules, "pattern", "host_run_ref")

    sessions = load_contract(CONTRACTS / "telemetry_sessions.yaml").fields["surface"]
    assert sessions.derivation["timing"] == "read" and sessions.derivation["realized"] is False
    for value in ("interactive", "subagent", "ci_agent", "ci_verdict", "fargate_persona", "lambda_agent", "local_agent"):
        assert value in sessions.description
    assert "surface" not in _projection("telemetry_sessions")["columns"]

    columns = ["content_omitted_reason", "content_sha256", "content_bytes"]
    trules = _rules("telemetry_transcripts", columns)
    withheld = {"content_omitted_reason": "withheld_by_policy", "content_sha256": EMPTY_SHA, "content_bytes": 0}
    check_row("telemetry_transcripts", withheld, trules, CREATED)
    _refused("telemetry_transcripts", {**withheld, "content_sha256": "a" * 64}, trules, "pattern_when", "content_sha256")
    _refused("telemetry_transcripts", {**withheld, "content_bytes": 7}, trules, "null_or_zero_when", "content_bytes")

    lexicon = yaml.safe_load((CONTRACTS / "telemetry-lexicon.yaml").read_text(encoding="utf-8"))
    terms = {t["term"] for t in lexicon["lexicon"]["terms"]}
    assert {"top-level run", "host run ref", "surface"} <= terms

    realization = agents.realization
    assert realization is not None
    assert "host_run_ref" in realization.unproduced["T3.20:c9"]
    assert "T3.22:c5" not in realization.unproduced
    assert set(realization.unproduced["rec-4711"]) >= {"lambda_request_id", "findings_count"}


@pytest.mark.parametrize("reason", ["lives in ducklake_telemetry", None, "", "   "])
def test_telemetry_catalog_scope_needs_a_scope_reason(reason: str | None) -> None:
    entry: dict[str, Any] = {"reconcile_scope": "telemetry_catalog", "pending_reconcile": {"history": ["x"]}}
    if reason is not None:
        entry["scope_reason"] = reason
    if reason and reason.strip():
        _enforce_reconcile_pending_subset("telemetry_agents", entry, {"x"}, "append_only")
        return
    with pytest.raises(ValueError, match="scope_reason"):
        _enforce_reconcile_pending_subset("telemetry_agents", entry, {"x"}, "append_only")


def test_host_run_ref_production_add_is_reconciled_in_the_sidecar() -> None:
    sidecar = yaml.safe_load((ROOT / "config" / "lambda" / "ducklake" / "field_semantics.static.yaml").read_text("utf-8"))
    ops = sidecar["contract_table_ops"]["telemetry_agents"]
    assert ops["pending_reconcile"]["history"] == []
    entry = ops["reconciled"]["history"]["host_run_ref"]
    assert "telemetry_agents.host_run_ref" in entry["evidence"]
    assert "added" in entry["evidence"]
