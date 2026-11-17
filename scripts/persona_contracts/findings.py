"""Projection of a verdict persona run's result into telemetry_persona_findings rows (rec-4434, Decision 199 cl.1).

Pure and derived at read: no warehouse, network or transcript access. The contract is
docs/contracts/telemetry_persona_findings.yaml; the persona result shapes are the verdict persona contracts' output_schema.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from scripts.persona_contracts import ROOT, load_contract, load_index

PROJECTION_VERSION = 1
BLOCK_MARKER = "PERSONA_RESULT: "

_SHA = re.compile(r"^[0-9a-f]{40}$")
_LEAF_KEYS = ("type", "enum")


@dataclass(frozen=True)
class RunIdentity:
    tenant_id: str
    project_id: str
    agent_run_id: str
    agent_type: str
    definition_sha256: str | None
    lane: str


@dataclass(frozen=True)
class FindingRow:
    tenant_id: str
    project_id: str
    agent_run_id: str
    finding_ordinal: int
    agent_type: str
    definition_sha256: str | None
    lane: str
    kind: str
    verdict: str | None
    finding_id: str | None
    severity: str | None
    origin: str | None
    anchor: str | None
    subject_sha: str | None
    subject_dirty: bool | None
    parse_status: str
    projection_version: int
    source_event_id: str | None


def _str(item: dict[str, Any], key: str) -> str | None:
    value = item.get(key)
    return value if isinstance(value, str) else None


def _dec(item: dict[str, Any]) -> str | None:
    value = item.get("decision")
    return f"dec:{value}" if isinstance(value, int) and not isinstance(value, bool) else None


@dataclass(frozen=True)
class ListSpec:
    path: str
    kind: str
    keys: tuple[str, ...]
    finding_id: Callable[[dict[str, Any]], str | None]
    severity: Callable[[dict[str, Any]], str | None]
    origin: Callable[[dict[str, Any]], str | None]
    anchor: Callable[[dict[str, Any]], str | None]


def _no(_: dict[str, Any]) -> str | None:
    return None


_REVIEW = ListSpec(
    "findings",
    "finding",
    ("id", "severity", "origin", "anchor"),
    lambda i: _str(i, "id"),
    lambda i: _str(i, "severity"),
    lambda i: _str(i, "origin"),
    lambda i: _str(i, "anchor"),
)

LISTS: dict[str, tuple[ListSpec, ...]] = {
    "code_reviewer": (_REVIEW,),
    "plan_critic": (_REVIEW,),
    "decision_scout": (
        ListSpec(
            "contradictions",
            "contradiction",
            ("id", "decision", "severity"),
            lambda i: _str(i, "id"),
            lambda i: _str(i, "severity"),
            _no,
            _dec,
        ),
        ListSpec(
            "spirit",
            "spirit",
            ("id", "target", "severity"),
            lambda i: _str(i, "id"),
            lambda i: _str(i, "severity"),
            _no,
            lambda i: _str(i, "target"),
        ),
        ListSpec(
            "roadmap.rows",
            "roadmap",
            ("id", "item", "lane"),
            lambda i: _str(i, "id"),
            lambda i: _str(i, "lane"),
            _no,
            lambda i: _str(i, "item"),
        ),
        ListSpec(
            "cite",
            "cite",
            ("decision",),
            lambda i: _dec(i).replace("dec:", "cite:") if _dec(i) else None,
            _no,
            _no,
            _dec,
        ),
    ),
}


@lru_cache(maxsize=1)
def _personas() -> dict[str, tuple[str, dict[str, Any]]]:
    """registry_key -> (persona id, output_schema) for the personas this relation covers."""
    out: dict[str, tuple[str, dict[str, Any]]] = {}
    for row in load_index().personas:
        if row.persona in LISTS:
            out[row.registry_key] = (row.persona, load_contract(ROOT / row.contract).output_schema)
    return out


def _leaf(schema: dict[str, Any]) -> dict[str, Any]:
    return {k: schema[k] for k in _LEAF_KEYS if k in schema}


def _node(path: str, schema: dict[str, Any]) -> dict[str, Any]:
    for part in path.split("."):
        schema = schema["properties"][part]
    return schema


def projected_schema(persona: str, output_schema: dict[str, Any]) -> dict[str, Any]:
    """The persona's output_schema restricted to the verdict and the mapped finding-list fields."""
    props = output_schema["properties"]
    root: dict[str, Any] = {"type": "object", "properties": {"verdict": _leaf(props["verdict"])}, "required": ["verdict"]}
    for spec in LISTS[persona]:
        item = _node(spec.path, output_schema)["items"]
        item_schema = {
            "type": "object",
            "properties": {k: _leaf(item["properties"][k]) for k in spec.keys},
            "required": [k for k in item.get("required", []) if k in spec.keys],
        }
        parts = spec.path.split(".")
        cursor = root["properties"]
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {"type": "object", "properties": {}})["properties"]
        cursor[parts[-1]] = {"type": "array", "items": item_schema}
    return root


_TYPES: dict[str, Callable[[Any], bool]] = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
}


def _valid(value: Any, schema: dict[str, Any]) -> bool:
    kind = schema.get("type")
    if kind is not None and not _TYPES[kind](value):
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    if isinstance(value, dict):
        if any(k not in value for k in schema.get("required", [])):
            return False
        for key, sub in schema.get("properties", {}).items():
            if key in value and not _valid(value[key], sub):
                return False
    if isinstance(value, list) and "items" in schema:
        return all(_valid(v, schema["items"]) for v in value)
    return True


def _read_block(text: str) -> tuple[str, Any]:
    lines = [line for line in text.splitlines() if line.startswith(BLOCK_MARKER)]
    if not lines:
        return "block_absent", None
    try:
        return "parsed", json.loads(lines[-1][len(BLOCK_MARKER) :])
    except (ValueError, RecursionError):
        return "block_invalid", None


def _sha(value: Any) -> str | None:
    return value if isinstance(value, str) and _SHA.match(value) else None


def _items(obj: dict[str, Any], path: str) -> list[dict[str, Any]]:
    node: Any = obj
    for part in path.split("."):
        node = node.get(part) if isinstance(node, dict) else None
    return [i for i in node if isinstance(i, dict)] if isinstance(node, list) else []


def project_result(
    identity: RunIdentity,
    *,
    text: str | None = None,
    result: dict[str, Any] | None = None,
    subject_sha: str | None = None,
    source_event_id: str | None = None,
) -> list[FindingRow]:
    persona = _personas().get(identity.agent_type)
    if persona is None:
        return []
    persona_id, output_schema = persona
    status = "content_unavailable"
    obj: Any = None
    if result is not None:
        status, obj = "parsed", result
    elif text is not None:
        status, obj = _read_block(text)
    if status == "parsed" and not _valid(obj, projected_schema(persona_id, output_schema)):
        status = "block_invalid"
    parsed = status == "parsed"
    sha = _sha(subject_sha) if subject_sha is not None else (_sha(obj.get("subject_sha")) if parsed else None)
    dirty = obj.get("subject_dirty") if parsed else None
    dirty = dirty if isinstance(dirty, bool) else None
    verdict = obj["verdict"] if parsed else None

    def row(ordinal: int, kind: str, fid: str | None, sev: str | None, origin: str | None, anchor: str | None) -> FindingRow:
        return FindingRow(
            identity.tenant_id,
            identity.project_id,
            identity.agent_run_id,
            ordinal,
            identity.agent_type,
            identity.definition_sha256,
            identity.lane,
            kind,
            verdict,
            fid,
            sev,
            origin,
            anchor,
            sha,
            dirty,
            status,
            PROJECTION_VERSION,
            source_event_id,
        )

    rows = [row(0, "verdict", None, None, None, None)]
    if parsed:
        for spec in LISTS[persona_id]:
            for item in _items(obj, spec.path):
                rows.append(
                    row(len(rows), spec.kind, spec.finding_id(item), spec.severity(item), spec.origin(item), spec.anchor(item))
                )
    return rows
