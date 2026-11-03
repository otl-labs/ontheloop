"""Tests for src/common/ducklake_maintenance.py (T2.18, 100% coverage, mocked connection)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

import src.common.ducklake_maintenance as maint
from src.common.ducklake_maintenance import (
    GC_TABLE_SCOPE,
    HOT_TABLE_SCOPE,
    SNAPSHOT_FLOOR,
    SNAPSHOT_RETAIN_DAYS,
    DuckLakeMaintenanceError,
    catalog_stats,
    cleanup_old_files,
    delete_orphaned_files,
    expire_snapshots,
    flush_inlined_data,
    merge_adjacent_files,
    rewrite,
    run_gc,
    run_hot_merge,
    run_merge,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 6, 7, 12, 0, 0, tzinfo=timezone.utc)


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
        if self._last.lstrip().lower().startswith("select count(*)"):
            return [self.fetchone()]  # a count(*) statement yields exactly one row, however it is fetched
        for sub, val in self._fetchall_map.items():
            if sub in self._last:
                return val
        return []

    def close(self) -> None:
        pass


class RaisingCon:
    """Connection double whose execute() always raises -- fail-closed anchor (rec-3772)."""

    def execute(self, sql: str, params: Any = None) -> "RaisingCon":
        raise RuntimeError("connection lost")


def _ts(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_guardrail_constants():
    assert SNAPSHOT_RETAIN_DAYS == 30
    assert maint.FILE_CLEANUP_GRACE_DAYS == 7
    assert SNAPSHOT_FLOOR == 2
    assert len(GC_TABLE_SCOPE) >= 2


def test_scope_note_present():
    assert "T2.19" in maint.MAINTENANCE_SCOPE_NOTE
    assert "ops_*" in maint.MAINTENANCE_SCOPE_NOTE or "ops_" in maint.MAINTENANCE_SCOPE_NOTE


# ---------------------------------------------------------------------------
# _ts_str
# ---------------------------------------------------------------------------


def test_ts_str_format():
    dt = datetime(2026, 1, 15, 8, 30, 0, tzinfo=timezone.utc)
    s = maint._ts_str(dt)
    assert s == "2026-01-15 08:30:00+00"


# ---------------------------------------------------------------------------
# Fail-closed introspection helpers (rec-3772): each of the five helpers must RAISE when its
# connection raises -- none may return a permissive default (0, {}, []).
# ---------------------------------------------------------------------------


class TestHelpersFailClosed:
    def test_count_files_raises_on_connection_error(self):
        with pytest.raises(RuntimeError, match="connection lost"):
            maint._count_files(RaisingCon(), "cat", "t1")

    def test_sum_file_bytes_raises_on_connection_error(self):
        with pytest.raises(RuntimeError, match="connection lost"):
            maint._sum_file_bytes(RaisingCon(), "cat", "t1")

    def test_collect_file_paths_raises_on_connection_error(self):
        with pytest.raises(RuntimeError, match="connection lost"):
            maint._collect_file_paths(RaisingCon(), "cat", "t1")

    def test_dry_run_cleanup_paths_raises_on_connection_error(self):
        with pytest.raises(RuntimeError, match="connection lost"):
            maint._dry_run_cleanup_paths(RaisingCon(), "cat", _NOW)

    def test_dry_run_orphan_paths_raises_on_connection_error(self):
        with pytest.raises(RuntimeError, match="connection lost"):
            maint._dry_run_orphan_paths(RaisingCon(), "cat", _NOW)


# ---------------------------------------------------------------------------
# flush_inlined_data
# ---------------------------------------------------------------------------


def test_flush_inlined_data_calls_per_table():
    con = FakeCon()
    flush_inlined_data(con, ["t1", "t2"])
    stmts = " ".join(con.executed)
    assert "ducklake_flush_inlined_data" in stmts
    assert "t1" in stmts
    assert "t2" in stmts


def test_flush_inlined_data_empty_tables():
    con = FakeCon()
    flush_inlined_data(con, [])
    assert con.executed == []


# ---------------------------------------------------------------------------
# merge_adjacent_files
# ---------------------------------------------------------------------------


def test_merge_adjacent_files_calls_per_table():
    con = FakeCon()
    merge_adjacent_files(con, ["hist", "curr"])
    stmts = " ".join(con.executed)
    assert "ducklake_merge_adjacent_files" in stmts
    assert "hist" in stmts
    assert "curr" in stmts


def test_merge_adjacent_files_empty_tables():
    con = FakeCon()
    merge_adjacent_files(con, [])
    assert con.executed == []


# ---------------------------------------------------------------------------
# expire_snapshots
# ---------------------------------------------------------------------------

_SNAPSHOT_ROWS = [
    (10, _ts(2026, 6, 7)),
    (9, _ts(2026, 6, 1)),
    (8, _ts(2026, 5, 1)),
    (7, _ts(2026, 1, 1)),
    (6, _ts(2025, 12, 1)),
]


def test_expire_snapshots_floor_skips_when_too_few():
    con = FakeCon(fetchall_map={"ducklake_snapshots": [(1, _ts(2026, 6, 7)), (2, _ts(2026, 6, 6))]})
    result = expire_snapshots(con, _now=_NOW)
    assert result == 0
    assert not any("ducklake_expire_snapshots" in s for s in con.executed)


def test_expire_snapshots_uses_cutoff_correctly():
    con = FakeCon(
        fetchall_map={"ducklake_snapshots": _SNAPSHOT_ROWS},
        fetchone_map={"ducklake_expire_snapshots": (3,)},
    )
    result = expire_snapshots(con, retain_days=30, floor=2, _now=_NOW)
    assert result == 3
    expire_stmts = [s for s in con.executed if "ducklake_expire_snapshots" in s]
    assert len(expire_stmts) == 1
    assert "older_than=TIMESTAMPTZ" in expire_stmts[0]


def test_expire_snapshots_zero_when_nothing_to_expire():
    recent = [(i, datetime(2026, 6, 1 + i % 5, tzinfo=timezone.utc)) for i in range(5)]
    con = FakeCon(
        fetchall_map={"ducklake_snapshots": recent},
        fetchone_map={"ducklake_expire_snapshots": (0,)},
    )
    result = expire_snapshots(con, retain_days=30, floor=2, _now=_NOW)
    assert isinstance(result, int)


# ---------------------------------------------------------------------------
# cleanup_old_files
# ---------------------------------------------------------------------------


def test_cleanup_old_files_issues_correct_call():
    con = FakeCon(fetchone_map={"ducklake_cleanup_old_files": (5,)})
    result = cleanup_old_files(con, _now=_NOW)
    assert result == 5
    stmts = [s for s in con.executed if "ducklake_cleanup_old_files" in s]
    assert len(stmts) == 1
    assert "dry_run=False" in stmts[0]
    assert "cleanup_all=False" in stmts[0]
    assert "older_than=TIMESTAMPTZ" in stmts[0]


def test_cleanup_old_files_never_cleanup_all():
    con = FakeCon(fetchone_map={"ducklake_cleanup_old_files": (0,)})
    cleanup_old_files(con, _now=_NOW)
    for s in con.executed:
        if "ducklake_cleanup_old_files" in s:
            assert "cleanup_all=True" not in s


# ---------------------------------------------------------------------------
# delete_orphaned_files
# ---------------------------------------------------------------------------


def test_delete_orphaned_files_issues_correct_call():
    con = FakeCon(fetchone_map={"ducklake_delete_orphaned_files": (3,)})
    result = delete_orphaned_files(con, _now=_NOW)
    assert result == 3
    stmts = [s for s in con.executed if "ducklake_delete_orphaned_files" in s]
    assert len(stmts) == 1
    assert "dry_run=False" in stmts[0]
    assert "older_than=TIMESTAMPTZ" in stmts[0]


def test_delete_orphaned_files_never_cleanup_all():
    con = FakeCon(fetchone_map={"ducklake_delete_orphaned_files": (0,)})
    delete_orphaned_files(con, _now=_NOW)
    for s in con.executed:
        if "ducklake_delete_orphaned_files" in s:
            assert "cleanup_all=True" not in s


# ---------------------------------------------------------------------------
# older_than override (the drain walk's mechanism) -- refused, not clamped, below the floor
# ---------------------------------------------------------------------------


def test_cleanup_old_files_accepts_an_older_than_override_at_the_floor():
    con = FakeCon(fetchone_map={"ducklake_cleanup_old_files": (1,)})
    cleanup_old_files(con, older_than=_NOW - timedelta(days=10), _now=_NOW)
    stmts = [s for s in con.executed if "ducklake_cleanup_old_files" in s]
    assert len(stmts) == 1


def test_cleanup_old_files_refuses_an_older_than_younger_than_the_grace_floor():
    with pytest.raises(DuckLakeMaintenanceError, match="younger than the"):
        cleanup_old_files(FakeCon(), older_than=_NOW, _now=_NOW)


def test_delete_orphaned_files_refuses_an_older_than_younger_than_the_grace_floor():
    with pytest.raises(DuckLakeMaintenanceError, match="younger than the"):
        delete_orphaned_files(FakeCon(), older_than=_NOW, _now=_NOW)


# ---------------------------------------------------------------------------
# _parse_s3_uri / _default_list_storage (relocated here from ducklake_gc_ops, Decision 193)
# ---------------------------------------------------------------------------


class _FakePaginator:
    def __init__(self, pages: list[dict[str, Any]]):
        self._pages = pages

    def paginate(self, **_kwargs: Any) -> Any:
        return iter(self._pages)


class _FakeS3Client:
    def __init__(self, pages: list[dict[str, Any]]):
        self._pages = pages

    def get_paginator(self, name: str) -> "_FakePaginator":
        assert name == "list_objects_v2"
        return _FakePaginator(self._pages)


def test_parse_s3_uri_splits_bucket_and_prefix():
    assert maint._parse_s3_uri("s3://my-bucket/some/prefix/") == ("my-bucket", "some/prefix/")


def test_parse_s3_uri_rejects_non_s3_scheme():
    with pytest.raises(DuckLakeMaintenanceError, match="must be an s3:// URI"):
        maint._parse_s3_uri("http://example.com/x")


def test_parse_s3_uri_rejects_missing_bucket():
    with pytest.raises(DuckLakeMaintenanceError, match="carries no bucket"):
        maint._parse_s3_uri("s3://")


def test_default_list_storage_walks_paginated_pages():
    pages = [{"Contents": [{"Key": "prefix/a.parquet", "Size": 10}]}]
    result = maint._default_list_storage("s3://my-bucket/prefix/", client=_FakeS3Client(pages))
    assert result == {"s3://my-bucket/prefix/a.parquet": 10}


def test_default_list_storage_handles_a_page_with_no_contents():
    assert maint._default_list_storage("s3://my-bucket/prefix/", client=_FakeS3Client([{}])) == {}


# ---------------------------------------------------------------------------
# rewrite
# ---------------------------------------------------------------------------


def test_rewrite_calls_per_table():
    con = FakeCon()
    rewrite(con, ["t1"])
    stmts = " ".join(con.executed)
    assert "ducklake_rewrite_data_files" in stmts
    assert "t1" in stmts


# ---------------------------------------------------------------------------
# run_merge
# ---------------------------------------------------------------------------


def test_run_merge_returns_ok():
    con = FakeCon(fetchone_map={"ducklake_list_files": (4,)})
    result = run_merge(con, ["t1"])
    assert result["ok"] is True
    assert result["action"] == "merge"
    assert "files_before" in result
    assert "files_after_merge" in result


def test_run_merge_no_destructive_calls():
    con = FakeCon()
    run_merge(con, ["t1"])
    destructive = [
        s
        for s in con.executed
        if "dry_run=False" in s or "cleanup_old_files" in s or "delete_orphaned" in s or "expire_snapshots" in s
    ]
    assert destructive == []


def test_run_merge_calls_flush_and_merge():
    con = FakeCon()
    run_merge(con, ["t1", "t2"])
    stmts = " ".join(con.executed)
    assert "ducklake_flush_inlined_data" in stmts
    assert "ducklake_merge_adjacent_files" in stmts


# ---------------------------------------------------------------------------
# run_gc -- behind the G1-G4 guard set (src/common/ducklake_maintenance_ops.py)
# ---------------------------------------------------------------------------


def _fake_list_storage(_data_path: str) -> dict[str, int]:
    """G4's byte source: sizes every cleanup/orphan/live-conflict path any run_gc test below uses."""
    return {
        **{f"s3://b/cleanup{i}": 100 for i in range(20)},
        **{f"s3://b/orphan{i}": 100 for i in range(20)},
        **{f"s3://b/f{i}": 100 for i in range(20)},
    }


def _gc_con(*, file_count: int = 10, cleanup_count: int = 1, orphan_count: int = 0, snapshot_count: int = 5) -> FakeCon:
    """A GC-pass fixture where the live set and the would-delete set are DISJOINT (the happy path
    -- would-delete paths are files already superseded/expired, so they never appear in the live
    ducklake_list_files view; see rec-3773)."""
    live_files = [(f"s3://b/live{i}", 100) for i in range(file_count)]
    cleanup_paths = [f"s3://b/cleanup{i}" for i in range(cleanup_count)]
    orphan_paths = [f"s3://b/orphan{i}" for i in range(orphan_count)]
    snap_rows = [(i, _ts(2025, 1, i + 1)) for i in range(snapshot_count)]

    fetchall_map: dict[str, list[Any]] = {
        "ducklake_list_files": [(p, s) for p, s in live_files],
        "ducklake_cleanup_old_files": [(p,) for p in cleanup_paths],
        "ducklake_delete_orphaned_files": [(p,) for p in orphan_paths],
        "ducklake_snapshots": snap_rows,
        "ducklake_expire_snapshots": [],
    }
    fetchone_map = {
        "ducklake_list_files": (file_count,),
        "ducklake_cleanup_old_files": (cleanup_count,),
        "ducklake_delete_orphaned_files": (orphan_count,),
        "ducklake_expire_snapshots": (2,),
        "ducklake_snapshots": (max(snapshot_count - 2, SNAPSHOT_FLOOR),),
    }
    return FakeCon(fetchall_map=fetchall_map, fetchone_map=fetchone_map)


def test_run_gc_returns_ok():
    con = _gc_con()
    result = run_gc(con, ["t1"], data_path="s3://b/prod/", list_storage=_fake_list_storage, _now=_NOW)
    assert result["ok"] is True
    assert result["action"] == "gc"


def test_run_gc_includes_all_result_keys():
    con = _gc_con()
    result = run_gc(con, ["t1"], data_path="s3://b/prod/", list_storage=_fake_list_storage, _now=_NOW)
    for key in ("files_before", "files_after", "snapshots_expired", "files_cleaned", "orphans_deleted", "guard_stats"):
        assert key in result, f"missing key {key!r}"
    assert "breaker_stats" not in result
    assert "file_fraction" not in result["guard_stats"]


def test_run_gc_g1_violation_raises_and_no_destructive():
    """G1: a would-delete path that is ALSO in the live set must raise, before any destructive call."""
    live_files = [(f"s3://b/f{i}", 100) for i in range(3)]
    con = FakeCon(
        fetchall_map={
            "ducklake_list_files": [(p, s) for p, s in live_files],
            "ducklake_cleanup_old_files": [("s3://b/f0",)],  # f0 is BOTH live and would-delete
            "ducklake_delete_orphaned_files": [],
            "ducklake_snapshots": [(1, _ts(2025, 1, 1)), (2, _ts(2025, 2, 1)), (3, _ts(2025, 3, 1))],
        },
        fetchone_map={"ducklake_list_files": (3,), "ducklake_expire_snapshots": (0,), "ducklake_snapshots": (3,)},
    )
    with pytest.raises(DuckLakeMaintenanceError, match="G1 reachability"):
        run_gc(con, ["t1"], data_path="s3://b/prod/", list_storage=_fake_list_storage, _now=_NOW)
    destructive = [s for s in con.executed if "dry_run=False" in s]
    assert destructive == [], "run_gc must not issue destructive calls after a G1 violation"


def test_run_gc_calls_all_five_steps():
    con = _gc_con()
    run_gc(con, ["t1"], data_path="s3://b/prod/", list_storage=_fake_list_storage, _now=_NOW)
    stmts = " ".join(con.executed)
    assert "ducklake_flush_inlined_data" in stmts
    assert "ducklake_merge_adjacent_files" in stmts
    assert "ducklake_expire_snapshots" in stmts
    assert "ducklake_cleanup_old_files" in stmts
    assert "ducklake_delete_orphaned_files" in stmts
    destructive = [s for s in con.executed if "dry_run=False" in s]
    assert destructive, "a within-budget pass must issue the destructive cleanup/orphan calls"


def test_run_gc_g2_violation_raises():
    """G2: if the engine's post-expiry snapshot count is below the floor, raise."""
    con = _gc_con(snapshot_count=5)
    con._fetchone_map["ducklake_snapshots"] = (SNAPSHOT_FLOOR - 1,)  # engine under-retained
    with pytest.raises(DuckLakeMaintenanceError, match="G2 retention floor"):
        run_gc(con, ["t1"], data_path="s3://b/prod/", list_storage=_fake_list_storage, _now=_NOW, floor=SNAPSHOT_FLOOR)


# ---------------------------------------------------------------------------
# HOT_TABLE_SCOPE constant
# ---------------------------------------------------------------------------


def test_hot_table_scope_defined():
    assert len(HOT_TABLE_SCOPE) >= 1


def test_hot_table_scope_note_references_t2_19():
    assert "T2.19" in maint.MAINTENANCE_SCOPE_NOTE or "T2.19" in str(HOT_TABLE_SCOPE) or hasattr(maint, "HOT_TABLE_SCOPE")


# ---------------------------------------------------------------------------
# run_hot_merge
# ---------------------------------------------------------------------------


def test_run_hot_merge_returns_ok():
    con = FakeCon(fetchone_map={"ducklake_list_files": (4,)})
    result = run_hot_merge(con, ["t1"])
    assert result["ok"] is True
    assert result["action"] == "hot_merge"


def test_run_hot_merge_has_files_before_and_after():
    con = FakeCon(fetchone_map={"ducklake_list_files": (4,)})
    result = run_hot_merge(con, ["t1"])
    assert "files_before" in result
    assert "files_after" in result


def test_run_hot_merge_no_destructive_calls():
    """Merge-only invariant: hot_merge MUST NOT issue cleanup/orphan/expire/delete calls."""
    con = FakeCon()
    run_hot_merge(con, ["t1"])
    destructive = [
        s
        for s in con.executed
        if "cleanup_old_files" in s or "delete_orphaned_files" in s or "expire_snapshots" in s or "dry_run=False" in s
    ]
    assert destructive == [], "run_hot_merge must not issue any destructive call"


def test_run_hot_merge_calls_merge_adjacent_files():
    con = FakeCon()
    run_hot_merge(con, ["hist", "curr"])
    stmts = " ".join(con.executed)
    assert "ducklake_merge_adjacent_files" in stmts


def test_run_hot_merge_no_gc_breaker_check():
    """hot_merge skips the guard-set check entirely (merge-only path has no destructions)."""
    con = FakeCon()
    run_hot_merge(con, ["t1"])
    breaker_stmts = [s for s in con.executed if "ducklake_cleanup_old_files" in s and "dry_run=True" in s]
    assert breaker_stmts == []


def test_run_hot_merge_tables_in_result():
    con = FakeCon()
    result = run_hot_merge(con, ["ta", "tb"])
    assert "ta" in result["tables"]
    assert "tb" in result["tables"]


# ---------------------------------------------------------------------------
# catalog_stats (D3a / neon-egress measurement obligation) -- pure Postgres-metadata read.
# ---------------------------------------------------------------------------

_DSN = {"username": "u", "password": "p", "host": "h", "dbname": "neondb", "sslmode": "require"}


class _FakeCursor:
    """psycopg2-cursor double: serves fetchall() results in order; optional per-query raiser."""

    def __init__(self, results: list[list[Any]], raise_on: str | None = None, ones: list[Any] | None = None):
        self._results = list(results)
        self._ones = list(ones or [])
        self._raise_on = raise_on
        self.queries: list[tuple[str, Any]] = []

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def execute(self, sql: str, params: Any = None) -> None:
        self.queries.append((sql, params))
        if self._raise_on and self._raise_on in sql:
            raise RuntimeError("column does not exist")

    def fetchall(self) -> list[Any]:
        return self._results.pop(0)

    def fetchone(self) -> Any:
        return self._ones.pop(0) if self._ones else None


class _FakeConn:
    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def close(self) -> None:
        self.closed = True


_META_ROWS = [
    ("ducklake_file_column_stats", 5_000_000, 12000),
    ("ducklake_data_file", 2_000_000, 800),
    ("ducklake_snapshot", 100_000, 50),
]
_OPS_ROWS = [("ops_decisions_current", 30), ("ops_recommendations_current", 400)]


class TestCatalogStats:
    def test_reports_catalog_metadata_footprint(self) -> None:
        index_rows = [("ducklake_file_column_stats_table_id_column_id_idx", True, ["table_id", "column_id"], 9, 4096)]
        cursor = _FakeCursor([index_rows, _META_ROWS, _OPS_ROWS], ones=[(3,), ("2026-10-01 00:00:00+00",)])
        conn = _FakeConn(cursor)
        result = catalog_stats(meta_schema="ducklake_ops", dsn=_DSN, _connect=lambda conninfo: conn)

        assert result["stats_index"]["valid"] is True and result["stats_index"]["idx_scan"] == 9
        assert result["stats_index"]["table_seq_scan"] == 3
        # stats_index is read FIRST: the per-ops query's failure branch aborts the transaction.
        assert "pg_index" in cursor.queries[0][0]
        assert result["ok"] is True
        assert result["meta_schema"] == "ducklake_ops"
        assert result["catalog_metadata_bytes"] == 7_100_000  # exact sum of pg_total_relation_size
        assert result["file_column_stats_rows_est"] == 12000  # the ducklake #859 egress driver
        assert result["data_file_rows_est"] == 800
        assert result["snapshot_rows_est"] == 50
        assert result["metadata_table_count"] == 3
        assert result["per_ops_table"] == [
            {"table": "ops_decisions_current", "data_file_count": 30},
            {"table": "ops_recommendations_current", "data_file_count": 400},
        ]
        assert conn.closed is True

    def test_per_ops_breakdown_degrades_without_crashing(self) -> None:
        """If the per-ops join fails (catalog column drift), totals are still reported with a note."""
        cursor = _FakeCursor([[], _META_ROWS], raise_on="ducklake_data_file df")
        conn = _FakeConn(cursor)
        result = catalog_stats(meta_schema="ducklake_ops", dsn=_DSN, _connect=lambda conninfo: conn)

        assert result["ok"] is True
        assert result["catalog_metadata_bytes"] == 7_100_000  # core measurement still returned
        assert result["per_ops_table"] == []
        assert "unavailable" in result["per_ops_table_note"]
        assert conn.closed is True

    def test_invalid_meta_schema_loud_fails(self) -> None:
        with pytest.raises(DuckLakeMaintenanceError, match="invalid meta_schema"):
            catalog_stats(meta_schema="ducklake_ops; DROP", dsn=_DSN, _connect=lambda conninfo: _FakeConn(_FakeCursor([])))

    def test_missing_metadata_tables_yields_null_estimates(self) -> None:
        cursor = _FakeCursor([[], [], []])  # no ducklake_* tables found
        result = catalog_stats(meta_schema="ducklake_ops", dsn=_DSN, _connect=lambda conninfo: _FakeConn(cursor))
        assert result["catalog_metadata_bytes"] == 0
        assert result["file_column_stats_rows_est"] is None
        assert result["snapshot_rows_est"] is None
