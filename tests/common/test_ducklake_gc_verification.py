"""Tests for src/common/ducklake_gc_verification.py (production-gc-and-storage-stability, T2.18).

referenced_missing and gc_debt_ratio are pure predicates. verify_read_path is the connection-owning
INDEPENDENT safety check -- three load-bearing fixtures:
  - over-reclaim: a live path absent from storage MUST trip referenced_missing.
  - shared-bug: a live-file function bug that corrupts BOTH the delete-set computation and
    referenced_missing identically (e.g. an s3:// vs s3a:// path-scheme mismatch) makes
    referenced_missing blind -- caught by verify_read_path ONLY, because it never touches the
    live-file path computation at all.
  - vacuity: catalog rows survive but backing Parquet is deleted -- a bare count(*) (answerable
    from catalog metadata alone) passes; only a value-scanning read (verify_read_path) fails.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.common import ducklake_gc_verification as gcver
from src.common import ducklake_maintenance as maint

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# referenced_missing -- SAFETY
# ---------------------------------------------------------------------------


def test_referenced_missing_detects_a_live_path_absent_from_storage():
    """Over-reclaim fixture: a live path absent from storage MUST trip."""
    live = {"s3://b/live0", "s3://b/live1"}
    storage = {"s3://b/live0"}
    assert gcver.referenced_missing(live, storage) == {"s3://b/live1"}


def test_referenced_missing_empty_when_every_live_path_is_present():
    live = {"s3://b/live0"}
    storage = {"s3://b/live0", "s3://b/orphan0"}  # an orphan is harmless, not referenced-missing
    assert gcver.referenced_missing(live, storage) == set()


def test_referenced_missing_is_the_l_minus_s_direction_only():
    """The reverse direction (a storage object the catalog does not track) is an ORPHAN, never
    computed by this function."""
    live = {"s3://b/live0"}
    storage = {"s3://b/live0", "s3://b/orphan0", "s3://b/orphan1"}
    assert gcver.referenced_missing(live, storage) == set()


# ---------------------------------------------------------------------------
# gc_debt_ratio -- EFFICACY
# ---------------------------------------------------------------------------


def test_gc_debt_ratio_computes_storage_minus_live_over_live():
    assert gcver.gc_debt_ratio(storage_bytes=1500, live_bytes=1000) == pytest.approx(0.5)


def test_gc_debt_ratio_at_the_bound_storage_equals_live():
    """Debt-ratio bound case: storage == live -- zero debt."""
    assert gcver.gc_debt_ratio(storage_bytes=1000, live_bytes=1000) == 0.0


def test_gc_debt_ratio_negative_when_storage_below_live():
    """A transient race (storage listed before a concurrent write lands) can read storage < live
    momentarily -- the ratio goes negative rather than raising; only live_bytes <= 0 is invalid."""
    assert gcver.gc_debt_ratio(storage_bytes=900, live_bytes=1000) == pytest.approx(-0.1)


def test_gc_debt_ratio_raises_on_non_positive_live_bytes():
    with pytest.raises(ValueError, match="live_bytes must be > 0"):
        gcver.gc_debt_ratio(storage_bytes=100, live_bytes=0)
    with pytest.raises(ValueError, match="live_bytes must be > 0"):
        gcver.gc_debt_ratio(storage_bytes=100, live_bytes=-5)


# ---------------------------------------------------------------------------
# verify_read_path (INDEPENDENT safety check)
# ---------------------------------------------------------------------------


class FakeCon:
    """Minimal connection double: returns a configurable (row_count, value_aggregate) per table."""

    def __init__(self, fetchone_map: dict[str, tuple[Any, ...]] | None = None, raise_for: set[str] | None = None):
        self.executed: list[str] = []
        self._fetchone_map = fetchone_map or {}
        self._raise_for = raise_for or set()
        self._last = ""

    def execute(self, sql: str, params: Any = None) -> "FakeCon":
        self.executed.append(sql)
        self._last = sql
        for table in self._raise_for:
            if f".{table} AS t AT" in sql:
                raise RuntimeError(f"simulated: backing Parquet object missing for table {table!r}")
        return self

    def fetchone(self) -> tuple[Any, ...]:
        for sub, val in self._fetchone_map.items():
            if sub in self._last:
                return val
        return (0, 0)


def test_verify_read_path_scans_every_table_at_the_pinned_snapshot():
    con = FakeCon(fetchone_map={".t1 AS t AT": (10, 111), ".t2 AS t AT": (5, 222)})
    result = gcver.verify_read_path(con, 42, ["t1", "t2"], catalog="cat")
    assert result["t1"] == {"row_count": 10, "value_aggregate": 111}
    assert result["t2"] == {"row_count": 5, "value_aggregate": 222}
    assert all("AT (VERSION => 42)" in s for s in con.executed)
    assert any("hash(" in s for s in con.executed), "must aggregate over column VALUES, never count(*)/min/max alone"


def test_verify_read_path_uses_one_pass_start_snapshot_id_for_every_table():
    """Snapshot identity is CATALOG-level: ONE id covers every table, never one id per table."""
    con = FakeCon()
    gcver.verify_read_path(con, 7, ["t1", "t2", "t3"], catalog="cat")
    assert all("VERSION => 7" in s for s in con.executed)


def test_verify_read_path_raises_when_a_table_scan_fails():
    con = FakeCon(raise_for={"t1"})
    with pytest.raises(maint.DuckLakeMaintenanceError, match="value-level read"):
        gcver.verify_read_path(con, 42, ["t1"], catalog="cat")


def test_verify_read_path_passes_through_an_existing_guard_error_unwrapped():
    class DirectMaintenanceErrorCon:
        def execute(self, sql: str, params: Any = None) -> "DirectMaintenanceErrorCon":
            raise maint.DuckLakeMaintenanceError("already a guard-set error")

    with pytest.raises(maint.DuckLakeMaintenanceError, match="already a guard-set error"):
        gcver.verify_read_path(DirectMaintenanceErrorCon(), 1, ["t1"], catalog="cat")


class VacuityCon:
    """VACUITY fixture: catalog rows survive (a bare count(*) succeeds, answerable from catalog
    metadata alone) but the backing Parquet is deleted -- any query that also touches column
    VALUES raises. This is the exact failure a count(*)-only or min/max-only read-path check
    cannot see."""

    def __init__(self) -> None:
        self.executed: list[str] = []

    def execute(self, sql: str, params: Any = None) -> "VacuityCon":
        self.executed.append(sql)
        if "hash(" in sql:
            raise RuntimeError("simulated: backing Parquet object missing from storage")
        return self

    def fetchone(self) -> tuple[Any, ...]:
        return (7,)  # a stale row count, answerable from catalog metadata alone


def test_vacuity_fixture_a_bare_count_passes_but_verify_read_path_catches_it():
    con = VacuityCon()
    naive_count = con.execute("SELECT count(*) FROM cat.t1").fetchone()
    assert naive_count == (7,), "a bare count(*) is fooled by surviving catalog rows -- the blind-oracle problem"

    with pytest.raises(maint.DuckLakeMaintenanceError, match="value-level read"):
        gcver.verify_read_path(con, 42, ["t1"], catalog="cat")


def test_shared_bug_fixture_referenced_missing_is_blind_but_read_path_catches_it():
    """SHARED-BUG fixture: a live-file function bug (e.g. an s3:// vs s3a:// path-scheme mismatch)
    corrupts BOTH the delete-set computation and referenced_missing's live-path collection
    identically, so referenced_missing sees them as matching -- a false negative wearing the
    costume of safety. verify_read_path never derives from that same live-file computation (it
    re-reads directly via the catalog snapshot id), so it still catches the real data loss.
    """
    corrupted_live = {"s3a://b/t1/file0.parquet"}
    corrupted_storage = {"s3a://b/t1/file0.parquet"}
    assert gcver.referenced_missing(corrupted_live, corrupted_storage) == set(), (
        "the shared corruption makes referenced_missing blind -- this demonstrates the trap the "
        "independent read-path check exists to close, not a defect in referenced_missing itself"
    )

    con = VacuityCon()
    with pytest.raises(maint.DuckLakeMaintenanceError, match="value-level read"):
        gcver.verify_read_path(con, 1, ["t1"], catalog="cat")
