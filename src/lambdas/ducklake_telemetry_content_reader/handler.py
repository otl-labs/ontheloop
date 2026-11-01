"""ducklake_telemetry_content_reader Lambda entrypoint (Decision 239).

Serves production transcript text masked, through two verbs only (one row, one session tree), on the telemetry reader's
SELECT-only catalog login attached READ_ONLY. Env-pinned to the production telemetry catalog. Every served byte is masked
here, every named_read writes exactly one value-free access-log line, and reads cover the last 90 days. Actions: describe
and named_read. A stub package until rec-4696 builds, locks and deploys it.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable

from src.common import ducklake_event_reads as ev
from src.common import ducklake_handler_errors as handler_errors
from src.common import ducklake_read_deadline as deadline
from src.common import ducklake_runtime as rt
from src.common.ducklake_content_read_verbs import (
    CONTENT_READS,
    content_read_version,
    in_read_window,
    tree_in_read_window,
    utc_now,
)
from src.common.ducklake_metrics import log_invocation
from src.lambdas.ducklake_telemetry_content_reader import access_log, masked

FUNCTION = "telemetry_content_reader"
EXTENSION_DIRECTORY = os.environ.get("DUCKLAKE_EXTENSION_DIRECTORY", rt.LAMBDA_EXTENSION_DIRECTORY)
SERVED_VERBS = frozenset(CONTENT_READS)
SESSION_VERB = "session_transcript_masked"
MAX_RESPONSE_BYTES = 6_000_000
_REFUSED_KEYS = ("deadline_ms", "window", "window_days", "limit")


def _secret_id() -> str:
    secret_id = os.environ.get("TELEMETRY_DSN_SECRET_ID", "")
    if not secret_id:
        raise rt.DuckLakeRuntimeError(
            "TELEMETRY_DSN_SECRET_ID is not set: the content reader needs the telemetry reader login"
        )
    return secret_id


def _describe() -> dict[str, Any]:
    meta_schema, data_path = ev.telemetry_pin()
    verbs: dict[str, Any] = {}
    for name, entry in CONTENT_READS.items():
        params_schema = ev._described_params(entry)
        if name == SESSION_VERB:
            params_schema["properties"]["include_tool_output"] = {"type": "boolean"}
        verbs[name] = {
            "table": entry.tables[0],
            "description": entry.description,
            "params": list(entry.params) + (["include_tool_output"] if name == SESSION_VERB else []),
            "params_schema": params_schema,
            "read_version": content_read_version(entry),
            "kind": ev.KIND,
            "tables": list(entry.tables),
            "catalog": ev.CATALOG,
            "binding": entry.binding,
        }
    return {
        "ok": True,
        "function": FUNCTION,
        "catalog": {
            "meta_schema": meta_schema,
            "data_path_is_smoke": data_path.rstrip("/") == rt.SMOKE_DATA_PATH.rstrip("/"),
        },
        "verbs": verbs,
    }


def _response(status: int, payload: dict[str, Any]) -> dict[str, Any]:
    return {"statusCode": status, "headers": {"Content-Type": "application/json"}, "body": json.dumps(payload)}


def _parse_event(event: Any) -> Any:
    if isinstance(event, dict) and event.get("body") is not None:
        body = event["body"]
        if isinstance(body, str):
            return json.loads(body) if body else {}
        if isinstance(body, dict):
            return body
    return event if isinstance(event, dict) else {}


def _guard_response_size(response: dict[str, Any], payload: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    size = len(json.dumps(response).encode("utf-8"))
    meta["response_bytes"] = size
    if size <= MAX_RESPONSE_BYTES:
        return response
    return _response(
        413,
        {
            "ok": False,
            "error_type": "response_too_large",
            "action": "named_read",
            "bytes": size,
            "cap": MAX_RESPONSE_BYTES,
            "hint": "use a smaller max_rows or max_bytes",
        },
    )


def _tool_output_flag(entry_verb: str, params: Any) -> tuple[Any, bool]:
    if entry_verb != SESSION_VERB or not isinstance(params, dict) or "include_tool_output" not in params:
        return params, False
    rest = dict(params)
    flag = rest.pop("include_tool_output")
    if not isinstance(flag, bool):
        raise ev.EventReadInputError(entry_verb, "include_tool_output", "must be a boolean")
    return rest, flag


def _named_read(payload: dict[str, Any], meta: dict[str, Any], remaining_ms: Callable[[], float] | None) -> dict[str, Any]:
    verb = payload.get("verb")
    if not isinstance(verb, str) or verb not in SERVED_VERBS:
        return _response(400, {"ok": False, "error_type": "unknown_verb", "served": sorted(SERVED_VERBS)})
    meta["verb"] = verb
    if any(key in payload for key in _REFUSED_KEYS):
        return _response(400, {"ok": False, "error_type": "bad_request", "error": "unsupported request field"})
    entry = CONTENT_READS[verb]
    try:
        params, include_tool_output = _tool_output_flag(verb, payload.get("params") or {})
        values = ev.bind_values(entry, params)
        meta["session_id"] = values["session_id"]
        ev.require_production_pin()
        if not in_read_window(values["session_id"], utc_now()):
            return _response(404, {"ok": False, "error_type": "outside_read_window", "verb": verb})
        meta_schema, data_path = ev.telemetry_pin()
        acquire = ev.telemetry_acquirer(
            meta_schema, data_path, EXTENSION_DIRECTORY, dsn_secret_id=_secret_id(), read_only=True
        )
        under_deadline = deadline.runner(remaining_ms, None, lambda: ev._INTERRUPTED_SLOTS.add(ev.TELEMETRY_SLOT))
        con, conn_meta = acquire()
        meta.update(connect_ms=conn_meta["connect_ms"], connect_reused=conn_meta["reused"])
        try:
            rows = under_deadline(con, lambda: ev.execute_entry(con, entry, params))
        except Exception as exc:  # noqa: BLE001 -- narrowed immediately to the dead-connection case
            if not rt.is_dead_connection_error(exc):
                raise
            con, conn_meta = acquire(force_reopen=True)
            meta.update(connect_ms=conn_meta["connect_ms"], connect_reused=conn_meta["reused"], reopened=True)
            rows = under_deadline(con, lambda: ev.execute_entry(con, entry, params))
        if verb == SESSION_VERB:
            started = rows[0].get("tree_started_at") if rows else None
            if started is not None and not tree_in_read_window(started, utc_now()):
                return _response(404, {"ok": False, "error_type": "outside_read_window", "verb": verb})
            served_rows, served = masked.serve_session(rows, values, include_tool_output, remaining_ms)
        else:
            served_rows, served = masked.serve_row(rows, values)
    except ev.EventReadInputError as exc:
        handler_errors.log_named_read_input(exc, function=FUNCTION, verb=exc.verb, request_id=meta.get("request_id"))
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
    except rt.VersionMismatchError:
        return _response(500, {"ok": False, "error_type": "version_mismatch"})
    except rt.DuckLakeRuntimeError:
        return _response(500, {"ok": False, "error_type": "runtime"})
    meta["served"] = served
    return _response(
        200,
        {
            "ok": True,
            "verb": verb,
            "registry_version": rt.NAMED_READS_VERSION,
            "read_version": content_read_version(entry),
            "rows": served_rows,
            "row_count": len(served_rows),
        },
    )


def _action_describe(_payload: dict[str, Any], _meta: dict[str, Any], _remaining_ms: Any) -> dict[str, Any]:
    return _response(200, _describe())


_ACTIONS: dict[str, Callable[[dict[str, Any], dict[str, Any], Callable[[], float] | None], dict[str, Any]]] = {
    "describe": _action_describe,
    "named_read": _named_read,
}


def _handle(payload: dict[str, Any], meta: dict[str, Any], remaining_ms: Callable[[], float] | None) -> dict[str, Any]:
    action = payload.get("action")
    fn = _ACTIONS.get(action) if isinstance(action, str) else None
    if fn is None:
        return _response(400, {"ok": False, "error": "unknown action", "actions": sorted(_ACTIONS)})
    return fn(payload, meta, remaining_ms)


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Entrypoint: dispatch, then one invocation line and, for a named_read, exactly one access-log line on every path."""
    started = time.perf_counter()
    payload: dict[str, Any] = {}
    meta: dict[str, Any] = {
        "connect_ms": None,
        "connect_reused": None,
        "reopened": False,
        "response_bytes": None,
        "request_id": handler_errors.request_id(context),
        "verb": "unknown",
        "session_id": None,
        "served": masked.Served(0, 0, {}),
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
        if action == "named_read":
            served = meta["served"] if status == 200 else masked.Served(0, 0, {})
            access_log.write(
                event,
                meta["verb"],
                status,
                served.rows,
                served.masked_bytes,
                served.mask_counts,
                meta["request_id"],
                meta["session_id"],
            )
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
