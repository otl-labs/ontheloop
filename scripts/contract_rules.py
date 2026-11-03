"""The ONE contract-rule reader (rec-4158 plan A; extracted from scripts/field_semantics_event_projection.py).

Reads a Class A contract's RAW dq_intent rules (never scripts/contracts.py's resolved view, which replaces a $ref
field's dq_intent wholesale and would drop the rules a local block layers over its target) and gives every consumer the
same effective per-field rule set: the telemetry projection, the ops rule-source check and the live census.

A rule is declared per field under dq_intent (or dq_intent_local over a $ref target). A date-gated rule carries
exclude_before, an ISO date: the rule binds rows created on or after it. An exemption sits beside the rule it exempts,
under write_time_exemptions keyed by the rule's kind.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACTS_DIR = REPO_ROOT / "docs" / "contracts"
REGISTRY_KEY_REF = ("source-lineage.yaml", "registry_key")

_LEG_META_KEYS = frozenset({"exclude_before", "enforced", "note"})
_EXEMPTION_REQUIRED = ("reason", "owner")


@dataclass(frozen=True)
class DeclaredRule:
    table: str
    column: str
    kind: str
    params: Mapping[str, Any] = field(default_factory=dict)
    exclude_before: str | None = None
    exemption: Mapping[str, Any] | None = None


_SAFE_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def parse_yaml(text: str) -> Any:
    """yaml.safe_load through libyaml when the build has it (the contract tree is parsed hundreds of times per run)."""
    return yaml.load(text, Loader=_SAFE_LOADER)  # noqa: S506 -- a safe loader, chosen for speed


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        data = parse_yaml(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read contract file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"contract {path} must be a YAML mapping")
    return data


def split_ref(ref: str) -> tuple[str, str]:
    file_part, _, fragment = ref.partition("#")
    return Path(file_part).name, fragment.rstrip("/").rsplit("/", 1)[-1]


def not_null_declared(value: Any) -> bool:
    """A not_null leg declares a rule by presence: `true` or a mapping. The legacy `{enforced: false}` (readable so a
    merge-base snapshot parses) and an absent leg declare none; any other legacy mapping is a rule."""
    if value is None or value is False:
        return False
    if isinstance(value, Mapping):
        return value.get("enforced") is not False
    return value is True


def _not_null_leg(table_id: str, column: str, value: Any) -> dict[str, Any] | bool:
    if value is None:
        return {}
    if isinstance(value, bool) and value:
        return True
    if not isinstance(value, Mapping):
        raise ValueError(f"{table_id}.{column}: rule kind 'not_null' must be true or a mapping, got {value!r}")
    return dict(value)


def _kept_subkeys(leg: dict[str, Any] | bool) -> dict[str, Any]:
    if isinstance(leg, bool):
        return {}
    return {k: v for k, v in leg.items() if k != "enforced"}


def layer_rules(table_id: str, name: str, inherited: dict[str, Any], local: dict[str, Any]) -> dict[str, Any]:
    """Layer a $ref field's local dq_intent over its raw target's: a local key may add a rule or tighten not_null, never
    drop an inherited rule, and a rule key both declare with different values fails closed. Not_null is read by
    presence on both sides before merging; the result is the presence form (no key, `True`, or a mapping of the kept
    sub-keys without `enforced`)."""
    merged = dict(inherited)
    merged.pop("not_null", None)
    for key, value in local.items():
        if key == "not_null":
            continue
        if key in inherited and inherited[key] != value:
            raise ValueError(f"{table_id}.{name}: dq_intent_local changes the inherited rule {key!r}")
        merged[key] = value
    legs = []
    for source in (inherited, local):
        raw = source.get("not_null")
        leg = _not_null_leg(table_id, name, raw)
        if raw is not None and not_null_declared(raw):
            legs.append(leg)
    if legs:
        kept: dict[str, Any] = {}
        for leg in legs:
            kept.update(_kept_subkeys(leg))
        merged["not_null"] = kept or True
    return merged


def registry_values(root: Path, target_doc: dict[str, Any]) -> list[str]:
    allowed = target_doc.get("allowed_values") or {}
    registry, key = allowed.get("registry"), allowed.get("key")
    if not registry or not key:
        raise ValueError("source-lineage allowed_values must name a registry and a key")
    entries = load_yaml(root / registry).get("entries") or []
    values = [entry[key] for entry in entries]
    if not values or len(set(values)) != len(values):
        raise ValueError(f"registry {registry} yields no values or duplicate {key} values")
    return values


def _resolved_value(spec: Any, name: str) -> Any:
    return getattr(spec, name) if hasattr(spec, name) else spec.get(name)


def read_intents(
    table_id: str,
    resolved_fields: dict[str, Any],
    *,
    contracts_dir: Path,
    repo_root: Path,
    registry_key_ref: tuple[str, str],
) -> dict[str, dict[str, Any]]:
    """Each field's row-rule dq_intent: the raw contract read again, with $ref targets layered under local blocks.

    A contract that is not in the contracts directory (a test fixture, never a registered table) has no raw $ref
    targets to read, so its resolved view is used as is.
    """
    path = contracts_dir / f"{table_id}.yaml"
    if not path.is_file():
        return {name: dict(_resolved_value(spec, "dq_intent") or {}) for name, spec in resolved_fields.items()}
    raw = load_yaml(path)
    out: dict[str, dict[str, Any]] = {}
    for name, spec in (raw.get("fields") or {}).items():
        ref = spec.get("$ref")
        if ref is None:
            out[name] = dict(spec.get("dq_intent") or {})
            continue
        target_file, target_field = split_ref(ref)
        target_doc = load_yaml(contracts_dir / target_file)
        target_spec = (target_doc.get("fields") or {}).get(target_field)
        if target_spec is None:
            raise ValueError(f"{table_id}.{name}: $ref target {ref!r} not found")
        intent = layer_rules(table_id, name, dict(target_spec.get("dq_intent") or {}), dict(spec.get("dq_intent_local") or {}))
        if (target_file, target_field) == registry_key_ref:
            if "accepted_values" in intent:
                raise ValueError(f"{table_id}.{name}: a registry-sourced field declares no accepted_values of its own")
            intent["accepted_values"] = {"values": registry_values(repo_root, target_doc)}
        out[name] = intent
    return out


def check_rule_keys(table_id: str, name: str, intent: dict[str, Any], allowed: frozenset[str]) -> None:
    unknown = set(intent) - allowed
    if unknown:
        raise ValueError(f"{table_id}.{name}: unknown dq_intent rule key(s) {sorted(unknown)} -- fail closed")
    accepted = intent.get("accepted_values")
    if accepted is not None and not (isinstance(accepted, dict) and isinstance(accepted.get("values"), list)):
        raise ValueError(f"{table_id}.{name}: accepted_values must carry a values list")
    exemptions = intent.get("write_time_exemptions")
    if exemptions is None:
        return
    if not isinstance(exemptions, dict):
        raise ValueError(f"{table_id}.{name}: write_time_exemptions must be a mapping of rule kind to exemption")
    for leg, why in exemptions.items():
        if not isinstance(why, dict) or not all(why.get(k) for k in _EXEMPTION_REQUIRED):
            raise ValueError(f"{table_id}.{name}: exemption {leg!r} needs a reason and an owner")
        if "class" in why and not (isinstance(why["class"], str) and why["class"]):
            raise ValueError(f"{table_id}.{name}: exemption {leg!r} class must be a non-empty string when present")


def _normalise_leg(value: Any) -> tuple[dict[str, Any], str | None]:
    if isinstance(value, Mapping):
        return {k: v for k, v in value.items() if k not in _LEG_META_KEYS}, value.get("exclude_before")
    return {"value": value}, None


def declared_rules(
    table_id: str,
    *,
    contracts_dir: Path = CONTRACTS_DIR,
    repo_root: Path = REPO_ROOT,
    registry_key_ref: tuple[str, str] = REGISTRY_KEY_REF,
) -> tuple[DeclaredRule, ...]:
    """Every rule the contract declares, field by field in contract order. A declared rule is an enforced rule: not_null
    is read by presence (the legacy `{enforced: false}` spelling declares none); every other leg is a rule."""
    if not (contracts_dir / f"{table_id}.yaml").is_file():
        raise ValueError(f"no contract for table {table_id!r} in {contracts_dir}")
    intents = read_intents(table_id, {}, contracts_dir=contracts_dir, repo_root=repo_root, registry_key_ref=registry_key_ref)
    rules: list[DeclaredRule] = []
    for column, intent in intents.items():
        exemptions = intent.get("write_time_exemptions") or {}
        for kind, value in intent.items():
            if kind == "write_time_exemptions":
                continue
            if kind == "not_null":
                _not_null_leg(table_id, column, value)
                if not not_null_declared(value):
                    continue
            params, exclude_before = ({}, None) if kind == "not_null" and value is True else _normalise_leg(value)
            rules.append(DeclaredRule(table_id, column, kind, params, exclude_before, exemptions.get(kind)))
    return tuple(rules)
