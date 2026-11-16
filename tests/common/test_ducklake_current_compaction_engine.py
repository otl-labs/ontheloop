"""Integration-marked real-engine conformance for compact_current_tables on the pinned engine.

Drives the module itself on a bucket(8, rec_id) MERGE-maintained current table (the smoke pair's current side)
with merged, delete-bearing files, on a local file catalog and on a local Postgres 16 stage (production's OCC and
snapshot-id path; DUCKLAKE_TEST_PG_ADMIN_DSN). A missing extension or an unset DSN is pytest.fail, never skip
(rec-4071). With CURRENT_COMPACTION_EVIDENCE_OUT set, the conformance node writes its measured counts to that path.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

import pytest

from src.common import ducklake_control_health as ch
from src.common import ducklake_current_compaction as cc
from src.common import ducklake_maintenance as maint
from src.common import ducklake_maintenance_scope as scope
from src.common import ducklake_scd2_schema as schema
from src.common import ducklake_tables as tables
from src.common.ducklake_partition_rewrite import _row_proof
from src.common.ducklake_writes import is_occ_collision

pytestmark = pytest.mark.integration

A = schema.CATALOG_ALIAS
TABLE = "ducklake_smoke_current"
FQ = f"{A}.{TABLE}"
BATCHES, PER_BATCH, UPDATE_SUFFIXES = 4, 64, (0, 1, 2)


def _load_extensions(con: Any, *names: str) -> None:
    for name in names:
        try:
            con.execute(f"INSTALL {name}")
            con.execute(f"LOAD {name}")
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"extension {name} did not load -- a skip is a FAIL (rec-4071): {exc}")


@pytest.fixture(params=["file", "postgres"])
def lake(request: pytest.FixtureRequest, tmp_path: Path):
    import duckdb

    con = duckdb.connect()
    _load_extensions(con, "ducklake")
    con.execute("SET ducklake_default_data_inlining_row_limit=0")
    con.execute("SET TimeZone='UTC'")
    data = f"{tmp_path / 'data'}/"
    cleanup = None
    if request.param == "file":
        con.execute(f"ATTACH 'ducklake:{tmp_path / 'cat.db'}' AS {A} (DATA_PATH '{data}')")
    else:
        import psycopg2

        dsn = os.environ.get("DUCKLAKE_TEST_PG_ADMIN_DSN", "")
        if not dsn:
            pytest.fail("DUCKLAKE_TEST_PG_ADMIN_DSN is unset -- a skip is a FAIL (rec-4071)")
        params = psycopg2.extensions.parse_dsn(dsn)
        if params.get("host", "") not in {"127.0.0.1", "::1", "localhost"}:
            pytest.fail("DUCKLAKE_TEST_PG_ADMIN_DSN must point at a local server")
        _load_extensions(con, "postgres")
        db = f"cc_{uuid.uuid4().hex[:12]}"
        admin = psycopg2.connect(**params)
        admin.autocommit = True
        admin.cursor().execute(f"CREATE DATABASE {db}")
        conn = " ".join(f"{k}={v}" for k, v in {**params, "dbname": db}.items())
        con.execute(f"ATTACH 'ducklake:postgres:{conn}' AS {A} (DATA_PATH '{data}')")

        def cleanup() -> None:
            con.close()
            admin.cursor().execute(f"DROP DATABASE IF EXISTS {db} WITH (FORCE)")
            admin.close()

    tables.create_scd2_tables(con, table=None, force_recreate=True)
    yield request.param, con
    if cleanup:
        cleanup()
    else:
        con.close()


def _insert(con: Any, keys: list[str]) -> None:
    spec = schema.resolve_table_spec(None)
    cols = [n for n, _ in spec.ordered_columns]
    rows = []
    for key in keys:
        vals = []
        for name, sql_type in spec.ordered_columns:
            if name == spec.merge_key:
                vals.append(f"'{key}'")
            elif name == "ulid":
                vals.append(f"'u{key}'")
            elif name in ("created_timestamp", "last_updated_timestamp"):
                vals.append("TIMESTAMPTZ '2026-01-01 00:00:00+00'")
            elif bool(spec.fields[name].get("nullable", True)):
                vals.append("NULL")
            elif sql_type.endswith("[]"):
                vals.append("[]")
            elif sql_type in ("BIGINT", "INTEGER"):
                vals.append("0")
            elif sql_type == "BOOLEAN":
                vals.append("false")
            else:
                vals.append("'x'")
        rows.append("(" + ",".join(vals) + ")")
    con.execute(f"INSERT INTO {FQ} ({','.join(cols)}) VALUES {','.join(rows)}")


def _update_suffix(con: Any, suffix: int, day: int) -> None:
    stamp = f"TIMESTAMPTZ '2026-02-0{day} 00:00:00+00'"
    con.execute(f"UPDATE {FQ} SET last_updated_timestamp = {stamp} WHERE rec_id LIKE '%\\_{suffix}' ESCAPE '\\'")


def _populate(con: Any) -> None:
    for b in range(BATCHES):
        _insert(con, [f"k{b}_{i}" for i in range(PER_BATCH)])
    for s in UPDATE_SUFFIXES:
        _update_suffix(con, s, s + 1)


def _files(con: Any) -> int:
    return maint._count_files(con, A, TABLE)


def _delete_files(con: Any) -> int:
    return cc._delete_file_counts(con, A)[TABLE]


def _snapshots(con: Any) -> int:
    return int(con.execute(f"SELECT count(*) FROM ducklake_snapshots('{A}')").fetchone()[0])


def _compact(con: Any) -> dict[str, Any]:
    semantics = schema.load_field_semantics()
    return cc.compact_current_tables(
        con,
        [TABLE, "ducklake_smoke_history"],
        semantics=semantics,
        registry=scope.build_registry(semantics),
        policy=scope.load_policy(semantics),
        catalog=A,
        meta_schema="ducklake_ops",
        emit=lambda n, v: None,
    )


def _rewrite_sql(threshold: str = "") -> str:
    return f"SELECT * FROM ducklake_rewrite_data_files('{A}', '{TABLE}'{threshold})"


def test_compaction_bounds_files_and_preserves_rows(lake: Any) -> None:
    backend, con = lake
    _populate(con)
    files_before, deletes_before = _files(con), _delete_files(con)
    rows_before = _row_proof(con, FQ)
    assert deletes_before > 0 and ch._file_counts(con, A, TABLE)[1] > 0

    snapshots_before_default = _snapshots(con)
    default_processed = sum(int(r[2]) for r in con.execute(_rewrite_sql()).fetchall())
    assert default_processed == 0 and _snapshots(con) == snapshots_before_default

    con.execute("BEGIN TRANSACTION")
    con.execute(_rewrite_sql(", delete_threshold=0.0"))
    assert _delete_files(con) == deletes_before  # a compaction is invisible inside its own transaction
    con.execute("ROLLBACK")

    result = _compact(con)
    entry = result["per_table"][0]
    assert not result["failures"] and entry["proof"] == "equal" and entry["files_processed"] > 0
    assert entry["commit_snapshot_id"] is not None and entry["pre_snapshot_id"] is not None
    con.execute(f"SELECT * FROM ducklake_merge_adjacent_files('{A}', '{TABLE}')").fetchall()
    files_after, deletes_after = _files(con), _delete_files(con)
    assert files_after <= 8 and deletes_after == 0 and ch._file_counts(con, A, TABLE) == (files_after, 0)
    assert _row_proof(con, FQ) == rows_before

    snapshots = _snapshots(con)
    second = _compact(con)
    assert second["per_table"][0]["files_processed"] == 0 and second["per_table"][0]["reproof"] == "equal"
    assert _snapshots(con) == snapshots

    out = os.environ.get("CURRENT_COMPACTION_EVIDENCE_OUT")
    if out:
        ext = con.execute("SELECT extension_version FROM duckdb_extensions() WHERE extension_name = 'ducklake'").fetchone()[0]
        import duckdb

        path = Path(out)
        evidence = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"backends": {}}
        evidence["duckdb_version"] = duckdb.__version__
        evidence["ducklake_extension_version"] = ext
        evidence["backends"][backend] = {
            "files_before": files_before,
            "delete_files_before": deletes_before,
            "files_after_merge": files_after,
            "delete_files_after_merge": deletes_after,
            "files_processed_default_threshold": default_processed,
            "files_processed_explicit_threshold": entry["files_processed"],
            "second_compaction_files_processed": second["per_table"][0]["files_processed"],
            "rows_equal": True,
            "digest_equal": True,
        }
        path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_update_races_conflict_in_both_directions(lake: Any) -> None:
    _, con = lake
    _populate(con)
    other = con.cursor()

    con.execute("BEGIN TRANSACTION")
    con.execute(_rewrite_sql(", delete_threshold=0.0"))
    _update_suffix(other, 5, 5)
    with pytest.raises(Exception) as first:
        con.execute("COMMIT")
    assert is_occ_collision(first.value), str(first.value)

    other.execute("BEGIN TRANSACTION")
    _update_suffix(other, 6, 6)
    con.execute("BEGIN TRANSACTION")
    con.execute(_rewrite_sql(", delete_threshold=0.0"))
    con.execute("COMMIT")
    with pytest.raises(Exception) as second:
        other.execute("COMMIT")
    assert is_occ_collision(second.value), str(second.value)


def test_concurrent_new_key_insert_does_not_conflict_and_predecessor_proof_holds(
    lake: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, con = lake
    _populate(con)
    other = con.cursor()
    real = maint.rewrite

    def racing(c: Any, tbls: Any, **kw: Any) -> int:
        processed = real(c, tbls, **kw)
        _insert(other, ["late_key"])
        return processed

    monkeypatch.setattr(maint, "rewrite", racing)
    result = _compact(con)
    entry = result["per_table"][0]
    assert not result["failures"] and entry["attempts"] == 1 and entry["proof"] == "equal"
    assert con.execute(f"SELECT count(*) FROM {FQ} WHERE rec_id = 'late_key'").fetchone()[0] == 1


def test_second_compaction_of_compacted_table_commits_no_snapshot(lake: Any) -> None:
    _, con = lake
    _populate(con)
    _compact(con)
    con.execute(f"SELECT * FROM ducklake_merge_adjacent_files('{A}', '{TABLE}')").fetchall()
    before = _snapshots(con)
    result = _compact(con)
    assert result["per_table"][0]["files_processed"] == 0 and result["per_table"][0]["commit_snapshot_id"] is None
    assert _snapshots(con) == before
