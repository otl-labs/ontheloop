"""Tests for src/common/ducklake_gc_ops.py -- the production destructive-GC pass body
(production-gc-and-storage-stability, T2.18 c2).

TestGuardLiveSetIsUniversal is this plan's central correctness property: the live set G1/G3 and
referenced_missing are computed over is UNCONDITIONAL, taking no policy input at all, and is never
derived from scope.resolve_scope -- the destructive verbs are catalog-wide, so filtering the live
set would not narrow what gets deleted, only what the guards can SEE.

TestMatrixCarriesNoCatalogWideVerb is a never-weaken assertion in the opposite direction: it guards
against re-adding the inert maintenance_policy column an earlier draft of this plan proposed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.common import ducklake_gc_ops as gc_ops_mod
from src.common import ducklake_maintenance as maint
from src.common import ducklake_maintenance_scope as scope
from src.common.ducklake_scd2_schema import load_field_semantics

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]


class FakeCon:
    """Minimal connection double: records SQL; returns configurable results per substring."""

    def __init__(self, fetchall_map: dict[str, list[Any]] | None = None, fetchone_map: dict[str, Any] | None = None):
        self.executed: list[str] = []
        self._fetchall_map = fetchall_map or {}
        self._fetchone_map = fetchone_map or {}
        self._last = ""

    def execute(self, sql: str, params: Any = None) -> "FakeCon":
        self.executed.append(sql)
        self._last = sql
        return self

    def fetchall(self) -> list[Any]:
        for sub, val in self._fetchall_map.items():
            if sub in self._last:
                return val
        return []

    def fetchone(self) -> tuple[Any, ...]:
        for sub, val in self._fetchone_map.items():
            if sub in self._last:
                return val
        return (0,)


_NO_GUARD_STATS = {
    "snapshots_expired": 0,
    "files_cleaned": 0,
    "orphans_deleted": 0,
    "guard_stats": {
        "g2_snapshots_remaining": 2,
        "g4_deferred_files": 0,
        "g4_drain_cutoff_days": 7,
        "pre_expiry_would_delete_candidates": 0,
        "post_expiry_would_delete_candidates": 0,
    },
}


# ---------------------------------------------------------------------------
# Internal helpers: the two raise-on-empty-read branches (_discover_all_tables /
# _current_snapshot_id). S3 URI parsing / the storage lister relocated to
# tests/common/test_ducklake_maintenance_storage.py (both now live in ducklake_maintenance.py).
# ---------------------------------------------------------------------------


class TestInternalHelpers:
    def test_discover_all_tables_raises_when_catalog_is_empty(self) -> None:
        con = FakeCon(fetchall_map={"information_schema": []})
        with pytest.raises(maint.DuckLakeMaintenanceError, match="no tables discovered"):
            gc_ops_mod._discover_all_tables(con, "cat")

    def test_current_snapshot_id_raises_when_no_snapshot_row(self) -> None:
        con = FakeCon(fetchone_map={"ducklake_snapshots": None})
        with pytest.raises(maint.DuckLakeMaintenanceError, match="could not determine the pass-start snapshot id"):
            gc_ops_mod._current_snapshot_id(con, "cat")

    def test_current_snapshot_id_returns_the_latest_snapshot_id(self) -> None:
        con = FakeCon(fetchone_map={"ducklake_snapshots": (42,)})
        assert gc_ops_mod._current_snapshot_id(con, "cat") == 42


# ---------------------------------------------------------------------------
# TestGuardLiveSetIsUniversal -- the plan's central correctness property.
# ---------------------------------------------------------------------------


class TestGuardLiveSetIsUniversal:
    def test_live_set_collector_takes_no_policy_input(self) -> None:
        """The universal live-set collector is unreachable from any policy input: the module never
        imports ducklake_maintenance_scope, and the discovery query itself carries no naming
        filter or maintenance_policy read."""
        assert not hasattr(gc_ops_mod, "scope"), "ducklake_gc_ops must never import ducklake_maintenance_scope"

        con = FakeCon(fetchall_map={"information_schema": [("t1",), ("t2",)]})
        tables = gc_ops_mod._discover_all_tables(con, "cat")
        assert tables == ["t1", "t2"]
        discovery_sql = con.executed[0]
        assert "LIKE" not in discovery_sql
        assert "maintenance_policy" not in discovery_sql

    def test_policy_excluded_table_live_file_is_in_the_guard_live_set(self) -> None:
        """FIXTURE-ONLY: gc_ops has no policy parameter at all, so a table standing in for a
        hypothetically policy-excluded class (the shipped matrix carries NO gc_ops column --
        TestMatrixCarriesNoCatalogWideVerb pins that) still has its live files collected into the
        guard set. Asserted by call evidence, not by injecting a real policy object gc_ops has no
        slot for."""
        excluded_table = "ops_fixture_policy_excluded_class"

        def fake_collect_file_paths(_con: Any, _catalog: str, table: str) -> dict[str, int]:
            if table == excluded_table:
                return {"s3://b/excluded/file0.parquet": 100}
            return {"s3://b/normal/file0.parquet": 50}

        with (
            patch.object(gc_ops_mod, "_discover_all_tables", return_value=["ops_normal_table", excluded_table]),
            patch.object(gc_ops_mod, "_current_snapshot_id", return_value=1),
            patch.object(gc_ops_mod.maint, "_collect_file_paths", side_effect=fake_collect_file_paths) as mock_collect,
            patch.object(gc_ops_mod.maint, "_dry_run_cleanup_paths", return_value=[]),
            patch.object(gc_ops_mod.maint, "_dry_run_orphan_paths", return_value=[]),
            patch.object(
                gc_ops_mod,
                "_default_list_storage",
                return_value={"s3://b/excluded/file0.parquet": 100, "s3://b/normal/file0.parquet": 50},
            ),
        ):
            result = gc_ops_mod.gc_ops(MagicMock(), catalog="cat", data_path="s3://b/prefix/", dry_run=True)

        called_tables = {c.args[2] for c in mock_collect.call_args_list}
        assert excluded_table in called_tables, "the guard live set must include the fixture-excluded class's live files"
        assert result["referenced_missing"] == 0


# ---------------------------------------------------------------------------
# No prelude + read-ordering (F52) + live_before is the universal catalog set, never storage.
# ---------------------------------------------------------------------------


class TestNoPreludeAndReadOrdering:
    def test_catalog_live_set_collected_before_storage_is_listed(self) -> None:
        call_order: list[str] = []

        def fake_collect_file_paths(_con: Any, _catalog: str, _table: str) -> dict[str, int]:
            call_order.append("catalog_live_collect")
            return {"s3://b/t1/f0.parquet": 100}  # non-empty: rec-3876 raises on an empty live set

        def fake_list_storage(_data_path: str) -> dict[str, int]:
            call_order.append("storage_list")
            return {"s3://b/t1/f0.parquet": 100}

        with (
            patch.object(gc_ops_mod, "_discover_all_tables", return_value=["t1"]),
            patch.object(gc_ops_mod, "_current_snapshot_id", return_value=1),
            patch.object(gc_ops_mod.maint, "_collect_file_paths", side_effect=fake_collect_file_paths),
            patch.object(gc_ops_mod.maint, "_dry_run_cleanup_paths", return_value=[]),
            patch.object(gc_ops_mod.maint, "_dry_run_orphan_paths", return_value=[]),
        ):
            gc_ops_mod.gc_ops(MagicMock(), catalog="cat", data_path="s3://b/p/", dry_run=True, list_storage=fake_list_storage)

        assert "catalog_live_collect" in call_order and "storage_list" in call_order
        assert call_order.index("catalog_live_collect") < call_order.index("storage_list")

    def test_gc_ops_issues_no_flush_or_merge_prelude_call(self) -> None:
        with (
            patch.object(gc_ops_mod, "_discover_all_tables", return_value=["t1"]),
            patch.object(gc_ops_mod, "_current_snapshot_id", return_value=1),
            patch.object(gc_ops_mod.maint, "_collect_file_paths", return_value={"s3://b/t1/f0.parquet": 100}),
            patch.object(gc_ops_mod.maint, "_dry_run_cleanup_paths", return_value=[]),
            patch.object(gc_ops_mod.maint, "_dry_run_orphan_paths", return_value=[]),
            patch.object(gc_ops_mod.maint, "flush_inlined_data") as mock_flush,
            patch.object(gc_ops_mod.maint, "merge_adjacent_files") as mock_merge,
            patch.object(gc_ops_mod, "_default_list_storage", return_value={"s3://b/t1/f0.parquet": 100}),
        ):
            gc_ops_mod.gc_ops(MagicMock(), catalog="cat", data_path="s3://b/p/", dry_run=True)
        mock_flush.assert_not_called()
        mock_merge.assert_not_called()

    def test_live_before_passed_to_run_guarded_gc_is_the_universal_catalog_set_not_storage(self) -> None:
        live_paths = {"s3://b/t1/f0.parquet": 100}
        with (
            patch.object(gc_ops_mod, "_discover_all_tables", return_value=["t1"]),
            patch.object(gc_ops_mod, "_current_snapshot_id", return_value=1),
            patch.object(gc_ops_mod.maint, "_collect_file_paths", return_value=dict(live_paths)),
            patch.object(gc_ops_mod.maint, "_dry_run_cleanup_paths", return_value=[]),
            patch.object(gc_ops_mod.maint, "_dry_run_orphan_paths", return_value=[]),
            patch.object(gc_ops_mod.guard, "run_guarded_gc", return_value=_NO_GUARD_STATS) as mock_run_guarded,
            patch.object(gc_ops_mod, "_default_list_storage", return_value=dict(live_paths)),
            patch.object(gc_ops_mod.gcver, "verify_read_path", return_value={}),
        ):
            gc_ops_mod.gc_ops(MagicMock(), catalog="cat", data_path="s3://b/p/", dry_run=False)

        assert mock_run_guarded.call_args.kwargs["live_before"] == live_paths


# ---------------------------------------------------------------------------
# TestG4SizeSourceIsStorage -- the pre-pass storage listing is G4's size source (rec-3894),
# taken catalog-first/storage-second at BOTH read pairs (Decision 192 pt 3 / property 4). The
# designated replacement for the first-occurrence-only coverage TestNoPreludeAndReadOrdering loses
# once the lister is called twice.
# ---------------------------------------------------------------------------


class TestG4SizeSourceIsStorage:
    def test_candidate_size_source_is_the_storage_derived_map(self) -> None:
        storage_map = {"s3://b/t1/f0.parquet": 100, "s3://b/cleanup0": 4096}
        with (
            patch.object(gc_ops_mod, "_discover_all_tables", return_value=["t1"]),
            patch.object(gc_ops_mod, "_current_snapshot_id", return_value=1),
            patch.object(gc_ops_mod.maint, "_collect_file_paths", return_value={"s3://b/t1/f0.parquet": 100}),
            patch.object(gc_ops_mod.maint, "_dry_run_cleanup_paths", return_value=["s3://b/cleanup0"]),
            patch.object(gc_ops_mod.maint, "_dry_run_orphan_paths", return_value=[]),
            patch.object(gc_ops_mod.guard, "run_guarded_gc", return_value=_NO_GUARD_STATS) as mock_run_guarded,
            patch.object(gc_ops_mod, "_default_list_storage", return_value=dict(storage_map)),
            patch.object(gc_ops_mod.gcver, "verify_read_path", return_value={}),
        ):
            gc_ops_mod.gc_ops(MagicMock(), catalog="cat", data_path="s3://b/p/", dry_run=False)
        assert mock_run_guarded.call_args.kwargs["candidate_size_source"] == storage_map

    def test_would_delete_bytes_nonzero_for_a_candidate_present_only_in_storage(self) -> None:
        # cleanup0 is absent from the catalog live set entirely, present only in storage. The live
        # file's own path is also in storage (else referenced_missing would trip first).
        with (
            patch.object(gc_ops_mod, "_discover_all_tables", return_value=["t1"]),
            patch.object(gc_ops_mod, "_current_snapshot_id", return_value=1),
            patch.object(gc_ops_mod.maint, "_collect_file_paths", return_value={"s3://b/t1/f0.parquet": 100}),
            patch.object(gc_ops_mod.maint, "_dry_run_cleanup_paths", return_value=["s3://b/cleanup0"]),
            patch.object(gc_ops_mod.maint, "_dry_run_orphan_paths", return_value=[]),
            patch.object(
                gc_ops_mod, "_default_list_storage", return_value={"s3://b/cleanup0": 4096, "s3://b/t1/f0.parquet": 100}
            ),
        ):
            result = gc_ops_mod.gc_ops(MagicMock(), catalog="cat", data_path="s3://b/p/", dry_run=True)
        assert result["would_delete_bytes"] == 4096

    def test_pre_pass_listing_precedes_any_destructive_call_and_is_taken_in_dry_run_too(self) -> None:
        call_order: list[str] = []
        live = {"s3://b/t1/f0.parquet": 100}
        with (
            patch.object(gc_ops_mod, "_discover_all_tables", return_value=["t1"]),
            patch.object(gc_ops_mod, "_current_snapshot_id", return_value=1),
            patch.object(gc_ops_mod.maint, "_collect_file_paths", return_value=dict(live)),
            patch.object(gc_ops_mod.maint, "_dry_run_cleanup_paths", return_value=[]),
            patch.object(gc_ops_mod.maint, "_dry_run_orphan_paths", return_value=[]),
            patch.object(
                gc_ops_mod.guard,
                "run_guarded_gc",
                side_effect=lambda *a, **kw: (call_order.append("run_guarded_gc"), _NO_GUARD_STATS)[1],
            ),
            patch.object(
                gc_ops_mod, "_default_list_storage", side_effect=lambda _p: (call_order.append("storage_list"), dict(live))[1]
            ),
            patch.object(gc_ops_mod.gcver, "verify_read_path", return_value={}),
        ):
            for dry_run in (True, False):
                call_order.clear()
                gc_ops_mod.gc_ops(MagicMock(), catalog="cat", data_path="s3://b/p/", dry_run=dry_run)
                assert "storage_list" in call_order, f"dry_run={dry_run}: pre-pass listing must run even in dry_run"
                if not dry_run:
                    assert call_order.index("storage_list") < call_order.index("run_guarded_gc")

    def test_catalog_before_storage_at_both_read_pairs(self) -> None:
        call_order: list[str] = []
        live = {"s3://b/t1/f0.parquet": 100}
        with (
            patch.object(gc_ops_mod, "_discover_all_tables", return_value=["t1"]),
            patch.object(gc_ops_mod, "_current_snapshot_id", return_value=1),
            patch.object(
                gc_ops_mod.maint,
                "_collect_file_paths",
                side_effect=lambda *a, **kw: (call_order.append("catalog"), dict(live))[1],
            ),
            patch.object(gc_ops_mod.maint, "_dry_run_cleanup_paths", return_value=[]),
            patch.object(gc_ops_mod.maint, "_dry_run_orphan_paths", return_value=[]),
            patch.object(
                gc_ops_mod, "_default_list_storage", side_effect=lambda _p: (call_order.append("storage"), dict(live))[1]
            ),
        ):
            gc_ops_mod.gc_ops(MagicMock(), catalog="cat", data_path="s3://b/p/", dry_run=True)
        # Two read pairs: pre-pass (indices 0,1) and post-pass (indices 2,3); catalog first in both.
        assert call_order == ["catalog", "storage", "catalog", "storage"]

    def test_three_new_metrics_emit_and_would_delete_files_keeps_pre_expiry_meaning(self) -> None:
        captured: list[tuple[str, float]] = []
        guard_stats = {
            "snapshots_expired": 0,
            "files_cleaned": 0,
            "orphans_deleted": 0,
            "guard_stats": {
                "g2_snapshots_remaining": 2,
                "post_expiry_would_delete_candidates": 5,
                "g4_deferred_files": 3,
                "g4_drain_cutoff_days": None,
            },
        }
        with (
            patch.object(gc_ops_mod, "_discover_all_tables", return_value=["t1"]),
            patch.object(gc_ops_mod, "_current_snapshot_id", return_value=1),
            patch.object(gc_ops_mod.maint, "_collect_file_paths", return_value={"s3://b/t1/f0.parquet": 100}),
            patch.object(gc_ops_mod.maint, "_dry_run_cleanup_paths", return_value=["s3://b/cleanup0"]),
            patch.object(gc_ops_mod.maint, "_dry_run_orphan_paths", return_value=[]),
            patch.object(gc_ops_mod.guard, "run_guarded_gc", return_value=guard_stats),
            patch.object(
                gc_ops_mod, "_default_list_storage", return_value={"s3://b/cleanup0": 4096, "s3://b/t1/f0.parquet": 100}
            ),
            patch.object(gc_ops_mod.gcver, "verify_read_path", return_value={}),
        ):
            result = gc_ops_mod.gc_ops(
                MagicMock(),
                catalog="cat",
                data_path="s3://b/p/",
                dry_run=False,
                metric_sink=lambda n, v: captured.append((n, v)),
            )
        captured_map = dict(captured)
        assert captured_map["GcWouldDeleteFilesPostExpiry"] == 5.0
        assert captured_map["GcDeferredFiles"] == 3.0
        assert captured_map["GcDrainCutoffDays"] == -1.0  # sentinel: g4_drain_cutoff_days is None
        assert captured_map["GcWouldDeleteFiles"] == 1.0  # pre-expiry local probe, unchanged meaning
        assert result["would_delete_files"] == 1


class TestDryRunUnsizedCandidates:
    def test_dry_run_reports_unsized_candidates_rather_than_raising(self) -> None:
        with (
            patch.object(gc_ops_mod, "_discover_all_tables", return_value=["t1"]),
            patch.object(gc_ops_mod, "_current_snapshot_id", return_value=1),
            patch.object(gc_ops_mod.maint, "_collect_file_paths", return_value={"s3://b/t1/f0.parquet": 100}),
            patch.object(gc_ops_mod.maint, "_dry_run_cleanup_paths", return_value=["s3://b/unsized0", "s3://b/unsized1"]),
            patch.object(gc_ops_mod.maint, "_dry_run_orphan_paths", return_value=[]),
            # storage sizes the live path (so referenced_missing stays empty) but not the candidates
            patch.object(gc_ops_mod, "_default_list_storage", return_value={"s3://b/t1/f0.parquet": 100}),
        ):
            result = gc_ops_mod.gc_ops(MagicMock(), catalog="cat", data_path="s3://b/p/", dry_run=True)
        assert result["unsized_candidates"] == 2
        assert result["guard_stats"] is None

    def test_dry_run_reports_drain_probe_counts_as_a_days_to_files_bytes_map(self) -> None:
        with (
            patch.object(gc_ops_mod, "_discover_all_tables", return_value=["t1"]),
            patch.object(gc_ops_mod, "_current_snapshot_id", return_value=1),
            patch.object(gc_ops_mod.maint, "_collect_file_paths", return_value={"s3://b/t1/f0.parquet": 100}),
            patch.object(gc_ops_mod.maint, "_dry_run_cleanup_paths", return_value=["s3://b/cleanup0"]),
            patch.object(gc_ops_mod.maint, "_dry_run_orphan_paths", return_value=[]),
            patch.object(
                gc_ops_mod, "_default_list_storage", return_value={"s3://b/cleanup0": 512, "s3://b/t1/f0.parquet": 100}
            ),
        ):
            result = gc_ops_mod.gc_ops(MagicMock(), catalog="cat", data_path="s3://b/p/", dry_run=True)
        assert result["drain_probe_counts"]["7"] == {"files": 1, "bytes": 512}
        assert result["guard_stats"] is None

    def test_non_dry_run_leaves_drain_probe_counts_none(self) -> None:
        with (
            patch.object(gc_ops_mod, "_discover_all_tables", return_value=["t1"]),
            patch.object(gc_ops_mod, "_current_snapshot_id", return_value=1),
            patch.object(gc_ops_mod.maint, "_collect_file_paths", return_value={"s3://b/t1/f0.parquet": 100}),
            patch.object(gc_ops_mod.maint, "_dry_run_cleanup_paths", return_value=[]),
            patch.object(gc_ops_mod.maint, "_dry_run_orphan_paths", return_value=[]),
            patch.object(gc_ops_mod.guard, "run_guarded_gc", return_value=_NO_GUARD_STATS),
            patch.object(gc_ops_mod, "_default_list_storage", return_value={"s3://b/t1/f0.parquet": 100}),
            patch.object(gc_ops_mod.gcver, "verify_read_path", return_value={}),
        ):
            result = gc_ops_mod.gc_ops(MagicMock(), catalog="cat", data_path="s3://b/p/", dry_run=False)
        assert result["drain_probe_counts"] is None


# ---------------------------------------------------------------------------
# TestMetricEmission -- the full 9-metric set, GcReferencedMissing before any raise.
# ---------------------------------------------------------------------------


class TestMetricEmission:
    def test_emits_the_full_metric_set_with_referenced_missing_before_any_raise(self) -> None:
        captured: list[tuple[str, float]] = []

        def fake_metric_sink(name: str, value: float) -> None:
            captured.append((name, value))

        # Storage is EMPTY while the live set has one path -- forces referenced_missing non-empty,
        # so the pass raises AFTER emitting the metric set.
        with (
            patch.object(gc_ops_mod, "_discover_all_tables", return_value=["t1"]),
            patch.object(gc_ops_mod, "_current_snapshot_id", return_value=99),
            patch.object(gc_ops_mod.maint, "_collect_file_paths", return_value={"s3://b/t1/f0.parquet": 100}),
            patch.object(gc_ops_mod.maint, "_dry_run_cleanup_paths", return_value=[]),
            patch.object(gc_ops_mod.maint, "_dry_run_orphan_paths", return_value=[]),
            patch.object(gc_ops_mod.guard, "run_guarded_gc", return_value=_NO_GUARD_STATS),
            patch.object(gc_ops_mod, "_default_list_storage", return_value={}),
        ):
            with pytest.raises(maint.DuckLakeMaintenanceError, match="referenced_missing"):
                gc_ops_mod.gc_ops(
                    MagicMock(), catalog="cat", data_path="s3://b/p/", dry_run=False, metric_sink=fake_metric_sink
                )

        names = [n for n, _v in captured]
        for expected in (
            "GcWouldDeleteFiles",
            "GcSnapshotsRetainedMin",
            "GcDeletedSnapshots",
            "GcDeletedFiles",
            "GcDeletedOrphans",
            "GcReferencedMissing",
            "GcDebtRatio",
            "GcDebtBytes",
            "GcStorageObjects",
        ):
            assert expected in names, f"{expected} was never emitted before the raise"
        # GcReferencedMissing's mere presence here, given the raise fired, proves it was emitted
        # BEFORE the raise -- a metric_sink call after a raise is structurally impossible.
        assert "GcReferencedMissing" in names

    def test_dry_run_emits_referenced_missing_and_would_delete_files_only_and_writes_nothing(self) -> None:
        captured: list[tuple[str, float]] = []

        def fake_metric_sink(name: str, value: float) -> None:
            captured.append((name, value))

        with (
            patch.object(gc_ops_mod, "_discover_all_tables", return_value=["t1"]),
            patch.object(gc_ops_mod, "_current_snapshot_id", return_value=1),
            patch.object(gc_ops_mod.maint, "_collect_file_paths", return_value={"s3://b/t1/f0.parquet": 100}),
            patch.object(gc_ops_mod.maint, "_dry_run_cleanup_paths", return_value=["s3://b/t1/f0.parquet"]),
            patch.object(gc_ops_mod.maint, "_dry_run_orphan_paths", return_value=[]),
            patch.object(gc_ops_mod.guard, "run_guarded_gc") as mock_run_guarded,
            patch.object(gc_ops_mod, "_default_list_storage", return_value={"s3://b/t1/f0.parquet": 100}),
        ):
            result = gc_ops_mod.gc_ops(
                MagicMock(), catalog="cat", data_path="s3://b/p/", dry_run=True, metric_sink=fake_metric_sink
            )

        mock_run_guarded.assert_not_called()
        assert result["dry_run"] is True
        assert result["guard_stats"] is None
        assert result["read_path"] is None
        assert result["would_delete_files"] == 1
        names = [n for n, _v in captured]
        assert "GcWouldDeleteFiles" in names
        assert "GcReferencedMissing" in names

    def test_zero_live_bytes_raises_rather_than_reporting_debt_ratio_zero(self) -> None:
        """rec-3876: an empty post-pass live set must raise, per gc_debt_ratio's own documented
        contract, never silently report GcDebtRatio=0.0 (the substitution this closes)."""
        with (
            patch.object(gc_ops_mod, "_discover_all_tables", return_value=["t1"]),
            patch.object(gc_ops_mod, "_current_snapshot_id", return_value=1),
            patch.object(gc_ops_mod.maint, "_collect_file_paths", return_value={}),  # empty live set both reads
            patch.object(gc_ops_mod.maint, "_dry_run_cleanup_paths", return_value=[]),
            patch.object(gc_ops_mod.maint, "_dry_run_orphan_paths", return_value=[]),
            patch.object(gc_ops_mod.guard, "run_guarded_gc", return_value=_NO_GUARD_STATS),
            patch.object(gc_ops_mod, "_default_list_storage", return_value={}),
        ):
            with pytest.raises(maint.DuckLakeMaintenanceError, match="live_bytes must be > 0"):
                gc_ops_mod.gc_ops(MagicMock(), catalog="cat", data_path="s3://b/p/", dry_run=False)


# ---------------------------------------------------------------------------
# TestMatrixCarriesNoCatalogWideVerb -- never-weaken: no gc_ops cell, ever.
# ---------------------------------------------------------------------------


class TestMatrixCarriesNoCatalogWideVerb:
    def test_verb_universe_holds_only_per_table_scoped_verbs(self) -> None:
        assert scope.VERB_UNIVERSE == (
            "merge_ops",
            "reconcile_partitions",
            "rewrite_partition_layout",
            "rewrite_current_deletes",
        )
        assert "gc_ops" not in scope.VERB_UNIVERSE

    def test_matrix_carries_no_gc_ops_cell(self) -> None:
        semantics = load_field_semantics()
        policy = semantics["maintenance_policy"]
        assert policy, "maintenance_policy must not be empty"
        for table_class, verbs in policy.items():
            assert "gc_ops" not in verbs, f"class {table_class!r} carries a gc_ops cell -- catalog-wide verbs take no cell"

    def test_matrix_header_documents_the_catalog_wide_rule(self) -> None:
        text = (_REPO_ROOT / "config" / "lambda" / "ducklake" / "field_semantics.static.yaml").read_text(encoding="utf-8")
        assert "CATALOG-WIDE VERBS TAKE NO CELL" in text
