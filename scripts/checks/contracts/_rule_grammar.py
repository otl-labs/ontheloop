"""Rule-declaration grammar for validate_ops_rule_source (rec-4158; Decision 210 cl.3).

A declared rule is an enforced rule: the `enforced` key is retired from every contract under docs/contracts/, and staging
is spelled only by an exclude_before date or an owned write_time_exemptions entry. This module checks that a writer
table's effective dq_intent is well-formed (per table_class vocabulary, leg shapes, dates, regex subset, exemptions) and
scans every contract for the retired key.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import Any

import yaml

from scripts import contract_rules
from scripts.field_semantics_event_projection import KNOWN_DQ_KEYS as EVENT_DQ_KEYS
from src.row_rules.rules import EXEMPTABLE_LEGS, PRODUCER_SIDE_KINDS, WAIVABLE_KINDS

SCD2_VOCABULARY = frozenset(
    {
        "not_null",
        "accepted_values",
        "min_length",
        "pattern",
        "array_element_format",
        "array_element_reference",
        "acceptance_lint",
        "glossary_lint",
        "not_before",
        "write_time_exemptions",
    }
)
CONTROL_VOCABULARY = frozenset({"not_null"})
_SUBKEYS: dict[str, frozenset[str]] = {
    "not_null": frozenset({"exclude_before"}),
    "accepted_values": frozenset({"values", "note", "exclude_before"}),
    "min_length": frozenset({"value", "exclude_before"}),
    "pattern": frozenset({"value", "exclude_before"}),
    "array_element_format": frozenset({"pattern", "exclude_before"}),
    "array_element_reference": frozenset({"exclude_before"}),
    "acceptance_lint": frozenset({"require_discrimination", "exclude_before"}),
    "glossary_lint": frozenset({"exclude_before"}),
}
PRODUCER_CLASS = {
    "array_element_reference": "cross_row",
    "acceptance_lint": "repository_state",
    "glossary_lint": "repository_state",
}
EXEMPTION_CLASSES = frozenset({"cross_row", "repository_state", "temporary"})
CONTENT_THRESHOLD = "content_inline_threshold_bytes"
RECS_DATE_FLOOR = "2026-05-01"
_REC_ID = re.compile(r"^rec-[0-9]+$")
_ISO_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_LOOKAROUND = ("(?=", "(?!", "(?<=", "(?<!", "(?P=")
_DQ_BLOCKS = ("dq_intent", "dq_intent_local")


def regex_subset_error(pattern: str) -> str | None:
    """Lookaround and backreferences are outside the subset RE2 and Python share."""
    try:
        re.compile(pattern)
    except re.error as exc:
        return f"is not a valid regex ({exc})"
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\":
            if i + 1 < len(pattern) and pattern[i + 1] in "123456789k":
                return "uses a backreference (outside the RE2-and-Python subset)"
            i += 2
            continue
        if any(pattern.startswith(token, i) for token in _LOOKAROUND):
            return "uses lookaround or a named backreference (outside the RE2-and-Python subset)"
        i += 1
    return None


def iso_date_error(label: str, value: Any, floor: str | None = None) -> str | None:
    if not (isinstance(value, str) and _ISO_DATE.match(value)):
        return f"{label} {value!r} is not an ISO date string"
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return f"{label} {value!r} is not a real calendar date"
    if floor is not None and value < floor:
        return f"{label} {value} is earlier than the Decision 64 anchor {floor}"
    return None


def _leg_well_formed(kind: str, value: Any) -> bool:
    if kind == "not_before":
        return isinstance(value, str) and bool(value)
    if kind == "pattern":
        return isinstance(value, (str, dict))
    return isinstance(value, dict)


def _leg_shape(kind: str) -> str:
    return {"not_before": "a column name", "pattern": "a regex string or a mapping"}.get(kind, "a mapping")


def _patterns_of(kind: str, value: Any) -> list[str]:
    if kind == "pattern_when":
        entries = value if isinstance(value, list) else []
        return [e["pattern"] for e in entries if isinstance(e, dict) and isinstance(e.get("pattern"), str)]
    leg = dict(value) if isinstance(value, dict) else {"value": value}
    pat = leg.get("value") if kind == "pattern" else leg.get("pattern") if kind == "array_element_format" else None
    return [pat] if isinstance(pat, str) else []


def _not_null_errors(where: str, value: Any, floor: str | None) -> list[str]:
    if value is True:
        return []
    if not isinstance(value, dict):
        return [
            f"{where}: not_null leg must be true or an exclude_before mapping, got {value!r} (an optional column has no leg)"
        ]
    errors = (
        [f"{where}: not_null carries unknown sub-key(s) {sorted(set(value) - _SUBKEYS['not_null'])}"]
        if set(value) - _SUBKEYS["not_null"]
        else []
    )
    if "exclude_before" in value:
        err = iso_date_error("exclude_before", value["exclude_before"], floor)
        errors.extend([f"{where}: not_null {err}"] if err else [])
    return errors


def _leg_errors(floor: str | None, where: str, kind: str, value: Any, exemptions: dict[str, Any]) -> list[str]:
    if kind == "not_null":
        return _not_null_errors(where, value, floor)
    if kind in EVENT_DQ_KEYS and kind not in _SUBKEYS and kind not in ("pattern", "not_before"):
        return [f"{where}: {kind} pattern {err}" for pat in _patterns_of(kind, value) if (err := regex_subset_error(pat))]
    if not _leg_well_formed(kind, value):
        return [f"{where}: {kind} leg must be {_leg_shape(kind)}, got {type(value).__name__}"]
    errors: list[str] = []
    if isinstance(value, dict):
        extra = set(value) - _SUBKEYS.get(kind, frozenset())
        if extra:
            errors.append(f"{where}: {kind} carries unknown sub-key(s) {sorted(extra)}")
        if "exclude_before" in value:
            err = iso_date_error("exclude_before", value["exclude_before"], floor)
            if err:
                errors.append(f"{where}: {kind} {err}")
    for pat in _patterns_of(kind, value):
        err = regex_subset_error(pat)
        if err:
            errors.append(f"{where}: {kind} pattern {err}")
    if kind in PRODUCER_SIDE_KINDS and kind not in exemptions:
        errors.append(f"{where}: producer-side rule {kind!r} needs a write_time_exemptions entry")
    return errors


def _declared_on_field(kind: str, intent: dict[str, Any]) -> bool:
    if kind in EXEMPTABLE_LEGS:
        return CONTENT_THRESHOLD in intent
    return kind in intent


def _exemption_errors(where: str, kind: str, why: Any, intent: dict[str, Any], root: Path) -> list[str]:
    if not isinstance(why, dict):
        return [f"{where}: exemption {kind!r} must be a mapping with class, reason and owner"]
    errors: list[str] = []
    if kind not in WAIVABLE_KINDS | PRODUCER_SIDE_KINDS | EXEMPTABLE_LEGS:
        errors.append(f"{where}: exemption {kind!r} names a kind that cannot be exempted")
    elif not _declared_on_field(kind, intent):
        errors.append(f"{where}: exemption {kind!r} names a rule the field does not declare")
    cls, reason, owner = why.get("class"), why.get("reason"), why.get("owner")
    if cls not in EXEMPTION_CLASSES:
        errors.append(f"{where}: exemption {kind!r} class {cls!r} is not one of {sorted(EXEMPTION_CLASSES)}")
    elif cls != "temporary" and PRODUCER_CLASS.get(kind) != cls:
        errors.append(f"{where}: exemption {kind!r} class {cls!r} is not bound to that kind (use temporary with a review_by)")
    if not (isinstance(reason, str) and reason.strip()):
        errors.append(f"{where}: exemption {kind!r} needs a reason")
    if not isinstance(owner, str) or not owner:
        errors.append(f"{where}: exemption {kind!r} needs an owner")
    elif cls in ("cross_row", "temporary") and not _REC_ID.match(owner):
        errors.append(f"{where}: exemption {kind!r} owner {owner!r} must be a rec id")
    elif cls == "repository_state" and not (root / owner).is_file():
        errors.append(f"{where}: exemption {kind!r} owner {owner!r} must be an existing repository path")
    review_by = why.get("review_by")
    if cls == "temporary":
        err = "needs a review_by (an ISO date)" if review_by is None else iso_date_error("review_by", review_by)
        if err:
            errors.append(f"{where}: exemption {kind!r} {err}")
    elif review_by is not None:
        errors.append(f"{where}: exemption {kind!r} review_by is only for a temporary exemption")
    return errors


def grammar_errors(
    table: str, table_class: str, raw_fields: dict[str, Any], intents: dict[str, dict[str, Any]], root: Path
) -> list[str]:
    vocabulary = {"event": EVENT_DQ_KEYS, "control": CONTROL_VOCABULARY}.get(table_class, SCD2_VOCABULARY)
    floor = RECS_DATE_FLOOR if table == "ops_recommendations" else None
    errors: list[str] = []
    for column, intent in intents.items():
        where = f"{table}.{column}"
        unknown = set(intent) - vocabulary
        if unknown:
            errors.append(f"{where}: rule key(s) {sorted(unknown)} outside the {table_class} vocabulary")
        exemptions = intent.get("write_time_exemptions") or {}
        if not isinstance(exemptions, dict):
            errors.append(f"{where}: write_time_exemptions must be a mapping of rule kind to exemption")
            exemptions = {}
        for kind, value in intent.items():
            if kind not in unknown and kind != "write_time_exemptions":
                errors.extend(_leg_errors(floor, where, kind, value, exemptions))
        for kind, why in exemptions.items():
            errors.extend(_exemption_errors(where, kind, why, intent, root))
        if (raw_fields.get(column) or {}).get("nullable") is False and intent.get("not_null") is not True:
            errors.append(f"{where}: a nullable false column without `not_null: true` (never dated, never absent)")
    return errors


def _enforced_paths(node: Any, path: str, out: list[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "enforced":
                out.append(path)
            _enforced_paths(value, f"{path}.{key}", out)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            _enforced_paths(value, f"{path}[{i}]", out)


def _scan_blocks(node: Any, path: str, out: list[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _DQ_BLOCKS:
                _enforced_paths(value, f"{path}.{key}", out)
            else:
                _scan_blocks(value, f"{path}.{key}", out)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            _scan_blocks(value, f"{path}[{i}]", out)


def enforced_ban_errors(contracts_dir: Path) -> tuple[list[str], int]:
    """Every contract under docs/contracts/ is scanned for the retired `enforced` key inside a dq_intent block."""
    errors: list[str] = []
    scanned = 0
    for path in sorted(contracts_dir.glob("*.yaml")):
        try:
            doc = contract_rules.parse_yaml(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        scanned += 1
        found: list[str] = []
        _scan_blocks(doc, "", found)
        errors.extend(
            f"{path.name}{loc}: the `enforced` key is retired (Decision 210): a declared rule is enforced" for loc in found
        )
    return errors, scanned


def table_class_of(raw: dict[str, Any]) -> str:
    cls = str(((raw.get("governance") or {}).get("table_class")) or "scd2").lower()
    return cls if cls in ("event", "control") else "scd2"


def read_table(table: str, contracts_dir: Path, repo_root: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]], str]:
    raw = contract_rules.load_yaml(contracts_dir / f"{table}.yaml")
    intents = contract_rules.read_intents(
        table, {}, contracts_dir=contracts_dir, repo_root=repo_root, registry_key_ref=contract_rules.REGISTRY_KEY_REF
    )
    return raw.get("fields") or {}, intents, table_class_of(raw)
