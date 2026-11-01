"""MIRROR for src/lambdas/ducklake_maintenance/health_actions.py -- control_health and catalog_stats.

The branch cases moved here from handler/test_control_health_action.py and handler/test_operational_actions.py
when the actions moved out of handler.py (Decision 128); they are re-pointed at health_actions, behaviour unchanged.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.common.ducklake_runtime import DuckLakeRuntimeError
from src.lambdas.ducklake_maintenance import _shared
from src.lambdas.ducklake_maintenance import health_actions as ha
from tests.fixtures.ducklake_maintenance_handler import _FULL_DSN

pytestmark = pytest.mark.unit


def test_control_health_action_dispatches(monkeypatch):
    """control_health emits its metric to the DuckLakeMaintenance namespace."""
    con = MagicMock()
    monkeypatch.setattr(ha.rt, "fetch_dsn", lambda: _FULL_DSN)
    monkeypatch.setattr(ha.rt, "open_connection", lambda **kw: con)
    emitted: list[tuple[str, float]] = []
    monkeypatch.setattr(_shared, "_emit_maintenance_metric", lambda name, value: emitted.append((name, value)))
    monkeypatch.setattr(ha, "_report_stats_index", lambda dsn, schema: {})
    monkeypatch.setattr(
        ha.ducklake_control_health, "control_health", lambda con, metric_sink=None: (metric_sink("X", 0.0), {"ok": True})[1]
    )

    out = ha.action_control_health({"data_path": "s3://bucket/ducklake/", "meta_schema": "ducklake_ops"}, None)
    assert out["ok"] is True
    assert ("X", 0.0) in emitted
    con.close.assert_called_once()


def test_control_health_action_requires_data_path_or_env_default(monkeypatch):
    """Without an explicit data_path (or DUCKLAKE_DATA_PATH set on the function), control_health
    loud-fails rather than defaulting to the smoke path."""
    monkeypatch.setattr(_shared, "DATA_PATH", None)
    with pytest.raises(DuckLakeRuntimeError, match="data_path"):
        ha.action_control_health({"meta_schema": "ducklake_ops"}, None)


def test_control_health_action_requires_meta_schema(monkeypatch):
    monkeypatch.setattr(_shared, "DATA_PATH", "s3://bucket/ducklake/")
    with pytest.raises(DuckLakeRuntimeError, match="meta_schema"):
        ha.action_control_health({}, None)


def test_control_health_action_falls_back_to_env_data_path(monkeypatch):
    """When the event omits data_path, the env-pinned DATA_PATH default is used (T2.26: this
    action is read-mostly, unlike the other operational actions which refuse no-arg invokes)."""
    con = MagicMock()
    monkeypatch.setattr(_shared, "DATA_PATH", "s3://bucket/ducklake/")
    monkeypatch.setattr(ha.rt, "fetch_dsn", lambda: _FULL_DSN)
    open_calls = []
    monkeypatch.setattr(ha.rt, "open_connection", lambda **kw: (open_calls.append(kw), con)[1])
    monkeypatch.setattr(ha, "_report_stats_index", lambda dsn, schema: {})
    monkeypatch.setattr(ha.ducklake_control_health, "control_health", lambda con, metric_sink=None: {"ok": True})

    out = ha.action_control_health({"meta_schema": "ducklake_ops"}, None)
    assert out["ok"] is True
    assert open_calls[0]["data_path"] == "s3://bucket/ducklake/"


def test_action_catalog_stats_success():
    """catalog_stats dispatches to maint.catalog_stats with the event meta_schema; emits the size metric."""
    stats = {
        "ok": True,
        "meta_schema": "ducklake_ops",
        "catalog_metadata_bytes": 7_100_000,
        "snapshot_rows_est": 50,
        "data_file_rows_est": 800,
        "file_column_stats_rows_est": 12000,
        "metadata_table_count": 3,
        "metadata_tables": [],
        "per_ops_table": [{"table": "ops_recommendations_current", "data_file_count": 400}],
        "per_ops_table_note": "",
    }
    with (
        patch.object(ha.rt, "fetch_dsn", return_value=_FULL_DSN),
        patch.object(ha.maint, "catalog_stats", return_value=stats) as mock_stats,
        patch.object(_shared, "_emit_maintenance_metric") as mock_emit,
    ):
        result = ha.action_catalog_stats({"meta_schema": "ducklake_ops"}, None)

    assert result["catalog_metadata_bytes"] == 7_100_000
    assert mock_stats.call_args.kwargs["meta_schema"] == "ducklake_ops"
    metric_names = [c.args[0] for c in mock_emit.call_args_list]
    assert "CatalogMetadataBytes" in metric_names
    assert "CatalogFileColumnStatsRows" in metric_names


def test_catalog_stats_emits_metrics_only_for_the_ops_catalog():
    """A measurement read of another catalog returns the same body and adds nothing to the undimensioned ops series."""
    stats = {"ok": True, "catalog_metadata_bytes": 5, "file_column_stats_rows_est": 9}
    for schema in ("ducklake_smoke", "ducklake_telemetry"):
        with (
            patch.object(ha.rt, "fetch_dsn", return_value=_FULL_DSN),
            patch.object(ha.maint, "catalog_stats", return_value=stats),
            patch.object(_shared, "_emit_maintenance_metric") as mock_emit,
        ):
            result = ha.action_catalog_stats({"meta_schema": schema}, None)
        assert result == stats
        mock_emit.assert_not_called()
    with (
        patch.object(ha.rt, "fetch_dsn", return_value=_FULL_DSN),
        patch.object(ha.maint, "catalog_stats", return_value=stats),
        patch.object(_shared, "_emit_maintenance_metric") as mock_emit,
    ):
        ha.action_catalog_stats({"meta_schema": "ducklake_ops"}, None)
    assert [c.args[0] for c in mock_emit.call_args_list] == ["CatalogMetadataBytes", "CatalogFileColumnStatsRows"]


def test_action_catalog_stats_requires_meta_schema():
    """No-arg invoke is refused -- catalog_stats needs an explicit meta_schema (no production default)."""
    with pytest.raises(DuckLakeRuntimeError, match="meta_schema"):
        ha.action_catalog_stats({}, None)


# ---------------------------------------------------------------------------
# stats-index reporting (Decision 214 / Decision 88 invariant v)
# ---------------------------------------------------------------------------

_HOST = "ep-" + __import__("secrets").token_hex(6) + ".example.invalid"


class _PgError(Exception):
    def __init__(self, message: str, pgcode: str | None = None) -> None:
        super().__init__(message)
        self.pgcode = pgcode


def _state(valid: bool, idx_scan: int | None, seq: int | None) -> dict:
    return {"present": valid, "valid": valid, "index_name": "i", "idx_scan": idx_scan, "table_seq_scan": seq}


def _stub_pg(monkeypatch, connect) -> None:
    import sys
    import types

    pg = types.ModuleType("psycopg2")
    pg.connect = connect  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg2", pg)
    monkeypatch.setattr(ha.rt, "libpq_conninfo", lambda dsn: "conninfo")


def _conn() -> MagicMock:
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = MagicMock()
    return conn


def _emit_sink(monkeypatch) -> list:
    emitted: list = []
    monkeypatch.setattr(
        _shared, "_emit_maintenance_metric", lambda name, value, **kw: emitted.append((name, value, kw.get("dimensions")))
    )
    return emitted


def test_report_emits_three_metrics_per_schema_with_the_metaschema_dimension(monkeypatch):
    emitted = _emit_sink(monkeypatch)
    _stub_pg(monkeypatch, MagicMock(return_value=_conn()))
    states = {"ducklake_ops": _state(True, 9, 2), "ducklake_smoke": _state(False, None, None)}
    monkeypatch.setattr(ha.index_status, "catalog_index_schemas", lambda cur: list(states))
    monkeypatch.setattr(ha.index_status, "catalog_index_status", lambda cur, s: states[s])

    report = ha._report_stats_index({"host": _HOST}, "ducklake_ops")

    assert report == states
    assert ("StatsIndexValid", 1.0, {"MetaSchema": "ducklake_ops"}) in emitted
    assert ("FileColumnStatsIdxScan", 9.0, {"MetaSchema": "ducklake_ops"}) in emitted
    assert ("FileColumnStatsSeqScan", 2.0, {"MetaSchema": "ducklake_ops"}) in emitted
    assert ("StatsIndexValid", 0.0, {"MetaSchema": "ducklake_smoke"}) in emitted
    assert ("FileColumnStatsIdxScan", 0.0, {"MetaSchema": "ducklake_smoke"}) in emitted
    assert len(emitted) == 6


def _telemetry_states() -> dict:
    return {
        "ducklake_ops": _state(True, 9, 2),
        "ducklake_smoke": _state(True, 1, 1),
        "ducklake_telemetry": _state(True, 3, 1),
    }


def test_control_health_emits_growth_for_the_telemetry_catalog(monkeypatch):
    emitted = _emit_sink(monkeypatch)
    _stub_pg(monkeypatch, MagicMock(return_value=_conn()))
    states = _telemetry_states()
    monkeypatch.setattr(ha.index_status, "catalog_index_schemas", lambda cur: list(states))
    monkeypatch.setattr(ha.index_status, "catalog_index_status", lambda cur, s: dict(states[s]))
    monkeypatch.setattr(ha.index_status, "stats_rows_est", lambda cur, s: 4321)

    report = ha._report_stats_index({"host": _HOST}, "ducklake_telemetry")

    growth = [e for e in emitted if e[0] == "CatalogFileColumnStatsRows"]
    assert growth == [("CatalogFileColumnStatsRows", 4321.0, {"MetaSchema": "ducklake_telemetry"})]
    assert report["ducklake_telemetry"]["stats_rows_est"] == 4321 and "stats_rows_est" not in report["ducklake_ops"]


def test_growth_estimate_none_emits_nothing(monkeypatch):
    emitted = _emit_sink(monkeypatch)
    _stub_pg(monkeypatch, MagicMock(return_value=_conn()))
    states = _telemetry_states()
    monkeypatch.setattr(ha.index_status, "catalog_index_schemas", lambda cur: list(states))
    monkeypatch.setattr(ha.index_status, "catalog_index_status", lambda cur, s: dict(states[s]))
    monkeypatch.setattr(ha.index_status, "stats_rows_est", lambda cur, s: None)

    ha._report_stats_index({"host": _HOST}, "ducklake_ops")

    assert not [e for e in emitted if e[0] == "CatalogFileColumnStatsRows"]


def test_growth_estimate_failure_leaves_stats_index_valid_untouched(monkeypatch):
    emitted = _emit_sink(monkeypatch)
    _stub_pg(monkeypatch, MagicMock(return_value=_conn()))
    states = _telemetry_states()
    monkeypatch.setattr(ha.index_status, "catalog_index_schemas", lambda cur: list(states))
    monkeypatch.setattr(ha.index_status, "catalog_index_status", lambda cur, s: dict(states[s]))

    def boom(cur, schema):
        raise _PgError(f"could not reach {_HOST}", "57014")

    monkeypatch.setattr(ha.index_status, "stats_rows_est", boom)

    report = ha._report_stats_index({"host": _HOST}, "ducklake_ops")

    assert [e for e in emitted if e[0] == "StatsIndexValid"] == [("StatsIndexValid", 1.0, {"MetaSchema": s}) for s in states]
    assert not [e for e in emitted if e[0] == "CatalogFileColumnStatsRows"]
    assert report["ducklake_telemetry"]["growth_error"] == {"error_class": "_PgError", "sqlstate": "57014"}
    assert _HOST not in str(report) and "growth_error" not in report
    assert "growth_error" not in report["ducklake_ops"]


def test_report_failure_is_class_and_sqlstate_only_and_never_raises(monkeypatch):
    emitted = _emit_sink(monkeypatch)
    _stub_pg(monkeypatch, MagicMock(side_effect=_PgError(f"could not connect to {_HOST}", "08006")))
    out = ha._report_stats_index({"host": _HOST}, "ducklake_ops")
    assert out == {"error_class": "_PgError", "sqlstate": "08006"} and _HOST not in str(out)
    assert emitted == [("StatsIndexValid", 0.0, {"MetaSchema": "ducklake_ops"})]


def test_control_health_reports_the_index_even_when_an_invariant_raises(monkeypatch):
    reported: list = []
    monkeypatch.setattr(ha.rt, "fetch_dsn", lambda: _FULL_DSN)
    con = MagicMock()
    monkeypatch.setattr(ha.rt, "open_connection", lambda **kw: con)
    monkeypatch.setattr(ha, "_report_stats_index", lambda dsn, schema: reported.append(schema) or {})

    def boom(con, metric_sink=None):
        raise DuckLakeRuntimeError("invariant violated")

    monkeypatch.setattr(ha.ducklake_control_health, "control_health", boom)
    with pytest.raises(DuckLakeRuntimeError, match="invariant violated"):
        ha.action_control_health({"data_path": "s3://bucket/ducklake/", "meta_schema": "ducklake_ops"}, None)
    assert reported == ["ducklake_ops"]
    con.close.assert_called_once()


def test_control_health_result_carries_stats_index_and_is_json_serialisable(monkeypatch):
    import datetime
    import json

    monkeypatch.setattr(ha.rt, "fetch_dsn", lambda: _FULL_DSN)
    monkeypatch.setattr(ha.rt, "open_connection", lambda **kw: MagicMock())
    stamp = datetime.datetime(2026, 10, 1, tzinfo=datetime.timezone.utc).isoformat()
    monkeypatch.setattr(ha, "_report_stats_index", lambda dsn, schema: {"ducklake_ops": {"stats_reset": stamp}})
    monkeypatch.setattr(ha.ducklake_control_health, "control_health", lambda con, metric_sink=None: {"ok": True})
    out = ha.action_control_health({"data_path": "s3://bucket/ducklake/", "meta_schema": "ducklake_ops"}, None)
    assert out["stats_index"]["ducklake_ops"]["stats_reset"] == stamp
    json.dumps(out)


def test_catalog_stats_result_is_json_serialisable_with_stats_reset_text(monkeypatch):
    import json

    stats = {"ok": True, "catalog_metadata_bytes": 1, "stats_index": {"stats_reset": "2026-10-01 00:00:00+00"}}
    monkeypatch.setattr(ha.rt, "fetch_dsn", lambda: _FULL_DSN)
    monkeypatch.setattr(ha.maint, "catalog_stats", lambda **kw: stats)
    monkeypatch.setattr(_shared, "_emit_maintenance_metric", lambda *a, **k: None)
    json.dumps(ha.action_catalog_stats({"meta_schema": "ducklake_ops"}, None))
