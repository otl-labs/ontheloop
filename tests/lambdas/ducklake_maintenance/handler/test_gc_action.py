"""action_gc_ops dispatch tests (production-gc-and-storage-stability, T2.18 c2).

Thin-dispatch contract: explicit data_path + meta_schema required (no-arg invokes refused), dry_run
defaults False and propagates, delegates to src.common.ducklake_gc_ops.gc_ops, never calls
scope.resolve_scope. TestSmokeScopeNotRepointed ships the Decision 143 cl.2 never-weaken invariant
as a standing regression test.
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import pytest

import src.lambdas.ducklake_maintenance.handler as h
from src.common.ducklake_runtime import DuckLakeRuntimeError
from tests.fixtures.ducklake_maintenance_handler import _FULL_DSN

pytestmark = pytest.mark.unit


def test_action_gc_ops_requires_s3_data_path():
    with pytest.raises(DuckLakeRuntimeError, match="data_path"):
        h.action_gc_ops({"action": "gc_ops"}, None)


def test_action_gc_ops_requires_explicit_meta_schema():
    """No-arg invoke refused -- Decision 84/81 destructive-action guard."""
    with pytest.raises(DuckLakeRuntimeError, match="EXPLICIT 'meta_schema'"):
        h.action_gc_ops({"action": "gc_ops", "data_path": "s3://b/ducklake/"}, None)


def test_action_gc_ops_rejects_bad_meta_schema():
    with pytest.raises(DuckLakeRuntimeError, match="invalid SQL identifier"):
        h.action_gc_ops({"data_path": "s3://b/ducklake/", "meta_schema": "bad-name;DROP"}, None)


def test_action_gc_ops_delegates_to_gc_ops_body_and_closes_connection():
    con = MagicMock()
    with (
        patch.object(h.rt, "fetch_dsn", return_value=_FULL_DSN),
        patch.object(h.rt, "open_connection", return_value=con) as open_mock,
        patch.object(h.gc_ops_body, "gc_ops", return_value={"ok": True, "referenced_missing": 0}) as gc_ops_mock,
    ):
        result = h.action_gc_ops({"data_path": "s3://b/ducklake/", "meta_schema": "ducklake_ops"}, None)

    assert result == {"ok": True, "referenced_missing": 0}
    assert open_mock.call_args.kwargs["data_path"] == "s3://b/ducklake/"
    assert open_mock.call_args.kwargs["meta_schema"] == "ducklake_ops"
    assert gc_ops_mock.call_args.kwargs["dry_run"] is False
    assert gc_ops_mock.call_args.kwargs["data_path"] == "s3://b/ducklake/"
    assert gc_ops_mock.call_args.kwargs["catalog"] == h.maint.CATALOG_ALIAS
    con.close.assert_called_once()


def test_gc_ops_on_a_telemetry_catalog_passes_a_catalog_bound_sink():
    con = MagicMock()
    with (
        patch.object(h.rt, "fetch_dsn", return_value=_FULL_DSN),
        patch.object(h.rt, "open_connection", return_value=con),
        patch.object(h.gc_ops_body, "gc_ops", return_value={"ok": True}) as gc_ops_mock,
        patch.object(h, "_emit_maintenance_metric") as mock_emit,
    ):
        h.action_gc_ops({"data_path": "s3://b/ducklake-telemetry/", "meta_schema": "ducklake_telemetry"}, None)
        sink = gc_ops_mock.call_args.kwargs["metric_sink"]
        sink("GcReferencedMissing", 0.0)
        sink("GcDebtBytes", 5.0)
        ops_con = MagicMock()
        h.rt.open_connection.return_value = ops_con
        h.action_gc_ops({"data_path": "s3://b/ducklake/", "meta_schema": "ducklake_ops"}, None)
        assert gc_ops_mock.call_args.kwargs["metric_sink"] is mock_emit
    assert mock_emit.call_args_list[0].args == ("GcReferencedMissing", 0.0)
    assert mock_emit.call_args_list[0].kwargs == {"dimensions": {"MetaSchema": "ducklake_telemetry"}}
    assert len(mock_emit.call_args_list) == 1


def test_action_gc_ops_dry_run_flag_propagates():
    con = MagicMock()
    with (
        patch.object(h.rt, "fetch_dsn", return_value=_FULL_DSN),
        patch.object(h.rt, "open_connection", return_value=con),
        patch.object(h.gc_ops_body, "gc_ops", return_value={"ok": True}) as gc_ops_mock,
    ):
        h.action_gc_ops({"data_path": "s3://b/ducklake/", "meta_schema": "ducklake_ops", "dry_run": True}, None)
    assert gc_ops_mock.call_args.kwargs["dry_run"] is True


def test_action_gc_ops_dry_run_defaults_false():
    con = MagicMock()
    with (
        patch.object(h.rt, "fetch_dsn", return_value=_FULL_DSN),
        patch.object(h.rt, "open_connection", return_value=con),
        patch.object(h.gc_ops_body, "gc_ops", return_value={"ok": True}) as gc_ops_mock,
    ):
        h.action_gc_ops({"data_path": "s3://b/ducklake/", "meta_schema": "ducklake_ops"}, None)
    assert gc_ops_mock.call_args.kwargs["dry_run"] is False


def test_dry_run_invoke_emits_referenced_missing_and_would_delete_files_and_writes_nothing():
    """Real (unmocked) gc_ops_body.gc_ops body run through the handler dispatch, with only its
    connection-level collaborators faked -- proves the dry_run contract end-to-end: emits
    GcReferencedMissing + GcWouldDeleteFiles, issues no destructive call."""
    con = MagicMock()
    captured: list[tuple[str, float]] = []

    def fake_emit(name, value, **_kwargs):
        captured.append((name, value))

    with (
        patch.object(h.rt, "fetch_dsn", return_value=_FULL_DSN),
        patch.object(h.rt, "open_connection", return_value=con),
        patch.object(h, "_emit_maintenance_metric", side_effect=fake_emit),
        patch.object(h.gc_ops_body, "_discover_all_tables", return_value=["t1"]),
        patch.object(h.gc_ops_body, "_current_snapshot_id", return_value=1),
        patch.object(h.gc_ops_body.maint, "_collect_file_paths", return_value={"s3://b/t1/f0.parquet": 100}),
        patch.object(h.gc_ops_body.maint, "_dry_run_cleanup_paths", return_value=["s3://b/t1/f0.parquet"]),
        patch.object(h.gc_ops_body.maint, "_dry_run_orphan_paths", return_value=[]),
        patch.object(h.gc_ops_body.guard, "run_guarded_gc") as mock_run_guarded,
        patch.object(h.gc_ops_body, "_default_list_storage", return_value={"s3://b/t1/f0.parquet": 100}),
    ):
        result = h.action_gc_ops({"data_path": "s3://b/ducklake/", "meta_schema": "ducklake_ops", "dry_run": True}, None)

    mock_run_guarded.assert_not_called()
    assert result["dry_run"] is True
    assert result["guard_stats"] is None
    names = [n for n, _v in captured]
    assert "GcReferencedMissing" in names
    assert "GcWouldDeleteFiles" in names


def test_non_zero_referenced_missing_raises_rather_than_returning_ok():
    con = MagicMock()
    with (
        patch.object(h.rt, "fetch_dsn", return_value=_FULL_DSN),
        patch.object(h.rt, "open_connection", return_value=con),
        patch.object(h.gc_ops_body, "_discover_all_tables", return_value=["t1"]),
        patch.object(h.gc_ops_body, "_current_snapshot_id", return_value=1),
        patch.object(h.gc_ops_body.maint, "_collect_file_paths", return_value={"s3://b/t1/f0.parquet": 100}),
        patch.object(h.gc_ops_body.maint, "_dry_run_cleanup_paths", return_value=[]),
        patch.object(h.gc_ops_body.maint, "_dry_run_orphan_paths", return_value=[]),
        patch.object(h.gc_ops_body, "_default_list_storage", return_value={}),  # empty storage -> referenced-missing
    ):
        with pytest.raises(h.maint.DuckLakeMaintenanceError, match="referenced_missing"):
            h.action_gc_ops({"data_path": "s3://b/ducklake/", "meta_schema": "ducklake_ops", "dry_run": True}, None)


def test_action_gc_ops_never_calls_scope_resolve_scope():
    con = MagicMock()
    with (
        patch.object(h.rt, "fetch_dsn", return_value=_FULL_DSN),
        patch.object(h.rt, "open_connection", return_value=con),
        patch.object(h.scope, "resolve_scope") as mock_resolve,
        patch.object(h.gc_ops_body, "gc_ops", return_value={"ok": True}),
    ):
        h.action_gc_ops({"data_path": "s3://b/ducklake/", "meta_schema": "ducklake_ops"}, None)
    mock_resolve.assert_not_called()


def test_gc_ops_registered_in_dispatch_table():
    assert h._ACTIONS["gc_ops"] is h.action_gc_ops


class TestSmokeScopeNotRepointed:
    """Decision 143 cl.2 env-pinning: production scope comes from catalog enumeration; the smoke
    constant GC_TABLE_SCOPE must never be repointed at production or read by the admin handler.
    Ships the invariant as a standing regression test rather than leaving it a plan-time grep."""

    def test_gc_table_scope_still_holds_the_smoke_pair(self):
        from src.common.ducklake_runtime import SMOKE_CURRENT_TABLE, SMOKE_HISTORY_TABLE

        assert h.maint.GC_TABLE_SCOPE == (SMOKE_HISTORY_TABLE, SMOKE_CURRENT_TABLE)

    def test_admin_handler_never_reads_gc_table_scope(self):
        assert "GC_TABLE_SCOPE" not in inspect.getsource(h.action_gc_ops)
        assert "GC_TABLE_SCOPE" not in inspect.getsource(h.gc_ops_body)
