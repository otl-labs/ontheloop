"""ducklake_telemetry_writer Lambda entrypoint (T2.36, slice 2a-2, option D part 1).

The function exists so telemetry identities never reach ops verbs (Decision 143 cl.3, Decision 213): it has
its own role and connects to the catalog as its own scoped login. It is env-pinned to the telemetry catalog
and never reads data_path or meta_schema from the event. It serves `describe`, `attach_check` (gate
instrumentation) and the telemetry verbs of telemetry_actions (docs/contracts/ducklake_telemetry_writer.yaml).

Caller identity comes from the Function URL authorizer context; neither the account id nor the user ARN, nor
any hash of them, is logged or returned (Decision 101). There is no direct-invoke fallback.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import time
from collections.abc import Callable
from typing import Any

from src.common import ducklake_handler_errors as handler_errors
from src.common import ducklake_runtime as rt
from src.common.ducklake_metrics import make_metric_sink
from src.common.ducklake_scd2_schema import SchemaGateError, load_field_semantics
from src.common.ducklake_telemetry_blobstore import (
    BlobGrantSelfCheckError,
    BlobIntegrityError,
    BlobRoot,
    BlobStoreAccessError,
    BlobStoreUnavailableError,
    LazyBlobStore,
    S3BlobStore,
    make_s3_client,
)
from src.common.ducklake_telemetry_handshake import capabilities
from src.common.ducklake_telemetry_registration import CallerPrincipal, clear_telemetry_caches
from src.common.ducklake_telemetry_txn import TelemetryOCCRetryExhaustedError, TelemetryRequestError
from src.common.ducklake_write_rules import RowRuleViolationError
from src.common.ducklake_writes import OCCRetryExhaustedError
from src.lambdas.ducklake_telemetry_writer import telemetry_actions as actions
from src.lambdas.ducklake_telemetry_writer import telemetry_edge as edge
from src.lambdas.ducklake_telemetry_writer import telemetry_log
from src.telemetry.gate import GateError, GrainConflictError, RowRuleGateError

METRIC_NAMESPACE = "DuckLakeTelemetryWriter"


class _ClientError(Exception):
    def __init__(self, status: int, error_type: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status = status
        self.error_type = error_type
        self.extra = extra


class _EnvUnsetError(_ClientError):
    def __init__(self, name: str) -> None:
        super().__init__(500, "telemetry_env_unset", f"required environment variable {name} is not set")


_CATALOG_LOGIN: dict[str, str] = {}


def _env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise _EnvUnsetError(name)
    return value


def _is_smoke(data_path: str) -> bool:
    return data_path.rstrip("/") == rt.SMOKE_DATA_PATH.rstrip("/")


def _require_caller(event: dict[str, Any]) -> None:
    ctx = event.get("requestContext") if isinstance(event, dict) else None
    auth = ctx.get("authorizer") if isinstance(ctx, dict) else None
    iam = auth.get("iam") if isinstance(auth, dict) else None
    if not isinstance(iam, dict) or not iam.get("accountId") or not iam.get("userArn"):
        raise _ClientError(403, "missing_caller_identity", "caller identity is required")


def _principal(event: dict[str, Any]) -> CallerPrincipal:
    """The signed caller as the registration port sees it; no AWS event shape crosses into src/common (Decision 184 cl.2)."""
    iam = event["requestContext"]["authorizer"]["iam"]
    segments = str(iam["userArn"]).split(":", 5)[-1].split("/")
    return CallerPrincipal(kind="aws_account", ref=str(iam["accountId"]), role_name=segments[1] if len(segments) > 1 else "")


def _parse_body(event: dict[str, Any]) -> tuple[dict[str, Any], int, int]:
    """The JSON object body, its raw (decoded) byte length and its invoke measure.

    The invoke measure is len(json.dumps(body field as delivered)), taken before any base64 decoding: the body re-escaped
    as a JSON string, ASCII-escaped, which is what Lambda's 6 MiB event limit applies to (an upper bound for a body that
    carries non-ASCII).
    """
    body: Any = event.get("body")
    if body is None or body == "":
        return {}, 0, 0
    invoke_bytes = len(json.dumps(body)) if isinstance(body, str) else 0
    nbytes = len(body.encode("utf-8", errors="replace")) if isinstance(body, str) else 0
    try:
        if isinstance(body, str):
            if event.get("isBase64Encoded"):
                raw = base64.b64decode(body, validate=True)
                nbytes = len(raw)
                body = raw.decode("utf-8")
            body = json.loads(body)
    except (ValueError, RecursionError, binascii.Error) as exc:
        raise _ClientError(400, "bad_request", "request body is not valid JSON") from exc
    if not isinstance(body, dict):
        raise _ClientError(400, "bad_request", "request body must be a JSON object")
    return body, nbytes, invoke_bytes


def action_describe(_payload: dict[str, Any]) -> dict[str, Any]:
    meta_schema = _env("TELEMETRY_META_SCHEMA")
    data_path = _env("TELEMETRY_DATA_PATH")
    return {
        "ok": True,
        "function": "ducklake_telemetry_writer",
        "actions": _all_actions(),
        "verbs": edge.describe_verbs(),
        "catalog": {"meta_schema": meta_schema, "data_path_is_smoke": _is_smoke(data_path)},
        **_capabilities(),
    }


def _capabilities() -> dict[str, Any]:
    """blob_protocol and the per-table schema view, from the bundled projection (connectionless)."""
    specs, _ = edge._specs_and_shapes(load_field_semantics())
    return capabilities(specs, edge.payload_rule(specs))


def _blob_store() -> S3BlobStore:
    root = BlobRoot.from_env(_env("TELEMETRY_BLOB_ROOT"), _env("TELEMETRY_DATA_PATH"), _env("TELEMETRY_META_SCHEMA"))
    return S3BlobStore(make_s3_client(_env("AWS_REGION")), root)


_BLOB_STORE = LazyBlobStore(_blob_store)


def _warm_connection() -> tuple[Any, dict[str, Any]]:
    """The pinned warm connection on the scoped login; captures the login and backend class from the secret's host only."""
    meta_schema = _env("TELEMETRY_META_SCHEMA")
    data_path = _env("TELEMETRY_DATA_PATH")
    parameter_name = _env("TELEMETRY_DSN_PARAMETER")
    extension_directory = os.environ.get("DUCKLAKE_EXTENSION_DIRECTORY") or rt.LAMBDA_EXTENSION_DIRECTORY

    def opener() -> Any:
        dsn = rt.fetch_dsn(parameter_name=parameter_name)
        _CATALOG_LOGIN["login"] = str(dsn.get("username", ""))
        _CATALOG_LOGIN["backend"] = actions.classify_backend(str(dsn.get("host", "")))
        return rt.open_connection(
            dsn=dsn, data_path=data_path, meta_schema=meta_schema, extension_directory=extension_directory
        )

    con, meta = rt.get_warm_connection(
        opener=opener, data_path=data_path, meta_schema=meta_schema, extension_directory=extension_directory
    )
    if meta.get("reopened"):
        clear_telemetry_caches()
    return con, meta


def action_attach_check(_payload: dict[str, Any]) -> dict[str, Any]:
    con, meta = _warm_connection()
    con.execute("SELECT 1").fetchall()
    return {
        "ok": True,
        "meta_schema": _env("TELEMETRY_META_SCHEMA"),
        "data_path_is_smoke": _is_smoke(_env("TELEMETRY_DATA_PATH")),
        "catalog_login": _CATALOG_LOGIN.get("login", ""),
        "connect_ms": meta["connect_ms"],
        "reused": meta["reused"],
    }


def run_verb(request: actions.TelemetryRequest, stats: telemetry_log.RequestStats | None = None) -> dict[str, Any]:
    """Validate (before any connection), open the pinned connection, run the verb.

    append_telemetry and begin_blobs validate in their own edge (the request grammar) and record OccRetryCount and
    CommitLatencyMs for the C6 line's EMF block instead of calling PutMetricData; the other verbs keep the function's
    metric sink.
    """
    own_edge = request.action in _EDGE_VERBS
    if not own_edge:
        actions.validate_request(request)
    con, meta = _warm_connection()
    stats = stats if stats is not None else telemetry_log.RequestStats()
    stats.connect_reused = bool(meta["reused"])
    deps = actions.Deps(
        semantics=load_field_semantics(),
        metric_sink=stats.sink if own_edge else make_metric_sink(namespace=METRIC_NAMESPACE),
        meta_schema=_env("TELEMETRY_META_SCHEMA"),
        data_path_is_smoke=_is_smoke(_env("TELEMETRY_DATA_PATH")),
        catalog_login=_CATALOG_LOGIN.get("login", ""),
        catalog_backend=_CATALOG_LOGIN.get("backend", "other"),
        connect_reused=bool(meta["reused"]),
        stats=stats,
        blob_store=_BLOB_STORE,
    )
    return _verbs()[request.action](request, con, deps)


_EDGE_VERBS = frozenset({"append_telemetry", "begin_blobs"})
_BLOB_FAULTS: tuple[tuple[type[Exception], str], ...] = (
    (BlobIntegrityError, "blob_integrity"),
    (BlobGrantSelfCheckError, "blob_grant_self_check"),
    (BlobStoreAccessError, "blob_store_access"),
)

_ACTIONS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "describe": action_describe,
    "attach_check": action_attach_check,
}


def _verbs() -> dict[str, Callable[[actions.TelemetryRequest, Any, actions.Deps], dict[str, Any]]]:
    return {**actions.VERBS, "append_telemetry": edge.action_append_telemetry, "begin_blobs": edge.action_begin_blobs}


def _all_actions() -> list[str]:
    return sorted({*_ACTIONS, *_verbs()})


def _kernel_fields(exc: Exception) -> dict[str, Any]:
    """Response fields of a kernel refusal, built from its attributes only, never str(exc) (a value-free body)."""
    if isinstance(exc, RowRuleGateError):
        before = edge.exclude_before(exc.table or "", exc.column or "", exc.rule)
        fields = {"table": exc.table, "row": exc.row, "rule": exc.rule, "column": exc.column, "exclude_before": before}
    elif isinstance(exc, GateError):
        fields = {"table": exc.table, "row": exc.row, "column": exc.column, "reason": exc.reason}
    else:
        assert isinstance(exc, GrainConflictError)
        fields = {"table": exc.table, "event_id": exc.event_id, "row": exc.row}
    return {k: v for k, v in fields.items() if v is not None}


_KERNEL_ERRORS: tuple[tuple[type[Exception], int, str], ...] = (
    (RowRuleGateError, 422, "row_rule"),
    (GateError, 422, "schema_gate"),
    (GrainConflictError, 409, "grain_conflict"),
)

_ERROR_MAP: tuple[tuple[type[Exception], int, str], ...] = (
    (RowRuleViolationError, 422, "row_rule"),
    (SchemaGateError, 422, "schema_gate"),
    (OCCRetryExhaustedError, 503, "occ_exhausted"),
    (BlobStoreUnavailableError, 503, "blob_store_unavailable"),
    (rt.DuckLakeRuntimeError, 500, "runtime"),
)


def _response(status: int, payload: dict[str, Any]) -> dict[str, Any]:
    return {"statusCode": status, "headers": {"Content-Type": "application/json"}, "body": json.dumps(payload)}


def _registered_action(action: Any) -> str:
    """A registered action name or the fixed token "unknown", never the caller's string (the log_invocation rule)."""
    return action if isinstance(action, str) and (action in _ACTIONS or action in _verbs()) else "unknown"


class _Answer:
    """What one request learned on its way to a response; read by the C6 line."""

    def __init__(self) -> None:
        self.action = "unknown"
        self.payload: Any = None
        self.principal: actions.reg.CallerPrincipal | None = None
        self.request: actions.TelemetryRequest | None = None
        self.stats = telemetry_log.RequestStats()


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Authenticate the caller, parse the Function URL body, dispatch `action`; emit one C6 line per answered request.

    No exception escapes as the runtime's untyped 502: an unmapped one answers 500 `unmapped` and a dead catalog
    connection 503 `catalog_unavailable`, each carrying the exception class and request id, never the message.
    """
    answer = _Answer()
    status, body = _answer(event, context, answer)
    request = answer.request
    telemetry_log.emit_request_line(
        answer.action,
        status,
        body.get("error_type"),
        payload=answer.payload,
        principal=answer.principal,
        request_bytes=request.request_bytes if request is not None else None,
        invoke_bytes=request.invoke_bytes if request is not None else None,
        stats=answer.stats,
    )
    return _response(status, body)


def _answer(event: dict[str, Any], context: Any, answer: _Answer) -> tuple[int, dict[str, Any]]:
    t0 = time.perf_counter()
    try:
        _require_caller(event)
        answer.principal = _principal(event)
        payload, nbytes, invoke_bytes = _parse_body(event)
        answer.payload = payload
        action = payload.get("action")
        answer.action = _registered_action(action)
        if isinstance(action, str) and action in _verbs():
            answer.request = actions.TelemetryRequest(action, payload, answer.principal, nbytes, invoke_bytes)
            return 200, run_verb(answer.request, answer.stats)
        fn = _ACTIONS.get(action) if isinstance(action, str) else None
        if fn is None:
            raise _ClientError(400, "unknown_action", "unknown or missing action", actions=_all_actions())
        return 200, fn(payload)
    except _ClientError as exc:
        return exc.status, {"ok": False, "error_type": exc.error_type, "error": str(exc), **exc.extra}
    except TelemetryRequestError as exc:
        return exc.status, {"ok": False, "error_type": exc.error_type, "error": str(exc), **exc.fields}
    except Exception as exc:
        for cls, status, error_type in _KERNEL_ERRORS:
            if isinstance(exc, cls):
                return status, {"ok": False, "error_type": error_type, **_kernel_fields(exc)}
        for cls, error_type in _BLOB_FAULTS:
            if isinstance(exc, cls):
                return 500, {"ok": False, "error_type": error_type, "error": error_type}
        for cls, status, error_type in _ERROR_MAP:
            if isinstance(exc, cls):
                if isinstance(exc, TelemetryOCCRetryExhaustedError):
                    answer.stats.masked_commit_retries = exc.masked_commit_retries
                return status, {
                    "ok": False,
                    "error_type": error_type,
                    "error": str(exc),
                    "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
                }
        return handler_errors.terminal(
            exc,
            function="telemetry-writer",
            action=answer.action,
            request_id=handler_errors.request_id(context),
            elapsed_ms=(time.perf_counter() - t0) * 1000,
        )
