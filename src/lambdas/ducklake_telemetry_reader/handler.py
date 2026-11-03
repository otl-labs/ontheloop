"""ducklake_telemetry_reader Lambda entrypoint (slice 2c, Decision N+1 cl.2-3).

Production telemetry is read only through this function: the event verbs minus transcript_content, no caller SQL and
no ops verb, on a SELECT-only catalog login attached READ_ONLY. It is env-pinned to the production telemetry catalog
and never reads a catalog from the event. Actions: describe, attach_check (gate instrumentation) and named_read.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable

from src.common import ducklake_event_reads as ev
from src.common import ducklake_handler_errors as handler_errors
from src.common import ducklake_runtime as rt
from src.common.ducklake_event_read_verbs import EVENT_READS
from src.common.ducklake_metrics import log_invocation

FUNCTION = "telemetry_reader"
EXTENSION_DIRECTORY = os.environ.get("DUCKLAKE_EXTENSION_DIRECTORY", rt.LAMBDA_EXTENSION_DIRECTORY)
SERVED_VERBS = frozenset(EVENT_READS) - {"transcript_content"}
MAX_RESPONSE_BYTES = 6_000_000
_CALLER_INPUT_CLASSES = frozenset(
    {"InvalidInputException", "ConversionException", "OutOfRangeException", "TypeMismatchException"}
)
_DUCKDB_MODULES = frozenset({"_duckdb", "duckdb"})


class NamedReadInputError(Exception):
    """A served verb refused caller input (a bad bind or a registry error() guard): answered 400 named_read_input."""

    def __init__(self, verb: str, cause: BaseException) -> None:
        super().__init__(str(cause))
        self.verb = verb
        self.error_class = type(cause).__name__


def _is_caller_input_error(exc: BaseException) -> bool:
    return isinstance(exc, ev.EventReadInputError) or any(
        c.__name__ in _CALLER_INPUT_CLASSES and c.__module__ in _DUCKDB_MODULES for c in type(exc).__mro__
    )


def _dsn_parameter() -> str:
    parameter = os.environ.get("TELEMETRY_DSN_PARAMETER", "")
    if not parameter:
        raise rt.DuckLakeRuntimeError("TELEMETRY_DSN_PARAMETER is not set: the telemetry reader needs its own scoped login")
    return parameter


def _is_smoke(data_path: str) -> bool:
    return data_path.rstrip("/") == rt.SMOKE_DATA_PATH.rstrip("/")


def _no_ops_slot(force_reopen: bool = False) -> tuple[Any, dict[str, Any]]:
    raise rt.DuckLakeRuntimeError("the telemetry reader has no ops slot")


def action_describe(_payload: dict[str, Any], _con: Any) -> dict[str, Any]:
    meta_schema, data_path = ev.telemetry_pin()
    verbs = {name: row for name, row in ev.describe_all().items() if name in SERVED_VERBS}
    return {
        "ok": True,
        "function": FUNCTION,
        "catalog": {"meta_schema": meta_schema, "data_path_is_smoke": _is_smoke(data_path)},
        "verbs": verbs,
    }


def action_attach_check(payload: dict[str, Any], con: Any) -> dict[str, Any]:
    meta_schema, data_path = ev.require_production_pin()
    rows = con.execute(
        "SELECT readonly FROM duckdb_databases() WHERE database_name = ?", [rt.TELEMETRY_CATALOG_ALIAS]
    ).fetchall()
    return {
        "ok": True,
        "meta_schema": meta_schema,
        "data_path_is_smoke": _is_smoke(data_path),
        "catalog_login": str(rt.fetch_dsn(_dsn_parameter()).get("username", "")),
        "read_only": bool(rows and rows[0][0]),
        "connect_ms": round(float(payload.get("_connect_ms", 0.0)), 2),
        "reused": bool(payload.get("_connect_reused", False)),
    }


def action_named_read(payload: dict[str, Any], con: Any) -> dict[str, Any]:
    verb = payload["verb"]
    try:
        rows = ev.execute(con, verb=verb, params=payload.get("params") or {}, limit=payload.get("limit"))
    except Exception as exc:
        if rt.is_dead_connection_error(exc) or isinstance(exc, rt.DuckLakeRuntimeError) or not _is_caller_input_error(exc):
            raise
        raise NamedReadInputError(verb, exc) from exc
    safe = [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()} for row in rows]
    return {
        "ok": True,
        "verb": verb,
        "registry_version": rt.NAMED_READS_VERSION,
        "read_version": ev.event_read_version(verb),
        "rows": safe,
        "row_count": len(safe),
    }


_ACTIONS: dict[str, Callable[[dict[str, Any], Any], dict[str, Any]]] = {
    "describe": action_describe,
    "attach_check": action_attach_check,
    "named_read": action_named_read,
}
_CONNECTIONLESS_ACTIONS = {"describe"}


def _parse_event(event: Any) -> Any:
    if isinstance(event, dict) and event.get("body") is not None:
        body = event["body"]
        if isinstance(body, str):
            return json.loads(body) if body else {}
        if isinstance(body, dict):
            return body
    return event if isinstance(event, dict) else {}


def _response(status: int, payload: dict[str, Any]) -> dict[str, Any]:
    return {"statusCode": status, "headers": {"Content-Type": "application/json"}, "body": json.dumps(payload)}


def _guard_response_size(response: dict[str, Any], payload: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
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
            "hint": "use a narrower window or a paginated served verb",
        },
    )


def _acquirer(action: str, payload: dict[str, Any]) -> Callable[..., tuple[Any, dict[str, Any]]]:
    if action == "named_read":
        return ev.acquirer_for(
            action,
            payload,
            _no_ops_slot,
            EXTENSION_DIRECTORY,
            pin=ev.require_production_pin,
            dsn_parameter=_dsn_parameter(),
            read_only=True,
        )
    meta_schema, data_path = ev.require_production_pin()
    return ev.telemetry_acquirer(meta_schema, data_path, EXTENSION_DIRECTORY, dsn_parameter=_dsn_parameter(), read_only=True)


def _handle(payload: dict[str, Any], meta: dict[str, Any], remaining_ms: Callable[[], float] | None = None) -> dict[str, Any]:
    action = payload.get("action")
    fn = _ACTIONS.get(action) if isinstance(action, str) else None
    if fn is None:
        return _response(400, {"ok": False, "error": "unknown action", "actions": sorted(_ACTIONS)})
    try:
        if action in _CONNECTIONLESS_ACTIONS:
            return _response(200, fn(payload, None))
        if action == "named_read":
            verb = payload.get("verb")
            if not isinstance(verb, str) or verb not in SERVED_VERBS:
                return _response(400, {"ok": False, "error_type": "unknown_verb", "served": sorted(SERVED_VERBS)})
        acquire = _acquirer(str(action), payload)
        under_deadline = ev.deadline_guard(action, payload, remaining_ms)
        con, conn_meta = acquire()
        payload["_connect_ms"], payload["_connect_reused"] = conn_meta["connect_ms"], conn_meta["reused"]
        meta.update(
            connect_ms=conn_meta["connect_ms"], connect_reused=conn_meta["reused"], reopened=bool(conn_meta.get("reopened"))
        )
        try:
            return _response(200, under_deadline(con, lambda: fn(payload, con)))
        except Exception as exc:  # noqa: BLE001 -- narrowed immediately to the dead-connection case
            if not rt.is_dead_connection_error(exc):
                raise
            con, conn_meta = acquire(force_reopen=True)
            payload["_connect_ms"], payload["_connect_reused"] = conn_meta["connect_ms"], conn_meta["reused"]
            meta.update(connect_ms=conn_meta["connect_ms"], connect_reused=conn_meta["reused"], reopened=True)
            return _response(200, under_deadline(con, lambda: fn(payload, con)))
    except (NamedReadInputError, ev.EventReadInputError) as exc:
        handler_errors.log_named_read_input(
            exc.__cause__ or exc, function=FUNCTION, verb=exc.verb, request_id=meta.get("request_id")
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
    except rt.VersionMismatchError as exc:
        return _response(500, {"ok": False, "error_type": "version_mismatch", "error": str(exc)})
    except rt.DuckLakeRuntimeError as exc:
        return _response(500, {"ok": False, "error_type": "runtime", "error": str(exc)})


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Entrypoint: dispatch, then log exactly one ducklake_invocation line on every path; no exception escapes as a 502."""
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
            status, body = handler_errors.bad_request(FUNCTION, "unknown")
            return _response(status, body)
        payload = parsed
        response = _guard_response_size(
            _handle(payload, meta, getattr(context, "get_remaining_time_in_millis", None)), payload, meta
        )
        status = response["statusCode"]
        return response
    except Exception as exc:
        action = payload.get("action")
        status, body = handler_errors.terminal(
            exc,
            function=FUNCTION,
            action=action if isinstance(action, str) and action in _ACTIONS else "unknown",
            request_id=meta["request_id"],
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            reopened=bool(meta["reopened"]),
        )
        return _response(status, body)
    finally:
        action = payload.get("action")
        log_invocation(
            FUNCTION,
            action if isinstance(action, str) and action in _ACTIONS else "unknown",
            status,
            meta["connect_ms"],
            meta["connect_reused"],
            meta["reopened"],
            (time.perf_counter() - started) * 1000.0,
            response_bytes=meta["response_bytes"],
        )
