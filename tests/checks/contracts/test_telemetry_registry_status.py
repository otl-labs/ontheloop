"""The six telemetry registry entries stay pre_production in scope telemetry_catalog, with the production catalog path named.

A failing guard names the missing text -- fix the document, not the test. The status vocabulary for a table in a non-ops
catalog is rec-4483's; no plan in slice 2c sets live.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[3]
_SIDECAR = _ROOT / "config" / "lambda" / "ducklake" / "field_semantics.static.yaml"
_ENTRIES = (
    "telemetry_sessions",
    "telemetry_observations",
    "telemetry_transcripts",
    "telemetry_agents",
    "ops_tenants",
    "ops_projects",
)


def test_telemetry_registry_stays_pre_production_with_the_production_column_path() -> None:
    doc = yaml.safe_load(_SIDECAR.read_text(encoding="utf-8"))
    for name in _ENTRIES:
        entry = doc["contract_table_ops"][name]
        assert entry["status"] == "pre_production" and entry["reconcile_scope"] == "telemetry_catalog", name
        assert "exempt_reason" not in entry, name
        reason = " ".join(entry["scope_reason"].split())
        for needle in ("ducklake_telemetry", "provision_telemetry_catalog", "rec-4483", "--telemetry-hold"):
            assert needle in reason, (name, needle)
        assert "slice 2c" not in reason, name

    comment_block = _SIDECAR.read_text(encoding="utf-8").split("  telemetry_sessions:\n", 1)[0].rsplit("\n\n", 1)[-1]
    assert "slice 2c" not in comment_block and "rec-4483" in comment_block

    tenants = yaml.safe_load((_ROOT / "docs/contracts/ops_tenants.yaml").read_text(encoding="utf-8"))
    note = " ".join(tenants["governance_notes"].split())
    assert "rec-4483" in note and "slice 2c" not in note and "pre_production" in note
    assert "telemetry_catalog" in note and "scope_reason" in note and "exempt_reason" not in note
    for kept in ("rec-4121", "rec-4024", "Decision 181"):
        assert kept in note, kept
