"""T2.19 operational actions concern (catalog_reinit / restore_drill / connectionless-skip /
merge_ops / catalog_stats) for src/lambdas/ducklake_maintenance/handler.py (100% coverage, mocked).

Split from the former tests/test_ducklake_maintenance_handler.py monolith (rec-2709 Wave 8).
Functions copied VERBATIM; _restore_drill_patches stays LOCAL to this module.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

import src.lambdas.ducklake_maintenance.handler as h
from src.common.ducklake_runtime import DuckLakeRuntimeError
from tests.fixtures.ducklake_maintenance_handler import _FULL_DSN, _response_body

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# _drop_meta_schema / subprocess_run (real bodies, psycopg2/subprocess mocked at the boundary)
# ---------------------------------------------------------------------------


def test_drop_meta_schema_drops_only():
    mock_conn = MagicMock()
    mock_cursor = mock_conn.cursor.return_value.__enter__.return_value
    with (
        patch.object(h.rt, "fetch_dsn", return_value=_FULL_DSN),
        patch.object(h.rt, "libpq_conninfo", return_value="conninfo"),
        patch("psycopg2.connect", return_value=mock_conn),
    ):
        result = h._drop_meta_schema("ducklake_ops")
    assert result is True
    executed_sql = [c.args[0] for c in mock_cursor.execute.call_args_list]
    assert any(s.startswith("DROP SCHEMA IF EXISTS ducklake_ops") for s in executed_sql)
    assert not any(s.startswith("CREATE SCHEMA") for s in executed_sql)
    mock_conn.close.assert_called_once()


def test_drop_meta_schema_recreate_also_creates():
    mock_conn = MagicMock()
    mock_cursor = mock_conn.cursor.return_value.__enter__.return_value
    with (
        patch.object(h.rt, "fetch_dsn", return_value=_FULL_DSN),
        patch.object(h.rt, "libpq_conninfo", return_value="conninfo"),
        patch("psycopg2.connect", return_value=mock_conn),
    ):
        h._drop_meta_schema("ducklake_ops", recreate=True)
    executed_sql = [c.args[0] for c in mock_cursor.execute.call_args_list]
    assert any(s.startswith("CREATE SCHEMA IF NOT EXISTS ducklake_ops") for s in executed_sql)


def test_drop_meta_schema_rejects_bad_identifier():
    with pytest.raises(DuckLakeRuntimeError, match="invalid SQL identifier"):
        h._drop_meta_schema("bad-name;DROP")


def test_subprocess_run_real_invocation():
    """subprocess_run's own thin wrapper body (not the mocked injection point used elsewhere in
    this file) -- a trivial real subprocess call."""
    result = h.subprocess_run([sys.executable, "-c", "print('ok')"])
    assert result.returncode == 0
    assert "ok" in result.stdout


# ---------------------------------------------------------------------------
# T2.19 operational actions: catalog_reinit / seed / restore_drill + connectionless dispatch
# ---------------------------------------------------------------------------


def test_catalog_reinit_drops_then_reattaches():
    con = MagicMock()
    with (
        patch.object(h, "_drop_meta_schema", return_value=True) as drop_mock,
        patch.object(h.rt, "fetch_dsn", return_value=_FULL_DSN),
        patch.object(h.rt, "open_connection", return_value=con) as open_mock,
        patch.object(h.index_actions, "ensure_index_for", return_value={"valid_after": True}) as ensure_mock,
    ):
        result = h.action_catalog_reinit(
            {
                "action": "catalog_reinit",
                "data_path": "s3://b/ducklake/",
                "meta_schema": "ducklake_ops",
                "confirm": "ducklake_ops",
            },
            None,
        )
    assert result["ok"] is True and result["reinitialized"] is True
    drop_mock.assert_called_once_with("ducklake_ops", recreate=True)
    ensure_mock.assert_called_once_with("ducklake_ops")
    assert result["stats_index"] == {"valid_after": True}
    assert open_mock.call_args.kwargs["data_path"] == "s3://b/ducklake/"
    assert open_mock.call_args.kwargs["meta_schema"] == "ducklake_ops"
    con.close.assert_called_once()


def test_catalog_reinit_requires_s3_data_path():
    with pytest.raises(DuckLakeRuntimeError, match="data_path"):
        h.action_catalog_reinit({"action": "catalog_reinit"}, None)


def test_catalog_reinit_rejects_bad_meta_schema():
    with pytest.raises(DuckLakeRuntimeError, match="invalid SQL identifier"):
        h.action_catalog_reinit(
            {"data_path": "s3://b/ducklake/", "meta_schema": "bad-name;DROP", "confirm": "bad-name;DROP"}, None
        )


def test_catalog_reinit_requires_explicit_meta_schema():
    """Destructive-action guard (Decision 84): a no-arg invoke must never target the live catalog."""
    with pytest.raises(DuckLakeRuntimeError, match="EXPLICIT 'meta_schema'"):
        h.action_catalog_reinit({"data_path": "s3://b/ducklake/"}, None)


def test_catalog_reinit_requires_matching_confirm():
    with pytest.raises(DuckLakeRuntimeError, match="confirm="):
        h.action_catalog_reinit({"data_path": "s3://b/ducklake/", "meta_schema": "ducklake_smoke"}, None)


def _restore_drill_patches(read_rows):
    return (
        patch.object(h, "_drop_meta_schema", return_value=True),
        patch.object(h.rt, "fetch_dsn", return_value=_FULL_DSN),
        patch.object(h.rt, "open_connection", return_value=MagicMock()),
        patch.object(h.rt, "create_scd2_tables"),
        patch.object(h.rt, "write_scd2"),
        patch.object(h.rt, "read_current", return_value=read_rows),
        patch.object(h, "subprocess_run", return_value=type("R", (), {"returncode": 0, "stderr": ""})()),
        patch.object(h.catalog_dr, "run_pg_restore"),
    )


def test_restore_drill_ok():
    p = _restore_drill_patches([{"rec_id": "drill-probe", "payload": "restore-drill"}])
    with p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7]:
        result = h.action_restore_drill({"action": "restore_drill"}, None)
    assert result["ok"] is True and result["restored"] is True


def test_restore_drill_probe_lost_loud_fails():
    p = _restore_drill_patches([])  # read-your-write finds nothing after restore
    with p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7]:
        with pytest.raises(h.catalog_dr.CatalogDrError, match="read-your-write FAILED"):
            h.action_restore_drill({"action": "restore_drill"}, None)


def test_restore_drill_pg_dump_failure_loud_fails():
    with (
        patch.object(h, "_drop_meta_schema", return_value=True),
        patch.object(h.rt, "fetch_dsn", return_value=_FULL_DSN),
        patch.object(h.rt, "open_connection", return_value=MagicMock()),
        patch.object(h.rt, "create_scd2_tables"),
        patch.object(h.rt, "write_scd2"),
        patch.object(h, "subprocess_run", return_value=type("R", (), {"returncode": 1, "stderr": "boom"})()),
    ):
        with pytest.raises(h.catalog_dr.CatalogDrError, match="pg_dump exited 1"):
            h.action_restore_drill({"action": "restore_drill"}, None)


def test_handler_dispatches_catalog_reinit_without_a_connection_arg():
    """The handler never pre-opens a connection -- every action receives con=None (T2.18 c9 split)."""
    action_mock = MagicMock(return_value={"ok": True})
    with patch.dict(h._ACTIONS, {"catalog_reinit": action_mock}):
        r = h.handler({"action": "catalog_reinit", "data_path": "s3://b/ducklake/"})
    assert r["statusCode"] == 200
    assert action_mock.call_args.args[1] is None


# ---------------------------------------------------------------------------
# action_merge_ops (compaction-scope-policy-matrix: catalog enumeration x declared policy, T2.18)
# ---------------------------------------------------------------------------


def _merge_ops_semantics() -> dict:
    """A field_semantics-shaped fixture covering both live scd2 tables + their policy cell."""
    return {
        "ops_tables": {
            "ops_recommendations": {
                "status": "live",
                "history_table": "ops_recommendations_history",
                "current_table": "ops_recommendations_current",
            },
            "ops_decisions": {
                "status": "live",
                "history_table": "ops_decisions_history",
                "current_table": "ops_decisions_current",
            },
        },
        "maintenance_policy": {
            "scd2": {"merge_ops": {"apply": True, "reason": "standard"}},
            "append_only": {"merge_ops": {"apply": True, "reason": "standard"}},
            "control": {"merge_ops": {"apply": True, "reason": "standard"}},
        },
    }


def test_action_merge_ops_requires_data_path():
    """Loud-fail when data_path is missing."""
    with pytest.raises(DuckLakeRuntimeError, match="data_path"):
        h.action_merge_ops({}, None)


def test_action_merge_ops_requires_s3_data_path():
    """Loud-fail when data_path is not an s3:// URI."""
    with pytest.raises(DuckLakeRuntimeError, match="data_path"):
        h.action_merge_ops({"data_path": "/local/path"}, None)


def test_action_merge_ops_requires_meta_schema():
    """Loud-fail when meta_schema is missing."""
    with pytest.raises(DuckLakeRuntimeError, match="meta_schema"):
        h.action_merge_ops({"data_path": "s3://b/ducklake/"}, None)


def test_action_merge_ops_no_tables_discovered():
    """Loud-fail when information_schema returns no tables at all."""
    con = MagicMock()
    con.execute.return_value.fetchall.return_value = []
    with (
        patch.object(h.rt, "fetch_dsn", return_value=_FULL_DSN),
        patch.object(h.rt, "open_connection", return_value=con),
    ):
        with pytest.raises(DuckLakeRuntimeError, match="no tables discovered"):
            h.action_merge_ops({"data_path": "s3://b/ducklake/", "meta_schema": "ducklake_ops"}, None)
    con.close.assert_called_once()


def test_action_merge_ops_discovers_and_merges_tables():
    """Discovery query triggers merge_adjacent_files for each discovered, classified table."""
    expected_tables = [
        "ops_decisions_current",
        "ops_decisions_history",
        "ops_recommendations_current",
        "ops_recommendations_history",
    ]
    con = MagicMock()
    con.execute.return_value.fetchall.return_value = [(t,) for t in expected_tables]

    with (
        patch.object(h.rt, "fetch_dsn", return_value=_FULL_DSN),
        patch.object(h.rt, "open_connection", return_value=con),
        patch.object(h.rt, "load_field_semantics", return_value=_merge_ops_semantics()),
        patch.object(h.maint, "_count_files", return_value=10),
        patch.object(h.maint, "merge_adjacent_files") as mock_merge,
        patch.object(h, "_emit_maintenance_metric"),
    ):
        result = h.action_merge_ops({"data_path": "s3://b/ducklake/", "meta_schema": "ducklake_ops"}, None)

    assert result["ok"] is True
    assert result["action"] == "merge_ops"
    assert sorted(result["tables"]) == expected_tables
    assert mock_merge.call_count == len(expected_tables)
    assert len(result["per_table"]) == len(expected_tables)
    assert "files_before" in result
    assert "files_after" in result
    assert "elapsed_ms" in result
    con.close.assert_called_once()


def test_action_merge_ops_covers_ops_recommendations_and_ops_decisions():
    """Both ops_recommendations AND ops_decisions pairs must be merged, not just one."""
    discovered = [
        "ops_decisions_current",
        "ops_decisions_history",
        "ops_recommendations_current",
        "ops_recommendations_history",
    ]
    con = MagicMock()
    con.execute.return_value.fetchall.return_value = [(t,) for t in discovered]

    merged: list[str] = []

    def capture_merge(c, tables, *, catalog=None, schema=None):
        merged.extend(tables)

    with (
        patch.object(h.rt, "fetch_dsn", return_value=_FULL_DSN),
        patch.object(h.rt, "open_connection", return_value=con),
        patch.object(h.rt, "load_field_semantics", return_value=_merge_ops_semantics()),
        patch.object(h.maint, "_count_files", return_value=5),
        patch.object(h.maint, "merge_adjacent_files", side_effect=capture_merge),
        patch.object(h, "_emit_maintenance_metric"),
    ):
        h.action_merge_ops({"data_path": "s3://b/ducklake/", "meta_schema": "ducklake_ops"}, None)

    assert any("ops_recommendations" in t for t in merged), "ops_recommendations tables not merged"
    assert any("ops_decisions" in t for t in merged), "ops_decisions tables not merged"


def test_action_merge_ops_emits_metrics():
    """MergeOps* metrics are emitted after a successful merge."""
    con = MagicMock()
    con.execute.return_value.fetchall.return_value = [
        ("ops_recommendations_history",),
        ("ops_recommendations_current",),
    ]

    with (
        patch.object(h.rt, "fetch_dsn", return_value=_FULL_DSN),
        patch.object(h.rt, "open_connection", return_value=con),
        patch.object(h.rt, "load_field_semantics", return_value=_merge_ops_semantics()),
        patch.object(h.maint, "_count_files", return_value=3),
        patch.object(h.maint, "merge_adjacent_files"),
        patch.object(h, "_emit_maintenance_metric") as mock_emit,
    ):
        h.action_merge_ops({"data_path": "s3://b/ducklake/", "meta_schema": "ducklake_ops"}, None)

    metric_names = [c.args[0] for c in mock_emit.call_args_list]
    assert "MergeOpsDurationMs" in metric_names
    assert "MergeOpsFilesBeforeTotal" in metric_names
    assert "MergeOpsFilesAfterTotal" in metric_names
    assert "MergeOpsTablesCount" in metric_names


_TELEMETRY_DISCOVERED = ["telemetry_sessions", "telemetry_agents"]


def _telemetry_semantics() -> dict:
    return {
        "ops_tables": {},
        "maintenance_policy": {"append_only": {"merge_ops": {"apply": True, "reason": "standard"}}},
    }


def _run_merge_ops(meta_schema: str):
    con = MagicMock()
    con.execute.return_value.fetchall.return_value = [(t,) for t in _TELEMETRY_DISCOVERED]
    with (
        patch.object(h.rt, "fetch_dsn", return_value=_FULL_DSN),
        patch.object(h.rt, "open_connection", return_value=con),
        patch.object(h.rt, "load_field_semantics", return_value=_merge_ops_semantics()),
        patch.object(h.scope, "resolve_scope") as resolve,
        patch.object(h.maint, "_count_files", return_value=3),
        patch.object(h.maint, "merge_adjacent_files"),
        patch.object(h, "_emit_maintenance_metric") as mock_emit,
    ):
        resolve.return_value = MagicMock(to_merge=list(_TELEMETRY_DISCOVERED), unclassified=[])
        h.action_merge_ops({"data_path": "s3://b/ducklake-telemetry/", "meta_schema": meta_schema}, None)
    return mock_emit


def test_merge_ops_on_a_telemetry_catalog_dimensions_every_metric():
    mock_emit = _run_merge_ops("ducklake_telemetry")
    names = {c.args[0] for c in mock_emit.call_args_list}
    assert names and names <= h.CATALOG_METRICS
    assert {"MergeOpsDurationMs", "MergeOpsFilesAfterTotal", "MergeOpsTablesCount", "MergeOpsPassFailed"} <= names
    assert all(c.kwargs.get("dimensions") == {"MetaSchema": "ducklake_telemetry"} for c in mock_emit.call_args_list)


def test_merge_ops_on_ops_calls_the_rebound_emitter_unchanged():
    mock_emit = _run_merge_ops("ducklake_ops")
    names = {c.args[0] for c in mock_emit.call_args_list}
    assert {"MergeOpsFilesBeforeTotal", "MergeOpsUnclassifiedTables"} <= names
    assert all("dimensions" not in c.kwargs for c in mock_emit.call_args_list)


def test_breaker_trip_is_dimensioned_by_catalog():
    err = h.maint.DuckLakeMaintenanceError("tripped")
    for event, dims in (
        ({"action": "merge_ops", "meta_schema": "ducklake_telemetry"}, {"MetaSchema": "ducklake_telemetry"}),
        ({"action": "merge_ops", "meta_schema": "ducklake_ops"}, None),
        ({"action": "merge_ops"}, None),
        ({"action": "merge_ops", "meta_schema": "bad-name;"}, None),
    ):
        with (
            patch.dict(h._ACTIONS, {"merge_ops": MagicMock(side_effect=err)}),
            patch.object(h, "_emit_maintenance_metric") as mock_emit,
        ):
            assert h.handler(event)["statusCode"] == 500
        (call,) = mock_emit.call_args_list
        assert call.args == ("MaintenanceBreakerTrip", 1.0)
        assert call.kwargs.get("dimensions") == dims


def test_action_merge_ops_handler_receives_no_connection():
    """Handler dispatches merge_ops with con=None -- the action opens its own connection."""
    action_mock = MagicMock(
        return_value={
            "ok": True,
            "action": "merge_ops",
            "tables": [],
            "files_before": 0,
            "files_after": 0,
            "elapsed_ms": 1.0,
            "per_table": [],
        }
    )
    with patch.dict(h._ACTIONS, {"merge_ops": action_mock}):
        r = h.handler({"action": "merge_ops", "data_path": "s3://b/ducklake/", "meta_schema": "ducklake_ops"})
    assert r["statusCode"] == 200
    assert action_mock.call_args.args[1] is None


def test_merge_ops_reports_count_unavailable_when_introspection_raises():
    """Guard vs observability split: a transient _count_files failure must degrade LOUDLY (null
    counts + count_unavailable) rather than aborting the every-6h production compaction cadence
    (Decision 88 cl.1(iii)) -- merge_adjacent_files still runs for every table regardless."""
    discovered = ["ops_recommendations_history", "ops_recommendations_current"]
    con = MagicMock()
    con.execute.return_value.fetchall.return_value = [(t,) for t in discovered]

    with (
        patch.object(h.rt, "fetch_dsn", return_value=_FULL_DSN),
        patch.object(h.rt, "open_connection", return_value=con),
        patch.object(h.rt, "load_field_semantics", return_value=_merge_ops_semantics()),
        patch.object(h.maint, "_count_files", side_effect=RuntimeError("connection lost")),
        patch.object(h.maint, "merge_adjacent_files") as mock_merge,
        patch.object(h, "_emit_maintenance_metric") as mock_emit,
    ):
        result = h.action_merge_ops({"data_path": "s3://b/ducklake/", "meta_schema": "ducklake_ops"}, None)

    assert result["ok"] is True
    assert result["files_before"] is None
    assert result["files_after"] is None
    assert result["count_unavailable"] is True
    assert mock_merge.call_count == len(discovered), "merge must still run for every table despite the count failure"
    for entry in result["per_table"]:
        assert entry["count_unavailable"] is True
        assert entry["files_before"] is None
        assert entry["files_after"] is None
    metric_names = [c.args[0] for c in mock_emit.call_args_list]
    assert "MergeOpsFilesBeforeTotal" not in metric_names
    assert "MergeOpsFilesAfterTotal" not in metric_names
    con.close.assert_called_once()


def test_merge_ops_nulls_only_the_failed_side_of_a_table():
    """rec-3785: when only ONE side of a table's two _count_files reads fails (before succeeds,
    after raises), BOTH sides of that table's pair must be nulled -- never an asymmetric
    one-null-one-real pair sitting next to count_unavailable: true."""
    discovered = ["ops_recommendations_history", "ops_recommendations_current"]
    con = MagicMock()
    con.execute.return_value.fetchall.return_value = [(t,) for t in discovered]

    with (
        patch.object(h.rt, "fetch_dsn", return_value=_FULL_DSN),
        patch.object(h.rt, "open_connection", return_value=con),
        patch.object(h.rt, "load_field_semantics", return_value=_merge_ops_semantics()),
        patch.object(
            h.maint, "_count_files", side_effect=[10, RuntimeError("after read lost"), 10, RuntimeError("after read lost")]
        ),
        patch.object(h.maint, "merge_adjacent_files"),
        patch.object(h, "_emit_maintenance_metric"),
    ):
        result = h.action_merge_ops({"data_path": "s3://b/ducklake/", "meta_schema": "ducklake_ops"}, None)

    assert result["count_unavailable"] is True
    assert result["files_before"] is None
    assert result["files_after"] is None
    for entry in result["per_table"]:
        assert entry["count_unavailable"] is True
        assert entry["files_before"] is None, "the successful 'before' read must be nulled alongside the failed 'after' read"
        assert entry["files_after"] is None


def test_action_merge_ops_no_destructive_primitives():
    """merge_ops must not dispatch expire_snapshots, cleanup_old_files, or delete_orphaned_files."""
    con = MagicMock()
    con.execute.return_value.fetchall.return_value = [
        ("ops_recommendations_history",),
        ("ops_recommendations_current",),
    ]

    with (
        patch.object(h.rt, "fetch_dsn", return_value=_FULL_DSN),
        patch.object(h.rt, "open_connection", return_value=con),
        patch.object(h.rt, "load_field_semantics", return_value=_merge_ops_semantics()),
        patch.object(h.maint, "_count_files", return_value=5),
        patch.object(h.maint, "merge_adjacent_files"),
        patch.object(h.maint, "expire_snapshots") as mock_expire,
        patch.object(h.maint, "cleanup_old_files") as mock_cleanup,
        patch.object(h.maint, "delete_orphaned_files") as mock_orphan,
        patch.object(h, "_emit_maintenance_metric"),
    ):
        h.action_merge_ops({"data_path": "s3://b/ducklake/", "meta_schema": "ducklake_ops"}, None)

    mock_expire.assert_not_called()
    mock_cleanup.assert_not_called()
    mock_orphan.assert_not_called()


def test_handler_merge_ops_listed_in_actions():
    """merge_ops must appear in the actions list returned on unknown action."""
    body = _response_body(h.handler({"action": "bad"}))
    assert "merge_ops" in body["actions"]


# ---------------------------------------------------------------------------
# action_reconcile_columns (non-destructive ALTER TABLE ADD COLUMN)
# ---------------------------------------------------------------------------


def test_action_reconcile_columns_requires_s3_data_path():
    with pytest.raises(DuckLakeRuntimeError, match="data_path"):
        h.action_reconcile_columns({"meta_schema": "ducklake_ops", "table": "ops_recommendations"}, None)


def test_action_reconcile_columns_requires_meta_schema():
    with pytest.raises(DuckLakeRuntimeError, match="meta_schema"):
        h.action_reconcile_columns({"data_path": "s3://b/ducklake/", "table": "ops_recommendations"}, None)


def test_action_reconcile_columns_requires_table():
    with pytest.raises(DuckLakeRuntimeError, match="'table'"):
        h.action_reconcile_columns({"data_path": "s3://b/ducklake/", "meta_schema": "ducklake_ops"}, None)


def test_action_reconcile_columns_success():
    con = MagicMock()
    with (
        patch.object(h.rt, "fetch_dsn", return_value=_FULL_DSN),
        patch.object(h.rt, "open_connection", return_value=con),
        patch.object(h.rt, "reconcile_table_columns", return_value={"added_history": ["x"], "added_current": []}),
    ):
        result = h.action_reconcile_columns(
            {"data_path": "s3://b/ducklake/", "meta_schema": "ducklake_ops", "table": "ops_recommendations"}, None
        )
    assert result["ok"] is True
    assert result["action"] == "reconcile_columns"
    assert result["added_history"] == ["x"]
    con.close.assert_called_once()
