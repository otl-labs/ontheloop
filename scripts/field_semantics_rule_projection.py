"""Project a Class A SCD2 contract's declared row rules into the field_semantics column entries (rec-4158 plan B).

scripts.contract_rules.declared_rules is the ONE reader (the live census compiles the same list), so the writer's
rule set and the census's are the same by construction. Each non-exempt leg becomes a column key the shared engine
(src/row_rules/rules.py) evaluates; a leg carrying an exemption ships only as write_time_exemptions[kind]. There is no
enforced flag: a declared rule is an enforced rule (Decision 210).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from scripts.contract_rules import CONTRACTS_DIR, DeclaredRule, declared_rules
from src.row_rules.rules import RowRules


def _rule_value(rule: DeclaredRule) -> Any:
    params = rule.params
    if rule.kind == "accepted_values":
        return list(params["values"])
    if rule.kind == "array_element_format":
        return params["pattern"]
    if rule.kind in ("min_length", "pattern", "not_before"):
        return params["value"]
    raise ValueError(f"{rule.table}.{rule.column}: unknown rule kind {rule.kind!r} -- fail closed")


def project_row_rules(table_id: str, columns: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Return *columns* with every declared rule of *table_id* merged into its column entry (inputs are not mutated)."""
    out = {name: dict(spec) for name, spec in columns.items()}
    for rule in declared_rules(table_id):
        spec = out.get(rule.column)
        if spec is None:
            raise ValueError(f"{table_id}.{rule.column}: a declared rule names a column the projection does not carry")
        if rule.exemption is not None:
            spec.setdefault("write_time_exemptions", {})[rule.kind] = dict(rule.exemption)
            continue
        if rule.kind == "not_null":
            if spec.get("nullable") is False:
                if rule.exclude_before is not None:
                    raise ValueError(f"{table_id}.{rule.column}: a dated not_null on a nullable:false column drops its date")
                continue
            spec["not_null"] = True
        else:
            spec[rule.kind] = _rule_value(rule)
        if rule.exclude_before is not None:
            spec.setdefault("exclude_before", {})[rule.kind] = str(rule.exclude_before)
    return out


def validate_sidecar_rules(entries: Mapping[str, Mapping[str, Any]]) -> None:
    """Run the engine's projection over each spliced sidecar entry, so a malformed rule fails the generator."""
    for table_id, entry in entries.items():
        RowRules.from_projection(entry.get("columns") or {}, entry.get("table_rules"))


def refuse_contract_overlap(table_ids: Iterable[str], contracts_dir: Path = CONTRACTS_DIR) -> None:
    """A sidecar table must never also have a contract: one table, one rule source (Decision 210)."""
    for table_id in table_ids:
        if (contracts_dir / f"{table_id}.yaml").is_file():
            raise ValueError(
                f"{table_id}: a sidecar (dormant or smoke) table also has docs/contracts/{table_id}.yaml -- "
                "move the id from the sidecar tuples to the contract tuple and delete its sidecar block in one change"
            )
