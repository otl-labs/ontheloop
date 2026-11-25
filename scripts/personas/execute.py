"""Schema helpers for the verdict chain's fixture persona (post_verdict and keyed_run read them).

The contracts and the index are read as YAML. Only a route named in executor-personas.yaml's fixture_kind.personas has a
schema here; any other route gets None, so a result for it is never judged against a schema it does not have.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
INDEX = "docs/contracts/executor-personas.yaml"
CONTRACTS = "docs/contracts/personas"
KINDS = {"input": "input_schema", "output": "output_schema"}


def _load(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None


def fixture_personas(root: Path) -> list[str]:
    doc = _load(root / INDEX)
    block = doc.get("fixture_kind") if isinstance(doc, dict) else None
    names = block.get("personas") if isinstance(block, dict) else None
    return [str(n) for n in names] if isinstance(names, list) else []


def _contract(root: Path, route_id: str) -> dict[str, Any] | None:
    if route_id not in fixture_personas(root):
        return None
    doc = _load(root / CONTRACTS / f"{route_id}.yaml")
    return doc if isinstance(doc, dict) else None


def schema_for(root: Path, route_id: str, kind: str) -> dict[str, Any] | None:
    contract = _contract(root, route_id)
    schema = contract.get(KINDS[kind]) if contract else None
    return schema if isinstance(schema, dict) else None


def failure_schema_for(root: Path, route_id: str) -> dict[str, Any] | None:
    contract = _contract(root, route_id)
    rows = contract.get("failure_outputs") if contract else None
    ids = [str(r["id"]) for r in rows or [] if isinstance(r, dict) and "id" in r]
    if not ids:
        return None
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["outcome", "failure", "detail"],
        "properties": {
            "outcome": {"const": "failure"},
            "failure": {"type": "string", "enum": ids},
            "detail": {"type": "string"},
        },
    }
