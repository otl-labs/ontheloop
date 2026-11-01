"""Closed data-quality check verbs for the ducklake_reader's dq_check action (Decision 84 I-3 follow-up).

Reader-bundle only (imported by the reader handler; never re-exported by the ducklake_runtime facade). A request names
one of a fixed set of check kinds, a table and a params object; this module owns every SQL template, takes every value
as a bound parameter, takes a column name only after finding it in the table's field-semantics spec, and returns one
integer violation count -- never row data. A refusal raises DqCheckInputError naming the kind and the parameter, never
the refused value, before any connection is acquired.
"""

from __future__ import annotations

import functools
import hashlib
import math
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

from src.common.ducklake_control_tables import is_control_table
from src.common.ducklake_scd2_schema import CATALOG_ALIAS, ops_table_names, resolve_table_spec, table_write_boundary

_REFUSED_BOUNDARIES = frozenset({"telemetry_append", "registration"})
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_MAX_VALUES = 200
_MAX_TEXT = 500
_MAX_COLUMNS = 8
_MAX_INT = 10**9
_PROJECTIONS = ("current", "history")


class DqCheckInputError(Exception):
    """A dq_check request the module refused: names the kind and the parameter, never the value."""

    def __init__(self, check: str | None, param: str, reason: str) -> None:
        super().__init__(f"dq_check {check!r}: parameter {param!r} {reason}")
        self.check = check
        self.param = param
        self.reason = reason


@functools.cache
def whitespace_class() -> str:
    """The RE2 character-class body equal to exactly the characters str.isspace() accepts (computed on first use)."""
    codes = [i for i in range(0x110000) if chr(i).isspace()]
    parts: list[str] = []
    start = prev = codes[0]
    for code in codes[1:] + [None]:  # type: ignore[list-item]
        if code is not None and code == prev + 1:
            prev = code
            continue
        parts.append(f"\\x{{{start:x}}}" if start == prev else f"\\x{{{start:x}}}-\\x{{{prev:x}}}")
        if code is not None:
            start = prev = code
    return "".join(parts)


_GATE = " AND created_timestamp >= CAST(? AS DATE)"

# Row-level predicates: TRUE for a violating row. {col}/{other} are spec-validated column names; every `?` is a bound value.
_PREDICATES: dict[str, str] = {
    "not_null": "{col} IS NULL",
    "accepted_values": "{col} IS NOT NULL AND {col} NOT IN ({marks})",
    "min_length": "{col} IS NOT NULL AND LENGTH(regexp_replace({col}, '^[{ws}]+|[{ws}]+$', '', 'g')) < ?",
    "pattern": "{col} IS NOT NULL AND NOT regexp_full_match({col}, ?)",
    "array_element_format": (
        "{col} IS NOT NULL AND len(list_filter({col}, x -> x IS NULL OR NOT regexp_full_match(x, ?))) > 0"
    ),
    "not_before": "{col} IS NOT NULL AND {other} IS NOT NULL AND {col} < {other}",
    "min_value": "{col} IS NOT NULL AND {col} < ?",
}
_ROW_COUNT = "SELECT COUNT(*) FROM {tbl} WHERE ({pred})"

_TEMPLATES: dict[str, str] = {
    "row_count_min": "SELECT CASE WHEN cnt < ? THEN 1 ELSE 0 END FROM (SELECT COUNT(*) AS cnt FROM {tbl})",
    "recency_hours": "SELECT CASE WHEN date_diff('hour', MAX({col}), CURRENT_TIMESTAMP) > ? THEN 1 ELSE 0 END FROM {tbl}",
    "unique": "SELECT COUNT(*) FROM (SELECT {cols}, COUNT(*) AS n FROM {tbl}{where} GROUP BY {cols} HAVING COUNT(*) > 1) d",
    "history_not_ahead_of_current": ("SELECT COUNT(*) FROM {hist} h JOIN {cur} c ON h.{mk} = c.{mk} WHERE h.{col} > c.{col}"),
    "key_absent": "SELECT COUNT(*) FROM {tbl} WHERE {col} = ?",
}
_TEMPLATES.update({kind: f"{_ROW_COUNT}[{_GATE}] WHERE {pred}" for kind, pred in _PREDICATES.items()})


@dataclass(frozen=True)
class KindSpec:
    required: tuple[str, ...]
    optional: tuple[str, ...] = ()


_GATED = ("exclude_before",)
DQ_KINDS: dict[str, KindSpec] = {
    "row_count_min": KindSpec(("min",)),
    "recency_hours": KindSpec(("column", "hours")),
    "not_null": KindSpec(("column",), _GATED),
    "accepted_values": KindSpec(("column", "values"), _GATED),
    "min_length": KindSpec(("column", "value"), _GATED),
    "pattern": KindSpec(("column", "value"), _GATED),
    "array_element_format": KindSpec(("column", "pattern"), _GATED),
    "not_before": KindSpec(("column", "value"), _GATED),
    "min_value": KindSpec(("column", "value"), _GATED),
    "unique": KindSpec(("columns",), ("projection", "exclude_before")),
    "history_not_ahead_of_current": KindSpec(("column",)),
    "key_absent": KindSpec(("column", "value")),
}
ROW_LEVEL_KINDS = tuple(_PREDICATES)


def check_version(kind: str) -> str:
    """The first 16 hex of sha256 over the kind's template text and param names (the read_version idiom)."""
    spec = DQ_KINDS[kind]
    text = "|".join([kind, _TEMPLATES[kind], ",".join(sorted(spec.required + spec.optional))])
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class ValidatedCheck:
    kind: str
    table: str
    params: dict[str, Any]


def _text(kind: str, params: dict[str, Any], name: str, cap: int = _MAX_TEXT) -> str:
    value = params[name]
    if not isinstance(value, str) or not value:
        raise DqCheckInputError(kind, name, "must be a non-empty string")
    if len(value) > cap:
        raise DqCheckInputError(kind, name, f"must be at most {cap} characters")
    return value


def _integer(kind: str, params: dict[str, Any], name: str) -> int:
    value = params[name]
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _MAX_INT:
        raise DqCheckInputError(kind, name, f"must be an integer between 0 and {_MAX_INT}")
    return value


def _number(kind: str, params: dict[str, Any], name: str) -> int | float:
    value = params[name]
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or (isinstance(value, float) and not math.isfinite(value))
    ):
        raise DqCheckInputError(kind, name, "must be a finite number")
    return value


def _require_table(kind: str, table: Any) -> None:
    if not isinstance(table, str) or not table:
        raise DqCheckInputError(kind, "table", "must be a non-empty string")
    if is_control_table(table) or table_write_boundary(table) in _REFUSED_BOUNDARIES or table not in ops_table_names():
        raise DqCheckInputError(kind, "table", "is not an ops table reachable through dq_check")


def _column(kind: str, spec: Any, params: dict[str, Any], name: str, value: Any = None) -> str:
    col = params[name] if value is None else value
    if not isinstance(col, str) or not _IDENT.match(col) or col not in spec.fields:
        raise DqCheckInputError(kind, name, "must name a column in the table's field-semantics contract")
    return col


def _exclude_before(kind: str, spec: Any, raw: Any) -> str:
    try:
        if not isinstance(raw, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            raise ValueError
        date.fromisoformat(raw)
    except ValueError:
        raise DqCheckInputError(kind, "exclude_before", "must be an ISO date (YYYY-MM-DD)") from None
    if "created_timestamp" not in spec.fields:
        raise DqCheckInputError(kind, "exclude_before", "needs a table with a created_timestamp column")
    return raw


def _values(kind: str, params: dict[str, Any]) -> list[str]:
    values = params["values"]
    if not isinstance(values, list) or not 1 <= len(values) <= _MAX_VALUES:
        raise DqCheckInputError(kind, "values", f"must be a list of 1 to {_MAX_VALUES} strings")
    if any(not isinstance(v, str) or len(v) > _MAX_TEXT for v in values):
        raise DqCheckInputError(kind, "values", f"must hold only strings of at most {_MAX_TEXT} characters")
    return list(values)


def _unique_params(kind: str, spec: Any, params: dict[str, Any]) -> dict[str, Any]:
    cols = params["columns"]
    if not isinstance(cols, list) or not 1 <= len(cols) <= _MAX_COLUMNS:
        raise DqCheckInputError(kind, "columns", f"must be a list of 1 to {_MAX_COLUMNS} column names")
    projection = params.get("projection", "current")
    if projection not in _PROJECTIONS:
        raise DqCheckInputError(kind, "projection", f"must be one of {list(_PROJECTIONS)}")
    return {"columns": [_column(kind, spec, params, "columns", c) for c in cols], "projection": projection}


# Per-kind value parameters: (kind) -> function(kind, spec, params) -> normalised value params.
_KIND_PARAMS: dict[str, Callable[[str, Any, dict[str, Any]], dict[str, Any]]] = {
    "row_count_min": lambda k, s, p: {"min": _integer(k, p, "min")},
    "recency_hours": lambda k, s, p: {"hours": _integer(k, p, "hours")},
    "accepted_values": lambda k, s, p: {"values": _values(k, p)},
    "min_length": lambda k, s, p: {"value": _integer(k, p, "value")},
    "pattern": lambda k, s, p: {"value": _text(k, p, "value")},
    "key_absent": lambda k, s, p: {"value": _text(k, p, "value")},
    "array_element_format": lambda k, s, p: {"pattern": _text(k, p, "pattern")},
    "not_before": lambda k, s, p: {"value": _column(k, s, p, "value")},
    "min_value": lambda k, s, p: {"value": _number(k, p, "value")},
    "unique": _unique_params,
}


def _normalise(kind: str, spec: Any, params: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "exclude_before" in params:
        out["exclude_before"] = _exclude_before(kind, spec, params["exclude_before"])
    if "column" in params:
        out["column"] = _column(kind, spec, params, "column")
    handler = _KIND_PARAMS.get(kind)
    if handler is not None:
        out.update(handler(kind, spec, params))
    return out


def validate(payload: dict[str, Any]) -> ValidatedCheck:
    """Validate a dq_check request payload before any connection; raises DqCheckInputError naming the parameter."""
    kind = payload.get("check")
    if not isinstance(kind, str) or kind not in DQ_KINDS:
        shown = kind if isinstance(kind, str) and _IDENT.match(kind) else None
        raise DqCheckInputError(shown, "check", f"must be one of {sorted(DQ_KINDS)}")
    table = payload.get("table")
    _require_table(kind, table)
    params = payload.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise DqCheckInputError(kind, "params", "must be an object")
    spec_kind = DQ_KINDS[kind]
    for name in params:
        if name not in spec_kind.required + spec_kind.optional:
            raise DqCheckInputError(
                kind, name if isinstance(name, str) and _IDENT.match(name) else "params", "is not accepted"
            )
    for name in spec_kind.required:
        if name not in params:
            raise DqCheckInputError(kind, name, "is required")
    table_spec = resolve_table_spec(table)
    normalised = _normalise(kind, table_spec, params)
    history_only = kind == "unique" and normalised["projection"] == "history"
    if not history_only and table_spec.current_table is None:
        raise DqCheckInputError(kind, "table", "has no current projection")
    return ValidatedCheck(kind, str(table), normalised)


def row_predicate(kind: str, table: str, column: str, params: dict[str, Any]) -> tuple[str, list[Any]]:
    """The violating-row predicate fragment and its binds for a row-level kind (shared with the real-DuckLake test).

    *table* names the table the caller resolved; the fragment itself is table-independent. Column names are not
    re-validated here: validate() is the gate, and the integration test passes its own fixture columns.
    """
    template = _PREDICATES[kind]
    binds: list[Any] = []
    if kind == "accepted_values":
        values = params["values"]
        fragment = template.replace("{marks}", ", ".join("?" for _ in values))
        binds = list(values)
    elif kind == "array_element_format":
        fragment, binds = template, [params["pattern"]]
    elif kind in ("min_length", "pattern", "min_value"):
        fragment, binds = template, [params["value"]]
    elif kind == "not_before":
        fragment = template.replace("{other}", params["value"])
    else:
        fragment = template
    if "{ws}" in fragment:
        fragment = fragment.replace("{ws}", whitespace_class())
    return fragment.replace("{col}", column), binds


def render(check: ValidatedCheck) -> tuple[str, list[Any]]:
    """The one bound statement and its binds for a validated check."""
    kind, params = check.kind, check.params
    spec = resolve_table_spec(check.table)
    cur = f"{CATALOG_ALIAS}.{spec.current_table}"
    hist = f"{CATALOG_ALIAS}.{spec.history_table}"
    gate = [params["exclude_before"]] if "exclude_before" in params else []
    if kind in _PREDICATES:
        pred, binds = row_predicate(kind, check.table, params["column"], params)
        sql = _ROW_COUNT.replace("{tbl}", cur).replace("{pred}", pred) + (_GATE if gate else "")
        return sql, binds + gate
    if kind == "row_count_min":
        return _TEMPLATES[kind].replace("{tbl}", cur), [params["min"]]
    if kind == "recency_hours":
        return _TEMPLATES[kind].replace("{col}", params["column"]).replace("{tbl}", cur), [params["hours"]]
    if kind == "key_absent":
        return _TEMPLATES[kind].replace("{col}", params["column"]).replace("{tbl}", cur), [params["value"]]
    if kind == "history_not_ahead_of_current":
        sql = _TEMPLATES[kind].replace("{hist}", hist).replace("{cur}", cur).replace("{mk}", spec.merge_key)
        return sql.replace("{col}", params["column"]), []
    cols = ", ".join(params["columns"])
    where = " WHERE created_timestamp >= CAST(? AS DATE)" if gate else ""
    tbl = hist if params["projection"] == "history" else cur
    return _TEMPLATES[kind].replace("{cols}", cols).replace("{tbl}", tbl).replace("{where}", where), gate


def run_check(con: Any, check: ValidatedCheck) -> int:
    """Execute a validated check and return its integer violation count."""
    sql, binds = render(check)
    row = con.execute(sql, binds).fetchone() if binds else con.execute(sql).fetchone()
    return int(row[0]) if row and row[0] is not None else 0
