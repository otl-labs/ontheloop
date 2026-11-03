"""Tests for src/common/ducklake_maintenance_ops.py -- the G1-G4 fail-closed guard set (T2.18,
Decision 188 amending Decision 81 clause 6 / CD.33 H1).

Mocked-connection coverage for G1 (reachability), G2 (retention floor), G3 (catalog sanity), and
G4 (deletion bound) runs unconditionally. TestG4RealDryRunSizeColumn is the one real-engine anchor
(marked @pytest.mark.integration, network-gated): every other cleanup/orphan test in this repo
mocks the connection with a tuple double, so without this anchor G4's byte accounting could be
green on a mock and byte-blind in production -- reproducing rec-3773 under a new name.
"""

from __future__ import annotations

import functools
import inspect
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from src.common import ducklake_maintenance as maint
from src.common import ducklake_maintenance_ops as gcops

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 6, 7, 12, 0, 0, tzinfo=timezone.utc)

_OLDER_THAN_RE = re.compile(r"older_than=TIMESTAMPTZ '([^']+)'")


def _parse_older_than(sql: str) -> datetime:
    match = _OLDER_THAN_RE.search(sql)
    assert match, f"no older_than TIMESTAMPTZ literal found in: {sql}"
    return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S+00").replace(tzinfo=timezone.utc)


class FakeCon:
    """Minimal connection double: records SQL; returns configurable results per substring."""

    def __init__(self, fetchall_map: dict[str, list[Any]] | None = None, fetchone_map: dict[str, Any] | None = None):
        self.executed: list[str] = []
        self._fetchall_map: dict[str, list[Any]] = fetchall_map or {}
        self._fetchone_map: dict[str, Any] = fetchone_map or {}
        self._last = ""

    def execute(self, sql: str, params: Any = None) -> "FakeCon":
        self.executed.append(sql)
        self._last = sql
        return self

    def fetchone(self) -> tuple[Any, ...]:
        for sub, val in self._fetchone_map.items():
            if sub in self._last:
                return val
        return (0,)

    def fetchall(self) -> list[Any]:
        for sub, val in self._fetchall_map.items():
            if sub in self._last:  # a count(*) statement yields exactly one row, however it is fetched
                return [self.fetchone()] if self._last.lower().startswith("select count(*)") else val
        return []

    def close(self) -> None:
        pass


class RaisingCon:
    """Connection double whose execute() always raises -- fail-closed anchor (rec-3772)."""

    def execute(self, sql: str, params: Any = None) -> "RaisingCon":
        raise RuntimeError("catalog introspection failed")


def _guarded_gc_con(*, cleanup_paths: list[str] | None = None, orphan_paths: list[str] | None = None) -> FakeCon:
    """A GC-pass fixture where the live set and would-delete set are DISJOINT (the happy path)."""
    live_files = [("s3://b/live0", 100), ("s3://b/live1", 100)]
    cleanup_paths = cleanup_paths if cleanup_paths is not None else ["s3://b/cleanup0"]
    orphan_paths = orphan_paths if orphan_paths is not None else []
    return FakeCon(
        fetchall_map={
            "ducklake_list_files": live_files,
            "ducklake_cleanup_old_files": [(p,) for p in cleanup_paths],
            "ducklake_delete_orphaned_files": [(p,) for p in orphan_paths],
            "ducklake_snapshots": [(1, _NOW), (2, _NOW)],
            "ducklake_expire_snapshots": [],
        },
        fetchone_map={
            "ducklake_cleanup_old_files": (len(cleanup_paths),),
            "ducklake_delete_orphaned_files": (len(orphan_paths),),
            "ducklake_expire_snapshots": (0,),
            "ducklake_snapshots": (2,),
        },
    )


def _run(con: Any, size_source: dict[str, int] | None = None, **overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"catalog": "cat", "live_before": {}, "grace_days": 7, "retain_days": 30, "floor": 2, "now": _NOW}
    kwargs.update(overrides)
    return gcops.run_guarded_gc(con, ["t1"], candidate_size_source=size_source or {}, **kwargs)


# ---------------------------------------------------------------------------
# Fail-closed anchor (VP1): a catalog-introspection failure must raise
# DuckLakeMaintenanceError -- never a permissive all-clear result.
# ---------------------------------------------------------------------------


def test_guards_raise_when_catalog_introspection_fails():
    """rec-3772: the shipped check_gc_breaker was demonstrated returning breaker_tripped False
    with total_files 0 against a raising connection (2026-09-12). The guard set must instead raise."""
    with pytest.raises(maint.DuckLakeMaintenanceError, match="catalog introspection failed"):
        _run(RaisingCon())


class DirectMaintenanceErrorCon:
    """Connection double whose execute() raises DuckLakeMaintenanceError directly (not a generic
    exception) -- exercises the pass-through branch that never double-wraps an error that is
    already the guard set's own type."""

    def execute(self, sql: str, params: Any = None) -> "DirectMaintenanceErrorCon":
        raise maint.DuckLakeMaintenanceError("already a guard-set error")


def test_guard_input_collection_passes_through_an_existing_guard_error_unwrapped():
    with pytest.raises(maint.DuckLakeMaintenanceError, match="already a guard-set error"):
        _run(DirectMaintenanceErrorCon())


class RaisesOnlyOnG2RecheckCon:
    """Connection double: every call succeeds (empty catalog, floor-skip on expiry) except the G2
    fresh snapshot-count re-read, which raises -- exercises G2's own fail-closed wrapping,
    distinct from the fail-closed wrapping around the initial reachability-input collection."""

    def __init__(self) -> None:
        self._last = ""

    def execute(self, sql: str, params: Any = None) -> "RaisesOnlyOnG2RecheckCon":
        if sql.startswith("SELECT count(*) FROM ducklake_snapshots"):
            raise RuntimeError("connection dropped before G2 re-read")
        self._last = sql
        return self

    def fetchall(self) -> list[Any]:
        if "ducklake_snapshots" in self._last:
            return [(1, _NOW), (2, _NOW)]  # 2 snapshots, floor=2 -> expire_snapshots floor-skips
        return []

    def fetchone(self) -> tuple[Any, ...]:
        return (0,)


def test_g2_recheck_introspection_failure_wraps_as_guard_error():
    with pytest.raises(maint.DuckLakeMaintenanceError, match="could not re-read snapshot count for G2"):
        _run(RaisesOnlyOnG2RecheckCon())


class RaisesOnlyOnPostExpiryProbeCon:
    """Succeeds through expire_snapshots/G2; raises `error` on the post-expiry re-probe (the 2nd
    cleanup/orphan call -- the first 2 calls are the pre-expiry probe)."""

    def __init__(self, error: Exception | None = None) -> None:
        self._last = ""
        self._probe_calls = 0
        self._error = error or RuntimeError("connection dropped before the post-expiry re-probe")

    def execute(self, sql: str, params: Any = None) -> "RaisesOnlyOnPostExpiryProbeCon":
        self._probe_calls += "ducklake_cleanup_old_files" in sql or "ducklake_delete_orphaned_files" in sql
        if self._probe_calls > 2:
            raise self._error
        self._last = sql
        return self

    def fetchall(self) -> list[Any]:
        return [(1, _NOW), (2, _NOW)] if "ducklake_snapshots" in self._last else []  # floor=2 -> floor-skip

    def fetchone(self) -> tuple[Any, ...]:
        return (2,) if self._last.startswith("SELECT count(*) FROM ducklake_snapshots") else (0,)


def test_post_expiry_reprobe_introspection_failure_wraps_as_guard_error():
    with pytest.raises(maint.DuckLakeMaintenanceError, match="re-probing the post-expiry candidate set"):
        _run(RaisesOnlyOnPostExpiryProbeCon())


def test_post_expiry_reprobe_guard_error_passes_through_unwrapped():
    con = RaisesOnlyOnPostExpiryProbeCon(maint.DuckLakeMaintenanceError("already a guard-set error, post-expiry"))
    with pytest.raises(maint.DuckLakeMaintenanceError, match="already a guard-set error, post-expiry"):
        _run(con)


class RaisesOnlyOnPostPassLiveSetCon:
    """Connection double: every call succeeds -- including both destructive calls -- except the
    SECOND `ducklake_list_files` read (the post-destructive live-set collection feeding the G1
    re-check and G3), which raises. Exercises the fail-closed wrapping around that specific
    collection, distinct from the pre-pass collection and the G2 recheck (code-review finding:
    this call site had no try/except, so a raw exception here -- right after real deletions have
    run -- would propagate past run_gc's own callers unwrapped)."""

    def __init__(self) -> None:
        self._last = ""
        self._list_files_calls = 0

    def execute(self, sql: str, params: Any = None) -> "RaisesOnlyOnPostPassLiveSetCon":
        if "ducklake_list_files" in sql:
            self._list_files_calls += 1
            if self._list_files_calls > 1:
                raise RuntimeError("connection dropped before the post-pass live-set re-read")
        self._last = sql
        return self

    def fetchall(self) -> list[Any]:
        if "ducklake_list_files" in self._last:
            return [("s3://b/live0", 100), ("s3://b/live1", 100)]
        if "ducklake_snapshots" in self._last:
            return [(1, _NOW), (2, _NOW)]  # 2 snapshots, floor=2 -> expire_snapshots floor-skips
        return []

    def fetchone(self) -> tuple[Any, ...]:
        if "SELECT count(*) FROM ducklake_snapshots" in self._last:
            return (2,)  # G2 recheck: 2 remaining, at the floor -- must not raise here
        return (0,)


class RaisesGuardErrorOnPostPassLiveSetCon(RaisesOnlyOnPostPassLiveSetCon):
    """Same fixture, but the second `ducklake_list_files` read raises DuckLakeMaintenanceError
    directly -- exercises the post-pass collection's own pass-through branch (never double-wrap
    an error that is already the guard set's own type), mirroring
    test_guard_input_collection_passes_through_an_existing_guard_error_unwrapped for the
    pre-pass collection."""

    def execute(self, sql: str, params: Any = None) -> "RaisesGuardErrorOnPostPassLiveSetCon":
        if "ducklake_list_files" in sql:
            self._list_files_calls += 1
            if self._list_files_calls > 1:
                raise maint.DuckLakeMaintenanceError("already a guard-set error, post-pass")
        self._last = sql
        return self


def test_post_pass_live_set_guard_error_passes_through_unwrapped():
    with pytest.raises(maint.DuckLakeMaintenanceError, match="already a guard-set error, post-pass"):
        _run(RaisesGuardErrorOnPostPassLiveSetCon(), live_before={"s3://b/live0": 100, "s3://b/live1": 100})


def test_post_pass_live_set_introspection_failure_wraps_as_guard_error():
    with pytest.raises(maint.DuckLakeMaintenanceError, match="collecting the post-pass live set for the G1 re-check and G3"):
        _run(RaisesOnlyOnPostPassLiveSetCon(), live_before={"s3://b/live0": 100, "s3://b/live1": 100})


# ---------------------------------------------------------------------------
# G1 reachability
# ---------------------------------------------------------------------------


def test_g1_disjoint_sets_do_not_raise():
    gcops.assert_reachability({"a", "b"}, {"c", "d"})  # no raise


def test_g1_intersecting_sets_raise_naming_the_path():
    with pytest.raises(maint.DuckLakeMaintenanceError, match="G1 reachability"):
        gcops.assert_reachability({"a", "b"}, {"b", "c"})


def test_g1_force_conflict_trips_independent_of_empty_inputs():
    with pytest.raises(maint.DuckLakeMaintenanceError, match="G1 reachability"):
        gcops.assert_reachability(set(), set(), force_conflict=gcops.G1_PROBE_FORCE_CONFLICT)


def test_g1_probe_g1_trip_always_raises():
    with pytest.raises(maint.DuckLakeMaintenanceError, match="G1 reachability"):
        gcops.probe_g1_trip()


def test_g1_non_forcing_call_does_not_trip():
    """Red case for the forcing mechanism itself: with no force_conflict and disjoint sets, the
    guard must NOT raise -- proving the FORCING ARGUMENT (not an unconditional raise) is what
    trips the probe."""
    gcops.assert_reachability(set(), set())  # no raise


def test_g1_nonempty_intersection_via_run_guarded_gc_raises_and_issues_zero_destructive_sql():
    con = _guarded_gc_con(cleanup_paths=["s3://b/live0"])  # live0 is BOTH live and would-delete
    with pytest.raises(maint.DuckLakeMaintenanceError, match="G1 reachability"):
        _run(con, floor=1)
    assert not any("dry_run=False" in s for s in con.executed), "no destructive call may be issued after a G1 violation"


def test_g1_disjoint_sets_via_run_guarded_gc_do_not_raise():
    con = _guarded_gc_con()  # cleanup0 is disjoint from live0/live1
    result = _run(con, {"s3://b/cleanup0": 100}, floor=1)
    assert result["guard_stats"]["pre_expiry_would_delete_candidates"] == 1


# ---------------------------------------------------------------------------
# G2 retention floor
# ---------------------------------------------------------------------------


def test_g2_raises_when_post_expiry_count_below_floor():
    with pytest.raises(maint.DuckLakeMaintenanceError, match="G2 retention floor"):
        gcops.assert_retention_floor(1, floor=2)


def test_g2_passes_when_at_or_above_floor():
    gcops.assert_retention_floor(2, floor=2)  # no raise
    gcops.assert_retention_floor(5, floor=2)  # no raise


def test_g2_violation_via_run_guarded_gc_raises():
    con = _guarded_gc_con()
    con._fetchone_map["ducklake_snapshots"] = (1,)  # engine under-retained relative to floor=2
    with pytest.raises(maint.DuckLakeMaintenanceError, match="G2 retention floor"):
        _run(con)


# ---------------------------------------------------------------------------
# G3 catalog sanity
# ---------------------------------------------------------------------------


def test_g3_raises_on_empty_live_set():
    with pytest.raises(maint.DuckLakeMaintenanceError, match="G3 catalog sanity"):
        gcops.assert_catalog_sane(set(), live_bytes_before=1000, live_bytes_after=0)


def test_g3_raises_on_byte_drop_past_bound():
    with pytest.raises(maint.DuckLakeMaintenanceError, match="G3 catalog sanity"):
        gcops.assert_catalog_sane({"a"}, live_bytes_before=100 * 1024**3, live_bytes_after=0, max_byte_drop=10)


def test_g3_passes_within_bound():
    gcops.assert_catalog_sane({"a"}, live_bytes_before=1000, live_bytes_after=900)  # no raise


# ---------------------------------------------------------------------------
# G4 deletion bound
# ---------------------------------------------------------------------------


def test_g4_within_budget_proceeds():
    bound = gcops.bound_deletions({"a": 100, "b": 200}, max_files=10, max_bytes=10_000)
    assert bound.proceed is True
    assert bound.would_delete_files == 2
    assert bound.would_delete_bytes == 300
    assert bound.deferred_files == 0
    assert bound.deferred_bytes == 0


def test_g4_over_file_count_defers_whole_pass():
    candidates = {f"f{i}": 10 for i in range(5)}
    bound = gcops.bound_deletions(candidates, max_files=3, max_bytes=10_000)
    assert bound.proceed is False
    assert bound.deferred_files == 5
    assert bound.deferred_bytes == 50


def test_g4_over_byte_budget_defers_whole_pass():
    candidates = {"big": 1_000_000}
    bound = gcops.bound_deletions(candidates, max_files=10, max_bytes=1000)
    assert bound.proceed is False
    assert bound.deferred_bytes == 1_000_000


def test_g4_bounds_large_candidate_pass_reports_nonzero_deferred_remainder():
    """rec-3773: a 24,435-candidate pass, each with a real non-zero size, must report a non-zero
    deferred remainder AND non-zero would-delete bytes -- the exact property the retired breaker's
    dead byte budget could never demonstrate (it always summed to 0)."""
    candidates = {f"s3://b/f{i}": 1024 for i in range(24_435)}
    bound = gcops.bound_deletions(candidates, max_files=20_000, max_bytes=10 * 1024**3)
    assert bound.proceed is False
    assert bound.deferred_files == 24_435
    assert bound.deferred_bytes == 24_435 * 1024
    assert bound.would_delete_bytes > 0


def test_g4_defers_via_run_guarded_gc_skips_destructive_calls():
    """Construct a would-delete candidate set that exceeds G4's real file-count bound and confirm
    run_guarded_gc skips the destructive calls entirely while G1-G3 still pass.

    SCENARIO (rec-3888): this fixture returns the identical candidate set regardless of the
    older_than cutoff probed, so EVERY ladder rung is equally over budget -- the surviving
    wholesale-defer path: no cutoff at or above the grace floor admits a positive count.
    """
    cleanup_paths = [f"s3://b/cleanup{i}" for i in range(gcops.G4_MAX_DELETE_FILES + 1)]
    con = _guarded_gc_con(cleanup_paths=cleanup_paths)
    live_before = {"s3://b/live0": 100, "s3://b/live1": 100}  # matches the fixture's live set exactly
    result = _run(con, dict.fromkeys(cleanup_paths, 100), live_before=live_before, floor=1)
    assert result["guard_stats"]["g4_bounded"] is True
    assert result["guard_stats"]["g4_deferred_files"] == len(cleanup_paths)
    assert result["guard_stats"]["g4_drain_cutoff_days"] is None
    assert not any("dry_run=False" in s for s in con.executed), "an over-budget pass must defer, not partially delete"
    assert result["files_cleaned"] == 0
    assert result["orphans_deleted"] == 0


# ---------------------------------------------------------------------------
# TestG4ByteSource (rec-3871's named acceptance class)
# ---------------------------------------------------------------------------


class TestG4ByteSource:
    def test_size_candidates_sizes_a_path_absent_from_the_catalog_but_present_in_the_size_source(self):
        # A candidate absent from the catalog live set, present in the declared source: sizes NON-ZERO.
        sizes, unsized = gcops.size_candidates({"s3://b/cleanup0"}, {"s3://b/cleanup0": 4096}, strict_sizes=True)
        assert sizes == {"s3://b/cleanup0": 4096}
        assert unsized == 0

    def test_size_candidates_raises_naming_the_unsized_path_under_strict(self):
        with pytest.raises(maint.DuckLakeMaintenanceError, match="s3://b/unsized"):
            gcops.size_candidates({"s3://b/unsized"}, {}, strict_sizes=True)

    def test_size_candidates_returns_a_count_instead_of_raising_under_non_strict(self):
        # The falsifiability pair proving the ARGUMENT, not an unconditional raise, trips it.
        sizes, unsized = gcops.size_candidates({"s3://b/u0", "s3://b/u1"}, {}, strict_sizes=False)
        assert sizes == {"s3://b/u0": 0, "s3://b/u1": 0}
        assert unsized == 2

    def test_run_guarded_gc_sizes_from_the_declared_source_not_the_catalog_live_set(self):
        # cleanup0 is absent from live_before (the catalog set) entirely, yet sizes non-zero.
        result = _run(_guarded_gc_con(), {"s3://b/cleanup0": 12345}, floor=1)
        assert result["guard_stats"]["g4_would_delete_bytes"] == 12345

    def test_run_guarded_gc_always_calls_strict_and_exposes_no_strictness_parameter(self):
        # No destructive path can be non-strict (Decision 163): an unsized candidate must raise.
        with pytest.raises(maint.DuckLakeMaintenanceError, match="s3://b/cleanup0"):
            _run(_guarded_gc_con(), {}, floor=1)
        assert "strict_sizes" not in inspect.signature(gcops.run_guarded_gc).parameters


# ---------------------------------------------------------------------------
# TestG4PostExpiryBound
# ---------------------------------------------------------------------------


_SNAP_ROWS = [(1, _NOW), (2, _NOW)]  # 2 snapshots, floor=2 -> expire_snapshots floor-skips
_LIVE0 = [("s3://b/live0", 100)]


def _con(**kw: Any) -> "GuardedGcCon":
    kw.setdefault("live_files", _LIVE0)
    kw.setdefault("snapshot_rows", _SNAP_ROWS)
    kw.setdefault("snapshot_count_after", 2)
    return GuardedGcCon(**kw)


class GuardedGcCon:
    # Cleanup candidates: (path, timestamp) pairs filtered by a REAL older_than comparison (shrinks
    # as the cutoff ages, rec-3888), OR pre/post_expiry_cleanup lists keyed by call sequence (call 1
    # = pre-expiry, 2+ = post-expiry) when the two sets must differ independent of any cutoff.
    # raise_after_cleanup_calls/raise_error inject a failure at a specific cleanup-probe call.

    def __init__(
        self,
        *,
        live_files: list[tuple[str, int]],
        snapshot_rows: list[tuple[int, datetime]],
        snapshot_count_after: int,
        cleanup_candidates: list[tuple[str, datetime]] | None = None,
        orphan_candidates: list[tuple[str, datetime]] | None = None,
        pre_expiry_cleanup: list[str] | None = None,
        post_expiry_cleanup: list[str] | None = None,
        live_files_after: list[tuple[str, int]] | None = None,
        raise_after_cleanup_calls: int | None = None,
        raise_error: Exception | None = None,
    ) -> None:
        self.executed: list[str] = []
        self._live, self._live_after = live_files, (live_files if live_files_after is None else live_files_after)
        self._cleanup, self._orphan = cleanup_candidates, (orphan_candidates or [])
        self._pre, self._post = pre_expiry_cleanup, post_expiry_cleanup
        self._snap_rows, self._snap_count = snapshot_rows, snapshot_count_after
        self._list_calls = self._cleanup_calls = 0
        self._raise_after, self._raise_error = raise_after_cleanup_calls, raise_error
        self._last = ""

    def execute(self, sql: str, params: Any = None) -> "GuardedGcCon":
        self._list_calls += "ducklake_list_files" in sql
        self._cleanup_calls += "ducklake_cleanup_old_files" in sql
        if self._raise_after is not None and self._cleanup_calls > self._raise_after:
            raise self._raise_error
        self.executed.append(sql)
        self._last = sql
        return self

    def _cleanup_paths(self) -> list[str]:
        if self._cleanup is not None:
            cutoff = _parse_older_than(self._last)
            return [p for p, ts in self._cleanup if ts < cutoff]
        return self._pre if self._cleanup_calls <= 1 else self._post

    def _orphan_paths(self) -> list[str]:
        cutoff = _parse_older_than(self._last)
        return [p for p, ts in self._orphan if ts < cutoff]

    def fetchall(self) -> list[Any]:
        sql = self._last
        if sql.lstrip().lower().startswith("select count(*)"):
            return [self.fetchone()]  # a count(*) statement yields exactly one row, however it is fetched
        if "ducklake_list_files" in sql:
            return [(p, s) for p, s in (self._live if self._list_calls <= 1 else self._live_after)]
        if "ducklake_cleanup_old_files" in sql:
            return [(p,) for p in self._cleanup_paths()]
        if "ducklake_delete_orphaned_files" in sql:
            return [(p,) for p in self._orphan_paths()]
        if "ducklake_snapshots" in sql and "count(*)" not in sql:
            return self._snap_rows
        return []

    def fetchone(self) -> tuple[Any, ...]:
        sql = self._last
        if "SELECT count(*) FROM ducklake_snapshots" in sql:
            return (self._snap_count,)
        if "ducklake_cleanup_old_files" in sql:
            return (len(self._cleanup_paths()),)
        if "ducklake_delete_orphaned_files" in sql:
            return (len(self._orphan_paths()),)
        return (0,)

    def close(self) -> None:
        pass


class TestG4PostExpiryBound:
    def test_no_drain_case_pre_and_post_expiry_coincide(self):
        con = _con(pre_expiry_cleanup=["s3://b/cleanup0"], post_expiry_cleanup=["s3://b/cleanup0"])
        stats = _run(con, {"s3://b/cleanup0": 500})["guard_stats"]
        assert stats["pre_expiry_would_delete_candidates"] == 1
        assert stats["post_expiry_would_delete_candidates"] == 1
        assert stats["g4_would_delete_files"] == 1
        assert stats["g4_drain_cutoff_days"] == 7

    def test_g1_precheck_and_g4_decision_evaluate_the_post_expiry_set_which_differs_from_pre_expiry(self):
        # Expiry reveals cleanup1, not a pre-expiry candidate: G4 must decide on the LARGER post-set.
        con = _con(pre_expiry_cleanup=["s3://b/cleanup0"], post_expiry_cleanup=["s3://b/cleanup0", "s3://b/cleanup1"])
        stats = _run(con, {"s3://b/cleanup0": 500, "s3://b/cleanup1": 700})["guard_stats"]
        assert stats["pre_expiry_would_delete_candidates"] == 1
        assert stats["post_expiry_would_delete_candidates"] == 2
        assert stats["g4_would_delete_files"] == 2
        assert stats["g4_would_delete_bytes"] == 1200

    def test_g1_precheck_catches_a_conflict_only_visible_post_expiry(self):
        # A pre-expiry-only G1 check (empty pre-set) would miss this and delete a live file.
        con = _con(live_files=[("s3://b/cleanup0", 100)], pre_expiry_cleanup=[], post_expiry_cleanup=["s3://b/cleanup0"])
        with pytest.raises(maint.DuckLakeMaintenanceError, match="G1 reachability"):
            _run(con, {"s3://b/cleanup0": 500})

    def test_post_destructive_recheck_uses_admitted_set_not_full_post_expiry_set(self, monkeypatch: pytest.MonkeyPatch):
        # deferred0 becomes live only POST-pass (e.g. a write landed mid-pass); it was correctly
        # left undeleted, so checking the full post-expiry set here would spuriously raise.
        monkeypatch.setattr(gcops, "G4_MAX_DELETE_FILES", 1)
        admitted0, deferred0 = ("s3://b/admitted0", _NOW - timedelta(days=40)), ("s3://b/deferred0", _NOW - timedelta(days=10))
        con = _con(cleanup_candidates=[admitted0, deferred0], live_files=[], live_files_after=[("s3://b/deferred0", 50)])
        stats = _run(con, {"s3://b/admitted0": 100, "s3://b/deferred0": 100})["guard_stats"]
        assert stats["g4_would_delete_files"] == 1  # admitted0 only
        assert stats["g4_deferred_files"] == 1  # deferred0 correctly left alone


# ---------------------------------------------------------------------------
# TestG4PartialDrain (rec-3888's own graduation slot)
# ---------------------------------------------------------------------------


class TestG4PartialDrain:
    def test_drain_selects_youngest_fitting_cutoff_admitting_more_than_the_oldest_fitting_rung(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        # Three age tiers bracket the (7, 14] ladder gap: 10@10d (qualify only 7-9), 3@12d (7-11),
        # 2@20d (7-19). The oldest-fitting rung (14) would admit only the 2 age-20 files -- the
        # anti-pattern rec-3871 named. The bisected youngest fit (day 10) admits 5, strictly more.
        monkeypatch.setattr(gcops, "G4_MAX_DELETE_FILES", 5)
        candidates = (
            [(f"s3://b/age10-{i}", _NOW - timedelta(days=10)) for i in range(10)]
            + [(f"s3://b/age12-{i}", _NOW - timedelta(days=12)) for i in range(3)]
            + [(f"s3://b/age20-{i}", _NOW - timedelta(days=20)) for i in range(2)]
        )
        con = _con(cleanup_candidates=candidates)
        result = _run(con, {p: 10 for p, _ts in candidates})
        stats = result["guard_stats"]
        assert stats["post_expiry_would_delete_candidates"] == 15
        assert stats["g4_drain_cutoff_days"] == 10
        assert stats["g4_would_delete_files"] == 5  # age12 + age20 -- more than the 2 an oldest-fitting rung admits
        assert stats["g4_deferred_files"] == 10
        assert stats["g4_bounded"] is True
        assert result["files_cleaned"] + result["orphans_deleted"] == 5
        for sql in con.executed:  # never crosses the grace floor (Decision 55)
            if "older_than=TIMESTAMPTZ" in sql:
                assert _parse_older_than(sql) <= _NOW - timedelta(days=7)

    def test_zero_admitting_cutoff_is_not_a_drain_defers_wholesale(self, monkeypatch: pytest.MonkeyPatch):
        # Clustered at age 20d: count(7)==count(14)==6 (over cap 5), count(30)==0 (fits trivially).
        # A bare ladder walk would select rung 30 and report a false drain of zero files.
        monkeypatch.setattr(gcops, "G4_MAX_DELETE_FILES", 5)
        candidates = [(f"s3://b/c{i}", _NOW - timedelta(days=20)) for i in range(6)]
        result = _run(_con(cleanup_candidates=candidates), {p: 10 for p, _ts in candidates})
        stats = result["guard_stats"]
        assert stats["g4_drain_cutoff_days"] is None
        assert stats["g4_bounded"] is True
        assert stats["g4_would_delete_files"] == 0
        assert stats["g4_deferred_files"] == 6
        assert result["files_cleaned"] == 0
        assert result["orphans_deleted"] == 0

    def test_successive_passes_strictly_shrink_a_simulated_backlog(self, monkeypatch: pytest.MonkeyPatch):
        # Run the drain twice, removing whatever pass 1 admitted from pass 2's pool (simulating
        # real deletion): the property a whole-pass defer could never satisfy.
        monkeypatch.setattr(gcops, "G4_MAX_DELETE_FILES", 15)
        tier_a = [(f"s3://b/a{i}", _NOW - timedelta(days=10)) for i in range(10)]  # qualifies only 7-9
        tier_b = [(f"s3://b/b{i}", _NOW - timedelta(days=20)) for i in range(8)]  # qualifies 7-19
        tier_c = [(f"s3://b/c{i}", _NOW - timedelta(days=40)) for i in range(4)]  # qualifies 7-39
        backlog = tier_a + tier_b + tier_c
        size_source = {p: 10 for p, _ts in backlog}

        admitted1 = _run(_con(cleanup_candidates=backlog), size_source)["guard_stats"]["g4_would_delete_files"]
        remaining_after_1 = len(backlog) - admitted1
        assert 0 < admitted1 < len(backlog), "a real drain must admit a positive but partial count"

        # tier_b/tier_c (age >= 20d) were the deletable-by-cutoff-10 subset, per the pass-1 trace.
        remaining_backlog = tier_a
        assert len(remaining_backlog) == remaining_after_1
        admitted2 = _run(_con(cleanup_candidates=remaining_backlog), size_source)["guard_stats"]["g4_would_delete_files"]
        remaining_after_2 = remaining_after_1 - admitted2

        assert remaining_after_1 < len(backlog)
        assert remaining_after_2 < remaining_after_1

    @pytest.mark.parametrize(
        ("error", "match"),
        [
            (RuntimeError("dropped"), "catalog introspection failed during the drain walk"),
            (maint.DuckLakeMaintenanceError("already a guard-set error, drain walk"), "already a guard-set error, drain walk"),
        ],
    )
    def test_drain_walk_introspection_failure(self, monkeypatch: pytest.MonkeyPatch, error: Exception, match: str):
        # cleanup calls: #1 pre-expiry probe, #2 post-expiry re-probe (both must succeed), #3 the
        # drain walk's own first re-probe -- that is where this must raise.
        monkeypatch.setattr(gcops, "G4_MAX_DELETE_FILES", 1)
        candidates = [(f"s3://b/c{i}", _NOW - timedelta(days=20)) for i in range(2)]
        con = _con(cleanup_candidates=candidates, raise_after_cleanup_calls=2, raise_error=error)
        with pytest.raises(maint.DuckLakeMaintenanceError, match=match):
            _run(con, {p: 10 for p, _ts in candidates}, floor=1)


@functools.lru_cache(maxsize=1)
def _has_ducklake_extension() -> bool:
    """Return True if the ducklake extension is available (installed or fetchable over network)."""
    try:
        import duckdb

        con = duckdb.connect()
        con.execute("INSTALL ducklake; LOAD ducklake")
        con.close()
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.integration
class TestG4RealDryRunSizeColumn:
    """G4's byte accounting must come from a REAL pre-merge size snapshot joined against a REAL
    dry-run candidate list, on the pinned DuckLake engine -- not a FakeCon tuple mock. Every other
    cleanup/orphan test in this repo mocks the connection, so without this anchor G4 could be green
    on a mock and byte-blind in production (rec-3773 under a new name, Decision 187 pt 2)."""

    @pytest.fixture(autouse=True)
    def _skip_if_no_ducklake_extension(self, _allow_network_for_integration: None) -> None:
        if not _has_ducklake_extension():
            pytest.skip("ducklake extension not available over the network")

    def test_g4_reads_real_dry_run_size_column_on_pinned_engine(self, tmp_path: Any) -> None:
        import duckdb

        catalog_path = tmp_path / "catalog.ducklake"
        data_path = tmp_path / "data"
        data_path.mkdir()

        con = duckdb.connect(":memory:")
        try:
            con.execute("INSTALL ducklake")
            con.execute("LOAD ducklake")
            con.execute(f"ATTACH 'ducklake:{catalog_path}' AS lk (DATA_PATH '{data_path}', DATA_INLINING_ROW_LIMIT 0)")
            con.execute("USE lk")
            con.execute("CREATE TABLE t1 (id INTEGER, val VARCHAR)")
            con.execute("INSERT INTO t1 SELECT range, 'v' || range FROM range(1000)")
            con.execute("INSERT INTO t1 SELECT range, 'v' || range FROM range(1000, 2000)")

            catalog = "lk"
            table = "t1"

            # Pre-pass size inventory -- BEFORE merge supersedes the two small files. This is the
            # exact fix: the retired breaker joined would-delete paths against a POST-merge
            # inventory, where a just-superseded path is always absent (rec-3773's dead byte budget).
            live_before = maint._collect_file_paths(con, catalog, table)
            assert live_before, "expected at least one live file before merge"

            maint.merge_adjacent_files(con, [table], catalog=catalog)

            now = datetime.now(timezone.utc) + timedelta(seconds=1)
            maint.expire_snapshots(con, catalog=catalog, retain_days=0, floor=1, _now=now)

            candidate_paths = set(
                maint._dry_run_cleanup_paths(con, catalog, now) + maint._dry_run_orphan_paths(con, catalog, now)
            )
            assert candidate_paths, "expected the two pre-merge files to be cleanup/orphan candidates"

            candidate_bytes = {p: live_before.get(p, 0) for p in candidate_paths}
            bound = gcops.bound_deletions(candidate_bytes, max_files=100, max_bytes=10 * 1024**3)

            assert bound.would_delete_bytes > 0, (
                "G4 byte accounting is dead (rec-3773): joining candidate paths against a "
                "PRE-merge size snapshot must yield a non-zero total on the real engine"
            )
        finally:
            con.close()
