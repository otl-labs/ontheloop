"""control_health and catalog_stats for the ducklake_maintenance Lambda (Decision 128 decomposition).

Moved out of handler.py (at its SLOC budget) with their behaviour unchanged. The emitter and DATA_PATH come
from _shared.py; this module never imports handler.py (handler imports it for dispatch).
"""

from __future__ import annotations

from typing import Any

from src.common import ducklake_catalog_index_status as index_status
from src.common import ducklake_control_health
from src.common import ducklake_maintenance as maint
from src.common import ducklake_runtime as rt
from src.lambdas.ducklake_maintenance import _shared
from src.lambdas.ducklake_maintenance._shared import EXTENSION_DIRECTORY, _require_identifier


def action_catalog_stats(event: dict[str, Any], _con: Any) -> dict[str, Any]:
    """OPERATIONAL read-only: catalog-metadata footprint of the production ops_* catalog (D3a).

    Connectionless and ATTACH-free: it reads the catalog's own Postgres metadata tables directly
    (psycopg2), so it needs only an explicit meta_schema -- NO data_path (unlike merge_ops). This is
    the supported measurement path for the neon-egress budget (the DR bucket + direct CloudWatch reads
    are IAM-blocked from the dev role by design). Read-only: no merge/expire/cleanup/orphan.

    The two metrics are the undimensioned ops series, so they are emitted only for the ops catalog; any other schema
    (a measurement read of ducklake_smoke or ducklake_telemetry) returns the same body and emits nothing.

    Expected event: {"action": "catalog_stats", "meta_schema": "ducklake_ops"}
    """
    raw_schema = event.get("meta_schema")
    if not raw_schema:
        raise rt.DuckLakeRuntimeError(
            "catalog_stats requires an explicit 'meta_schema' (e.g. 'ducklake_ops') -- no default production schema"
        )
    meta_schema = _require_identifier(raw_schema)
    ops_filter = event.get("ops_table_filter", "ops_%")
    result = maint.catalog_stats(meta_schema=meta_schema, dsn=rt.fetch_dsn(), ops_table_filter=ops_filter)

    if meta_schema != rt.META_SCHEMA:
        return result
    _shared._emit_maintenance_metric("CatalogMetadataBytes", float(result.get("catalog_metadata_bytes") or 0))
    if result.get("file_column_stats_rows_est") is not None:
        _shared._emit_maintenance_metric("CatalogFileColumnStatsRows", float(result["file_column_stats_rows_est"]))
    return result


def action_control_health(event: dict[str, Any], _con: Any) -> dict[str, Any]:
    """OPERATIONAL: assert control-class table invariants (row count, counter floor, live-file
    ceiling) -- the periodic health assertion docs/contracts/ops_entity_counters.yaml's dq_scope
    exemption names as its substitute for DQ-runner coverage (T2.26).

    Unlike the other operational actions above, `data_path` falls back to the env-pinned
    DATA_PATH (see its module-level comment) since this action only reads and never mutates.

    Expected event: {"action": "control_health", "meta_schema": "ducklake_ops"} (data_path
    optional when DUCKLAKE_DATA_PATH is set on the function).
    """
    data_path = event.get("data_path") or _shared.DATA_PATH
    if not isinstance(data_path, str) or not data_path.startswith("s3://"):
        raise rt.DuckLakeRuntimeError(
            "control_health requires a 'data_path' s3:// URI (the production DuckLake path) -- "
            "pass it explicitly, or set DUCKLAKE_DATA_PATH on the function"
        )
    raw_schema = event.get("meta_schema")
    if not raw_schema:
        raise rt.DuckLakeRuntimeError("control_health requires an explicit 'meta_schema' (e.g. 'ducklake_ops')")
    meta_schema = _require_identifier(raw_schema)

    dsn = rt.fetch_dsn()
    con = rt.open_connection(dsn=dsn, data_path=data_path, meta_schema=meta_schema, extension_directory=EXTENSION_DIRECTORY)
    try:
        result = ducklake_control_health.control_health(
            con, metric_sink=lambda name, value: _shared._emit_maintenance_metric(name, value)
        )
    finally:
        con.close()
        stats_index = _report_stats_index(dsn, meta_schema)
    result["stats_index"] = stats_index
    return result


def _report_stats_index(dsn: dict[str, str], meta_schema: str) -> dict[str, Any]:
    """Report (never raise) the stats index state per schema as metrics; the alarm on it is rec-4220's."""
    try:
        import psycopg2  # noqa: PLC0415

        conn = psycopg2.connect(rt.libpq_conninfo(dsn))
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                report = {s: index_status.catalog_index_status(cur, s) for s in index_status.catalog_index_schemas(cur)}
                _add_growth(cur, report)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 -- the message can name the host: class and SQLSTATE only
        _shared._emit_maintenance_metric("StatsIndexValid", 0.0, dimensions={"MetaSchema": meta_schema})
        return {"error_class": type(exc).__name__, "sqlstate": getattr(exc, "pgcode", None)}
    for schema, state in report.items():
        dims = {"MetaSchema": schema}
        _shared._emit_maintenance_metric("StatsIndexValid", 1.0 if state["valid"] else 0.0, dimensions=dims)
        _shared._emit_maintenance_metric("FileColumnStatsIdxScan", float(state["idx_scan"] or 0), dimensions=dims)
        _shared._emit_maintenance_metric("FileColumnStatsSeqScan", float(state["table_seq_scan"] or 0), dimensions=dims)
        if state.get("stats_rows_est") is not None:
            _shared._emit_maintenance_metric("CatalogFileColumnStatsRows", float(state["stats_rows_est"]), dimensions=dims)
    return report


def _add_growth(cur: Any, report: dict[str, Any]) -> None:
    """Stats-row growth estimate for the production telemetry catalog only, inside its own report entry. A failure
    here records class and SQLSTATE and never reaches the caller's except, which would emit StatsIndexValid=0."""
    entry = report.get(rt.TELEMETRY_PROD_META_SCHEMA)
    if entry is None:
        return
    try:
        entry["stats_rows_est"] = index_status.stats_rows_est(cur, rt.TELEMETRY_PROD_META_SCHEMA)
    except Exception as exc:  # noqa: BLE001 -- class and SQLSTATE only: the message can name the host
        entry["growth_error"] = {"error_class": type(exc).__name__, "sqlstate": getattr(exc, "pgcode", None)}
