"""Lambda-only transport policy and the append_telemetry and begin_blobs verb bodies (T2.36, slice 2a-2, plans 3 and 4).

The caps live here because they are this function's capacity, not the grammar's (the grammar is src/telemetry/request.py,
shared by every plane): the body cap is measured on TelemetryRequest.invoke_bytes, the body re-escaped as a JSON string
(len(json.dumps(raw_body))), because Lambda's 6 MiB synchronous limit applies to the invocation event, in which the body is
so escaped, plus about 2.2 KB of envelope (measured 2026-10-09, evidence-invoke-payload-limit.md). A producer chunks on the
same measure, len(json.dumps(S)) with S the exact body string sent. Rows are capped per table and per request.

Event specs and table shapes are built lazily on the first call and cached per container from the bundled
field_semantics.yaml (one rule source, Decision 210 cl.3). No response carries a row value or principal.ref.
"""

from __future__ import annotations

from typing import Any

from src.common import ducklake_telemetry_blobs as blobs
from src.common import ducklake_telemetry_provision as prov
from src.common import ducklake_telemetry_registration as reg
from src.common.ducklake_telemetry_append import append_request
from src.common.ducklake_telemetry_txn import TelemetryRequestError
from src.row_rules.rules import ContentRule
from src.telemetry.append import EventTableSpec
from src.telemetry.blobs import BLOB_PROTOCOL_VERSION, MAX_BLOB_ITEMS, parse_begin_blobs_request
from src.telemetry.request import RequestError, TableShape, parse_append_request

MAX_INVOKE_BODY_BYTES = 5 * 1024 * 1024
MAX_ROWS_PER_TABLE = 2000
MAX_ROWS_PER_REQUEST = 5000
_STATUS = {
    "bad_request": 400,
    "write_order": 400,
    "mixed_producer": 400,
    "payload_too_large": 413,
    "too_many_blobs": 413,
    "schema_gate": 422,
}

_BUILT: dict[str, Any] = {}


def reset_cache() -> None:
    _BUILT.clear()


def _specs_and_shapes(semantics: dict[str, Any]) -> tuple[dict[str, EventTableSpec], dict[str, TableShape]]:
    if not _BUILT:
        specs = {
            name: EventTableSpec.from_projection(name, semantics["ops_tables"][name])
            for name in prov.event_table_names(semantics)
        }
        _BUILT.update(specs=specs, shapes={name: TableShape.from_spec(spec) for name, spec in specs.items()})
    return _BUILT["specs"], _BUILT["shapes"]


def exclude_before(table: str, column: str, rule: str) -> str | None:
    """The dated rule's cut-over date for a row-rule refusal, from the cached spec; None when not dated or not built."""
    spec = _BUILT.get("specs", {}).get(table)
    dated = spec.rules.exclude_before.get(column, {}).get(rule) if spec is not None else None
    return dated.isoformat() if dated is not None else None


def payload_rule(specs: dict[str, EventTableSpec]) -> ContentRule:
    """The transcripts payload rule (threshold and cap) from the bundled projection: the only source of both."""
    rule = specs[blobs.TRANSCRIPTS].rules.content
    if rule is None or rule.cap is None:
        raise TelemetryRequestError(500, "projection_defect", {}, "the bundled projection declares no payload rule")
    return rule


def describe_entry() -> dict[str, Any]:
    return {
        "description": "Append telemetry event rows for one project in one transaction; an identical re-send is a no-op.",
        "params_schema": {
            "project_ref": "non-empty string",
            "component": "optional ^[a-z][a-z0-9_-]{0,63}$, default unknown",
            "batches": "non-empty list of {table, rows}; the telemetry_sessions batch last when present",
        },
        "caps": {
            "max_invoke_body_bytes": MAX_INVOKE_BODY_BYTES,
            "max_rows_per_table": MAX_ROWS_PER_TABLE,
            "max_rows_per_request": MAX_ROWS_PER_REQUEST,
        },
    }


def describe_begin_blobs_entry() -> dict[str, Any]:
    return {
        "description": "Issue a content-addressed, checksum- and length-bound presigned PUT per payload above the threshold.",
        "params_schema": {
            "project_ref": "non-empty string",
            "component": "optional ^[a-z][a-z0-9_-]{0,63}$, default unknown",
            "items": "non-empty list of {session_ref, session_started_at, content_sha256, content_bytes}",
        },
        "caps": {"max_items": MAX_BLOB_ITEMS},
    }


def describe_verbs() -> dict[str, Any]:
    return {"append_telemetry": describe_entry(), "begin_blobs": describe_begin_blobs_entry()}


def _check_caps(body: dict[str, Any], invoke_bytes: int, known_tables: set[str]) -> None:
    if invoke_bytes > MAX_INVOKE_BODY_BYTES:
        raise TelemetryRequestError(
            413, "payload_too_large", {"measure": "invoke_bytes", "max": MAX_INVOKE_BODY_BYTES}, "request body too large"
        )
    batches = body.get("batches")
    if not isinstance(batches, list):
        return
    total = 0
    for batch in batches:
        rows = batch.get("rows") if isinstance(batch, dict) else None
        if not isinstance(rows, list):
            continue
        total += len(rows)
        if len(rows) > MAX_ROWS_PER_TABLE:
            table = batch.get("table")
            fields: dict[str, Any] = {"measure": "rows_per_table", "max": MAX_ROWS_PER_TABLE}
            if isinstance(table, str) and table in known_tables:
                fields["table"] = table
            raise TelemetryRequestError(413, "too_many_rows", fields, "too many rows in one table")
    if total > MAX_ROWS_PER_REQUEST:
        raise TelemetryRequestError(
            413, "too_many_rows", {"measure": "rows_per_request", "max": MAX_ROWS_PER_REQUEST}, "too many rows in the request"
        )


def _translate(exc: RequestError) -> TelemetryRequestError:
    located = {
        "reason": exc.reason,
        "table": exc.table,
        "row": exc.row,
        "column": exc.column,
        "rule": exc.rule,
        "threshold": exc.threshold,
        "cap": exc.cap,
    }
    return TelemetryRequestError(
        _STATUS[exc.error_type], exc.error_type, {k: v for k, v in located.items() if v is not None}, str(exc)
    )


def _tenant_id(con: Any, request: Any, semantics: dict[str, Any]) -> str:
    principal = request.principal
    tenant_id = reg.cached_tenant_id(principal.kind, principal.ref)
    if tenant_id is None:
        tenant_id = reg.lookup_tenant(con, principal.kind, principal.ref, semantics=semantics)
        if tenant_id is None:
            raise TelemetryRequestError(403, "tenant_unresolved", {"remedy": "register_tenant"})
        reg._TENANT_CACHE[(principal.kind, principal.ref)] = tenant_id
    return tenant_id


def action_append_telemetry(request: Any, con: Any, deps: Any) -> dict[str, Any]:
    specs, shapes = _specs_and_shapes(deps.semantics)
    _check_caps(request.body, request.invoke_bytes, set(specs))
    try:
        parsed = parse_append_request(request.body, shapes)
    except RequestError as exc:
        raise _translate(exc) from exc
    tenant_id = _tenant_id(con, request, deps.semantics)
    stats = deps.stats
    if stats is not None:
        stats.tenant_id = tenant_id
    outcome = append_request(
        con,
        parsed,
        tenant_id=tenant_id,
        specs=specs,
        semantics=deps.semantics,
        metric_sink=deps.metric_sink,
        sleep=deps.sleep,
        store=deps.blob_store,
    )
    if stats is not None:
        stats.project_id = outcome.project_id
        stats.tables = {t: (r.submitted, r.inserted) for t, r in outcome.tables.items()}
        stats.masked_commit_retries = outcome.masked_commit_retries
        stats.blob_rows = outcome.blob_rows
        stats.blob_head_ms = outcome.blob_head_ms
    return {
        "ok": True,
        "tenant_id": outcome.tenant_id,
        "project_id": outcome.project_id,
        "project_registration": outcome.project_registration,
        "tables": {
            t: {"submitted": r.submitted, "collapsed": r.collapsed, "inserted": r.inserted} for t, r in outcome.tables.items()
        },
        "occ_retries": outcome.occ_retries,
        "commit_ms": int(round(outcome.commit_ms)),
        "blobs": {"rows": outcome.blob_rows, "verified": outcome.blobs_verified},
    }


def _upload(grant: Any) -> dict[str, Any]:
    return {"method": "PUT", "url": grant.url, "headers": grant.headers, "expires_at": grant.expires_at}


def action_begin_blobs(request: Any, con: Any, deps: Any) -> dict[str, Any]:
    specs, _ = _specs_and_shapes(deps.semantics)
    rule = payload_rule(specs)
    try:
        parsed = parse_begin_blobs_request(request.body, threshold=rule.threshold, cap=rule.cap or 0)
    except RequestError as exc:
        raise _translate(exc) from exc
    tenant_id = _tenant_id(con, request, deps.semantics)
    stats = deps.stats
    if stats is not None:
        stats.tenant_id = tenant_id
    outcome = blobs.begin_blobs(
        con,
        parsed,
        tenant_id=tenant_id,
        store=deps.blob_store,
        semantics=deps.semantics,
        metric_sink=deps.metric_sink,
        sleep=deps.sleep,
    )
    if stats is not None:
        stats.project_id = outcome.project_id
        stats.blob_counts = dict(outcome.counts)
    return {
        "ok": True,
        "tenant_id": tenant_id,
        "project_id": outcome.project_id,
        "project_registration": outcome.project_registration,
        "protocol_version": BLOB_PROTOCOL_VERSION,
        "items": [
            {
                "existed": item.existed,
                "upload": _upload(item.grant) if item.grant is not None else None,
                "duplicate_of": item.duplicate_of,
            }
            for item in outcome.items
        ],
        "counts": outcome.counts,
    }
