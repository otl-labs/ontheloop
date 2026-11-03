"""ducklake_writer Lambda entrypoint (T2.17 / CD.33, Decision 81).

Write-scoped DuckLake runtime Lambda invoked over an AWS_IAM-signed Function URL.

PRODUCTION OPS PATH (T2.19 / Decision 81): the writer is the SOLE write authority for the ops_*
governance tables (CD.33 clause 4). `write_ops` (INSERT history + MERGE current, schema-gated,
bounded OCC) and `update_ops` (in-transaction referential existence check before MERGE -- loud-fail
if the rec is absent, CD.33 clause 8 / D-5; also enforces the rec status DAG, Decision 103) are the
production actions; `describe` is the agent-facing per-verb parameter schema (CD.10 / CD.15). Every
ops write transits this URL; no out-of-band write path exists.

Split invariant (PLAN-sloc-ducklake-layer): the T2.17 smoke/probe/churn gates that prove the
CD.33 primitives in the live Lambda execution context now live in smoke_actions.py, imported and
re-exported here so `_ACTIONS` dispatch and `h.<action>`/`h.<helper>` test access keep resolving.
smoke_actions imports `ducklake_runtime` directly (never this module), so there is no import cycle.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable

from src.common import ducklake_handler_errors as handler_errors
from src.common import ducklake_runtime as rt
from src.common.ducklake_metrics import log_invocation
from src.lambdas.ducklake_writer.smoke_actions import (
    _AlwaysCollidingConnection,  # noqa: F401 -- re-exported for direct test access (h._AlwaysCollidingConnection)
    _churn_one_single_write,  # noqa: F401 -- re-exported for direct test access (h._churn_one_single_write)
    _churn_one_writer,  # noqa: F401 -- re-exported for direct test access (h._churn_one_writer)
    _concurrency_probe,  # noqa: F401 -- re-exported for direct test access (h._concurrency_probe)
    _count_files,  # noqa: F401 -- re-exported for direct test access (h._count_files)
    _count_inlined_rows,  # noqa: F401 -- re-exported for direct test access (h._count_inlined_rows)
    _frozen_creds,  # noqa: F401 -- re-exported for direct test access (h._frozen_creds)
    _p95,  # noqa: F401 -- re-exported for direct test access (h._p95)
    _partition_layout,  # noqa: F401 -- re-exported for direct test access (h._partition_layout)
    action_attach_check,
    action_churn,
    action_churn_single,
    action_connect_probe,
    action_create_tables,
    action_idempotency_probe,
    action_inlining_probe,
    action_loudfail_probe,
    action_partition_probe,
    action_reset_warm_connection,
    action_write,
)

DATA_PATH = os.environ.get("DUCKLAKE_DATA_PATH", rt.SMOKE_DATA_PATH)
META_SCHEMA = os.environ.get("DUCKLAKE_META_SCHEMA", rt.META_SCHEMA)
EXTENSION_DIRECTORY = os.environ.get("DUCKLAKE_EXTENSION_DIRECTORY", rt.LAMBDA_EXTENSION_DIRECTORY)


class WriterActionError(rt.DuckLakeRuntimeError):
    """Raised for an unknown/invalid writer action (distinct from a runtime loud-fail)."""


def _open_writer_connection() -> Any:
    """Open a write-scoped baked-extension connection to the Neon catalog (loud-fail on version)."""
    dsn = rt.fetch_dsn()
    return rt.open_connection(dsn=dsn, data_path=DATA_PATH, meta_schema=META_SCHEMA, extension_directory=EXTENSION_DIRECTORY)


def _warm_writer_connection(force_reopen: bool = False) -> tuple[Any, dict[str, Any]]:
    """Acquire the per-container warm write connection for the SINGLE-STATEMENT request path (D2).

    Reuses one ATTACHed connection across sequential warm invocations -- no per-request re-ATTACH and
    so no repeated ducklake_file_column_stats COPY / Neon metadata egress. The actual open routes
    through _open_writer_connection (the single open seam), invoked only when (re)opening; the
    warm-reuse path skips it (and its fetch_dsn) entirely. The 8-thread churn harness does NOT use
    this -- it opens an independent connection per thread (connectionless dispatch path).
    """
    return rt.get_warm_connection(opener=_open_writer_connection, force_reopen=force_reopen)


# ---------------------------------------------------------------------------
# Production ops actions -- each returns a JSON-serialisable dict (the handler wraps status + body)
# ---------------------------------------------------------------------------


def action_write_ops(event: dict[str, Any], con: Any) -> dict[str, Any]:
    """Production: write one SCD2 record to an ops_* table (schema-gated, OCC-retried, idempotent).

    The table is selected from the field_semantics ops_tables contract. The tables are created once by
    the backfill (force-recreate); a production write assumes they exist and MERGEs into them. A
    schema-gate rejection -> 422, OCC exhaustion -> 503 (handled by the dispatcher).
    """
    table = event.get("table")
    record = event.get("record") or {}
    _require_ops_table(table, direction="write")
    record["_contract_version"] = record.get("_contract_version", "1")
    result = rt.write_scd2(con, record, table=table, metric_sink=rt.make_metric_sink())
    return {
        "ok": True,
        "table": table,
        "ulid": result.ulid,
        "key": result.rec_id,
        "occ_retries": result.occ_retries,
        "commit_ms": round(result.commit_ms, 2),
    }


def action_file_ops(event: dict[str, Any], con: Any) -> dict[str, Any]:
    """Production: CREATE one ops_* record, allocating its entity id inside the write transaction.

    The record arrives WITHOUT the merge key (Decision 84 I-2: the writer owns the keyspace).
    `idempotency_ulid` (client-minted, replayed unchanged on retry) makes a response-lost retry
    return the originally allocated id instead of double-filing. The allocated id is returned
    as `key`.
    """
    table = event.get("table")
    record = event.get("record") or {}
    _require_ops_table(table, direction="write")
    record["_contract_version"] = record.get("_contract_version", "1")
    identity = None
    idem = event.get("idempotency_ulid")
    if idem is not None:
        import re as _re  # noqa: PLC0415

        if not isinstance(idem, str) or not _re.fullmatch(r"[0-9A-Za-z]{10,40}", idem):
            raise WriterActionError(f"invalid idempotency_ulid {idem!r}: expected a 10-40 char alphanumeric ULID")
        import dataclasses  # noqa: PLC0415

        identity = dataclasses.replace(rt.mint_write_identity(), ulid=idem)
    result = rt.file_scd2(con, record, table=table, identity=identity, metric_sink=rt.make_metric_sink())
    return {
        "ok": True,
        "table": table,
        "ulid": result.ulid,
        "key": result.rec_id,
        "occ_retries": result.occ_retries,
        "commit_ms": round(result.commit_ms, 2),
    }


def action_update_ops(event: dict[str, Any], con: Any) -> dict[str, Any]:
    """Production: update an existing ops_* record. Loud-fail (referential) if the merge key is absent.

    The portal sends the FULL merged record (existing <- updates). The writer enforces the CD.33
    clause-8 / D-5 referential invariant in-transaction (require_exists=True): an update of an absent
    merge key raises ReferentialError -> 409, never a silent create.
    """
    table = event.get("table")
    record = event.get("record") or {}
    _require_ops_table(table, direction="write")
    record["_contract_version"] = record.get("_contract_version", "1")
    result = rt.write_scd2(con, record, table=table, require_exists=True, metric_sink=rt.make_metric_sink())
    return {
        "ok": True,
        "table": table,
        "ulid": result.ulid,
        "key": result.rec_id,
        "occ_retries": result.occ_retries,
        "commit_ms": round(result.commit_ms, 2),
    }


def _check_force_recreate_confirm(table: Any, event: dict[str, Any], force: bool) -> None:
    """Destructive-action guard shared by both create_ops_tables branches (Decision 84)."""
    if force and event.get("confirm_force_recreate") != table:
        raise WriterActionError(
            f"force_recreate_tables on {table!r} DROPS the production table(s): pass "
            f"confirm_force_recreate={table!r} to proceed (destructive-action guard, Decision 84)"
        )


def action_create_ops_tables(event: dict[str, Any], con: Any) -> dict[str, Any]:
    """Production: create (optionally re-create) an ops_* table (pair) with partition transforms.

    Used by the backfill's resurrection-loop guard (force_recreate drops + recreates so a failed
    mid-sequence run never appends onto a half-populated catalog). A control-class table (T2.26)
    branches BEFORE _require_ops_table's generic refusal: this verb is its ONLY legitimate
    provisioning path (docs/contracts/ops_entity_counters.yaml write_boundary=writer_internal) --
    a single CREATE + SET PARTITIONED BY, no history/current pair, no counter to seed (it IS the
    counter).
    """
    table = event.get("table")
    force = bool(event.get("force_recreate_tables", False))
    if isinstance(table, str) and rt.is_control_table(table):
        _check_force_recreate_confirm(table, event, force)
        rt.create_control_table(con, table=table, force_recreate=force)
        control_spec = rt.resolve_control_spec(table)
        return {
            "ok": True,
            "table": table,
            "tables": [control_spec.table],
            "force_recreate": force,
            "counter_seed": None,
        }

    _require_ops_table(table, direction="write", provisioning=True)
    _check_force_recreate_confirm(table, event, force)
    rt.create_scd2_tables(con, table=table, force_recreate=force)
    spec = rt.resolve_table_spec(table)
    counter_seed = None
    if spec.entity_id_prefix and spec.id_keyspace == "writer":
        # Serial bootstrap/repair of the allocation counter (Decision 84 I-2): the hot path
        # never self-seeds, so provisioning owns the seed (and repairs duplicate-row state).
        counter_seed = rt.bootstrap_entity_counter(con, spec)
    return {
        "ok": True,
        "table": table,
        "tables": [spec.history_table, spec.current_table],
        "force_recreate": force,
        "counter_seed": counter_seed,
    }


def action_describe(event: dict[str, Any], _con: Any) -> dict[str, Any]:
    """Agent-facing describe verb (CD.10 / CD.15): description + params_schema for every VERB_REGISTRY entry.

    Connectionless (pure metadata, Decision 88 bounded egress) -- registered in
    _CONNECTIONLESS_ACTIONS so it runs before any connection open.
    """
    return {"ok": True, "verbs": rt.describe_write_verbs()}


_GENERIC_REFUSED_BOUNDARIES = frozenset({"telemetry_append", "registration"})
_DORMANT_BOUNDARY = "dormant"


def _require_ops_table(table: Any, *, direction: str, provisioning: bool = False) -> None:
    """Loud-fail if *table* is not a configured ops_* table, or is a control-class table not
    reachable through the generic write verbs (closed-boundary table allow-list).

    Control-class tables (write_boundary=writer_internal, T2.26) are refused HERE -- narrow and
    class-specific, never a general per-direction capability field (that is rec-3771, deliberately
    out of this plan) -- so write_ops/file_ops/update_ops loud-fail instead of falling through
    write_scd2 into the SCD2 branch against a spec with current_table=None. create_ops_tables is
    the one legitimate exception and branches BEFORE ever reaching this function. A dormant table (Decision 210
    cl.4) is refused by the generic verbs but still provisioned: create_ops_tables passes provisioning=True.
    """
    if rt.is_control_table(table):
        raise WriterActionError(
            f"{table!r} is a control-class table (write_boundary=writer_internal): not reachable "
            f"through the generic {direction} verbs -- provisioned only via create_ops_tables "
            "(docs/contracts/ops_entity_counters.yaml)"
        )
    boundary = rt.table_write_boundary(table) if isinstance(table, str) else None
    if boundary == _DORMANT_BOUNDARY and not provisioning:
        raise WriterActionError(
            f"{table!r} is dormant (write_boundary={boundary}): the generic {direction} verbs refuse it until its Class A "
            "contract lands and its row rules project into field_semantics.yaml (Decision 210 cl.4); create_ops_tables "
            "still provisions it"
        )
    if boundary in _GENERIC_REFUSED_BOUNDARIES:
        raise WriterActionError(
            f"{table!r} is not reachable through the generic {direction} verbs: written only by the "
            f"telemetry verbs (write_boundary={boundary}) -- Decision 55 loud fail"
        )
    if not isinstance(table, str) or table not in rt.ops_table_names():
        raise WriterActionError(f"unknown or missing ops table {table!r}: expected one of {list(rt.ops_table_names())}")


# ---------------------------------------------------------------------------
# Dispatch + Lambda entrypoint
# ---------------------------------------------------------------------------

_ACTIONS: dict[str, Callable[[dict[str, Any], Any], dict[str, Any]]] = {
    "attach_check": action_attach_check,
    "create_tables": action_create_tables,
    "write": action_write,
    "write_ops": action_write_ops,
    "file_ops": action_file_ops,
    "update_ops": action_update_ops,
    "create_ops_tables": action_create_ops_tables,
    "idempotency_probe": action_idempotency_probe,
    "partition_probe": action_partition_probe,
    "inlining_probe": action_inlining_probe,
    "inlining": action_inlining_probe,
    "loudfail_probe": action_loudfail_probe,
    "churn": action_churn,
    "churn_single": action_churn_single,
    "connect_probe": action_connect_probe,
    "reset_warm_connection": action_reset_warm_connection,
    "describe": action_describe,
}

# Actions that manage their own connections (churn opens many; attach measures connect time itself;
# connect_probe runs BEFORE the connection open to diagnose a hanging connect; reset_warm_connection
# drops the warm connection without opening a new one; describe is pure metadata, Decision 88).
_CONNECTIONLESS_ACTIONS = {"churn", "churn_single", "connect_probe", "reset_warm_connection", "describe"}


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
    """Build a Function-URL response envelope."""
    return {"statusCode": status, "headers": {"Content-Type": "application/json"}, "body": json.dumps(payload)}


def _handle(payload: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    """Writer dispatch. Dispatches `action`; loud-fail maps to a 4xx/5xx (no silent drop)."""
    action = payload.get("action")
    fn = _ACTIONS.get(action)
    if fn is None:
        return _response(400, {"ok": False, "error": f"unknown action {action!r}", "actions": sorted(_ACTIONS)})

    try:
        if action in _CONNECTIONLESS_ACTIONS:
            return _response(200, fn(payload, None))
        # Warm-reuse acquisition (D2) for the single-statement path: the connection is kept open for
        # the next invocation (NOT closed in a finally). The SELECT-1 liveness probe reopens a closed
        # connection at acquisition; a session that dies mid-statement (Neon scale-to-zero) surfaces as
        # a connection error -- reopen ONCE and retry. The production write actions are replay-safe
        # under retry (file_ops via the client-replayed idempotency ULID; update_ops/write_ops mint
        # a fresh write ULID per call, so a replay adds a history row while the current row converges, rec-4121),
        # and a connection death aborts the catalog txn so the
        # first attempt left nothing committed. Any non-connection error reaches handler()'s terminal answer (Decision 55).
        con, conn_meta = _warm_writer_connection()
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
            con, conn_meta = _warm_writer_connection(force_reopen=True)
            payload["_connect_ms"] = conn_meta["connect_ms"]
            payload["_connect_reused"] = conn_meta["reused"]
            meta.update(connect_ms=conn_meta["connect_ms"], connect_reused=conn_meta["reused"], reopened=True)
            return _response(200, fn(payload, con))
    except rt.RowRuleViolationError as exc:
        return _response(
            422,
            {
                "ok": False,
                "error_type": "row_rule",
                "table": exc.table,
                "rule": exc.rule,
                "column": exc.column,
                "exclude_before": exc.exclude_before,
                "error": str(exc),
            },
        )
    except rt.SchemaGateError as exc:
        return _response(422, {"ok": False, "error_type": "schema_gate", "error": str(exc)})
    except rt.StatusTransitionError as exc:
        return _response(422, {"ok": False, "error_type": "status_transition", "error": str(exc)})
    except rt.ReferentialError as exc:
        return _response(409, {"ok": False, "error_type": "referential", "error": str(exc)})
    except WriterActionError as exc:
        return _response(400, {"ok": False, "error_type": "action", "error": str(exc)})
    except rt.OCCRetryExhaustedError as exc:
        return _response(503, {"ok": False, "error_type": "occ_exhausted", "error": str(exc)})
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
    meta: dict[str, Any] = {"connect_ms": None, "connect_reused": None, "reopened": False}
    status = 500
    try:
        try:
            parsed = _parse_event(event)
        except (ValueError, RecursionError):
            parsed = None
        if not isinstance(parsed, dict):
            status, body = handler_errors.bad_request("writer", "unknown")
            return _response(status, body)
        payload = parsed
        response = _handle(payload, meta)
        status = response["statusCode"]
        return response
    except Exception as exc:
        action = payload.get("action")
        status, body = handler_errors.terminal(
            exc,
            function="writer",
            action=action if isinstance(action, str) and action in _ACTIONS else "unknown",
            request_id=handler_errors.request_id(context),
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            reopened=bool(meta["reopened"]),
        )
        return _response(status, body)
    finally:
        action = payload.get("action")
        log_invocation(
            "writer",
            action if isinstance(action, str) and action in _ACTIONS else "unknown",
            status,
            meta["connect_ms"],
            meta["connect_reused"],
            meta["reopened"],
            (time.perf_counter() - started) * 1000.0,
        )
