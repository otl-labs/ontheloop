"""Integration-marked real-DuckLake conformance for the calendar-day partition fix (rec-4068, rec-3808).

Attaches a LOCAL DuckLake catalog (no S3/AWS credentials needed -- only the extension download) as
CATALOG_ALIAS, matching what create_scd2_tables/create_control_table hardcode, and exercises every
declared table's REAL DDL + write path against duckdb's own day()/month()/year() partition
semantics -- not the FakeCon SQL-recording double the rest of the suite uses.

NON-VACUITY (critique r3 F1): default data inlining leaves ZERO rows as partition-value metadata
rows, so this attaches with ducklake_default_data_inlining_row_limit=0 and TimeZone UTC, mirroring
open_connection, and asserts live file count >= distinct calendar dates written > 0 before the
per-file single-day assertion.

The layout assertion reads partition values through the SAME smoke_actions partition-value helper
EC6 uses (S4), so its metadata SQL runs on a real engine pre-merge, not first at post-deploy step 21.
"""

from __future__ import annotations

import functools
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from src.common import ducklake_control_tables as ctl
from src.common import ducklake_partition_layout as layout_mod
from src.common import ducklake_partition_rewrite as rewrite_mod
from src.common import ducklake_scd2_schema as schema
from src.common import ducklake_tables as tables
from src.common.ducklake_scd2_schema import CATALOG_ALIAS
from src.lambdas.ducklake_writer.smoke_actions import _partition_layout

pytestmark = pytest.mark.integration

_PROBE_DAYS = (
    datetime(2026, 1, 24, tzinfo=timezone.utc),
    datetime(2026, 2, 24, tzinfo=timezone.utc),
    datetime(2027, 1, 24, tzinfo=timezone.utc),
)


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


@pytest.fixture
def con(tmp_path: Path):
    if not _has_ducklake_extension():
        pytest.skip("ducklake extension unavailable (no network)")
    import duckdb

    c = duckdb.connect()
    c.execute("INSTALL ducklake")
    c.execute("LOAD ducklake")
    # Mirror open_connection (Decision 96 / rec-4068): inlining off + UTC pinned BEFORE the ATTACH.
    c.execute("SET ducklake_default_data_inlining_row_limit=0")
    c.execute("SET TimeZone='UTC'")
    db_path = tmp_path / "cat.db"
    data_path = str(tmp_path / "data") + "/"
    c.execute(f"ATTACH 'ducklake:{db_path}' AS {CATALOG_ALIAS} (DATA_PATH '{data_path}')")
    yield c
    c.close()


def _sql_literal(sql_type: str) -> str:
    """A generic non-null literal for *sql_type*, used only to satisfy a NOT NULL column."""
    if sql_type.endswith("[]"):
        return "[]"
    if sql_type in ("BIGINT", "INTEGER"):
        return "0"
    if sql_type == "BOOLEAN":
        return "false"
    return "'x'"


def _insert_history_row(con: Any, spec: schema.ScdTableSpec, ulid: str, merge_key_value: str, created_ts: datetime) -> None:
    """Direct SQL INSERT into *spec*'s history table, bypassing write_scd2/the schema gate --
    this test proves the DECLARED partition specs behave as calendar days on the real engine, not
    the write-path plumbing (which the rest of the suite already covers via FakeCon)."""
    cols: list[str] = []
    vals: list[str] = []
    for name, sql_type in spec.ordered_columns:
        cols.append(name)
        if name == "ulid":
            vals.append(f"'{ulid}'")
        elif name == spec.merge_key:
            vals.append(f"'{merge_key_value}'")
        elif name in ("created_timestamp", "last_updated_timestamp"):
            vals.append(f"TIMESTAMPTZ '{created_ts.isoformat()}'")
        elif bool(spec.fields[name].get("nullable", True)):
            vals.append("NULL")
        else:
            vals.append(_sql_literal(spec.fields[name]["sql_type"]))
    con.execute(f"INSERT INTO {CATALOG_ALIAS}.{spec.history_table} ({', '.join(cols)}) VALUES ({', '.join(vals)})")


def _conformance_targets() -> list[str | None]:
    """None = smoke pair; else every non-control, non-event ops table in the generated projection."""
    semantics = schema.load_field_semantics()
    targets: list[str | None] = [None]
    for name in schema.ops_table_names():
        if semantics["ops_tables"][name].get("write_mode") == "control":
            continue
        if schema.is_event_table(name, semantics):
            continue  # event partition conformance is the kernel's (slice 1's local-DuckLake tests)
        targets.append(name)
    return targets


@pytest.mark.parametrize("table", _conformance_targets(), ids=lambda t: t or "smoke")
def test_history_partition_is_one_file_per_calendar_day(con: Any, table: str | None) -> None:
    """For every declared table, rows sharing a day-of-month across a month AND a year boundary
    stay in DISTINCT calendar-day partitions through ducklake_merge_adjacent_files."""
    spec = schema.resolve_table_spec(table)
    tables.create_scd2_tables(con, table=table, force_recreate=True)

    for i, ts in enumerate(_PROBE_DAYS):
        _insert_history_row(con, spec, ulid=f"01PROBE{i}", merge_key_value=f"probe-{i}", created_ts=ts)

    con.execute(f"CALL ducklake_merge_adjacent_files('{CATALOG_ALIAS}')")

    layout = _partition_layout(con, CATALOG_ALIAS, spec.history_table)

    # Non-vacuity: real files were written and their partition values were actually read back.
    distinct_dates = set(layout["value_tuples"].values())
    assert layout["total"] >= len(distinct_dates) > 0, (table, layout)
    assert len(distinct_dates) == len(_PROBE_DAYS), (table, layout)

    # Per-file single-day assertion: every live file's partition tuple is unique to its day.
    assert len(layout["value_tuples"]) == layout["total"], (table, layout)
    assert len(set(layout["value_tuples"].values())) == layout["total"], (
        f"{table}: files collapsed into fewer partitions than days written -- {layout}"
    )


def test_read_partition_layout_classifies_smoke_harness_pair_alongside_ops_table(con: Any) -> None:
    """rec-3864 / Decision 191 amendment, real engine: a catalog carrying an ops table AND the
    declared smoke-harness pair reads and classifies both without error, the pair classified
    smoke_harness; adding one undeclared raw table then raises exactly one aggregated
    PartitionLayoutError naming only that table."""
    ops_spec = schema.resolve_table_spec("ops_recommendations")
    tables.create_scd2_tables(con, table="ops_recommendations", force_recreate=True)
    tables.create_scd2_tables(con, table=None, force_recreate=True)  # the smoke pair
    semantics = schema.load_field_semantics()

    layouts = layout_mod.read_partition_layout(con, catalog_alias=CATALOG_ALIAS, semantics=semantics)
    assert layouts[ops_spec.history_table].table_class == "scd2"
    assert layouts["ducklake_smoke_history"].table_class == "smoke_harness"
    assert layouts["ducklake_smoke_current"].table_class == "smoke_harness"

    drifts = layout_mod.compare_to_declared(layouts, semantics=semantics)
    assert not any(d.physical in ("ducklake_smoke_history", "ducklake_smoke_current") for d in drifts)

    con.execute(f"CREATE TABLE {CATALOG_ALIAS}.a_stray_raw_table (x INTEGER)")
    with pytest.raises(layout_mod.PartitionLayoutError) as exc_info:
        layout_mod.read_partition_layout(con, catalog_alias=CATALOG_ALIAS, semantics=semantics)
    message = str(exc_info.value)
    assert "a_stray_raw_table" in message
    assert "ducklake_smoke_history" not in message
    assert "ducklake_smoke_current" not in message


def test_create_scd2_tables_repeated_call_is_idempotent(con: Any) -> None:
    """rec-3808 twin: a repeated non-force create_scd2_tables call does not error."""
    tables.create_scd2_tables(con, force_recreate=True)
    tables.create_scd2_tables(con, force_recreate=False)  # must not raise


def test_create_control_table_repeated_call_is_idempotent(con: Any) -> None:
    """rec-3808 acceptance: a repeated non-force create_control_table call raises nothing and
    leaves exactly one active partition_info row."""
    spec = ctl.resolve_control_spec("ops_entity_counters")
    tables.create_control_table(con, table="ops_entity_counters", force_recreate=True)
    tables.create_control_table(con, table="ops_entity_counters", force_recreate=False)  # must not raise

    rows = con.execute(
        f"SELECT count(*) FROM __ducklake_metadata_{CATALOG_ALIAS}.ducklake_partition_info pi "
        f"JOIN __ducklake_metadata_{CATALOG_ALIAS}.ducklake_table t ON pi.table_id = t.table_id "
        f"WHERE t.table_name = ? AND pi.end_snapshot IS NULL",
        [spec.table],
    ).fetchone()
    assert int(rows[0]) == 1


# ---------------------------------------------------------------------------
# Decision 204 (PLAN-ducklake-partition-layout-remediation): alter_to_declared +
# rewrite_legacy_layout proven on the real engine -- a day-of-month table with cross-month rows,
# re-laid onto the calendar-day scheme with a full-row digest proof and time-travel recovery.
# ---------------------------------------------------------------------------


def _legacy_day_of_month_history_table(con: Any, table: str) -> schema.ScdTableSpec:
    """Create *table*'s history table under the day-of-month LEGACY scheme (rec-4068's original
    defect) and insert one row per _PROBE_DAYS entry, so its live files span a month AND a year
    boundary under a bare day() transform."""
    spec = schema.resolve_table_spec(table)
    tables.create_scd2_tables(con, table=table, force_recreate=True)
    con.execute(f"ALTER TABLE {CATALOG_ALIAS}.{spec.history_table} SET PARTITIONED BY (day(created_timestamp))")
    for i, ts in enumerate(_PROBE_DAYS):
        _insert_history_row(con, spec, ulid=f"01LEGACY{i}", merge_key_value=f"legacy-{i}", created_ts=ts)
    return spec


def test_alter_to_declared_and_rewrite_legacy_layout_real_engine(con: Any) -> None:
    """rec-4070: a day-of-month history table is ALTERed to the declared calendar-day spec, then
    its legacy-scheme files are re-laid -- every live file lands on a distinct calendar day, the
    full-row digest is equal before and after, and time travel to pre_snapshot_id returns every
    original row."""
    spec = _legacy_day_of_month_history_table(con, "ops_recommendations")
    semantics = schema.load_field_semantics()

    layouts = layout_mod.read_partition_layout(con, catalog_alias=CATALOG_ALIAS, semantics=semantics)
    drifts = layout_mod.compare_to_declared(layouts, semantics=semantics)
    history_drift = [d for d in drifts if d.physical == spec.history_table]
    assert history_drift, "day() vs the declared calendar-day triple must be reported as drift"

    altered = rewrite_mod.alter_to_declared(con, history_drift, catalog_alias=CATALOG_ALIAS)
    assert altered == [spec.history_table]

    post_alter = layout_mod.read_partition_layout(con, catalog_alias=CATALOG_ALIAS, semantics=semantics)[spec.history_table]
    assert post_alter.legacy_scheme_files == len(_PROBE_DAYS)  # every day-of-month file is now legacy

    result = rewrite_mod.rewrite_legacy_layout(con, spec.history_table, catalog_alias=CATALOG_ALIAS, semantics=semantics)

    assert result["rows_pre"] == result["rows_commit"] == len(_PROBE_DAYS)
    assert result["digest_pre"] == result["digest_commit"]
    assert result["pre_snapshot_id"] < result["commit_snapshot_id"]
    assert result["attempts"] == 1

    post_rewrite = layout_mod.read_partition_layout(con, catalog_alias=CATALOG_ALIAS, semantics=semantics)[spec.history_table]
    assert post_rewrite.legacy_scheme_files == 0

    layout = _partition_layout(con, CATALOG_ALIAS, spec.history_table)
    distinct_dates = set(layout["value_tuples"].values())
    assert len(distinct_dates) == len(_PROBE_DAYS)  # one calendar day per file, cross-month/year preserved
    assert len(layout["value_tuples"]) == layout["total"]

    pre_rows = con.execute(
        f"SELECT {spec.merge_key} FROM {CATALOG_ALIAS}.{spec.history_table} AT (VERSION => {result['pre_snapshot_id']})"
    ).fetchall()
    assert {r[0] for r in pre_rows} == {f"legacy-{i}" for i in range(len(_PROBE_DAYS))}


def test_rewrite_legacy_layout_retries_past_a_concurrent_insert(con: Any) -> None:
    """A concurrent INSERT that commits while the rewrite's own transaction is open aborts that
    attempt with a real DuckLake transaction-conflict error; the retry re-snapshots the
    now-current table and succeeds, preserving the concurrently-inserted row."""
    spec = _legacy_day_of_month_history_table(con, "ops_recommendations")
    semantics = schema.load_field_semantics()

    layouts = layout_mod.read_partition_layout(con, catalog_alias=CATALOG_ALIAS, semantics=semantics)
    drifts = layout_mod.compare_to_declared(layouts, semantics=semantics)
    rewrite_mod.alter_to_declared(con, [d for d in drifts if d.physical == spec.history_table], catalog_alias=CATALOG_ALIAS)

    calls = {"n": 0}

    def _uuid_factory() -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            # A second cursor on the SAME connection shares its attached catalog file handle,
            # giving a genuine concurrent DuckLake transaction (a separate ATTACH to the same
            # local catalog file conflicts on the file handle itself, unlike production's shared
            # Neon/Postgres metadata backend).
            concurrent = con.cursor()
            _insert_history_row(
                concurrent, spec, ulid="01CONCURRENT", merge_key_value="concurrent-row", created_ts=_PROBE_DAYS[0]
            )
            concurrent.close()
        return f"attempt-{calls['n']}"

    result = rewrite_mod.rewrite_legacy_layout(
        con,
        spec.history_table,
        catalog_alias=CATALOG_ALIAS,
        semantics=semantics,
        uuid_factory=_uuid_factory,
        sleep=lambda _s: None,
    )

    assert result["attempts"] == 2  # attempt 1 aborted by the concurrent commit; attempt 2 succeeded
    assert result["rows_pre"] == result["rows_commit"] == len(_PROBE_DAYS) + 1  # the concurrent row survived the retry

    post_rewrite = layout_mod.read_partition_layout(con, catalog_alias=CATALOG_ALIAS, semantics=semantics)[spec.history_table]
    assert post_rewrite.legacy_scheme_files == 0

    rows_after = {r[0] for r in con.execute(f"SELECT {spec.merge_key} FROM {CATALOG_ALIAS}.{spec.history_table}").fetchall()}
    assert rows_after == {f"legacy-{i}" for i in range(len(_PROBE_DAYS))} | {"concurrent-row"}
