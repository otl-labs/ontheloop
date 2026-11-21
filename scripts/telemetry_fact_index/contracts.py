"""Contract projection: one row per resolved field of the telemetry Class A contracts, at call time."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from scripts.contracts import load_contract, resolve_refs
from scripts.telemetry_fact_index.model import TABLES, FactRow, Serving

_READER_PREFIX = "reader_verb:"


def _field_lines(path: Path) -> dict[str, int]:
    node = yaml.compose(path.read_text(encoding="utf-8"))
    lines: dict[str, int] = {}
    if node is None or not hasattr(node, "value"):
        return lines
    for key, value in node.value:
        if key.value == "fields" and hasattr(value, "value"):
            for fkey, _ in value.value:
                lines[fkey.value] = fkey.start_mark.line + 1
    return lines


def _first_sentence(text: str) -> str:
    flat = " ".join(str(text).split())
    cut = flat.find(". ")
    return flat if cut < 0 else flat[: cut + 1]


def _kind_of(
    table: str, name: str, raw: dict[str, Any], realization: dict[str, Any], resolved: Any
) -> tuple[str, str, list[str], str]:
    derivation = (raw or {}).get("derivation") or getattr(resolved, "derivation", None) or {}
    if derivation.get("timing") == "read":
        return "read_derived", "", [], str(derivation.get("derived_by") or "")
    produced = [k for k, names in ((realization.get("produced") or {}).items()) if name in (names or [])]
    owners = [k for k, names in ((realization.get("unproduced") or {}).items()) if name in (names or [])]
    if produced:
        return "stored", "", produced, ""
    if owners:
        return "unproduced", owners[0], [], ""
    raise ValueError(f"{table}.{name}: stored field is in neither realization.produced nor unproduced")


def _required_when_address(table: str, name: str, required_when: dict[str, Any]) -> str:
    keys = sorted(required_when)
    return f"{table}.{name}[" + ",".join(f"{k}={'|'.join(str(v) for v in required_when[k])}" for k in keys) + "]"


def project_contracts(root: Path, registered_verbs: set[str]) -> list[FactRow]:
    """Rows for the five telemetry contracts; `registered_verbs` decides when a read-derived fact counts as served."""
    contracts_dir = root / "docs" / "contracts"
    rows: list[FactRow] = []
    for table in TABLES:
        path = contracts_dir / f"{table}.yaml"
        doc = load_contract(path)
        resolved = resolve_refs(doc, contracts_dir)
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        raw_fields = raw.get("fields") or {}
        realization = raw.get("realization") or {}
        lines = _field_lines(path)
        rel = f"docs/contracts/{table}.yaml"
        for name, spec in resolved.items():
            kind, owner, producers, derived_by = _kind_of(table, name, raw_fields.get(name) or {}, realization, spec)
            base = FactRow(
                address=f"{table}.{name}",
                kind=kind,
                table=table,
                column=name,
                type=str(spec.type or ""),
                description=_first_sentence(spec.description or "") if spec.description else "",
                populated_by=" ".join(str(spec.populated_by or "").split()),
                owner=owner,
                producers=producers,
                joins=list(spec.joins or []),
                source=f"{rel}:{lines.get(name, 0)}",
                derived_by=derived_by,
            )
            base.concept_words = []
            if kind == "read_derived":
                verb = derived_by[len(_READER_PREFIX) :] if derived_by.startswith(_READER_PREFIX) else ""
                if verb and verb in registered_verbs:
                    base.serving.append(Serving(verb, True))
            rows.append(base)
            required_when = (spec.dq_intent or {}).get("required_when")
            if isinstance(required_when, dict) and required_when:
                extra = FactRow(**base.__dict__)
                extra.address = _required_when_address(table, name, required_when)
                extra.condition = extra.address[extra.address.index("[") + 1 : -1]
                extra.serving = list(base.serving)
                rows.append(extra)
    return rows
