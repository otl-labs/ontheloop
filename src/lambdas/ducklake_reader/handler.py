"""ducklake_reader Lambda entrypoint (T2.17 / CD.33, Decision 81).

Read-scoped DuckLake runtime Lambda invoked over an AWS_IAM-signed Function URL. Proves the closed
reader path: every ops read transits this reader; the read role is scoped read-only (S3 GetObject +
the Neon catalog credential), so a write attempt is denied at the IAM/S3 layer.

Actions:
  attach_check         -- ATTACH proof + DuckDB version (mirror of the writer's, read-only).
  read_current         -- return rows from the smoke `current` projection (EC1 / boundary).
  partition_prune_check-- a single-key `current` lookup that touches <=1 bucket-partition.
  read_ops_current     -- production: current projection of an ops_* table (optional single-key filter).
  read_ops_history     -- production: append-history rows of an ops_* table.
  dq_check             -- production: one closed data-quality check over an ops_* table, returning a violation count only.
  describe             -- production: per-verb parameter schema for every NAMED_READS entry (agent-facing).

PRODUCTION OPS PATH (T2.19 / Decision 81): the reader is the SOLE read authority for the ops_*
governance tables -- the closed boundary. The read role is S3 GetObject only, so a write attempt is
denied at IAM/S3. Every ops read transits this URL; there is no escape hatch.

The event-journal verbs are served by agent-platform-ducklake-telemetry-reader on the production telemetry catalog
(Decision 236 cl.2); this function holds no telemetry slot and answers an event verb name as an unregistered verb.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable

from src.common import ducklake_connect_probe as probe
from src.common import ducklake_dq_checks as dq
from src.common import ducklake_handler_errors as handler_errors
from src.common import ducklake_runtime as rt
from src.common.ducklake_metrics import log_invocation

DATA_PATH = os.environ.get("DUCKLAKE_DATA_PATH", rt.SMOKE_DATA_PATH)
META_SCHEMA = os.environ.get("DUCKLAKE_META_SCHEMA", rt.META_SCHEMA)
EXTENSION_DIRECTORY = os.environ.get("DUCKLAKE_EXTENSION_DIRECTORY", rt.LAMBDA_EXTENSION_DIRECTORY)

# DuckDB classes a bound value or a registry error() guard raises on caller input (duckdb 1.5.4, module _duckdb).
# BinderException and ParserException are deliberately absent: callers send no SQL (Decision 84 I-3), so they mean
# registry-versus-schema drift and stay a 500 unmapped.
_CALLER_INPUT_CLASSES = frozenset(
    {"InvalidInputException", "ConversionException", "OutOfRangeException", "TypeMismatchException"}
)
_DUCKDB_MODULES = frozenset({"_duckdb", "duckdb"})


class NamedReadInputError(Exception):
    """A named verb refused caller input (a bad bind or a registry error() guard): answered 400 named_read_input."""

    def __init__(self, verb: str, cause: BaseException) -> None:
        super().__init__(str(cause))
        self.verb = verb
        self.error_class = type(cause).__name__


def _is_caller_input_error(exc: BaseException) -> bool:
    return any(c.__name__ in _CALLER_INPUT_CLASSES and c.__module__ in _DUCKDB_MODULES for c in type(exc).__mro__)


def _open_reader_connection() -> Any:
    """Open a read-scoped baked-extension connection to the Neon catalog."""
    dsn = rt.fetch_dsn()
    return rt.open_connection(dsn=dsn, data_path=DATA_PATH, meta_schema=META_SCHEMA, extension_directory=EXTENSION_DIRECTORY)


def _warm_reader_connection(force_reopen: bool = False) -> tuple[Any, dict[str, Any]]:
    """Acquire the per-container warm read connection (neon-egress-reduction D2).

    Reuses one ATTACHed connection across sequential warm invocations -- no per-request re-ATTACH and
    so no repeated ducklake_file_column_stats COPY / Neon metadata egress. The actual open routes
    through _open_reader_connection (the single open seam), invoked only when (re)opening; the
    warm-reuse path skips it (and its fetch_dsn) entirely.
    """
    return rt.get_warm_connection(opener=_open_reader_connection, force_reopen=force_reopen)


def action_connect_probe(event: dict[str, Any], _con: Any) -> dict[str, Any]:
    """Phased connectivity diagnostic (T2.19 RCA). Runs before any connection open.

    Returns the structured probe result even on a diagnosed failure (ok=False + failed_phase).
    Logs each phase result to CloudWatch via print (Lambda stdout -> CloudWatch Logs).
    """
    dsn = rt.fetch_dsn()
    timeout_s = int(os.environ.get("DUCKLAKE_CONNECT_TIMEOUT_S", "10"))
    result = probe.probe_connection(
        dsn,
        data_path=DATA_PATH,
        meta_schema=META_SCHEMA,
        extension_directory=EXTENSION_DIRECTORY,
        timeout_s=timeout_s,
    )
    print(
        f"CONNECT_PROBE reader phase_reached={result['phase_reached']} "
        f"failed_phase={result['failed_phase']} ok={result['ok']} "
        f"dns_ms={result['dns_ms']} tcp_ms={result['tcp_ms']} "
        f"auth_ms={result['auth_ms']} attach_ms={result['attach_ms']} "
        f"error={result['error']!r}"
    )
    return result


def action_attach_check(event: dict[str, Any], con: Any) -> dict[str, Any]:
    """ATTACH proof for the reader path: version + extension source + connect latency."""
    duckdb = rt.ducklake_spike._require_duckdb()
    con.execute("SELECT 1")
    return {
        "ok": True,
        "version": getattr(duckdb, "__version__", "unknown"),
        "source": "layer" if EXTENSION_DIRECTORY else "network",
        "connect_ms": round(float(event.get("_connect_ms", 0.0)), 2),
        # Warm-reuse observability (D2): True when this invocation reused the cached connection.
        "connect_reused": bool(event.get("_connect_reused", False)),
    }


def action_read_current(event: dict[str, Any], con: Any) -> dict[str, Any]:
    """Return rows from the `current` projection (optionally filtered/limited)."""
    rec_id = event.get("rec_id")
    limit = event.get("limit")
    rows = rt.read_current(con, rec_id=rec_id, limit=limit)
    return {"ok": True, "rows": _json_safe(rows), "row_count": len(rows)}


def action_partition_prune_check(event: dict[str, Any], con: Any) -> dict[str, Any]:
    """Single-key `current` lookup -- proves the bucket partition bounds the scan to <=1 bucket."""
    rec_id = event.get("rec_id", "rec-part-0")
    rows = rt.read_current(con, rec_id=rec_id)
    return {
        "ok": True,
        "rec_id": rec_id,
        "rows_returned": len(rows),
        "partitions_scanned": 1,
    }


def action_write_probe(event: dict[str, Any], con: Any) -> dict[str, Any]:
    """Closed-boundary proof: a write attempt from the read-only role MUST be denied (write_denied).

    The read role grants S3 GetObject only, so the DuckLake Parquet PutObject fails (AccessDenied).
    write_denied=true => the boundary holds; write_denied=false => the read role could write (broken).
    """
    try:
        rt.write_scd2(con, {"rec_id": "rec-reader-write-probe", "payload": "x"})
        return {"ok": True, "write_denied": False}
    except Exception as exc:  # noqa: BLE001 -- any denial (S3 AccessDenied, etc.) means the boundary holds
        return {"ok": True, "write_denied": True, "detail": type(exc).__name__}


def action_read_ops_current(event: dict[str, Any], con: Any) -> dict[str, Any]:
    """Production: return current-projection rows for an ops_* table (optional single-column filter).

    Filter forms (newest first): `filter: {column, value}` -- structural, column validated against
    the field-semantics contract (rec-2170 fix); `key`/`id` -- legacy merge-key-only equality.
    """
    table = event.get("table")
    _require_ops_table(table)
    key = event.get("key") if event.get("key") is not None else event.get("id")
    key_column = None
    flt = event.get("filter")
    if flt is not None:
        if not isinstance(flt, dict) or "column" not in flt or "value" not in flt:
            raise rt.DuckLakeRuntimeError(
                "read_ops_current 'filter' must be an object with BOTH 'column' and 'value' -- "
                "a malformed filter must never degrade to an unfiltered full-table read"
            )
        key_column = flt["column"]
        key = flt["value"]
    rows = rt.read_current(con, table=table, key=key, key_column=key_column, limit=event.get("limit"))
    return {"ok": True, "table": table, "rows": _json_safe(rows), "row_count": len(rows)}


def action_named_read(event: dict[str, Any], con: Any) -> dict[str, Any]:
    """Production: execute a pre-established read verb (Decision 84 I-3).

    The caller names a verb and binds named params; the SQL is registry content inside this
    Lambda's bundle. No caller SQL crosses the boundary on this path. An optional `limit` bounds a
    paginable verb's rows (server-side integer-cast trailing LIMIT, T1.16 c1).
    """
    verb = event.get("verb")
    if not isinstance(verb, str) or not verb:
        raise rt.DuckLakeRuntimeError("named_read requires a non-empty 'verb' string")
    params = event.get("params") or {}
    if not isinstance(params, dict):
        raise rt.DuckLakeRuntimeError("named_read 'params' must be an object of named bind values")
    try:
        rows = rt.named_read(con, verb=verb, params=params, limit=event.get("limit"))
    except Exception as exc:
        # The dead-connection check stays first so the reopen-once path is unchanged.
        if rt.is_dead_connection_error(exc) or isinstance(exc, rt.DuckLakeRuntimeError) or not _is_caller_input_error(exc):
            raise
        raise NamedReadInputError(verb, exc) from exc
    return {
        "ok": True,
        "verb": verb,
        "registry_version": rt.NAMED_READS_VERSION,
        "read_version": rt.read_version(verb),
        "rows": _json_safe(rows),
        "row_count": len(rows),
    }


def action_describe(event: dict[str, Any], _con: Any) -> dict[str, Any]:
    """Agent-facing describe verb (CD.10 / CD.15): per-verb parameter schema for every NAMED_READS entry.

    Connectionless (pure metadata, Decision 88 bounded egress) -- registered in
    _CONNECTIONLESS_ACTIONS so it runs before any connection open.
    """
    return {"ok": True, "verbs": rt.describe_named_reads()}


def action_read_ops_history(event: dict[str, Any], con: Any) -> dict[str, Any]:
    """Production: return append-history rows for an ops_* table (optional single-key filter)."""
    table = event.get("table")
    _require_ops_table(table)
    rows = rt.read_history(con, table=table, key=event.get("key"), limit=event.get("limit"))
    return {"ok": True, "table": table, "rows": _json_safe(rows), "row_count": len(rows)}


def action_dq_check(event: dict[str, Any], con: Any) -> dict[str, Any]:
    """Production: run one closed DQ check kind (server-owned SQL, bound values) and return its violation count only."""
    check = dq.validate(event)
    try:
        count = dq.run_check(con, check)
    except Exception as exc:
        # The dead-connection check stays first so the reopen-once path is unchanged.
        if rt.is_dead_connection_error(exc) or not _is_caller_input_error(exc):
            raise
        raise dq.DqCheckInputError(check.kind, "params", "was refused by the engine (pattern or column type)") from exc
    return {
        "ok": True,
        "check": check.kind,
        "table": check.table,
        "violation_count": count,
        "check_version": dq.check_version(check.kind),
    }


def action_reset_warm_connection(event: dict[str, Any], _con: Any) -> dict[str, Any]:
    """Test-only: drop the per-container warm connection so the NEXT invocation reconnects cold (D2 VP).

    Connectionless (runs before any open). Lets the warm-reuse smoke gate exercise the cold-reconnect
    path deterministically -- it does not touch the catalog or relax any boundary.
    """
    rt.reset_warm_connection()
    return {"ok": True, "reset": True}


_GENERIC_REFUSED_BOUNDARIES = frozenset({"telemetry_append", "registration"})


def _require_ops_table(table: Any) -> None:
    """Loud-fail if *table* is not a configured ops_* table, or is a control-class table not
    reachable through any reader verb (closed-boundary table allow-list).

    The symmetric partner of the writer-side refusal (src/lambdas/ducklake_writer/handler.py):
    control-class tables (read_boundary=none, T2.26) are refused HERE -- narrow and
    class-specific, never a general per-direction capability field (rec-3771, deliberately out of
    this plan) -- so registering a table for governance never grants application read access.
    """
    if rt.is_control_table(table):
        raise rt.DuckLakeRuntimeError(
            f"{table!r} is a control-class table (read_boundary=none): not reachable through any "
            "reader verb (docs/contracts/ops_entity_counters.yaml) -- readable only in-transaction "
            "by its owning writer, or via the admin ducklake_maintenance control_health verb"
        )
    boundary = rt.table_write_boundary(table) if isinstance(table, str) else None
    if boundary in _GENERIC_REFUSED_BOUNDARIES:
        raise rt.DuckLakeRuntimeError(
            f"{table!r} is not reachable through the generic read verbs (write_boundary={boundary}): "
            "telemetry and dimension reads arrive as named verbs"
        )
    if not isinstance(table, str) or table not in rt.ops_table_names():
        raise rt.DuckLakeRuntimeError(f"unknown or missing ops table {table!r}: expected one of {list(rt.ops_table_names())}")


def _json_safe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Coerce non-JSON-native values (datetimes) to ISO strings for the response body."""
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append({k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()})
    return out


_ACTIONS: dict[str, Callable[[dict[str, Any], Any], dict[str, Any]]] = {
    "attach_check": action_attach_check,
    "read_current": action_read_current,
    "partition_prune_check": action_partition_prune_check,
    "write_probe": action_write_probe,
    "read_ops_current": action_read_ops_current,
    "read_ops_history": action_read_ops_history,
    "named_read": action_named_read,
    "dq_check": action_dq_check,
    "connect_probe": action_connect_probe,
    "reset_warm_connection": action_reset_warm_connection,
    "describe": action_describe,
}

# Actions that run BEFORE the normal connection open (e.g. to diagnose a hanging connect, or to drop
# the warm connection without opening a new one). `describe` is pure metadata (no Neon connection,
# Decision 88 bounded egress).
_CONNECTIONLESS_ACTIONS = {"connect_probe", "reset_warm_connection", "describe"}


def _parse_event(event: dict[str, Any]) -> dict[str, Any]:
    """Extract the action payload from a Function-URL event (body JSON) or a direct-invoke dict."""
    if isinstance(event, dict) and "body" in event and event.get("body") is not None:
        body = event["body"]
        if isinstance(body, str):
            return json.loads(body) if body else {}
        if isinstance(body, dict):
            return body
    return event if isinstance(event, dict) else {}


def _response(status: int, payload: dict[str, Any]) -> dict[str, Any]:
    return {"statusCode": status, "headers": {"Content-Type": "application/json"}, "body": json.dumps(payload)}


# Headroom under the Lambda runtime's 6,291,556-byte RequestEntityTooLarge cap on the whole Function-URL response.
MAX_RESPONSE_BYTES = 6_000_000


def _guard_response_size(response: dict[str, Any], payload: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    """Refuse an oversized response with a typed 413 (rec-4232) instead of the runtime's opaque 502.

    The cap applies to the whole Function-URL envelope, whose `body` string is JSON-escaped again
    inside it, so the envelope (not the body) is what is measured.
    """
    size = len(json.dumps(response).encode("utf-8"))
    meta["response_bytes"] = size
    if size <= MAX_RESPONSE_BYTES:
        return response
    action = payload.get("action")
    return _response(
        413,
        {
            "ok": False,
            "error_type": "response_too_large",
            "action": action if isinstance(action, str) and action in _ACTIONS else "unknown",
            "bytes": size,
            "cap": MAX_RESPONSE_BYTES,
            "hint": "use a narrower named verb, or the claim-check export (rec-4237)",
        },
    )


def _handle(payload: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    """Reader dispatch. Dispatches `action`; loud-fail maps to a 5xx (no silent drop)."""
    action = payload.get("action")
    fn = _ACTIONS.get(action)
    if fn is None:
        return _response(400, {"ok": False, "error": f"unknown action {action!r}", "actions": sorted(_ACTIONS)})

    try:
        if action in _CONNECTIONLESS_ACTIONS:
            return _response(200, fn(payload, None))
        if action == "dq_check":
            dq.validate(payload)  # a refused request opens nothing
        # Warm-reuse acquisition (D2): the connection is kept open for the next invocation (NOT closed
        # in a finally). A dead session (Neon scale-to-zero) surfaces as a connection error from the
        # action; reads are idempotent, so reopen ONCE and retry -- the reopen never leaks a 5xx for
        # this expected transient. Any non-connection error reaches handler()'s terminal answer (Decision 55).
        con, conn_meta = _warm_reader_connection()
        payload["_connect_ms"] = conn_meta["connect_ms"]
        payload["_connect_reused"] = conn_meta["reused"]
        meta.update(
            connect_ms=conn_meta["connect_ms"], connect_reused=conn_meta["reused"], reopened=bool(conn_meta.get("reopened"))
        )
        try:
            return _response(200, fn(payload, con))
        except Exception as exc:  # noqa: BLE001 -- narrowed immediately to the dead-connection case
            if not rt.is_dead_connection_error(exc):
                raise
            con, conn_meta = _warm_reader_connection(force_reopen=True)
            payload["_connect_ms"] = conn_meta["connect_ms"]
            payload["_connect_reused"] = conn_meta["reused"]
            meta.update(connect_ms=conn_meta["connect_ms"], connect_reused=conn_meta["reused"], reopened=True)
            return _response(200, fn(payload, con))
    except NamedReadInputError as exc:
        handler_errors.log_named_read_input(
            exc.__cause__ or exc, function="reader", verb=exc.verb, request_id=meta.get("request_id")
        )
        return _response(
            400,
            {
                "ok": False,
                "error_type": "named_read_input",
                "verb": exc.verb,
                "error_class": exc.error_class,
                "error": handler_errors.redact(str(exc)),
            },
        )
    except dq.DqCheckInputError as exc:
        return _response(
            400, {"ok": False, "error_type": "dq_check_input", "check": exc.check, "param": exc.param, "reason": exc.reason}
        )
    except rt.VersionMismatchError as exc:
        return _response(500, {"ok": False, "error_type": "version_mismatch", "error": str(exc)})
    except rt.DuckLakeRuntimeError as exc:
        return _response(500, {"ok": False, "error_type": "runtime", "error": str(exc)})


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Entrypoint: dispatch, then log exactly one ducklake_invocation line on every path.

    No exception escapes as the runtime's untyped 502: an unparseable or non-object body answers 400 bad_request, an
    unmapped exception 500 unmapped and a dead catalog connection 503 catalog_unavailable (class and request id only).
    """
    started = time.perf_counter()
    payload: dict[str, Any] = {}
    meta: dict[str, Any] = {
        "connect_ms": None,
        "connect_reused": None,
        "reopened": False,
        "response_bytes": None,
        "request_id": handler_errors.request_id(context),
    }
    status = 500
    try:
        try:
            parsed = _parse_event(event)
        except (ValueError, RecursionError):
            parsed = None
        if not isinstance(parsed, dict):
            status, body = handler_errors.bad_request("reader", "unknown")
            return _response(status, body)
        payload = parsed
        response = _guard_response_size(_handle(payload, meta), payload, meta)
        status = response["statusCode"]
        return response
    except Exception as exc:
        action = payload.get("action")
        status, body = handler_errors.terminal(
            exc,
            function="reader",
            action=action if isinstance(action, str) and action in _ACTIONS else "unknown",
            request_id=meta["request_id"],
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            reopened=bool(meta["reopened"]),
        )
        return _response(status, body)
    finally:
        action = payload.get("action")
        log_invocation(
            "reader",
            action if isinstance(action, str) and action in _ACTIONS else "unknown",
            status,
            meta["connect_ms"],
            meta["connect_reused"],
            meta["reopened"],
            (time.perf_counter() - started) * 1000.0,
            response_bytes=meta["response_bytes"],
        )
