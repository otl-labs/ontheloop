"""merge_ops wiring for the current-table compaction step (PLAN-ops-current-table-compaction): compaction runs
before the merge loop; a recorded failure or refusal still merges every table and fails the pass; a proof failure
merges nothing and maps to the breaker 500."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import src.lambdas.ducklake_maintenance.handler as h
from src.common import ducklake_maintenance_scope as scope_mod
from src.common.ducklake_current_compaction import CurrentCompactionProofError
from src.common.ducklake_runtime import DuckLakeRuntimeError
from src.common.ducklake_scd2_schema import load_field_semantics
from tests.fixtures.ducklake_maintenance_handler import _FULL_DSN

pytestmark = pytest.mark.unit

EVENT = {"data_path": "s3://b/ducklake/", "meta_schema": "ducklake_ops"}
CLEAN = {"failures": [], "refused": [], "per_table": [], "skipped": [], "deferred": []}


def _run(compaction: Any, *, discovered: list[str] | None = None, call: Any = None) -> dict[str, Any]:
    semantics = load_field_semantics()
    names = discovered if discovered is not None else sorted(scope_mod.build_registry(semantics))
    con = MagicMock()
    con.execute.return_value.fetchall.return_value = [(n,) for n in names]
    order: list[str] = []
    merged: list[str] = []
    emitted: list[tuple[str, float]] = []

    def merge(_con: Any, tables: list[str], **_k: Any) -> None:
        order.append("merge")
        merged.extend(tables)

    def compact(*a: Any, **k: Any) -> Any:
        order.append("compact")
        if isinstance(compaction, Exception):
            raise compaction
        return compaction

    patches = [
        patch.object(h.rt, "fetch_dsn", return_value=_FULL_DSN),
        patch.object(h.rt, "open_connection", return_value=con),
        patch.object(h.rt, "load_field_semantics", return_value=semantics),
        patch.object(h.maint, "_count_files", return_value=1),
        patch.object(h.maint, "merge_adjacent_files", side_effect=merge),
        patch.object(h, "_emit_maintenance_metric", side_effect=lambda n, v, **k: emitted.append((n, v))),
    ]
    if compaction is not None:
        patches.append(patch.object(h.current_compaction, "compact_current_tables", side_effect=compact))
    for p in patches:
        p.start()
    try:
        outcome: dict[str, Any] = {"order": order, "merged": merged}
        try:
            outcome["result"] = (call or h.action_merge_ops)({"action": "merge_ops", **EVENT}, None)
        except Exception as exc:  # noqa: BLE001
            outcome["error"] = exc
        outcome["emitted"] = dict(emitted)
        return outcome
    finally:
        for p in reversed(patches):
            p.stop()


def test_compaction_runs_before_merge() -> None:
    out = _run({**CLEAN, "meta_schema": "ducklake_ops"})
    assert out["order"][0] == "compact" and "merge" in out["order"]
    assert out["result"]["compaction"]["meta_schema"] == "ducklake_ops"
    assert out["emitted"]["MergeOpsPassFailed"] == 0.0


def test_transient_failure_still_merges_every_table() -> None:
    out = _run({**CLEAN, "failures": [{"table": "ops_decisions_current", "error_class": "RuntimeError"}]})
    assert isinstance(out["error"], DuckLakeRuntimeError) and "ops_decisions_current" in str(out["error"])
    assert "ops_decisions_current" in out["merged"] and "ops_recommendations_current" in out["merged"]
    assert out["emitted"]["MergeOpsPassFailed"] == 1.0


def test_refusal_still_merges_and_fails_pass() -> None:
    out = _run({**CLEAN, "refused": [{"table": "ops_recommendations_current", "legacy_scheme_files": 3}]})
    assert isinstance(out["error"], DuckLakeRuntimeError) and "legacy_scheme_files" in str(out["error"])
    assert "ops_recommendations_current" in out["merged"]
    assert out["emitted"]["MergeOpsPassFailed"] == 1.0


def test_proof_failure_merges_nothing_and_trips_breaker() -> None:
    out = _run(CurrentCompactionProofError("snapshot 7 differs from 6"), call=h.handler)
    assert out["merged"] == []
    assert out["result"]["statusCode"] == 500
    body = json.loads(out["result"]["body"])
    assert body["error_type"] == "breaker" and body["breaker_tripped"] is True
    assert out["emitted"]["MaintenanceBreakerTrip"] == 1.0


def test_unclassified_table_with_real_compaction_still_merges() -> None:
    semantics = load_field_semantics()
    names = [*sorted(scope_mod.build_registry(semantics)), "a_stray_table"]
    out = _run(None, discovered=names)
    assert isinstance(out["error"], DuckLakeRuntimeError)
    assert "a_stray_table" in str(out["error"]) and "PartitionLayoutError" in str(out["error"])
    assert "ops_recommendations_current" in out["merged"] and "ducklake_smoke_current" in out["merged"]
    assert out["emitted"]["CurrentCompactionFailures"] == 1.0
