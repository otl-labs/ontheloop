"""Merge-base ratchet for validate_ops_rule_source (rec-4158; Decision 210 cl.4, Decision 64, Decision 70).

contracts_enforcement.check_amendment_for_diff compares only description and semantics, so a rule-only diff was
unguarded. This module compares two snapshot trees (a base root and a head root, each holding docs/contracts/*.yaml, the
source registry and field_semantics.yaml) and reports every loosening or narrowing move that lacks its marker. It does no
git: the entry module hands it a materialised base. Free: accepted values added, a dated new SCD2 leg, any new
event-table leg, a new contract's founding legs, a first review_by on a class-less exemption.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scripts import contract_rules
from scripts.checks.contracts._rule_grammar import CONTENT_THRESHOLD, table_class_of
from src.row_rules.rules import EXEMPTABLE_LEGS

FIELD_SEMANTICS_REL = "config/lambda/ducklake/field_semantics.yaml"
REGISTRY_REL = "config/agent/data_quality/source_registry.yaml"
CENSUS_TOKEN = re.compile(r"census:\s*0 violators \(\d{4}-\d{2}-\d{2}\)")
_CONTENT_LEG_KEYS = {
    CONTENT_THRESHOLD: ("inline_within_threshold", "uri_above_threshold"),
    "full_output_cap_bytes": ("within_cap",),
    "integrity": ("integrity",),
}


@dataclass
class TableView:
    table_class: str
    rules: dict[tuple[str, str], contract_rules.DeclaredRule] = field(default_factory=dict)
    exemptions: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    sized: set[str] = field(default_factory=set)
    registry_sourced: set[str] = field(default_factory=set)
    amendments: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


def in_scope_tables(root: Path) -> list[str]:
    path = root / FIELD_SEMANTICS_REL
    if not path.is_file():
        return []
    doc = contract_rules.parse_yaml(path.read_text(encoding="utf-8")) or {}
    contracts = root / "docs" / "contracts"
    return [t for t in (doc.get("ops_tables") or {}) if (contracts / f"{t}.yaml").is_file()]


def registry_ids(root: Path) -> set[str]:
    path = root / REGISTRY_REL
    doc = contract_rules.parse_yaml(path.read_text(encoding="utf-8")) if path.is_file() else None
    entries = (doc or {}).get("entries") or []
    return {e["canonical_id"] for e in entries if isinstance(e, dict) and "canonical_id" in e}


def view_of(root: Path, table: str) -> TableView:
    contracts = root / "docs" / "contracts"
    raw = contract_rules.load_yaml(contracts / f"{table}.yaml")
    intents = contract_rules.read_intents(
        table, {}, contracts_dir=contracts, repo_root=root, registry_key_ref=contract_rules.REGISTRY_KEY_REF
    )
    view = TableView(table_class_of(raw))
    for rule in contract_rules.declared_rules(table, contracts_dir=contracts, repo_root=root):
        view.rules[(rule.column, rule.kind)] = rule
    for column, intent in intents.items():
        for kind, why in (intent.get("write_time_exemptions") or {}).items():
            view.exemptions[(column, kind)] = dict(why) if isinstance(why, dict) else {}
        if CONTENT_THRESHOLD in intent:
            view.sized.add(column)
    for column, spec in (raw.get("fields") or {}).items():
        view.amendments[column] = [e for e in (spec.get("amendment_log") or []) if isinstance(e, dict)]
        ref = spec.get("$ref")
        if ref and contract_rules.split_ref(ref) == contract_rules.REGISTRY_KEY_REF:
            view.registry_sourced.add(column)
    return view


def _gained(base: TableView, head: TableView, column: str) -> list[dict[str, Any]]:
    before = base.amendments.get(column, [])
    return [e for e in head.amendments.get(column, []) if e not in before]


def _census_entry(entries: list[dict[str, Any]]) -> bool:
    return any(e.get("semantic_break") is True and CENSUS_TOKEN.search(e.get("migration_story") or "") for e in entries)


def _break_entry(entries: list[dict[str, Any]]) -> bool:
    return any(e.get("semantic_break") is True for e in entries)


def _narrow_value_entry(entries: list[dict[str, Any]], removed: set[Any]) -> bool:
    return any(
        e.get("change_class") == "accepted_values_narrow"
        and e.get("semantic_break") is True
        and CENSUS_TOKEN.search(e.get("migration_story") or "")
        and all(str(v) in (e.get("migration_story") or "") for v in removed)
        for e in entries
    )


def _marked_temporary(base: TableView, column: str, kind: str) -> bool:
    keys = (kind, *_CONTENT_LEG_KEYS.get(kind, ()))
    return any((base.exemptions.get((column, k)) or {}).get("class") == "temporary" for k in keys)


def _params_narrowed(kind: str, base_params: dict[str, Any], head_params: dict[str, Any]) -> tuple[bool, set[Any]]:
    if kind != "accepted_values":
        return base_params != head_params, set()
    removed = set(base_params.get("values") or []) - set(head_params.get("values") or [])
    rest_b = {k: v for k, v in base_params.items() if k != "values"}
    rest_h = {k: v for k, v in head_params.items() if k != "values"}
    return rest_b != rest_h, removed


def _date_errors(where: str, b: str | None, h: str | None, entries: list[dict[str, Any]]) -> list[str]:
    if b is not None and h is None:
        return [f"ratchet: {where} exclude_before was removed (a date never moves later or is removed, Decision 64)"]
    if b is None and h is not None:
        return [f"ratchet: {where} exclude_before moved later (undated -> {h}); a date is never added to a live leg"]
    if b is not None and h is not None and h > b:
        return [f"ratchet: {where} exclude_before moved later ({b} -> {h})"]
    if b is not None and h is not None and h < b and not _census_entry(entries):
        message = "exclude_before moved earlier without a semantic-break entry carrying `census: 0 violators (<date>)`"
        return [f"ratchet: {where} {message}"]
    return []


def _leg_errors(table: str, key: tuple[str, str], base: TableView, head: TableView) -> list[str]:
    column, kind = key
    where = f"{table}.{column}.{kind}"
    if key not in head.rules:
        if _marked_temporary(base, column, kind):
            return []
        return [
            f"ratchet: {where} was removed; a removal needs a prior temporary exemption (mark-then-drop, Decision 210 cl.4)"
        ]
    b, h = base.rules[key], head.rules[key]
    entries = _gained(base, head, column)
    errors = _date_errors(where, b.exclude_before, h.exclude_before, entries)
    sourced = column in head.registry_sourced and kind == "accepted_values"
    narrowed, removed = (False, set()) if sourced else _params_narrowed(kind, dict(b.params), dict(h.params))
    if removed and not _narrow_value_entry(entries, removed):
        errors.append(
            f"ratchet: {where} lost value(s) {sorted(map(str, removed))} without an accepted_values_narrow entry "
            "(semantic_break true) naming them and carrying `census: 0 violators (<date>)`"
        )
    elif narrowed and not _census_entry(entries):
        errors.append(
            f"ratchet: {where} params narrowed without a semantic-break entry carrying `census: 0 violators (<date>)`"
        )
    return errors


def _new_leg_errors(table: str, key: tuple[str, str], head: TableView) -> list[str]:
    rule = head.rules[key]
    if head.table_class != "scd2" or rule.exclude_before is not None or rule.exemption or key in head.exemptions:
        return []
    message = "is a new SCD2 leg without an exclude_before (Decision 64: a new rule carries its own date)"
    return [f"ratchet: {table}.{key[0]}.{key[1]} {message}"]


def _existed(base: TableView, column: str, kind: str) -> bool:
    return column in base.sized if kind in EXEMPTABLE_LEGS else (column, kind) in base.rules


def _added_or_changed(table: str, base: TableView, head: TableView) -> list[str]:
    errors: list[str] = []
    for (column, kind), why in head.exemptions.items():
        where = f"{table}.{column}.{kind}"
        entries = _gained(base, head, column)
        prior = base.exemptions.get((column, kind))
        if prior is None:
            if _existed(base, column, kind) and not _break_entry(entries):
                errors.append(f"ratchet: {where} is a new exemption on an existing rule without a semantic-break field entry")
            continue
        if prior.get("class") is not None and why.get("class") != prior.get("class") and not _break_entry(entries):
            errors.append(
                f"ratchet: {where} exemption class changed ({prior.get('class')} -> {why.get('class')}) "
                "without a semantic-break entry"
            )
        old, new = prior.get("review_by"), why.get("review_by")
        if old is not None and new is not None and str(new) > str(old):
            named = any(
                e.get("change_class") == "governance_note_add" and str(new) in f"{e.get('summary')} {e.get('migration_story')}"
                for e in entries
            )
            if not named:
                errors.append(
                    f"ratchet: {where} review_by moved later ({old} -> {new}) without a governance_note_add entry "
                    "naming the new date"
                )
    return errors


def _leg_stays_undated(head: TableView, column: str, kind: str) -> bool:
    if kind in EXEMPTABLE_LEGS:
        return column in head.sized
    rule = head.rules.get((column, kind))
    return rule is not None and rule.exclude_before is None


def _rearmed(table: str, base: TableView, head: TableView) -> list[str]:
    errors: list[str] = []
    for (column, kind), prior in base.exemptions.items():
        if (column, kind) in head.exemptions or prior.get("class") != "temporary" or head.table_class != "scd2":
            continue
        if _leg_stays_undated(head, column, kind) and not _census_entry(_gained(base, head, column)):
            errors.append(
                f"ratchet: {table}.{column}.{kind} temporary exemption removed from an undated rule that stays; it re-arms "
                "against every older row, so it needs a semantic-break entry carrying `census: 0 violators (<date>)`"
            )
    return errors


def ratchet_errors(base_root: Path, head_root: Path) -> list[str]:
    errors = [
        f"ratchet: source {removed!r} was removed from the source registry; a removal strands every row carrying it "
        "(Decision 70) -- a redefinition needing its own plan"
        for removed in sorted(registry_ids(base_root) - registry_ids(head_root))
    ]
    base_tables, head_tables = set(in_scope_tables(base_root)), set(in_scope_tables(head_root))
    for table in sorted(base_tables | head_tables):
        head = view_of(head_root, table) if table in head_tables else TableView("scd2")
        base = view_of(base_root, table) if table in base_tables else TableView(head.table_class)
        for key in base.rules:
            errors.extend(_leg_errors(table, key, base, head))
        if table in base_tables:
            for key in head.rules:
                if key not in base.rules:
                    errors.extend(_new_leg_errors(table, key, head))
        errors.extend(_added_or_changed(table, base, head))
        errors.extend(_rearmed(table, base, head))
    return errors
