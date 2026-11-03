"""All-writer conformance check (rec-4158; Decision 210 coverage, Decision 163 structural enforcement).

Every table the DuckLake writer can write must be accounted for by exactly one arm, and for every contract-backed table what
the contract DECLARES equals what the writer ENFORCES: the declared write-decidable rules (scripts.contract_rules, the one
reader) are compared kind by kind with the RowRules the engine builds from that table's generated field_semantics.yaml entry
(the object the writer evaluates). Inventory = the writer's own registries: every ops_tables key and the `tables:` block of
field_semantics.yaml, plus the control registry (the same union validate_table_registration treats as writer-reachable).

Arms: (1) contract SCD2 or event table: declared equals enforced; (2) control table: not_null only, on exactly the columns
the control registry marks nullable false; (3) contract-less dormant table: write_boundary dormant and a pending_contract
row_rule_source whose owner resolves to a roadmap criterion; (4) smoke table: the frozen id with a smoke_harness
disposition; (5) the `tables:` block holds exactly the smoke harness pair. Imports scripts.contract_rules,
src.row_rules.rules, src.common.ducklake_control_tables and PyYAML only.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from scripts import contract_rules
from scripts.checks import _common, registry
from src.common import ducklake_control_tables as control
from src.row_rules.rules import EXEMPTABLE_LEGS, PRODUCER_SIDE_KINDS, WAIVABLE_KINDS, RowRules, RuleProjectionError

_FS_REL = "config/lambda/ducklake/field_semantics.yaml"
_STATIC_REL = "config/lambda/ducklake/field_semantics.static.yaml"
_OPS_YAML_REL = "config/agent/data_quality/ops.yaml"
_ROADMAP_REL = "docs/ROADMAP-PLATFORM.yaml"
SMOKE_IDS = frozenset({"ops_smoke_events"})
SMOKE_PAIR = frozenset({"history", "current"})
_SHAPE_KEYS = frozenset({"role", "sql_type", "nullable", "description", "semantics"})
_ENGINE_ONLY_KINDS = frozenset({"content_inline_threshold_bytes", "full_output_cap_bytes", "integrity"})
_SUPPORTED = WAIVABLE_KINDS | PRODUCER_SIDE_KINDS | _ENGINE_ONLY_KINDS | {"representation_of", "exactly_one_of"}


def _frozen_condition(cond: Mapping[str, Any]) -> tuple[tuple[str, tuple[Any, ...]], ...]:
    return tuple(sorted((k, tuple(sorted(v, key=str))) for k, v in dict(cond).items()))


def _expected_and_actual(
    rules_decl: list[contract_rules.DeclaredRule], rules: RowRules, nullable_false: set[str]
) -> dict[str, tuple[Any, Any]]:
    live = [r for r in rules_decl if r.exemption is None]
    by_kind = lambda kind: [r for r in live if r.kind == kind]  # noqa: E731
    required = {r.column for r in by_kind("required_when")}
    out: dict[str, tuple[Any, Any]] = {}
    out["not_null"] = (
        frozenset(
            {r.column for r in by_kind("not_null") if r.column not in required}
            | {c for c in nullable_false if c not in required}
        ),
        frozenset(rules.not_null),
    )
    out["accepted_values"] = (
        {r.column: frozenset(r.params["values"]) for r in by_kind("accepted_values")},
        dict(rules.accepted_values),
    )
    out["min_length"] = ({r.column: r.params["value"] for r in by_kind("min_length")}, dict(rules.min_length))
    out["pattern"] = (
        {r.column: r.params["value"] for r in by_kind("pattern")},
        {c: p.pattern for c, p in rules.patterns.items()},
    )
    out["pattern_when"] = (
        {
            r.column: tuple((_frozen_condition(e["when"]), e["pattern"]) for e in r.params["value"])
            for r in by_kind("pattern_when")
        },
        {c: tuple((_frozen_condition(w), p.pattern) for w, p in entries) for c, entries in rules.pattern_when.items()},
    )
    out["array_element_format"] = (
        {r.column: r.params["pattern"] for r in by_kind("array_element_format")},
        {c: p.pattern for c, p in rules.element_formats.items()},
    )
    for kind, attr in (("required_when", "required_when"), ("null_or_zero_when", "null_or_zero_when")):
        out[kind] = (
            {r.column: _frozen_condition(r.params) for r in by_kind(kind)},
            {c: _frozen_condition(cond) for c, cond in getattr(rules, attr).items()},
        )
    for kind in ("not_before", "at_most", "max_after_write_seconds"):
        out[kind] = ({r.column: r.params["value"] for r in by_kind(kind)}, dict(getattr(rules, kind)))
    out["representation_of"] = (
        frozenset(r.column for r in by_kind("representation_of")),
        frozenset(rules.representation_only),
    )
    out["exactly_one_of"] = (
        frozenset(frozenset(r.params["fields"]) for r in by_kind("exactly_one_of")),
        frozenset(frozenset(g) for g in rules.exactly_one_of),
    )
    out["exclude_before"] = (
        {(r.column, r.kind): str(r.exclude_before) for r in live if r.exclude_before is not None},
        {(c, k): d.date().isoformat() for c, kinds in rules.exclude_before.items() for k, d in kinds.items()},
    )
    sized = by_kind("content_inline_threshold_bytes")
    cap = {r.column: r.params["value"] for r in by_kind("full_output_cap_bytes")}
    integrity = {r.column for r in by_kind("integrity")}
    expected_content = None
    if sized:
        col = sized[0].column
        expected_content = (col, sized[0].params["value"], cap.get(col), col in integrity)
    actual_content = None
    if rules.content is not None:
        actual_content = (rules.content.inline, rules.content.threshold, rules.content.cap, rules.content.integrity)
    out["payload"] = (expected_content, actual_content)
    return out


def _exemption_maps(intents: dict[str, dict[str, Any]]) -> dict[str, tuple[Any, Any]]:
    producer: dict[Any, Any] = {}
    legs: dict[Any, Any] = {}
    waived: dict[Any, Any] = {}
    for column, intent in intents.items():
        for kind, why in (intent.get("write_time_exemptions") or {}).items():
            recorded = (str(why.get("reason")), str(why.get("owner")))
            if kind in PRODUCER_SIDE_KINDS:
                producer[(column, kind)] = recorded
            elif kind in EXEMPTABLE_LEGS:
                legs[kind] = recorded
            else:
                waived[(column, kind)] = recorded
    return {"producer exemption": (producer, None), "content-leg exemption": (legs, None), "waived exemption": (waived, None)}


def _contract_nullable_false(raw_fields: dict[str, Any], contracts_dir: Path) -> set[str]:
    out: set[str] = set()
    for name, spec in raw_fields.items():
        nullable = spec.get("nullable")
        if nullable is None and spec.get("$ref"):
            target_file, target_field = contract_rules.split_ref(spec["$ref"])
            target = (contract_rules.load_yaml(contracts_dir / target_file).get("fields") or {}).get(target_field) or {}
            nullable = target.get("nullable")
        if nullable is False:
            out.add(name)
    return out


def _diff_errors(table: str, compare: dict[str, tuple[Any, Any]]) -> list[str]:
    errors: list[str] = []
    for kind, (want, got) in compare.items():
        if want == got:
            continue
        if not (isinstance(want, dict) and isinstance(got, dict)):
            errors.append(f"{table}: {kind} differs between the contract ({want!r}) and the writer ({got!r})")
            continue
        for key in sorted(set(want) | set(got), key=str):
            if want.get(key) != got.get(key):
                side = "enforced but not declared" if key in got and key not in want else "declared but not enforced"
                errors.append(f"{table}.{key}: {kind} {side} (declared {want.get(key)!r}, writer {got.get(key)!r})")
    return errors


def _declared_errors(table: str, declared: list[Any], columns: dict[str, Any], read_derived: set[str]) -> list[str]:
    errors: list[str] = []
    for rule in declared:
        if rule.column not in columns and rule.column not in read_derived:
            errors.append(f"{table}.{rule.column}: declared {rule.kind} names a column the projection does not carry")
        elif rule.kind not in _SUPPORTED:
            errors.append(f"{table}.{rule.column}: declared {rule.kind} has no comparison arm -- fail closed")
    return errors


def _check_contract_table(table: str, entry: dict[str, Any], contracts_dir: Path, repo_root: Path) -> tuple[list[str], int]:
    raw = contract_rules.load_yaml(contracts_dir / f"{table}.yaml")
    raw_fields = raw.get("fields") or {}
    is_event = str((raw.get("governance") or {}).get("table_class") or "").lower() == "event"
    if not is_event and entry.get("table_rules"):
        return [
            f"{table}: an SCD2 entry carries a table-level rule the writer never passes to the engine (rules_for passes none)"
        ], 0
    columns = entry.get("columns") or {}
    declared = list(contract_rules.declared_rules(table, contracts_dir=contracts_dir, repo_root=repo_root))
    read_derived = {n for n, spec in raw_fields.items() if (spec.get("derivation") or {}).get("timing") == "read"}
    kept = [r for r in declared if r.column in columns]
    errors = _declared_errors(table, declared, columns, read_derived)
    try:
        rules = RowRules.from_projection(columns, entry.get("table_rules"))
    except RuleProjectionError as exc:
        return [*errors, f"{table}: the projection does not build an engine ({exc})"], len(kept)
    intents = contract_rules.read_intents(
        table, {}, contracts_dir=contracts_dir, repo_root=repo_root, registry_key_ref=contract_rules.REGISTRY_KEY_REF
    )
    nullable_false = _contract_nullable_false(raw_fields, contracts_dir) & set(columns)
    compare = _expected_and_actual(kept, rules, nullable_false)
    exempt_expected = _exemption_maps({c: i for c, i in intents.items() if c in columns})
    actual_exempt = {
        "producer exemption": dict(rules.producer_exemptions),
        "content-leg exemption": dict(rules.exemptions),
        "waived exemption": dict(rules.waived),
    }
    compare.update({k: (v[0], actual_exempt[k]) for k, v in exempt_expected.items()})
    errors.extend(_diff_errors(table, compare))
    return errors, len(kept)


def _check_control_table(table: str, contracts_dir: Path, repo_root: Path) -> tuple[list[str], int]:
    spec = control.resolve_control_spec(table)
    required = {c for c, f in spec.fields.items() if f.get("nullable") is False}
    declared = contract_rules.declared_rules(table, contracts_dir=contracts_dir, repo_root=repo_root)
    errors = [
        f"{table}.{r.column}: a control table declares {r.kind}; only not_null (the DDL NOT NULL) is permitted"
        for r in declared
        if r.kind != "not_null" or r.exclude_before is not None
    ]
    nn = {r.column for r in declared if r.kind == "not_null"}
    if nn != required:
        errors.append(
            f"{table}: contract not_null columns {sorted(nn)} differ from the control registry's NOT NULL columns "
            f"{sorted(required)}"
        )
    return errors, len(declared)


def owner_resolves(roadmap_text: str, owner: str) -> bool:
    """A targeted text lookup of `T<item>:<criterion>` in the roadmap, never a full YAML load (Decision 208)."""
    item, _, crit = owner.partition(":")
    if not (re.fullmatch(r"T[0-9]+\.[0-9]+[a-z]?", item) and re.fullmatch(r"c[0-9]+", crit)):
        return False
    start = re.search(rf"^  - id: {re.escape(item)}$", roadmap_text, re.MULTILINE)
    if start is None:
        return False
    nxt = re.search(r"^  - id: ", roadmap_text[start.end() :], re.MULTILINE)
    block = roadmap_text[start.end() : start.end() + nxt.start()] if nxt else roadmap_text[start.end() :]
    return re.search(rf"^\s+- id: {crit}$", block, re.MULTILINE) is not None


def _contractless_errors(
    table: str, entry: dict[str, Any], disposition: dict[str, Any] | None, roadmap_text: str
) -> list[str]:
    errors = [
        f"{table}.{column}: a contract-less table's column carries rule key(s) {sorted(set(spec) - _SHAPE_KEYS)}"
        for column, spec in (entry.get("columns") or {}).items()
        if set(spec) - _SHAPE_KEYS
    ]
    errors.extend(f"{table}: unexpected entry key {k!r}" for k in ("table_rules",) if entry.get(k))
    kind = (disposition or {}).get("disposition")
    if entry.get("write_boundary") == "dormant":
        owner = (disposition or {}).get("owner")
        if kind != "pending_contract" or not isinstance(owner, str) or not owner_resolves(roadmap_text, owner):
            errors.append(
                f"{table}: a dormant table needs a pending_contract row_rule_source whose owner resolves to a roadmap "
                "criterion"
            )
    elif entry.get("status") == "smoke":
        if table not in SMOKE_IDS or kind != "smoke_harness":
            errors.append(
                f"{table}: a smoke table must be in the frozen set {sorted(SMOKE_IDS)} with a smoke_harness disposition"
            )
    else:
        errors.append(f"{table}: matches no conformance arm (no contract, not dormant, not a smoke table)")
    return errors


def conformance_errors(
    fs: dict[str, Any],
    static: dict[str, Any],
    *,
    contracts_dir: Path,
    repo_root: Path,
    control_names: tuple[str, ...],
    roadmap_text: str,
    ops_yaml_tables: set[str],
) -> tuple[list[str], int]:
    errors: list[str] = []
    examined = 0
    ops_tables = fs.get("ops_tables") or {}
    if set((fs.get("tables") or {})) != SMOKE_PAIR:
        errors.append(
            f"field_semantics tables: block holds {sorted(fs.get('tables') or {})}, expected exactly {sorted(SMOKE_PAIR)}"
        )
    for table in sorted(set(ops_tables) | set(control_names)):
        entry = ops_tables.get(table)
        has_contract = (contracts_dir / f"{table}.yaml").is_file()
        sidecar_table = table in (static.get("dormant_ops_tables") or {}) or table in (static.get("smoke_ops_tables") or {})
        if sidecar_table and has_contract:
            errors.append(f"{table}: a sidecar table also has docs/contracts/{table}.yaml -- two rule sources")
            continue
        if entry is None:
            errors.append(f"{table}: a control-registry table missing from ops_tables matches no conformance arm")
        elif table in control_names:
            if not has_contract:
                errors.append(f"{table}: a control table without a contract matches no conformance arm")
            else:
                found, count = _check_control_table(table, contracts_dir, repo_root)
                errors.extend(found)
                examined += count
        elif has_contract:
            found, count = _check_contract_table(table, entry, contracts_dir, repo_root)
            errors.extend(found)
            examined += count
        else:
            disposition = ((static.get("contract_table_ops") or {}).get(table) or {}).get("row_rule_source")
            errors.extend(_contractless_errors(table, entry, disposition, roadmap_text))
    errors.extend(
        f"config/agent/data_quality/ops.yaml names table {name!r}, which the writer registry lacks"
        for name in sorted(ops_yaml_tables - set(ops_tables) - set(control_names))
    )
    return errors, examined


def _load(root: Path) -> dict[str, Any]:
    ops = contract_rules.parse_yaml((root / _OPS_YAML_REL).read_text(encoding="utf-8")) or {}
    return {
        "fs": contract_rules.parse_yaml((root / _FS_REL).read_text(encoding="utf-8")) or {},
        "static": contract_rules.parse_yaml((root / _STATIC_REL).read_text(encoding="utf-8")) or {},
        "roadmap_text": (root / _ROADMAP_REL).read_text(encoding="utf-8"),
        "ops_yaml_tables": set((ops.get("tables") or {})),
    }


@registry.register("validate_write_time_enforcement_conformance", owner="platform")
def validate_write_time_enforcement_conformance(failed: list[str], *, root: Path | None = None) -> None:
    """Fail when a writer table matches no arm or a contract's declared rules differ from what the writer enforces."""
    print("\n=== Write-time enforcement conformance ===")
    root = root if root is not None else _common.ROOT
    try:
        inputs = _load(root)
        errors, examined = conformance_errors(
            inputs["fs"],
            inputs["static"],
            contracts_dir=root / "docs" / "contracts",
            repo_root=root,
            control_names=control.control_table_names(),
            roadmap_text=inputs["roadmap_text"],
            ops_yaml_tables=inputs["ops_yaml_tables"],
        )
    except (OSError, ValueError, yaml.YAMLError) as exc:
        errors, examined, inputs = [f"inputs unreadable: {exc}"], 0, {"fs": {}}
    registry.examined(examined, unit="rule_legs")
    for err in errors:
        print(f"  FAIL: {err}")
        failed.append(f"Write-time conformance: {err}")
    if not errors:
        tables = len(inputs["fs"].get("ops_tables") or {})
        print(
            f"  PASS: {tables} writer tables each match one arm; {examined} declared rule legs equal what the writer enforces."
        )
