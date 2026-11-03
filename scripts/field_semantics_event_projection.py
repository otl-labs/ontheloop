"""Generator projection for table_class: event Class A contracts (Decision 199, telemetry kernel).

Split out from scripts/schema_to_field_semantics.py (Decision 128 decompose-by-default: the
generator's mapped test file, tests/test_schema_to_field_semantics.py, is at 479/500 SLOC, and
per-file coverage runs only a source's own mapped test file). schema_to_field_semantics.py imports
this module INSIDE its event dispatch branch only (function scope), so validate_field_semantics_drift's
module-scope import of the generator gains no new edge and this module's own KEY_PLANS/identity
import (src.telemetry.identity) never appears at that module's top level.

Projects a table_class: event Class A contract to the history-only append_only shape: no
merge_key/current_table/entity_id_prefix/id_keyspace, history_table = the contract id, partition
from governance.partition_by (a "history=..." role-prefixed calendar-day triple, per the shared
ops grammar), dedupe_key [producer, event_id, parser_version] (the telemetry grain, enforced at
the write boundary), and entity_key = the table's own KEY_PLANS entity column. Fail-closed on a
missing envelope column, a partition_by that does not parse or does not resolve to exactly the
day-grain calendar triple, or a migration_columns entry (an SCD2-only concept never valid for an
append-only event table).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from scripts.contract_rules import check_rule_keys, not_null_declared, read_intents
from src.common.ducklake_partition_spec import PartitionSpecError, parse_partition_by, validate_partition_spec
from src.telemetry.identity import KEY_PLANS

_ENVELOPE_REQUIRED_FIELDS = (
    "event_id",
    "event_timestamp",
    "session_started_at",
    "external_ref",
    "entity_ref",
    "producer",
    "parser_version",
    "created_timestamp",
    "tenant_id",
    "project_id",
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CONTRACTS_DIR = _REPO_ROOT / "docs" / "contracts"
_REGISTRY_KEY_REF = ("source-lineage.yaml", "registry_key")

_COLUMN_RULE_KEYS = (
    "required_when",
    "not_before",
    "max_after_write_seconds",
    "at_most",
    "null_or_zero_when",
    "representation_of",
    "pattern",
    "pattern_when",
    "write_time_exemptions",
)
_TABLE_RULE_KEYS = ("exactly_one_of", "content_inline_threshold_bytes", "full_output_cap_bytes", "integrity")
_KNOWN_DQ_KEYS = frozenset({"not_null", "accepted_values", *_COLUMN_RULE_KEYS, *_TABLE_RULE_KEYS})
KNOWN_DQ_KEYS = _KNOWN_DQ_KEYS

_DAY_GRAIN_TRIPLE_RE = re.compile(r"^year\((?P<col>\w+)\), month\((?P=col)\), day\((?P=col)\)$")


def _entity_key_column(table_id: str) -> str:
    for column, plan in KEY_PLANS[table_id].items():
        if plan.ref_field == "entity_ref":
            return column
    raise ValueError(f"{table_id}: KEY_PLANS has no entity-key column (no plan with ref_field='entity_ref')")


def _validate_partition_by(table_id: str, partition_by: str | None, columns: dict[str, Any]) -> str:
    """Delegate parsing/role-grammar validation to the shared ducklake ops grammar (rec-4073),
    then enforce the telemetry-specific tightening: the history role must resolve to EXACTLY the
    day-grain calendar triple over one column (the shared validate_partition_spec predicate alone
    also accepts year-only / year+month / +hour, which is too loose for an event table). Returns
    the resolved (role-prefix-stripped) history spec, which is what the projection stores.
    """
    if not partition_by:
        raise ValueError(f"{table_id}: governance.partition_by is required for a table_class: event contract")
    try:
        roles = parse_partition_by(partition_by)
    except PartitionSpecError as exc:
        raise ValueError(f"{table_id}: governance.partition_by is malformed: {exc}") from exc
    if "current" in roles:
        raise ValueError(
            f"{table_id}: governance.partition_by must carry no 'current=' role -- an append_only "
            "event table has no current projection to reconcile"
        )
    history_spec = roles.get("history")
    if not history_spec:
        raise ValueError(f"{table_id}: governance.partition_by must declare a 'history=' role")
    try:
        validate_partition_spec(history_spec)
    except PartitionSpecError as exc:
        raise ValueError(f"{table_id}: governance.partition_by history spec is invalid: {exc}") from exc
    match = _DAY_GRAIN_TRIPLE_RE.match(history_spec)
    if match is None:
        raise ValueError(
            f"{table_id}: governance.partition_by's history spec must be the calendar-day triple "
            "'year(C), month(C), day(C)' over one column -- a bare day(C) alone is DuckLake "
            f"DAY-OF-MONTH, not a calendar-day partition. Got: {history_spec!r}"
        )
    col = match.group("col")
    if columns.get(col, {}).get("sql_type") != "TIMESTAMP WITH TIME ZONE":
        raise ValueError(f"{table_id}: partition column {col!r} must project to TIMESTAMP WITH TIME ZONE")
    return history_spec


def _effective_intents(table_id: str, resolved_fields: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return read_intents(
        table_id,
        resolved_fields,
        contracts_dir=_CONTRACTS_DIR,
        repo_root=_REPO_ROOT,
        registry_key_ref=_REGISTRY_KEY_REF,
    )


def _check_rule_keys(table_id: str, name: str, intent: dict[str, Any]) -> None:
    check_rule_keys(table_id, name, intent, _KNOWN_DQ_KEYS)


def _column_rules(table_id: str, name: str, intent: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    exempt = intent.get("write_time_exemptions") or {}
    if "accepted_values" in intent and "accepted_values" not in exempt:
        out["accepted_values"] = list(intent["accepted_values"]["values"])
    for key in _COLUMN_RULE_KEYS:
        if key in intent and (key == "write_time_exemptions" or key not in exempt):
            out[key] = intent[key]
    return out


def _table_rules(table_id: str, intents: dict[str, dict[str, Any]], columns: dict[str, Any]) -> dict[str, Any]:
    rules: dict[str, Any] = {}
    groups = []
    for name, intent in intents.items():
        group = intent.get("exactly_one_of")
        if group is not None:
            fields = group.get("fields") if isinstance(group, dict) else None
            if not fields or name not in fields:
                raise ValueError(f"{table_id}.{name}: exactly_one_of must list fields including itself")
            groups.append(list(fields))
    if groups:
        rules["exactly_one_of"] = groups
    sized = [n for n, i in intents.items() if "content_inline_threshold_bytes" in i]
    stray = [n for n, i in intents.items() if n not in sized and ("full_output_cap_bytes" in i or "integrity" in i)]
    if stray:
        raise ValueError(f"{table_id}: full_output_cap_bytes/integrity on {stray} without content_inline_threshold_bytes")
    if len(sized) > 1:
        raise ValueError(f"{table_id}: more than one content_inline_threshold_bytes field {sized}")
    if sized:
        rules["payload"] = _content_rule(table_id, sized[0], intents, columns)
    return rules


def _content_rule(table_id: str, inline: str, intents: dict[str, dict[str, Any]], columns: dict[str, Any]) -> dict[str, Any]:
    intent = intents[inline]
    represented = intent.get("representation_of")
    siblings = [n for n, i in intents.items() if n != inline and i.get("representation_of") == represented]
    if not represented or len(siblings) != 1:
        raise ValueError(f"{table_id}.{inline}: a sized payload needs exactly one sibling with the same representation_of")
    kinds = {columns[c]["sql_type"]: c for c in represented if c in columns}
    if sorted(kinds) != ["BIGINT", "VARCHAR"] or len(represented) != 2:
        raise ValueError(f"{table_id}.{inline}: representation_of must name one VARCHAR digest and one BIGINT size column")
    return {
        "inline": inline,
        "uri": siblings[0],
        "sha": kinds["VARCHAR"],
        "size": kinds["BIGINT"],
        "threshold": intent["content_inline_threshold_bytes"],
        "cap": intent.get("full_output_cap_bytes"),
        "integrity": bool(intent.get("integrity")),
    }


def _event_role(name: str, derived_columns: frozenset[str]) -> str:
    return "derived" if name in derived_columns else "input"


def _is_not_null(dq_intent: dict[str, Any] | None, nullable: bool | None) -> tuple[bool, Any]:
    dq_intent = dq_intent or {}
    required_when = dq_intent.get("required_when")
    declared = not_null_declared(dq_intent.get("not_null")) and "not_null" not in (
        dq_intent.get("write_time_exemptions") or {}
    )
    is_not_null = (nullable is False or declared) and not required_when
    return is_not_null, required_when


def project_event_table(
    table_id: str,
    resolved_fields: dict[str, Any],
    ops_config: dict[str, Any],
    partition_by: str | None,
    *,
    map_iceberg_type: Callable[[str | None, str], str],
    include_prose: bool = False,
) -> dict[str, Any]:
    """Project a table_class: event contract's resolved fields into a history-only ops entry."""
    if ops_config.get("migration_columns"):
        raise ValueError(f"{table_id}: migration_columns is an SCD2-only concept, invalid for a table_class: event contract")

    for required in _ENVELOPE_REQUIRED_FIELDS:
        if required not in resolved_fields:
            raise ValueError(f"{table_id}: missing required envelope column {required!r}")

    entity_key = _entity_key_column(table_id)
    intents = _effective_intents(table_id, resolved_fields)
    for fname in resolved_fields:
        _check_rule_keys(table_id, fname, intents.get(fname) or {})
    derived_columns = frozenset({"event_id", "created_timestamp", "tenant_id", "project_id"}) | frozenset(KEY_PLANS[table_id])

    columns: dict[str, Any] = {}
    for fname, fspec in resolved_fields.items():
        derivation = fspec.derivation if hasattr(fspec, "derivation") else fspec.get("derivation")
        if derivation and derivation.get("timing") == "read":
            if derivation.get("realized") is True:
                raise ValueError(f"{table_id}.{fname}: a derivation.timing=read field must never have realized=true")
            continue  # derived-at-read fields have no physical column

        iceberg_type = fspec.iceberg_type if hasattr(fspec, "iceberg_type") else fspec.get("iceberg_type")
        sql_type = map_iceberg_type(iceberg_type, fname)
        nullable = fspec.nullable if hasattr(fspec, "nullable") else fspec.get("nullable")
        dq_intent = fspec.dq_intent if hasattr(fspec, "dq_intent") else fspec.get("dq_intent")

        intent = intents.get(fname) or {}
        is_not_null, required_when = _is_not_null(intent or dq_intent, nullable)
        col: dict[str, Any] = {
            "role": _event_role(fname, derived_columns),
            "sql_type": sql_type,
            "nullable": not is_not_null,
        }
        col.update(_column_rules(table_id, fname, intent))
        if include_prose:
            desc = fspec.description if hasattr(fspec, "description") else fspec.get("description")
            sem = fspec.semantics if hasattr(fspec, "semantics") else fspec.get("semantics")
            if desc is not None:
                col["description"] = desc
            if sem is not None:
                col["semantics"] = sem
        columns[fname] = col

    history_spec = _validate_partition_by(table_id, partition_by, columns)
    table_rules = _table_rules(table_id, {n: intents.get(n) or {} for n in columns}, columns)

    partition_column = _DAY_GRAIN_TRIPLE_RE.match(history_spec).group("col")  # type: ignore[union-attr]  # validated above
    entry = {
        "status": ops_config.get("status", "target"),
        "table_class": "event",
        "write_boundary": ops_config.get("write_boundary"),
        "write_mode": "append_only",
        "history_table": table_id,
        "partition": {"history": history_spec},
        "partition_column": partition_column,
        "dedupe_key": ["producer", "event_id", "parser_version"],
        "entity_key": entity_key,
        "columns": columns,
    }
    if table_rules:
        entry["table_rules"] = table_rules
    return entry


__all__ = ["project_event_table"]
