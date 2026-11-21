"""Contract projection (one row per resolved telemetry contract field, at call time) and the shared row shape."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from scripts.contracts import load_contract, resolve_refs

TABLES = (
    "telemetry_sessions",
    "telemetry_observations",
    "telemetry_agents",
    "telemetry_transcripts",
    "telemetry_persona_findings",
)

_ADDRESS_RE = re.compile(
    r"^(?P<table>[a-z_]+)\.(?P<column>[a-z0-9_]+)(?:#(?P<key>[A-Za-z0-9_.-]+))?(?:\[(?P<disc>[^\]]*)\])?$"
)


@dataclass
class Serving:
    verb: str
    agent_facing: bool
    masked: bool = False
    ambiguous: bool = False


@dataclass
class FactRow:
    address: str
    kind: str
    table: str = ""
    column: str = ""
    key: str = ""
    condition: str = ""
    type: str = ""
    description: str = ""
    populated_by: str = ""
    owner: str = ""
    producers: list[str] = field(default_factory=list)
    joins: list[str] = field(default_factory=list)
    serving: list[Serving] = field(default_factory=list)
    source: str = ""
    concept_words: list[str] = field(default_factory=list)
    declared: bool | None = None
    link_from: str = ""
    link_to: str = ""
    derived_by: str = ""


def parse_address(address: str) -> tuple[str, str, str, dict[str, str]]:
    """Split table.column[#key][[disc=value,...]] into (table, column, key, discriminators); ValueError if malformed."""
    match = _ADDRESS_RE.match(address)
    if match is None:
        raise ValueError(f"not a fact address: {address!r}")
    disc: dict[str, str] = {}
    raw = match.group("disc")
    if raw is not None:
        for part in raw.split(","):
            name, sep, value = part.partition("=")
            if not sep or not name or not value:
                raise ValueError(f"bad discriminator in {address!r}")
            disc[name] = value
    return match.group("table"), match.group("column"), match.group("key") or "", disc


def condition_of(disc: dict[str, str]) -> str:
    return ",".join(f"{k}={disc[k]}" for k in sorted(disc))


def tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]


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
