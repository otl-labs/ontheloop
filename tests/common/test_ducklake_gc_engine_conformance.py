"""Integration-marked real-DuckLake conformance for the destructive GC primitives and the gc_ops
read-path check, on the pinned engine.

Every other GC test drives these functions through connection doubles, which cannot see two
engine-level defects that split the 2026-09-20 production gc_ops pass (18,744 files deleted from S3
and still listed as scheduled for deletion; the post-pass read check failing on every run):

- expire_snapshots / cleanup_old_files must run their DuckLake statement to completion, or the
  catalog change is discarded while cleanup's storage deletes stand;
- verify_read_path's time-travel SQL must parse (the alias goes before AT, not after it).
"""

from __future__ import annotations

import functools
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.common import ducklake_gc_verification as gcver
from src.common import ducklake_maintenance as maint

pytestmark = pytest.mark.integration

_CATALOG = "lake"


@functools.lru_cache(maxsize=1)
def _has_ducklake_extension() -> bool:
    """Return True if the ducklake extension is available over the network."""
    try:
        import duckdb

        c = duckdb.connect()
        c.execute("INSTALL ducklake; LOAD ducklake")
        c.close()
        return True
    except Exception:  # noqa: BLE001
        return False


def _attach(tmp_path: Path):
    import duckdb

    c = duckdb.connect()
    c.execute("LOAD ducklake")
    c.execute("SET ducklake_default_data_inlining_row_limit=0")
    c.execute("SET TimeZone='UTC'")
    c.execute(f"ATTACH 'ducklake:{tmp_path / 'cat.db'}' AS {_CATALOG} (DATA_PATH '{tmp_path / 'data'}/')")
    return c


@pytest.fixture
def con(tmp_path: Path):
    """A catalog holding compaction leftovers (scheduled for deletion) and several snapshots."""
    if not _has_ducklake_extension():
        pytest.skip("ducklake extension unavailable (no network)")
    c = _attach(tmp_path)
    c.execute(f"CREATE TABLE {_CATALOG}.t (id INTEGER, v VARCHAR)")
    for i in range(6):
        c.execute(f"INSERT INTO {_CATALOG}.t VALUES ({i}, 'v{i}')")
    c.execute(f"UPDATE {_CATALOG}.t SET v = v || '_u' WHERE id < 3")
    c.execute(f"SELECT * FROM ducklake_merge_adjacent_files('{_CATALOG}')").fetchall()
    yield c
    c.close()


def _scheduled(c) -> int:
    return c.execute(f"SELECT count(*) FROM __ducklake_metadata_{_CATALOG}.ducklake_files_scheduled_for_deletion").fetchone()[
        0
    ]


def _snapshots(c) -> int:
    return c.execute(f"SELECT count(*) FROM ducklake_snapshots('{_CATALOG}')").fetchone()[0]


def _files_on_disk(tmp_path: Path) -> int:
    return sum(1 for p in (tmp_path / "data").rglob("*") if p.is_file())


def test_cleanup_old_files_records_its_deletes_in_the_catalog(con, tmp_path):
    scheduled_before = _scheduled(con)
    files_before = _files_on_disk(tmp_path)
    assert scheduled_before > 0, "fixture must leave compaction leftovers scheduled for deletion"

    # _now a week ahead, so every scheduled file is past the 7-day grace floor.
    deleted = maint.cleanup_old_files(con, catalog=_CATALOG, _now=datetime.now(timezone.utc) + timedelta(days=8))

    assert deleted == scheduled_before
    assert _files_on_disk(tmp_path) == files_before - deleted
    assert _scheduled(con) == 0, "storage deletes happened but the catalog still lists them as scheduled"
    con.close()
    reopened = _attach(tmp_path)
    try:
        assert _scheduled(reopened) == 0
        assert reopened.execute(f"SELECT count(*) FROM {_CATALOG}.t").fetchone()[0] == 6
    finally:
        reopened.close()


def test_expire_snapshots_commits_what_it_reports(con, tmp_path):
    # expire_snapshots' cutoff literal has second granularity: the fixture's snapshots share one
    # second, so put the retained (floor=1) snapshot in a later second than the ones to expire.
    time.sleep(1.1)
    con.execute(f"INSERT INTO {_CATALOG}.t VALUES (6, 'v6')")
    before = _snapshots(con)

    expired = maint.expire_snapshots(
        con, catalog=_CATALOG, retain_days=0, floor=1, _now=datetime.now(timezone.utc) + timedelta(days=1)
    )

    assert expired > 0
    assert _snapshots(con) == before - expired
    con.close()
    reopened = _attach(tmp_path)
    try:
        assert _snapshots(reopened) == before - expired
    finally:
        reopened.close()


def test_verify_read_path_scans_at_the_snapshot_on_the_pinned_engine(con):
    snapshot_id = con.execute(
        f"SELECT snapshot_id FROM ducklake_snapshots('{_CATALOG}') ORDER BY snapshot_time DESC LIMIT 1"
    ).fetchone()[0]

    result = gcver.verify_read_path(con, snapshot_id, ["t"], catalog=_CATALOG)

    assert result["t"]["row_count"] == 6
    assert result["t"]["value_aggregate"] is not None
