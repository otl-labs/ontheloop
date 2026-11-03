"""Real-DuckLake integration for src/telemetry/append.py (Decision 199, rec-4024 slice 1).

pytest.mark.integration: runs append_events against a genuine local DuckLake file catalog on the
pinned duckdb/ducklake (Decision 99). This tier runs only under -m integration, so a skip here
would be silently invisible to the unit CI tier and never surface as red -- the fixture below FAILS
instead (rec-4071): the extension-unavailable case is a real environment defect this VP step must
catch, never a quiet no-op.
"""

from __future__ import annotations

import functools
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

duckdb = pytest.importorskip("duckdb")

from src.telemetry.append import EventTableSpec, append_events  # noqa: E402

TENANT = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
PROJECT = "01BRZ3NDEKTSV4RRFFQ69G5FBW"

_COLUMNS = {
    "event_id": "VARCHAR",
    "event_kind": "VARCHAR",
    "event_timestamp": "TIMESTAMP WITH TIME ZONE",
    "session_started_at": "TIMESTAMP WITH TIME ZONE",
    "external_ref": "VARCHAR",
    "entity_ref": "VARCHAR",
    "producer": "VARCHAR",
    "producer_version": "VARCHAR",
    "parser_version": "BIGINT",
    "created_timestamp": "TIMESTAMP WITH TIME ZONE",
    "tenant_id": "VARCHAR",
    "project_id": "VARCHAR",
    "session_id": "VARCHAR",
}
_NOT_NULL = frozenset({"event_id", "parser_version", "session_started_at", "created_timestamp", "session_id", "producer"})
_PARTITION_SQL = "year(session_started_at), month(session_started_at), day(session_started_at)"


def _spec() -> EventTableSpec:
    return EventTableSpec(table="telemetry_sessions", columns=dict(_COLUMNS), not_null=_NOT_NULL)


@functools.lru_cache(maxsize=1)
def _has_ducklake_extension() -> bool:
    try:
        con = duckdb.connect()
        con.execute("INSTALL ducklake; LOAD ducklake")
        con.close()
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture
def _skip_if_no_extension(_allow_network_for_integration: None) -> None:
    if not _has_ducklake_extension():
        pytest.fail(
            "ducklake extension could not load -- a skip here is a FAIL for VP step 6 (rec-4071); "
            "see the fix_if guidance for this VP step (environment defect, not a green path)"
        )


def _local_catalog(tmp_path: Path) -> Any:
    """ATTACH a fresh local-file DuckLake catalog, UTC, inlining disabled.

    TimeZone is pinned BEFORE the ATTACH (mirrors src.common.ducklake_runtime.open_connection,
    rec-4068): DuckLake evaluates year()/month()/day() partition transforms in the connection's
    TimeZone, so an un-pinned environment default would make the calendar-day partition boundary
    follow the local TZ instead of UTC.
    """
    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake")
    con.execute("SET ducklake_default_data_inlining_row_limit=0")
    con.execute("SET TimeZone='UTC'")
    meta = tmp_path / "catalog.ducklake"
    data = tmp_path / "data"
    con.execute(f"ATTACH 'ducklake:{meta}' AS lake (DATA_PATH '{data}')")
    return con


def _create_table(con: Any) -> None:
    col_ddl = ", ".join(f"{c} {t}" for c, t in _COLUMNS.items())
    con.execute(f"CREATE TABLE lake.telemetry_sessions ({col_ddl})")
    con.execute(f"ALTER TABLE lake.telemetry_sessions SET PARTITIONED BY ({_PARTITION_SQL})")


def _file_count(con: Any) -> int:
    return con.execute("SELECT count(*) FROM ducklake_list_files('lake', 'telemetry_sessions')").fetchone()[0]


def _row(**overrides: Any) -> dict[str, Any]:
    t = datetime(2026, 9, 25, 10, 0, 0, tzinfo=timezone.utc)
    base = {
        "event_kind": "open",
        "event_timestamp": t,
        "session_started_at": t,
        "external_ref": "src#0/open",
        "entity_ref": "sess-ref-1",
        "producer": "claude_code",
        "producer_version": "1.0",
        "parser_version": 1,
    }
    base.update(overrides)
    return base


class _ProfileCapturingConnection:
    """Wraps a real connection; snapshots the JSON profile right after the MERGE executes --
    any later statement (COMMIT) overwrites profiling_output, so capture must happen here."""

    def __init__(self, real: Any, profile_path: Path) -> None:
        self._real = real
        self._profile_path = profile_path
        self.captured_profile: dict[str, Any] | None = None

    def execute(self, sql: str, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        result = self._real.execute(sql, *args, **kwargs)
        if sql.strip().upper().startswith("MERGE"):
            self.captured_profile = json.loads(self._profile_path.read_text(encoding="utf-8"))
        return result


def _find_total_files_read(node: dict[str, Any], acc: list[int]) -> None:
    extra = node.get("extra_info") or {}
    if "Total Files Read" in extra:
        acc.append(int(extra["Total Files Read"]))
    for child in node.get("children") or []:
        _find_total_files_read(child, acc)


@pytest.mark.integration
class TestSingleSessionBatchOneFile:
    def test_single_session_batch_adds_exactly_one_data_file(self, tmp_path: Path, _skip_if_no_extension: None) -> None:
        con = _local_catalog(tmp_path)
        _create_table(con)
        assert _file_count(con) == 0

        rows = [_row(external_ref="src#0/open"), _row(external_ref="src#1/close", event_kind="close")]
        result = append_events(con, _spec(), rows, tenant_id=TENANT, project_id=PROJECT, catalog="lake")

        assert result.inserted == 2
        assert _file_count(con) == 1


@pytest.mark.integration
class TestReplayAddsNoFiles:
    def test_replay_adds_zero_new_files_and_rows(self, tmp_path: Path, _skip_if_no_extension: None) -> None:
        con = _local_catalog(tmp_path)
        _create_table(con)
        rows = [_row()]
        append_events(con, _spec(), rows, tenant_id=TENANT, project_id=PROJECT, catalog="lake")
        files_after_first = _file_count(con)
        rows_after_first = con.execute("SELECT count(*) FROM lake.telemetry_sessions").fetchone()[0]

        result = append_events(con, _spec(), rows, tenant_id=TENANT, project_id=PROJECT, catalog="lake")

        assert result.inserted == 0
        assert _file_count(con) == files_after_first
        assert con.execute("SELECT count(*) FROM lake.telemetry_sessions").fetchone()[0] == rows_after_first


@pytest.mark.integration
class TestDayBoundedMergeReadsExactlyOneFile:
    def test_day_bounded_merge_scans_only_the_touched_day(self, tmp_path: Path, _skip_if_no_extension: None) -> None:
        con = _local_catalog(tmp_path)
        _create_table(con)

        # Seed one wide-event_id-range file for the touched day, the adjacent day, and the SAME
        # day-of-month in another month -- each spans low..high so the id-equality dynamic filter
        # cannot prune it; only the day-partition boundary should restrict which file is read.
        low, high = "0" * 20, "z" * 20
        for date in ("2026-09-24", "2026-09-25", "2026-10-25"):
            con.execute(
                "INSERT INTO lake.telemetry_sessions VALUES "
                f"('{low}', 'point', TIMESTAMP '{date} 09:00:00+00', TIMESTAMP '{date} 09:00:00+00', "
                f"'seed-lo', 'seed-lo', 'claude_code', '1.0', 1, TIMESTAMP '{date} 09:00:00+00', "
                f"'{TENANT}', '{PROJECT}', 'seed-session'), "
                f"('{high}', 'point', TIMESTAMP '{date} 09:00:00+00', TIMESTAMP '{date} 09:00:00+00', "
                f"'seed-hi', 'seed-hi', 'claude_code', '1.0', 1, TIMESTAMP '{date} 09:00:00+00', "
                f"'{TENANT}', '{PROJECT}', 'seed-session')"
            )
        assert _file_count(con) == 3

        profile_path = tmp_path / "profile.json"
        con.execute("PRAGMA enable_profiling=json")
        con.execute(f"PRAGMA profiling_output='{profile_path}'")
        recorder = _ProfileCapturingConnection(con, profile_path)

        rows = [_row(external_ref="new-batch/open")]
        result = append_events(recorder, _spec(), rows, tenant_id=TENANT, project_id=PROJECT, catalog="lake")
        con.execute("PRAGMA disable_profiling")

        assert result.inserted == 1
        assert recorder.captured_profile is not None
        assert recorder.captured_profile["query_name"].strip().upper().startswith("MERGE")
        found: list[int] = []
        _find_total_files_read(recorder.captured_profile, found)
        assert found, "no DuckLake scan node reported Total Files Read in the captured profile"
        assert found == [1], found


@pytest.mark.integration
class TestGrainEnforcedAtWriteOnRealDuckLake:
    def test_cross_batch_conflict_rejected_table_unchanged(self, tmp_path: Path, _skip_if_no_extension: None) -> None:
        con = _local_catalog(tmp_path)
        _create_table(con)
        from src.telemetry.gate import AppendError

        append_events(con, _spec(), [_row()], tenant_id=TENANT, project_id=PROJECT, catalog="lake")
        with pytest.raises(AppendError, match="TELEMETRY_GRAIN_CONFLICT"):
            append_events(con, _spec(), [_row(event_kind="close")], tenant_id=TENANT, project_id=PROJECT, catalog="lake")
        assert con.execute("SELECT count(*) FROM lake.telemetry_sessions").fetchone()[0] == 1

    def test_cross_producer_same_event_id_both_stored(self, tmp_path: Path, _skip_if_no_extension: None) -> None:
        con = _local_catalog(tmp_path)
        _create_table(con)
        append_events(con, _spec(), [_row(producer="claude_code")], tenant_id=TENANT, project_id=PROJECT, catalog="lake")
        result = append_events(con, _spec(), [_row(producer="litellm")], tenant_id=TENANT, project_id=PROJECT, catalog="lake")
        assert result.inserted == 1
        assert con.execute("SELECT count(*) FROM lake.telemetry_sessions").fetchone()[0] == 2

    def test_identical_and_producer_version_only_replays_insert_zero(
        self, tmp_path: Path, _skip_if_no_extension: None
    ) -> None:
        con = _local_catalog(tmp_path)
        _create_table(con)
        append_events(con, _spec(), [_row()], tenant_id=TENANT, project_id=PROJECT, catalog="lake")
        result = append_events(con, _spec(), [_row()], tenant_id=TENANT, project_id=PROJECT, catalog="lake")
        assert result.inserted == 0
        result2 = append_events(
            con, _spec(), [_row(producer_version="2.0")], tenant_id=TENANT, project_id=PROJECT, catalog="lake"
        )
        assert result2.inserted == 0
        assert con.execute("SELECT count(*) FROM lake.telemetry_sessions").fetchone()[0] == 1

    def test_row_omitting_producer_rejected(self, tmp_path: Path, _skip_if_no_extension: None) -> None:
        con = _local_catalog(tmp_path)
        _create_table(con)
        from src.telemetry.gate import AppendError

        row = _row()
        del row["producer"]
        with pytest.raises(AppendError):
            append_events(con, _spec(), [row], tenant_id=TENANT, project_id=PROJECT, catalog="lake")
        assert con.execute("SELECT count(*) FROM lake.telemetry_sessions").fetchone()[0] == 0


@pytest.mark.integration
class TestCalendarDayCompactionGuarantee:
    def test_compaction_yields_one_file_per_calendar_date_no_mixing(self, tmp_path: Path, _skip_if_no_extension: None) -> None:
        con = _local_catalog(tmp_path)
        _create_table(con)

        # Same day-of-month across a month AND a year boundary (mirrors _PROBE_DAYS in
        # tests/common/test_ducklake_partition_conformance.py) -- a bare day() partition would
        # collapse these into one partition; the calendar-day triple must not.
        dates = ("2026-01-24", "2026-02-24", "2027-01-24")
        for date in dates:
            for i in range(2):  # >=2 seed files per date
                con.execute(
                    "INSERT INTO lake.telemetry_sessions VALUES "
                    f"('id-{date}-{i}', 'point', TIMESTAMP '{date} 0{i}:00:00+00', "
                    f"TIMESTAMP '{date} 0{i}:00:00+00', 'ref-{date}-{i}', 'ref-{date}-{i}', "
                    f"'claude_code', '1.0', 1, "
                    f"TIMESTAMP '{date} 0{i}:00:00+00', '{TENANT}', '{PROJECT}', 'seed-session')"
                )
        assert _file_count(con) == 2 * len(dates)

        con.execute("SET TimeZone='UTC'")
        con.execute("CALL ducklake_merge_adjacent_files('lake')")
        con.execute("CALL ducklake_cleanup_old_files('lake', cleanup_all => true)")

        remaining = con.execute("SELECT data_file FROM ducklake_list_files('lake', 'telemetry_sessions')").fetchall()
        assert len(remaining) == len(dates)

        for (path,) in remaining:
            file_dates = con.execute(
                f"SELECT DISTINCT date_trunc('day', session_started_at) FROM read_parquet('{path}')"
            ).fetchall()
            assert len(file_dates) == 1, f"{path} mixes more than one UTC calendar date: {file_dates}"


@pytest.mark.integration
def test_four_tables_one_transaction_all_or_nothing(tmp_path: Path, _skip_if_no_extension: None) -> None:
    from src.telemetry.append import execute_prepared, prepare_batch
    from src.telemetry.gate import GrainConflictError
    from src.turn_capture.record_turn import record_turn
    from tests.fixtures.turn_capture_corpus import PROJECT_REF, load_case, mem_tree
    from tests.fixtures.turn_capture_ducklake import create_tables, load_specs

    con = _local_catalog(tmp_path)
    specs = load_specs()
    create_tables(con, specs)
    files, _ = load_case("subagent_sync.json")
    result = record_turn(mem_tree(files), None, project_ref=PROJECT_REF, session_final=True)
    assert [t for t, rows in result.batches if rows] == [t for t, _ in result.batches]
    append_events(
        con, specs["telemetry_agents"], [dict(result.agents[0], model="a different model")],
        tenant_id=TENANT, project_id=PROJECT, catalog="lake",
    )  # fmt: skip

    def counts() -> dict[str, int]:
        return {t: con.execute(f"SELECT count(*) FROM lake.{t}").fetchone()[0] for t, _ in result.batches}

    before = counts()
    prepared = [prepare_batch(specs[t], rows, tenant_id=TENANT, project_id=PROJECT) for t, rows in result.batches]
    con.execute("BEGIN")
    with pytest.raises(GrainConflictError, match="TELEMETRY_GRAIN_CONFLICT"):
        for batch in prepared:
            execute_prepared(con, batch, catalog="lake")
    con.execute("ROLLBACK")
    assert counts() == before and before["telemetry_observations"] == 0 and before["telemetry_agents"] == 1

    clean_agents = prepare_batch(specs["telemetry_agents"], result.agents[1:], tenant_id=TENANT, project_id=PROJECT)
    con.execute("BEGIN")
    done = [execute_prepared(con, batch, catalog="lake") for batch in prepared[:2]] + [
        execute_prepared(con, clean_agents, catalog="lake"),
        execute_prepared(con, prepared[3], catalog="lake"),
    ]
    con.execute("COMMIT")
    assert [d.inserted for d in done] == [len(r) for t, r in result.batches if t != "telemetry_agents"][:2] + [
        len(result.agents) - 1,
        len(result.sessions),
    ]
    assert counts()["telemetry_agents"] == len(result.agents)
