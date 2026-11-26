"""Schema helpers for the verdict chain's fixture persona (post_verdict and keyed_run read them).

The contracts and the index are read as YAML. Only a route named in executor-personas.yaml's fixture_kind.personas has a
schema here; any other route gets None, so a result for it is never judged against a schema it does not have.
guidance_slots is the exception: it reads any persona's contract under the trusted root, fixture or not.
registry_key is the second whole-index read: it answers any personas row's registry_key, fixture or not.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
INDEX = "docs/contracts/executor-personas.yaml"
CONTRACTS = "docs/contracts/personas"
AGENT_KEY = re.compile(r"[a-z][a-z0-9_-]{0,63}")
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


def registry_key(root: Path, persona: str) -> str | None:
    doc = _load(root / INDEX)
    rows = doc.get("personas") if isinstance(doc, dict) else None
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and row.get("persona") == persona:
            key = row.get("registry_key")
            return key if isinstance(key, str) and AGENT_KEY.fullmatch(key) else None
    return None


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


_ROUTE = re.compile(r"^[a-z][a-z0-9_]*$")


def guidance_slots(root: Path, route_id: str) -> list[str]:
    """The guidance slots route_id's contract declares under root; [] when the contract or key is absent, else ValueError."""
    if not isinstance(route_id, str) or not _ROUTE.match(route_id):
        raise ValueError(f"route id {route_id!r} is off ^[a-z][a-z0-9_]*$")
    path = root / CONTRACTS / f"{route_id}.yaml"
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"contract {path.name} is unreadable") from exc
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"contract {path.name} does not parse") from exc
    if not isinstance(doc, dict):
        raise ValueError(f"contract {path.name} is not a mapping")
    if "guidance_slots" not in doc:
        return []
    slots = doc["guidance_slots"]
    if not isinstance(slots, list) or not all(isinstance(slot, str) for slot in slots):
        raise ValueError(f"contract {path.name} guidance_slots is not a list of strings")
    return list(slots)
