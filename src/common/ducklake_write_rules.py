"""The DuckLake writer's adapter onto the shared row-rule engine (rec-4158 plan B, Decision 210 cl.1/cl.3).

The rules reach the writer only through the generated field_semantics.yaml projection of the Class A contract; the
engine is src/row_rules/rules.py. This module is pure and I/O-free: it builds the RowRules for a table spec, shapes the
single existing-row statement write_scd2 already issues (Decision 88: still one statement) and evaluates the bound row.
A rejection raises RowRuleViolationError carrying the table, rule, column and the rule's exclude_before, never a value.
Kept out of ducklake_scd2_schema.py, which is being split (telemetry slice 2a-2 plan 1).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from src.common.ducklake_scd2_schema import CATALOG_ALIAS, ScdTableSpec, SchemaGateError
from src.row_rules.rules import (
    WRITER_REFERENCE_KINDS,
    WRITER_SHAPE_KINDS,
    RowRuleError,
    RowRules,
    RuleProjectionError,
    check_row,
)


class RowRuleViolationError(SchemaGateError):
    """A contract-declared row rule rejected the record. Terminal, never retried; carries no row value."""

    def __init__(self, table: str | None, rule: str, column: str, exclude_before: str | None = None) -> None:
        self.table = table
        self.rule = rule
        self.column = column
        self.exclude_before = exclude_before
        super().__init__(f"{table}.{column}: row rule {rule} violated")


def reference_columns(fields: Mapping[str, Mapping[str, Any]]) -> frozenset[str]:
    """The columns whose elements must name an existing merge key of the same table (Decision 268), from the projection.

    A projected array_element_reference must be true and must not share a column with its write_time_exemptions entry.
    The pure engine never evaluates it: the writer does, inside its own transaction (check_references).
    """
    columns: set[str] = set()
    for name, spec in fields.items():
        for kind in sorted(WRITER_REFERENCE_KINDS & set(spec)):
            if spec[kind] is not True:
                raise RuleProjectionError(f"{name}: {kind} must be true")
            if kind in (spec.get("write_time_exemptions") or {}):
                raise RuleProjectionError(f"{name}: {kind} is exempt and must not also ship as a rule")
            columns.add(name)
    return frozenset(columns)


_KEY_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SCALARS = {"string": str, "integer": int, "number": (int, float), "boolean": bool}
_MAX_BYTES_CEILING = 65536


@dataclass(frozen=True)
class JsonObjectSpec:
    """One column's projected json_object leg: a byte cap and, per schema_version, a closed set of typed keys."""

    column: str
    max_bytes: int
    versions: Mapping[int, Mapping[str, Mapping[str, Any]]]


def _type_error(column: str, key: str, tdef: Any) -> str | None:
    if isinstance(tdef, str):
        if tdef.rstrip("?") not in _SCALARS or tdef.count("?") > 1:
            return f"{column}: key {key} type {tdef!r} is not string, integer, number or boolean (optionally with ?)"
        return None
    inner = tdef.get("map") if isinstance(tdef, Mapping) and set(tdef) == {"map"} else None
    if not isinstance(inner, Mapping) or set(inner) != {"key_pattern", "values"}:
        return f"{column}: key {key} type must be a scalar name or a map of key_pattern and values"
    pattern, values = inner["key_pattern"], inner["values"]
    if not (isinstance(pattern, str) and len(pattern) <= 256 and pattern.startswith("^") and pattern.endswith("$")):
        return f"{column}: key {key} map key_pattern must be an anchored regex of at most 256 characters"
    try:
        re.compile(pattern)
    except re.error:
        return f"{column}: key {key} map key_pattern does not compile"
    if not (
        isinstance(values, list) and values and all(isinstance(v, str) for v in values) and len(set(values)) == len(values)
    ):
        return f"{column}: key {key} map values must be a non-empty list of distinct strings"
    return None


def json_object_spec(column: str, params: Mapping[str, Any]) -> JsonObjectSpec:
    """Validate one projected json_object leg ({max_bytes, versions}); raise RuleProjectionError on a malformed one."""
    if not isinstance(params, Mapping) or set(params) - {"max_bytes", "versions", "exclude_before"}:
        raise RuleProjectionError(f"{column}: json_object carries only max_bytes, versions and exclude_before")
    cap, versions = params.get("max_bytes"), params.get("versions")
    if type(cap) is not int or not 1 <= cap <= _MAX_BYTES_CEILING:
        raise RuleProjectionError(f"{column}: json_object max_bytes must be an integer from 1 to {_MAX_BYTES_CEILING}")
    if not isinstance(versions, Mapping):
        raise RuleProjectionError(f"{column}: json_object versions must be a mapping of schema_version to its keys")
    out: dict[int, Mapping[str, Mapping[str, Any]]] = {}
    for version, shape in versions.items():
        if type(version) is not int or version < 1:
            raise RuleProjectionError(f"{column}: json_object version {version!r} must be an integer >= 1")
        if not isinstance(shape, Mapping) or set(shape) - {"required", "optional"}:
            raise RuleProjectionError(f"{column}: json_object version {version} carries only required and optional")
        required, optional = dict(shape.get("required") or {}), dict(shape.get("optional") or {})
        if required.get("schema_version") != "integer":
            raise RuleProjectionError(f"{column}: json_object version {version} must require schema_version: integer")
        if set(required) & set(optional):
            raise RuleProjectionError(f"{column}: json_object version {version} has a key both required and optional")
        for key, tdef in {**required, **optional}.items():
            if not (isinstance(key, str) and _KEY_NAME.match(key)):
                raise RuleProjectionError(f"{column}: json_object key {key!r} is not a valid key name")
            if (error := _type_error(column, key, tdef)) is not None:
                raise RuleProjectionError(error)
        out[version] = {"required": required, "optional": optional}
    return JsonObjectSpec(column, cap, out)


def json_object_columns(fields: Mapping[str, Mapping[str, Any]]) -> dict[str, JsonObjectSpec]:
    """Every column's json_object spec from the projection. A leg on a non-VARCHAR column, or one that also ships its
    own exemption, is refused."""
    specs: dict[str, JsonObjectSpec] = {}
    for name, spec in fields.items():
        for kind in sorted(WRITER_SHAPE_KINDS & set(spec)):
            if str(spec.get("sql_type", "")).upper() != "VARCHAR":
                raise RuleProjectionError(f"{name}: {kind} is only legal on a VARCHAR column")
            if kind in (spec.get("write_time_exemptions") or {}):
                raise RuleProjectionError(f"{name}: {kind} is exempt and must not also ship as a rule")
            specs[name] = json_object_spec(name, spec[kind])
    return specs


def _no_constant(_: str) -> Any:
    raise ValueError("non-finite number")


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate key")
        out[key] = value
    return out


def _type_ok(tdef: Any, value: Any) -> bool:
    if isinstance(tdef, str):
        if value is None:
            return tdef.endswith("?")
        name = tdef.rstrip("?")
        return isinstance(value, _SCALARS[name]) and (name == "boolean") == isinstance(value, bool)
    inner = tdef["map"]
    pattern = re.compile(inner["key_pattern"])
    return isinstance(value, dict) and all(pattern.fullmatch(k) and v in inner["values"] for k, v in value.items())


def json_object_valid(spec: JsonObjectSpec, value: Any) -> bool:
    """True when *value* is None or a declared-shape JSON object string within the byte cap."""
    if value is None:
        return True
    if not isinstance(value, str) or len(value.encode("utf-8")) > spec.max_bytes:
        return False
    try:
        data = json.loads(value, parse_constant=_no_constant, object_pairs_hook=_no_duplicates)
    except ValueError:
        return False
    if not isinstance(data, dict):
        return False
    version = data.get("schema_version")
    shape = spec.versions.get(version) if type(version) is int else None
    if shape is None:
        return False
    required, optional = shape["required"], shape["optional"]
    if set(data) - set(required) - set(optional) or set(required) - set(data):
        return False
    return all(_type_ok({**required, **optional}[key], v) for key, v in data.items())


def check_json_objects(
    spec: ScdTableSpec,
    rules: RowRules | None,
    record: dict[str, Any],
    created_timestamp: datetime,
    prior: dict[str, Any] | None,
) -> None:
    """Reject a malformed json_object column value. Binds like a dated row rule (Decision 210, 2026-10-04): when the
    leg is undated, on an insert, for a row created on or after its exclude_before, or when the write changes the column.
    The error names the table, rule and column and carries no value."""
    if rules is None:
        return
    for column, jspec in sorted(json_object_columns(spec.fields).items()):
        value = record.get(column)
        since = rules.exclude_before.get(column, {}).get("json_object")
        binds = since is None or prior is None or created_timestamp >= since or value != prior.get(column)
        if binds and not json_object_valid(jspec, value):
            raise RowRuleViolationError(spec.table, "json_object", column, since.date().isoformat() if since else None)


def rules_for(spec: ScdTableSpec) -> RowRules | None:
    """The engine rules for an ops table's projected columns; None for the smoke pair (no contract, no rules)."""
    if spec.table is None:
        return None
    return RowRules.from_projection(spec.fields)


def _dated_columns(rules: RowRules | None) -> list[str]:
    if rules is None:
        return []
    columns: set[str] = set()
    for column, kinds in rules.exclude_before.items():
        columns.add(column)
        for kind in kinds:
            if kind == "not_before":
                columns.add(rules.not_before[column])
            elif kind == "at_most":
                columns.add(rules.at_most[column])
    return sorted(columns)


def existing_row_select(spec: ScdTableSpec, rules: RowRules | None, include_status: bool) -> tuple[str, tuple[str, ...]]:
    """The one existing-row SELECT and the column names it returns, in order.

    created_timestamp, status when the table has a status DAG, and -- only when the rules carry a dated leg -- every
    column a dated rule reads, plus every reference column: the only prior values the changed-column tests need.
    """
    columns = ["created_timestamp"]
    if include_status:
        columns.append("status")
    wanted = {*_dated_columns(rules), *(reference_columns(spec.fields) if rules is not None else ())}
    columns.extend(c for c in sorted(wanted) if c not in columns)
    sql = f"SELECT {', '.join(columns)} FROM {CATALOG_ALIAS}.{spec.current_table} WHERE {spec.merge_key} = ?"
    return sql, tuple(columns)


def check_write(
    spec: ScdTableSpec,
    rules: RowRules | None,
    record: dict[str, Any],
    created_timestamp: datetime,
    last_updated_timestamp: datetime,
    ulid: str,
    prior: dict[str, Any] | None,
) -> None:
    """Evaluate the row the writer is about to MERGE: every column from the record (absent = None) plus the minted
    ulid, created_timestamp and last_updated_timestamp. *prior* is the existing row, None on an insert."""
    if rules is None:
        return
    row: dict[str, Any] = {}
    for name, _ in spec.ordered_columns:
        if name == "ulid":
            row[name] = ulid
        elif name == "created_timestamp":
            row[name] = created_timestamp
        elif name == "last_updated_timestamp":
            row[name] = last_updated_timestamp
        else:
            row[name] = record.get(name)
    try:
        check_row(str(spec.table), row, rules, created_timestamp, prior=prior)
    except RowRuleError as exc:
        since = rules.exclude_before.get(exc.column, {}).get(exc.rule)
        raise RowRuleViolationError(spec.table, exc.rule, exc.column, since.date().isoformat() if since else None) from exc
    check_json_objects(spec, rules, record, created_timestamp, prior)


def check_references(con: Any, spec: ScdTableSpec, record: dict[str, Any], prior: dict[str, Any] | None) -> None:
    """Reject an element of a reference column that names no existing merge key of this table (Decision 268).

    Runs on the caller's open transaction: one SELECT of the merge key from the current table, one placeholder per distinct
    element. It binds on an insert (prior None) or when the write changes the column, so an unchanged stored value is never
    re-read. The error names the table, rule and column and carries no value.
    """
    for column in sorted(reference_columns(spec.fields)):
        value = record.get(column)
        if not value or (prior is not None and value == prior.get(column)):
            continue
        wanted = sorted({str(e) for e in value})
        marks = ", ".join("?" for _ in wanted)
        sql = f"SELECT {spec.merge_key} FROM {CATALOG_ALIAS}.{spec.current_table} WHERE {spec.merge_key} IN ({marks})"
        found = {row[0] for row in con.execute(sql, wanted).fetchall()}
        if not set(wanted) <= found:
            raise RowRuleViolationError(spec.table, "array_element_reference", column)
