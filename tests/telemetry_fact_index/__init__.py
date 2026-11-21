"""Tests for scripts.telemetry_fact_index; the synthetic-root builder the test modules share."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]


def _field(**extra: Any) -> dict[str, Any]:
    return {"type": "str", "iceberg_type": "string", "nullable": True, "description": "A field. More words.", **extra}


def _contract(table: str, fields: dict[str, Any], realization: dict[str, Any] | None) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "contract": {
            "id": table,
            "class": "A",
            "contract_version": 1,
            "status": "draft",
            "description": "d",
            "projects_to": {"artifact": "a", "table": table, "generator": "g"},
        },
        "fields": fields,
    }
    if realization is not None:
        doc["realization"] = realization
    return doc


def make_root(tmp_path: Path, overrides: dict[str, dict[str, Any]] | None = None) -> Path:
    """A tmp repo root holding five minimal telemetry contracts plus the two Class C targets they reference."""
    contracts = tmp_path / "docs" / "contracts"
    contracts.mkdir(parents=True)
    for name in ("tenant-id.yaml", "project-id.yaml"):
        shutil.copy(REPO / "docs" / "contracts" / name, contracts / name)
    docs: dict[str, dict[str, Any]] = {
        "telemetry_sessions": _contract(
            "telemetry_sessions",
            {
                "session_id": _field(),
                "workflow": _field(),
                "surface": _field(
                    derivation={"timing": "read", "derived_by": "reader_verb:session_surface", "realized": False}
                ),
            },
            {"produced": {"claude_code": ["session_id", "workflow"]}, "unproduced": {}},
        ),
        "telemetry_observations": _contract(
            "telemetry_observations",
            {
                "name": _field(dq_intent={"required_when": {"event_kind": ["point"], "observation_type": ["process_event"]}}),
                "output_sha256": _field(dq_intent={"required_when": {"output_capture": ["captured", "omitted_oversize"]}}),
                "legacy": _field(),
                "metadata": _field(),
            },
            {"produced": {"claude_code": ["name", "output_sha256", "metadata"]}, "unproduced": {"rec-1": ["legacy"]}},
        ),
        "telemetry_agents": _contract(
            "telemetry_agents", {"agent_type": _field()}, {"produced": {"claude_code": ["agent_type"]}}
        ),
        "telemetry_transcripts": _contract(
            "telemetry_transcripts",
            {
                "content": _field(),
                "content_sha256": _field(),
                "purpose": _field(dq_intent={"accepted_values": {"values": ["tool_input", "prompt"]}}),
            },
            {"produced": {"claude_code": ["content", "content_sha256", "purpose"]}},
        ),
        "telemetry_persona_findings": _contract(
            "telemetry_persona_findings",
            {
                "tenant_id": {
                    "$ref": "tenant-id.yaml#/contract/fields/tenant_id",
                    "derivation": {"timing": "read", "derived_by": "library:scripts.x.y", "realized": False},
                },
                "verdict": _field(derivation={"timing": "read", "derived_by": "library:scripts.x.y", "realized": False}),
            },
            None,
        ),
    }
    for table, doc in (overrides or {}).items():
        docs[table] = doc
    for table, doc in docs.items():
        (contracts / f"{table}.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    return tmp_path
