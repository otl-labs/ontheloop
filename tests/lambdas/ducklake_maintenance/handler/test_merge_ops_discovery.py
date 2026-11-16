"""Discovery + loud-isolation + wiring concern for action_merge_ops (compaction-scope-policy-matrix,
T2.18). Split from test_operational_actions.py's merge_ops block so the rec-3762 named acceptance
node and the isolate-then-raise / reconciliation-wiring nodes have their own focused module.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

import src.lambdas.ducklake_maintenance.handler as h
from src.common import ducklake_maintenance_scope as scope_mod
from src.common.ducklake_runtime import DuckLakeRuntimeError
from src.common.ducklake_scd2_schema import load_field_semantics
from tests.fixtures.ducklake_maintenance_handler import _FULL_DSN

pytestmark = pytest.mark.unit


_NO_COMPACTION_CELL = {"rewrite_current_deletes": {"apply": False, "reason": "fixture: compaction is out of scope here"}}
_NO_COMPACTION_BODY = {"failures": [], "refused": [], "per_table": [], "skipped": [], "deferred": []}


def _semantics() -> dict:
    return {
        "ops_tables": {
            "ops_recommendations": {
                "status": "live",
                "history_table": "ops_recommendations_history",
                "current_table": "ops_recommendations_current",
            },
            "ops_entity_counters": {
                "status": "live",
                "write_mode": "control",
            },
            "ops_priority_queue": {
                "status": "dormant",
                "history_table": "ops_priority_queue_history",
                "current_table": "ops_priority_queue_current",
            },
        },
        "maintenance_policy": {
            "scd2": {"merge_ops": {"apply": True, "reason": "standard"}, **_NO_COMPACTION_CELL},
            "control": {"merge_ops": {"apply": True, "reason": "rec-3762"}, **_NO_COMPACTION_CELL},
            "append_only": {"merge_ops": {"apply": True, "reason": "standard"}, **_NO_COMPACTION_CELL},
        },
    }


def test_discovery_covers_non_scd2_ops_tables():
    """rec-3762: unfiltered discovery must surface ops_entity_counters (a control-class table the
    retired LIKE 'ops_%_history'/'ops_%_current' predicate could never match) and merge it."""
    con = MagicMock()
    con.execute.return_value.fetchall.return_value = [
        ("ops_entity_counters",),
        ("ops_recommendations_current",),
        ("ops_recommendations_history",),
    ]

    with (
        patch.object(h.rt, "fetch_dsn", return_value=_FULL_DSN),
        patch.object(h.rt, "open_connection", return_value=con),
        patch.object(h.rt, "load_field_semantics", return_value=_semantics()),
        patch.object(h.maint, "_count_files", return_value=1),
        patch.object(h.maint, "merge_adjacent_files") as mock_merge,
        patch.object(h, "_emit_maintenance_metric"),
    ):
        result = h.action_merge_ops({"data_path": "s3://b/ducklake/", "meta_schema": "ducklake_ops"}, None)

    assert result["ok"] is True
    assert "ops_entity_counters" in result["tables"]
    merged_tables = {c.args[1][0] for c in mock_merge.call_args_list}
    assert "ops_entity_counters" in merged_tables
    assert result["unclassified"] == []
    con.close.assert_called_once()


def test_no_naming_convention_predicate_in_discovery_query():
    """The information_schema query must not filter by table name -- unfiltered enumeration only."""
    con = MagicMock()
    con.execute.return_value.fetchall.return_value = [("ops_entity_counters",)]

    with (
        patch.object(h.rt, "fetch_dsn", return_value=_FULL_DSN),
        patch.object(h.rt, "open_connection", return_value=con),
        patch.object(h.rt, "load_field_semantics", return_value=_semantics()),
        patch.object(h.maint, "_count_files", return_value=1),
        patch.object(h.maint, "merge_adjacent_files"),
        patch.object(h, "_emit_maintenance_metric"),
    ):
        h.action_merge_ops({"data_path": "s3://b/ducklake/", "meta_schema": "ducklake_ops"}, None)

    discovery_sql = con.execute.call_args_list[0].args[0]
    assert "LIKE" not in discovery_sql


def test_one_table_merge_failure_isolates_but_still_raises():
    """A single table's merge_adjacent_files failure must not prevent the remaining classified
    tables from being merged, but the whole invocation must still terminate non-successfully
    (Decision 188 pt 3, extended by this plan's Decision)."""
    con = MagicMock()
    con.execute.return_value.fetchall.return_value = [
        ("ops_entity_counters",),
        ("ops_recommendations_current",),
        ("ops_recommendations_history",),
    ]

    def merge_side_effect(_con, tables, **_kwargs):
        if tables == ["ops_entity_counters"]:
            raise RuntimeError("simulated merge failure")

    with (
        patch.object(h.rt, "fetch_dsn", return_value=_FULL_DSN),
        patch.object(h.rt, "open_connection", return_value=con),
        patch.object(h.rt, "load_field_semantics", return_value=_semantics()),
        patch.object(h.maint, "_count_files", return_value=1),
        patch.object(h.maint, "merge_adjacent_files", side_effect=merge_side_effect) as mock_merge,
        patch.object(h, "_emit_maintenance_metric") as mock_emit,
    ):
        with pytest.raises(DuckLakeRuntimeError, match="ops_entity_counters") as exc_info:
            h.action_merge_ops({"data_path": "s3://b/ducklake/", "meta_schema": "ducklake_ops"}, None)

    # The per-table error DETAIL (not just the failed table's name) must survive into the raised
    # message -- it is the only thing an operator sees once handler() converts this to a bare
    # {ok: false, error: str(exc)} 500 response, so a bare table-name list would silently lose it.
    assert "simulated merge failure" in str(exc_info.value)
    merged_tables = {c.args[1][0] for c in mock_merge.call_args_list}
    assert merged_tables == {"ops_entity_counters", "ops_recommendations_current", "ops_recommendations_history"}
    metric_names = [c.args[0] for c in mock_emit.call_args_list]
    assert "MergeOpsTableFailure" in metric_names
    con.close.assert_called_once()


def test_handler_response_body_surfaces_per_table_merge_error_detail():
    """The wiring end-to-end: a per-table merge failure's error text must be visible in the
    Function-URL response body handler() returns, not just in an internal per_table list that
    gets discarded once action_merge_ops raises."""
    con = MagicMock()
    con.execute.return_value.fetchall.return_value = [("ops_entity_counters",)]

    def merge_side_effect(_con, _tables, **_kwargs):
        raise RuntimeError("simulated merge failure detail")

    with (
        patch.object(h.rt, "fetch_dsn", return_value=_FULL_DSN),
        patch.object(h.rt, "open_connection", return_value=con),
        patch.object(h.rt, "load_field_semantics", return_value=_semantics()),
        patch.object(h.maint, "_count_files", return_value=1),
        patch.object(h.maint, "merge_adjacent_files", side_effect=merge_side_effect),
        patch.object(h, "_emit_maintenance_metric"),
    ):
        response = h.handler({"action": "merge_ops", "data_path": "s3://b/ducklake/", "meta_schema": "ducklake_ops"})

    assert response["statusCode"] == 500
    body = json.loads(response["body"])
    assert "simulated merge failure detail" in body["error"]
    assert "ops_entity_counters" in body["error"]


def test_unclassified_table_also_triggers_the_aggregate_raise():
    """An unclassifiable table is collected, never silently dropped -- every classified table is
    still merged before the pass raises."""
    con = MagicMock()
    con.execute.return_value.fetchall.return_value = [
        ("a_stray_table",),
        ("ops_recommendations_current",),
        ("ops_recommendations_history",),
    ]

    with (
        patch.object(h.rt, "fetch_dsn", return_value=_FULL_DSN),
        patch.object(h.rt, "open_connection", return_value=con),
        patch.object(h.rt, "load_field_semantics", return_value=_semantics()),
        patch.object(h.maint, "_count_files", return_value=1),
        patch.object(h.maint, "merge_adjacent_files") as mock_merge,
        patch.object(h, "_emit_maintenance_metric"),
    ):
        with pytest.raises(DuckLakeRuntimeError, match="a_stray_table"):
            h.action_merge_ops({"data_path": "s3://b/ducklake/", "meta_schema": "ducklake_ops"}, None)

    merged_tables = {c.args[1][0] for c in mock_merge.call_args_list}
    assert merged_tables == {"ops_recommendations_current", "ops_recommendations_history"}


def test_smoke_schema_tables_never_surface_unclassified_in_production():
    """rec-3864's acceptance node: binds the PRODUCTION data path against the REAL generated
    field_semantics.yaml (never this module's hand-rolled _semantics() fixture) -- the ops
    tables plus the declared smoke-harness pair enumerate cleanly, unclassified == [], the pair
    merges, and both pass-outcome metrics emit 0.0."""
    real_semantics = load_field_semantics()
    registry = scope_mod.build_registry(real_semantics)
    discovered = sorted(registry)  # every physical table the live registry declares, incl. the smoke pair

    con = MagicMock()
    con.execute.return_value.fetchall.return_value = [(name,) for name in discovered]

    with (
        patch.object(h.rt, "fetch_dsn", return_value=_FULL_DSN),
        patch.object(h.rt, "open_connection", return_value=con),
        patch.object(h.rt, "load_field_semantics", return_value=real_semantics),
        patch.object(h.current_compaction, "compact_current_tables", return_value=_NO_COMPACTION_BODY),
        patch.object(h.maint, "_count_files", return_value=1),
        patch.object(h.maint, "merge_adjacent_files"),
        patch.object(h, "_emit_maintenance_metric") as mock_emit,
    ):
        result = h.action_merge_ops({"data_path": "s3://b/ducklake/", "meta_schema": "ducklake_ops"}, None)

    assert result["ok"] is True
    assert result["unclassified"] == []
    assert "ducklake_smoke_history" in result["tables"]
    assert "ducklake_smoke_current" in result["tables"]
    metric_calls = {c.args[0]: c.args[1] for c in mock_emit.call_args_list}
    assert metric_calls["MergeOpsUnclassifiedTables"] == 0.0
    assert metric_calls["MergeOpsPassFailed"] == 0.0


def test_pass_outcome_metrics_flag_an_unclassified_table():
    """A stray table alongside the real production roster still fails the pass and emits
    MergeOpsUnclassifiedTables 1.0 and MergeOpsPassFailed 1.0."""
    real_semantics = load_field_semantics()
    registry = scope_mod.build_registry(real_semantics)
    discovered = [*sorted(registry), "a_stray_table"]

    con = MagicMock()
    con.execute.return_value.fetchall.return_value = [(name,) for name in discovered]

    with (
        patch.object(h.rt, "fetch_dsn", return_value=_FULL_DSN),
        patch.object(h.rt, "open_connection", return_value=con),
        patch.object(h.rt, "load_field_semantics", return_value=real_semantics),
        patch.object(h.current_compaction, "compact_current_tables", return_value=_NO_COMPACTION_BODY),
        patch.object(h.maint, "_count_files", return_value=1),
        patch.object(h.maint, "merge_adjacent_files"),
        patch.object(h, "_emit_maintenance_metric") as mock_emit,
    ):
        with pytest.raises(DuckLakeRuntimeError, match="a_stray_table"):
            h.action_merge_ops({"data_path": "s3://b/ducklake/", "meta_schema": "ducklake_ops"}, None)

    metric_calls = {c.args[0]: c.args[1] for c in mock_emit.call_args_list}
    assert metric_calls["MergeOpsUnclassifiedTables"] == 1.0
    assert metric_calls["MergeOpsPassFailed"] == 1.0


def test_merge_failure_only_pass_emits_pass_failed():
    """A per-table merge_adjacent_files failure ALONE (no unclassified table) must still set
    MergeOpsPassFailed 1.0, with MergeOpsUnclassifiedTables 0.0 -- proves PassFailed is keyed on
    EITHER merge_failures OR unclassified, not unclassified alone."""
    con = MagicMock()
    con.execute.return_value.fetchall.return_value = [("ops_entity_counters",)]

    def merge_side_effect(_con, _tables, **_kwargs):
        raise RuntimeError("simulated merge failure")

    with (
        patch.object(h.rt, "fetch_dsn", return_value=_FULL_DSN),
        patch.object(h.rt, "open_connection", return_value=con),
        patch.object(h.rt, "load_field_semantics", return_value=_semantics()),
        patch.object(h.maint, "_count_files", return_value=1),
        patch.object(h.maint, "merge_adjacent_files", side_effect=merge_side_effect),
        patch.object(h, "_emit_maintenance_metric") as mock_emit,
    ):
        with pytest.raises(DuckLakeRuntimeError):
            h.action_merge_ops({"data_path": "s3://b/ducklake/", "meta_schema": "ducklake_ops"}, None)

    metric_calls = {c.args[0]: c.args[1] for c in mock_emit.call_args_list}
    assert metric_calls["MergeOpsPassFailed"] == 1.0
    assert metric_calls["MergeOpsUnclassifiedTables"] == 0.0


def test_merge_ops_response_carries_reconciliation_result():
    """VP2 third node -- the WIRING: reconcile_catalog runs inside action_merge_ops and its result
    is carried in the response (VP1's pure-predicate coverage cannot reach this)."""
    con = MagicMock()
    con.execute.return_value.fetchall.return_value = [
        ("ops_entity_counters",),
        ("ops_recommendations_current",),
        ("ops_recommendations_history",),
    ]

    with (
        patch.object(h.rt, "fetch_dsn", return_value=_FULL_DSN),
        patch.object(h.rt, "open_connection", return_value=con),
        patch.object(h.rt, "load_field_semantics", return_value=_semantics()),
        patch.object(h.maint, "_count_files", return_value=1),
        patch.object(h.maint, "merge_adjacent_files"),
        patch.object(h, "_emit_maintenance_metric"),
    ):
        result = h.action_merge_ops({"data_path": "s3://b/ducklake/", "meta_schema": "ducklake_ops"}, None)

    assert "reconciliation" in result
    assert set(result["reconciliation"]) == {"registered_absent", "catalog_unregistered"}
    absent_ids = {e["table_id"] for e in result["reconciliation"]["registered_absent"]}
    assert "ops_priority_queue" in absent_ids  # dormant, not physically present in this catalog
    assert result["reconciliation"]["catalog_unregistered"] == []
    assert result["skipped"] == []
