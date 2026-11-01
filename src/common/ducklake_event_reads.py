"""Event-journal read mechanism for the ducklake_telemetry_reader Lambda (Decision 199 cl.1, Decision 207, Decision 84 I-3).

The telemetry tables are event journals read through an isolated warm connection (the "telemetry" slot) by named verbs
whose SQL is registry content. This module owns the EventRead type, parameter validation (before any SQL or connection),
the production-pin refusal, the partition binding, the compared-column set behind the conflict report, the
event_read_version stamp, execution, and the routing helpers the telemetry reader handler calls.

Imported by the telemetry reader handler only; the shared reader serves no event verb (Decision 236). Import direction (no
cycle): ducklake_event_read_verbs imports EventRead from here at module scope; the routing helpers here import EVENT_READS
function-locally. Nothing in ducklake_runtime's import closure imports this module, so the other DuckLake bundles need no
new file. Event reads use src.telemetry.identity/timestamps and src.row_rules only, never src.telemetry.append.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from src.common import ducklake_read_deadline as deadline
from src.common import ducklake_runtime as rt
from src.common.ducklake_event_read_sql import AGENTS, OBSERVATIONS, SESSIONS, TRANSCRIPTS
from src.row_rules.rules import RowRules
from src.telemetry.identity import IdentityError, canonical_ulid, decode_time_prefix
from src.telemetry.timestamps import TimestampError, parse_iso8601_utc

TELEMETRY_SLOT = "telemetry"
TELEMETRY_MEMORY_LIMIT = "300MB"
TELEMETRY_TEMP_DIRECTORY = "/tmp/telemetry-spill"
TELEMETRY_MAX_TEMP_SIZE = "300MB"
KIND = "event_journal"
CATALOG = "telemetry"

_STRUCTURAL_EXCLUDED = frozenset({"created_timestamp", "producer_version"})
_EVENT_TABLES = (SESSIONS, OBSERVATIONS, TRANSCRIPTS, AGENTS)
_PLACEHOLDER_TABLES = {
    "sessions": SESSIONS,
    "observations": OBSERVATIONS,
    "agents": AGENTS,
    "transcripts": TRANSCRIPTS,
}
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_BOUND_NAME = re.compile(r"\$([a-z_]+)")
_SCOPE_SESSION = "session_started_at >= $part_from AND session_started_at < $part_to AND session_id = $session_id"
_INT_MIN = {"parser_version": 1, "offset": 0, "max_bytes": 1, "max_rows": 1}
_PRODUCER_MAX = 200
_SCOPE_WINDOW = "session_started_at >= $part_from AND session_started_at < $part_to"
_IDENT = re.compile(r"^[a-z][a-z0-9_]*$")
_LIVE_CTE = {SESSIONS: "s", OBSERVATIONS: "o", AGENTS: "a", TRANSCRIPTS: "t"}
_JOINS_PLACEHOLDER = "{telemetry_joins}"


class EventReadInputError(Exception):
    """A caller value is refused before any SQL: answered 400 named_read_input naming the parameter, never its value."""

    def __init__(self, verb: str, param: str, reason: str) -> None:
        super().__init__(f"{verb}: parameter {param!r} {reason}")
        self.verb = verb
        self.param = param
        self.error_class = type(self).__name__


@dataclass(frozen=True)
class EventRead:
    """One event-journal read verb: registry SQL over the telemetry tables, bound by a session or a day window."""

    verb: str
    tables: tuple[str, ...]
    sql: str
    params: tuple[str, ...] = ()
    binding: str = "none"  # session | window | tree | none
    max_window_days: int | None = None
    data: Mapping[str, Any] = field(default_factory=dict)
    description: str = ""
    paginable: bool = False
    agent_facing: bool = True
    int_params: tuple[str, ...] = ()


def _event_reads() -> dict[str, EventRead]:
    from src.common.ducklake_event_read_verbs import (
        EVENT_READS,  # noqa: PLC0415 -- the verbs module imports EventRead from here
    )

    return EVENT_READS


# ---------------------------------------------------------------------------
# Pin, validation and partition binding
# ---------------------------------------------------------------------------


def telemetry_pin() -> tuple[str, str]:
    """The pinned (meta_schema, data_path) the reader's telemetry slot attaches."""
    return os.environ.get("TELEMETRY_META_SCHEMA", ""), os.environ.get("TELEMETRY_DATA_PATH", "")


def require_production_pin() -> tuple[str, str]:
    """Refuse every event verb unless the pin names the production telemetry catalog (Decision 200 cl.2, Decision 236)."""
    meta_schema, data_path = telemetry_pin()
    if meta_schema != rt.TELEMETRY_PROD_META_SCHEMA or data_path.rstrip("/") != rt.TELEMETRY_PROD_DATA_PATH.rstrip("/"):
        raise rt.DuckLakeRuntimeError(
            "event-journal reads are refused unless TELEMETRY_META_SCHEMA and TELEMETRY_DATA_PATH name the production "
            "telemetry catalog"
        )
    return meta_schema, data_path


def _ulid(entry: EventRead, name: str, value: Any) -> str:
    if not isinstance(value, str):
        raise EventReadInputError(entry.verb, name, "must be a string")
    try:
        canonical = canonical_ulid(value)
    except IdentityError:
        raise EventReadInputError(entry.verb, name, "must be a canonical 26-character ULID") from None
    if canonical != value:
        raise EventReadInputError(entry.verb, name, "must be a canonical 26-character ULID")
    return value


def _producer(entry: EventRead, value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > _PRODUCER_MAX or "\x00" in value:
        raise EventReadInputError(entry.verb, "producer", f"must be a non-empty string of at most {_PRODUCER_MAX} characters")
    return value


def _integer(entry: EventRead, name: str, value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise EventReadInputError(entry.verb, name, "must be an integer")
    if value < _INT_MIN.get(name, 0):
        raise EventReadInputError(entry.verb, name, f"must be at least {_INT_MIN.get(name, 0)}")
    return value


def _as_of(entry: EventRead, value: Any) -> datetime:
    try:
        return parse_iso8601_utc(value)
    except TimestampError:
        raise EventReadInputError(entry.verb, "as_of", "must be a timezone-aware ISO-8601 timestamp") from None


def _day(entry: EventRead, name: str, value: Any) -> date:
    if not isinstance(value, str) or _DAY.match(value) is None:
        raise EventReadInputError(entry.verb, name, "must be an ISO-8601 date (YYYY-MM-DD)")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise EventReadInputError(entry.verb, name, "must be an ISO-8601 date (YYYY-MM-DD)") from None


def _midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


def bind_values(entry: EventRead, params: Any) -> dict[str, Any]:
    """Validate the caller's params and return the named bind values (never text spliced into SQL)."""
    if not isinstance(params, dict):
        raise EventReadInputError(entry.verb, "params", "must be an object")
    if set(params) != set(entry.params):
        raise EventReadInputError(entry.verb, "params", f"must be exactly {list(entry.params)}")
    values: dict[str, Any] = {}
    if "session_id" in entry.params:
        session_id = _ulid(entry, "session_id", params["session_id"])
        start = _midnight(decode_time_prefix(session_id).date())
        values.update(session_id=session_id, part_from=start, part_to=start + timedelta(days=1))
    if "producer" in entry.params:
        values["producer"] = _producer(entry, params["producer"])
    if "event_id" in entry.params:
        values["event_id"] = _ulid(entry, "event_id", params["event_id"])
    for name in entry.int_params:
        values[name] = _integer(entry, name, params[name])
    if entry.binding == "tree":
        from src.common.ducklake_event_read_verbs import MAX_TREE_ROWS  # noqa: PLC0415 -- the verbs module imports this one

        values["max_rows"] = min(values["max_rows"], MAX_TREE_ROWS)
    if "as_of" in entry.params:
        values["as_of"] = _as_of(entry, params["as_of"])
    if "day_from" in entry.params:
        first, last = _day(entry, "day_from", params["day_from"]), _day(entry, "day_to", params["day_to"])
        span = (last - first).days + 1
        if span < 1 or span > (entry.max_window_days or 1):
            raise EventReadInputError(entry.verb, "day_to", f"window must span 1 to {entry.max_window_days} days")
        values.update(part_from=_midnight(first), part_to=_midnight(last) + timedelta(days=1))
    if entry.binding == "none":
        values["meta_schema"] = telemetry_pin()[0]
    return values


# ---------------------------------------------------------------------------
# Compared columns, rendering and versioning
# ---------------------------------------------------------------------------


def compared_columns(table: str, semantics: dict[str, Any] | None = None) -> tuple[str, ...]:
    """Every stored column except created_timestamp, producer_version and the row rules' representation-only columns.

    Same set the writer's grain check compares (EventTableSpec.compare_excluded), derived from the same projection.
    """
    semantics = semantics if semantics is not None else rt.load_field_semantics()
    projection = semantics["ops_tables"][table]
    columns = projection["columns"]
    excluded = _STRUCTURAL_EXCLUDED | RowRules.from_projection(columns, projection.get("table_rules")).representation_only
    return tuple(name for name in columns if name not in excluded)


def telemetry_joins(semantics: dict[str, Any] | None = None) -> list[dict[str, str]]:
    """The telemetry referential joins the generator projected from docs/contracts/_joins.yaml (read only when called)."""
    semantics = semantics if semantics is not None else rt.load_field_semantics()
    joins = semantics.get("telemetry_joins")
    if not joins:
        raise rt.DuckLakeRuntimeError("field_semantics carries no telemetry_joins projection")
    return [dict(join) for join in joins]


def _joins_cte(joins: list[dict[str, str]]) -> str:
    """The jn and edges CTEs over the deduped live rows (s, o, a, t) of one window, one SELECT per join."""
    edges = []
    for join in joins:
        child, parent = _LIVE_CTE.get(join["child_table"]), _LIVE_CTE.get(join["parent_table"])
        names = (join["join_id"], join["child_column"], join["parent_column"])
        if child is None or parent is None or not all(_IDENT.match(name) for name in names):
            raise rt.DuckLakeRuntimeError(f"telemetry join {join.get('join_id')!r} names an unsupported table or column")
        edges.append(
            f"SELECT '{join['join_id']}' AS join_id, c.tenant_id AS tenant_id, c.project_id AS project_id, "
            f"c.session_started_at AS session_started_at, EXISTS (SELECT 1 FROM {parent} p "
            "WHERE p.tenant_id = c.tenant_id AND p.project_id = c.project_id AND p.session_started_at = c.session_started_at "
            f"AND p.{join['parent_column']} = c.{join['child_column']}) AS has_parent FROM {child} c "
            f"WHERE c.{join['child_column']} IS NOT NULL"
        )
    ids = " UNION ALL ".join(f"SELECT '{join['join_id']}' AS join_id" for join in joins)
    return f"jn AS ({ids}), edges AS ({' UNION ALL '.join(edges)})"


def render_sql(entry: EventRead, semantics: dict[str, Any] | None = None) -> str:
    """Render the verb's template: table names under the telemetry alias, scope, compared columns and registry data."""
    from src.common.ducklake_event_read_verbs import render_data  # noqa: PLC0415

    semantics = semantics if semantics is not None else rt.load_field_semantics()
    alias = rt.TELEMETRY_CATALOG_ALIAS
    projects = rt.resolve_table_spec("ops_projects", semantics).current_table
    sql = entry.sql
    replacements: dict[str, str] = {
        "scope": _SCOPE_SESSION if entry.binding == "session" else _SCOPE_WINDOW,
        "projects": f"{alias}.{projects}",
        "metadata": f"__ducklake_metadata_{alias}",
        **{name: f"{alias}.{table}" for name, table in _PLACEHOLDER_TABLES.items()},
        **{f"cmp_{name}": ", ".join(compared_columns(table, semantics)) for name, table in _PLACEHOLDER_TABLES.items()},
        **render_data(entry.data),
    }
    if _JOINS_PLACEHOLDER in sql:
        replacements["telemetry_joins"] = _joins_cte(telemetry_joins(semantics))
    for key, text in replacements.items():
        sql = sql.replace("{" + key + "}", text)
    leftover = re.findall(r"\{[a-z_]+\}", sql)
    if leftover:
        raise rt.DuckLakeRuntimeError(f"event verb {entry.verb!r} has unbound template placeholder(s) {sorted(set(leftover))}")
    return sql


def event_read_version(verb: str, semantics: dict[str, Any] | None = None) -> str:
    """Opaque stamp: first 16 hex of sha256 over the verb's definition, registry data and compared columns.

    Moves with the SQL, any datum of the verb's data block, and any compared column, so a before/after delta is never read
    across a derivation change.
    """
    entry = _event_reads()[verb]
    semantics = semantics if semantics is not None else rt.load_field_semantics()
    definition: dict[str, Any] = {
        "verb": entry.verb,
        "tables": list(entry.tables),
        "params": list(entry.params),
        "binding": entry.binding,
        "max_window_days": entry.max_window_days,
        "paginable": entry.paginable,
        "sql": " ".join(entry.sql.split()),
        "data": entry.data,
        "compared": {t: list(compared_columns(t, semantics)) for t in _EVENT_TABLES},
    }
    if _JOINS_PLACEHOLDER in entry.sql:
        definition["joins"] = telemetry_joins(semantics)
    canonical = json.dumps(definition, sort_keys=True, separators=(",", ":"), default=list)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Execution and routing
# ---------------------------------------------------------------------------


def execute(con: Any, *, verb: str, params: Any, limit: Any = None) -> list[dict[str, Any]]:
    """Run one registered event verb: validated, one SQL statement, every value bound."""
    return execute_entry(con, _event_reads()[verb], params, limit)


def execute_entry(con: Any, entry: EventRead, params: Any, limit: Any = None) -> list[dict[str, Any]]:
    values = bind_values(entry, params)
    sql = render_sql(entry)
    content = entry.data.get("response") == "content"
    if limit is not None:
        if not entry.paginable:
            raise rt.DuckLakeRuntimeError(f"read verb {entry.verb!r} is not paginable: `limit` is not accepted")
        sql += f" LIMIT {int(limit)}"
    used = set(_BOUND_NAME.findall(sql))
    cursor = con.execute(sql, {name: value for name, value in values.items() if name in used})
    columns = [desc[0] for desc in cursor.description]
    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    if content:
        from src.common.ducklake_event_content import serve_content  # noqa: PLC0415 -- no deployed reader carries it

        return serve_content(rows, values)
    return rows


def _telemetry_opener(
    meta_schema: str, data_path: str, extension_directory: str, dsn_secret_id: str | None = None, read_only: bool = False
) -> Callable[[], Any]:
    options: dict[str, Any] = {"read_only": True} if read_only else {}

    def opener() -> Any:
        con = rt.open_connection(
            dsn=rt.fetch_dsn() if dsn_secret_id is None else rt.fetch_dsn(dsn_secret_id),
            data_path=data_path,
            meta_schema=meta_schema,
            extension_directory=extension_directory,
            alias=rt.TELEMETRY_CATALOG_ALIAS,
            **options,
        )
        con.execute(f"SET memory_limit='{TELEMETRY_MEMORY_LIMIT}'")
        con.execute(f"SET temp_directory='{TELEMETRY_TEMP_DIRECTORY}'")
        con.execute(f"SET max_temp_directory_size='{TELEMETRY_MAX_TEMP_SIZE}'")
        return con

    return opener


_INTERRUPTED_SLOTS: set[str] = set()


def _catalog_probe_sql() -> str:
    return f"SELECT count(*) FROM __ducklake_metadata_{rt.TELEMETRY_CATALOG_ALIAS}.ducklake_metadata"


def _deadline_ms(verb: str, payload: dict[str, Any]) -> int | None:
    value = payload.get("deadline_ms")
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise EventReadInputError(verb, "deadline_ms", "must be a positive integer")
    return value


def deadline_guard(
    action: Any, payload: dict[str, Any], remaining_ms: Callable[[], float] | None
) -> Callable[[Any, Callable[[], Any]], Any]:
    """The deadline runner for this request: armed for a named_read of an event verb, a pass-through for everything else."""
    verb = payload.get("verb") if action == "named_read" else None
    if not isinstance(verb, str) or verb not in _event_reads():
        return deadline.runner(None, None, lambda: None)
    return deadline.runner(remaining_ms, _deadline_ms(verb, payload), lambda: _INTERRUPTED_SLOTS.add(TELEMETRY_SLOT))


def telemetry_acquirer(
    meta_schema: str, data_path: str, extension_directory: str, *, dsn_secret_id: str | None = None, read_only: bool = False
) -> Callable[..., tuple[Any, dict[str, Any]]]:
    """The telemetry-slot acquire callable: opened lazily; the first acquire after a deadline interrupt probes the catalog."""
    opener = _telemetry_opener(meta_schema, data_path, extension_directory, dsn_secret_id, read_only)

    def acquire(force_reopen: bool = False) -> tuple[Any, dict[str, Any]]:
        probe = _catalog_probe_sql() if TELEMETRY_SLOT in _INTERRUPTED_SLOTS else "SELECT 1"
        _INTERRUPTED_SLOTS.discard(TELEMETRY_SLOT)
        return rt.get_warm_connection(
            opener=opener,
            data_path=data_path,
            meta_schema=meta_schema,
            extension_directory=extension_directory,
            slot=TELEMETRY_SLOT,
            alias=rt.TELEMETRY_CATALOG_ALIAS,
            probe_sql=probe,
            force_reopen=force_reopen,
        )

    return acquire


def acquirer_for(
    action: Any,
    payload: dict[str, Any],
    ops: Callable[..., tuple[Any, dict[str, Any]]],
    extension_directory: str,
    *,
    pin: Callable[[], tuple[str, str]],
    dsn_secret_id: str | None = None,
    read_only: bool = False,
) -> Callable[..., tuple[Any, dict[str, Any]]]:
    """The connection acquirer for this request: the handler's own ops acquirer, unchanged, for everything but an event verb.

    An event verb first passes the caller's declared pin refusal and parameter validation, so a refused request never pays
    an ATTACH, then binds the telemetry slot (opened lazily on its first verb). The first acquire after a deadline interrupt
    probes the catalog connection once; a failed probe reopens through get_warm_connection's existing path.
    """
    verb = payload.get("verb") if action == "named_read" else None
    if not isinstance(verb, str) or verb not in _event_reads():
        if action == "named_read" and payload.get("deadline_ms") is not None:
            raise rt.DuckLakeRuntimeError("`deadline_ms` is accepted only for event-journal read verbs")
        return ops
    meta_schema, data_path = pin()
    params = payload.get("params")
    bind_values(_event_reads()[verb], {} if params is None else params)
    _deadline_ms(verb, payload)
    return telemetry_acquirer(meta_schema, data_path, extension_directory, dsn_secret_id=dsn_secret_id, read_only=read_only)


def _described_params(entry: EventRead) -> dict[str, Any]:
    from src.common.ducklake_named_reads import _params_schema  # noqa: PLC0415

    schema = _params_schema(entry.params)
    for name in entry.int_params:
        schema["properties"][name] = {"type": "integer"}
    return schema


def describe_all(semantics: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """Both registries for the agent-facing `describe`: the ops rows unchanged plus one superset row per event verb."""
    verbs = rt.describe_named_reads() if semantics is None else rt.describe_named_reads(semantics)
    for verb, entry in _event_reads().items():
        verbs[verb] = {
            "table": entry.tables[0],
            "description": entry.description,
            "params": list(entry.params),
            "paginable": entry.paginable,
            "agent_facing": entry.agent_facing,
            "params_schema": _described_params(entry),
            "read_version": event_read_version(verb, semantics),
            "kind": KIND,
            "tables": list(entry.tables),
            "catalog": CATALOG,
            "binding": entry.binding,
            "max_window_days": entry.max_window_days,
        }
    return verbs
