"""MIRROR for src/common/ducklake_writes.py (rec-2709 Wave 7).

Split out of the former tests/test_ducklake_runtime.py monolith: the write primitives
(mint_write_identity, is_occ_collision/_occ_backoff, write_scd2 -- core derivation/idempotency/OCC,
table-parameterized require_exists + status-DAG, and append_only -- _safe_rollback/
_emit_write_metrics, the churn budget constants, and the writer-owned entity-id allocation
cluster (Decision 84 I-2): file_scd2/bootstrap_entity_counter/_advance_entity_counter, with the
FileOpsCon test double (single-consumer) and the _APPEND_ONLY_RT_SEMANTICS dict (single-consumer).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.common import ducklake_runtime as rt
from tests.fixtures.ducklake_fakes import _SEMANTICS, FakeCon, ops_rec_fields, ops_rec_record

pytestmark = pytest.mark.unit


def test_mint_write_identity_default_now():
    wid = rt.mint_write_identity()
    assert len(wid.ulid) == 26
    assert wid.timestamp.tzinfo is not None


def test_mint_write_identity_explicit_now():
    moment = datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)
    wid = rt.mint_write_identity(now=moment)
    assert wid.timestamp == moment
    assert len(wid.ulid) == 26


def test_churn_budget_constants_values():
    assert rt.COMMIT_LATENCY_BUDGET_MS == 2000.0
    assert rt.OCC_COLLISION_RATE_BUDGET == 0.20
    assert rt.CHURN_WRITERS == 4  # Decision 82: N steered 8->4; budget VALUES (above) unchanged
    assert rt.CHURN_WRITES_PER_WRITER == 5


def test_is_occ_collision_true_and_false():
    assert rt.is_occ_collision(Exception("ERROR: could not serialize access")) is True
    assert rt.is_occ_collision(Exception("transaction conflict detected")) is True
    assert rt.is_occ_collision(Exception("relation does not exist")) is False


def test_occ_backoff_sleeps():
    slept: list[float] = []
    rt._occ_backoff(3, sleep=slept.append)
    assert len(slept) == 1
    assert 0.0 <= slept[0] <= rt.OCC_MAX_BACKOFF_S


def test_write_scd2_insert_path():
    con = FakeCon(created_lookup=[])  # no existing -> insert
    wid = rt.mint_write_identity(now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    result = rt.write_scd2(con, {"rec_id": "rec-1", "payload": "v1"}, identity=wid, semantics=_SEMANTICS)
    assert result.ulid == wid.ulid
    assert result.created_timestamp == wid.timestamp
    assert result.occ_retries == 0
    # both MERGEs + COMMIT executed
    sqls = [s for s, _ in con.executed]
    assert any(rt.SMOKE_HISTORY_TABLE in s and s.startswith("MERGE") for s in sqls)
    assert any(rt.SMOKE_CURRENT_TABLE in s and s.startswith("MERGE") for s in sqls)
    assert sqls.count("COMMIT") == 1


def test_created_timestamp_carried_on_update():
    carried = datetime(2025, 12, 1, tzinfo=timezone.utc)
    con = FakeCon(created_lookup=[(carried,)])  # existing row -> update path carries created
    wid = rt.mint_write_identity(now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    result = rt.write_scd2(con, {"rec_id": "rec-1", "payload": "v2"}, identity=wid, semantics=_SEMANTICS)
    # created carried from the existing row, NOT re-stamped to identity.timestamp
    assert result.created_timestamp == carried
    assert result.last_updated_timestamp == wid.timestamp
    # the history MERGE bound the carried created_timestamp (param index 3)
    hist_params = con.merge_history_params()[0]
    assert hist_params[3] == carried


def test_write_scd2_created_override_on_insert():
    """Seed path: created_override supplies the historical created on a FRESH insert; last_updated stays
    identity.timestamp. The agent path leaves created_override=None -> created = identity.timestamp."""
    con = FakeCon(created_lookup=[])  # fresh insert (no existing row)
    wid = rt.mint_write_identity(now=datetime(2026, 6, 1, tzinfo=timezone.utc))
    original_created = datetime(2026, 5, 2, tzinfo=timezone.utc)
    result = rt.write_scd2(
        con,
        {"rec_id": "rec-1", "payload": "v1"},
        identity=wid,
        semantics=_SEMANTICS,
        created_override=original_created,
    )
    assert result.created_timestamp == original_created  # preserved, NOT re-stamped to identity.timestamp
    assert result.last_updated_timestamp == wid.timestamp
    assert con.merge_history_params()[0][3] == original_created


def test_last_updated_minted_once():
    con = FakeCon(created_lookup=[], occ_fail_times=2)  # fail twice then succeed
    wid = rt.mint_write_identity(now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    rt.write_scd2(con, {"rec_id": "rec-1"}, identity=wid, semantics=_SEMANTICS, sleep=lambda s: None)
    # last_updated_timestamp (param index 4) identical across every attempt
    last_updated = {(p[4],) for p in con.merge_history_params()}
    assert last_updated == {(wid.timestamp,)}


def test_ulid_minted_once_outside_retry():
    """The ULID is minted once and is the SAME across the lookup->merge->retry sequence."""
    con = FakeCon(created_lookup=[], occ_fail_times=3)
    wid = rt.mint_write_identity()
    rt.write_scd2(con, {"rec_id": "rec-1"}, identity=wid, semantics=_SEMANTICS, sleep=lambda s: None)
    ulids = {p[0] for p in con.merge_history_params()}
    assert ulids == {wid.ulid}  # identical on every attempt


def test_ulid_stable_across_retry():
    """A retried write reuses its ULID; the number of attempts equals fails+1, all same ULID."""
    con = FakeCon(created_lookup=[], occ_fail_times=2)
    wid = rt.mint_write_identity()
    rt.write_scd2(con, {"rec_id": "rec-1"}, identity=wid, semantics=_SEMANTICS, sleep=lambda s: None)
    attempts = con.merge_history_params()
    assert len(attempts) == 3  # 2 failures + 1 success
    assert all(p[0] == wid.ulid for p in attempts)


def test_write_scd2_mints_identity_when_absent():
    con = FakeCon(created_lookup=[])
    result = rt.write_scd2(con, {"rec_id": "rec-1"}, semantics=_SEMANTICS)
    assert len(result.ulid) == 26


def test_occ_retry_exhaustion_raises():
    con = FakeCon(created_lookup=[], occ_fail_times=99)  # always collide
    wid = rt.mint_write_identity()
    with pytest.raises(rt.OCCRetryExhaustedError, match="exhausted"):
        rt.write_scd2(con, {"rec_id": "rec-1"}, identity=wid, semantics=_SEMANTICS, max_attempts=3, sleep=lambda s: None)
    # ULID still minted once across all exhausted attempts
    assert {p[0] for p in con.merge_history_params()} == {wid.ulid}


def test_write_scd2_non_occ_error_propagates():
    con = FakeCon(created_lookup=[], hard_fail_substr=rt.SMOKE_HISTORY_TABLE)
    with pytest.raises(ValueError, match="hard failure"):
        rt.write_scd2(con, {"rec_id": "rec-1"}, semantics=_SEMANTICS)
    # rolled back, did not retry
    assert any(s == "ROLLBACK" for s, _ in con.executed)


def test_write_scd2_schema_gate_blocks_before_catalog():
    con = FakeCon(created_lookup=[])
    with pytest.raises(rt.SchemaGateError):
        rt.write_scd2(con, {"rec_id": "rec-1", "bogus": "x"}, semantics=_SEMANTICS)
    assert con.executed == []  # gate fires before any SQL


def test_write_scd2_emits_metrics():
    con = FakeCon(created_lookup=[], occ_fail_times=1)
    emitted: list[tuple[str, float]] = []
    rt.write_scd2(
        con,
        {"rec_id": "rec-1"},
        semantics=_SEMANTICS,
        metric_sink=lambda n, v: emitted.append((n, v)),
        sleep=lambda s: None,
    )
    names = {n for n, _ in emitted}
    assert names == {"OccRetryCount", "CommitLatencyMs"}
    occ = next(v for n, v in emitted if n == "OccRetryCount")
    assert occ == 1.0


def test_write_scd2_loads_default_semantics(monkeypatch):
    monkeypatch.delenv(rt._FIELD_SEMANTICS_ENV, raising=False)
    con = FakeCon(created_lookup=[])
    result = rt.write_scd2(con, {"rec_id": "rec-1", "payload": "x"})  # default contract
    assert result.rec_id == "rec-1"


def test_safe_rollback_swallows_error():
    con = FakeCon(rollback_raises=True)
    rt._safe_rollback(con)  # must not raise


def test_emit_write_metrics_none_sink_noop():
    rt._emit_write_metrics(None, 1, 2.0)  # no raise, no-op


def test_write_scd2_ops_binds_columns_in_order(monkeypatch):
    """write_scd2(table=...) binds ulid, then inputs (id first), then created/updated -- in column order."""
    con = FakeCon(created_lookup=None)  # insert path (no existing row)
    moment = datetime(2026, 6, 8, tzinfo=timezone.utc)
    identity = rt.mint_write_identity(now=moment)
    record = ops_rec_record(automatable=False, execution_steps=2)
    result = rt.write_scd2(con, record, table="ops_recommendations", identity=identity)
    assert result.rec_id == "rec-1"
    # The history MERGE params: positional, matching ordered_columns.
    spec = rt.resolve_table_spec("ops_recommendations")
    ordered = [c for c, _ in spec.ordered_columns]
    hist_params = [p for sql, p in con.executed if "ops_recommendations_history" in sql and sql.startswith("MERGE INTO")]
    assert len(hist_params) == 1
    params = hist_params[0]
    assert params[0] == identity.ulid
    assert params[ordered.index("id")] == "rec-1"
    assert params[ordered.index("status")] == "open"
    assert params[ordered.index("created_timestamp")] == moment
    assert params[ordered.index("last_updated_timestamp")] == moment


def test_write_scd2_ops_require_exists_loud_fails_on_absent():
    """update path (require_exists=True) raises ReferentialError when the merge key is absent."""
    con = FakeCon(created_lookup=None)  # no existing current row
    record = ops_rec_record(id="rec-absent", status="closed")
    with pytest.raises(rt.ReferentialError, match="absent"):
        rt.write_scd2(con, record, table="ops_recommendations", require_exists=True)
    # The MERGE must NOT have run (rolled back before any write).
    merged = [sql for sql, _ in con.executed if sql.startswith("MERGE INTO")]
    assert merged == []


def test_write_scd2_ops_require_exists_proceeds_when_present():
    """update path proceeds and carries the original created_timestamp when the row exists."""
    original = datetime(2026, 1, 1, tzinfo=timezone.utc)
    con = FakeCon(created_lookup=[(original, "open")])  # existing current row, status included
    record = ops_rec_record(status="closed")
    result = rt.write_scd2(con, record, table="ops_recommendations", require_exists=True)
    assert result.created_timestamp == original  # carried, not re-stamped
    assert any(sql.startswith("MERGE INTO") for sql, _ in con.executed)


def test_write_scd2_ops_require_exists_select_includes_status():
    """The require_exists existing-row fetch on a DAG-declaring table selects status too (no 2nd round-trip)."""
    con = FakeCon(created_lookup=[(datetime(2026, 1, 1, tzinfo=timezone.utc), "open")])
    rt.write_scd2(con, ops_rec_record(status="closed"), table="ops_recommendations", require_exists=True)
    selects = [sql for sql, _ in con.executed if sql.startswith("SELECT created_timestamp")]
    assert len(selects) == 1
    assert "status" in selects[0]


def test_write_scd2_ops_require_exists_rejects_resolved_reactivation():
    """A resolved rec (closed) reactivated to open raises StatusTransitionError before any MERGE."""
    con = FakeCon(created_lookup=[(datetime(2026, 1, 1, tzinfo=timezone.utc), "closed")])
    with pytest.raises(rt.StatusTransitionError, match="illegal status transition"):
        rt.write_scd2(con, ops_rec_record(), table="ops_recommendations", require_exists=True)
    merged = [sql for sql, _ in con.executed if sql.startswith("MERGE INTO")]
    assert merged == []


def test_write_scd2_ops_require_exists_allows_live_transitions():
    """Every live transition (failed->open, *->superseded, open/failed->declined) proceeds to MERGE."""
    live = [("failed", "open"), ("open", "superseded"), ("closed", "superseded"), ("failed", "declined"), ("open", "declined")]
    for existing_status, new_status in live:
        con = FakeCon(created_lookup=[(datetime(2026, 1, 1, tzinfo=timezone.utc), existing_status)])
        rt.write_scd2(con, ops_rec_record(status=new_status), table="ops_recommendations", require_exists=True)
        assert any(sql.startswith("MERGE INTO") for sql, _ in con.executed), (existing_status, new_status)


def test_write_scd2_ops_require_exists_skips_unknown_vocab():
    """An unrecognised status value is skipped narrowly (never treated as illegal)."""
    con = FakeCon(created_lookup=[(datetime(2026, 1, 1, tzinfo=timezone.utc), "banana")])
    rt.write_scd2(con, ops_rec_record(), table="ops_recommendations", require_exists=True)
    assert any(sql.startswith("MERGE INTO") for sql, _ in con.executed)


def test_write_scd2_no_dag_table_select_omits_status():
    """A table with no declared DAG (ops_decisions) does not select status on the require_exists fetch."""
    con = FakeCon(created_lookup=[(datetime(2026, 1, 1, tzinfo=timezone.utc),)])
    rt.write_scd2(con, {"id": "dec-1", "title": "t", "status": "open"}, table="ops_decisions", require_exists=True)
    selects = [sql for sql, _ in con.executed if sql.startswith("SELECT created_timestamp")]
    assert len(selects) == 1
    assert "status" not in selects[0]


class FileOpsCon:
    """Scripted double for the file_scd2 transaction shape against ops_recommendations."""

    def __init__(
        self,
        *,
        replay_rows: list | None = None,
        counter_value: int | None = None,
        seed_max: int = 2170,
        existing_rows: list | None = None,
        occ_fail_on_update: int = 0,
        hard_fail_substr: str | None = None,
    ):
        self.executed: list[tuple[str, list | None]] = []
        self._replay_rows = replay_rows or []
        self._counter_value = counter_value
        self._seed_max = seed_max
        self._existing_rows = existing_rows or []
        self._occ_fail_on_update = occ_fail_on_update
        self._hard_fail_substr = hard_fail_substr
        self._update_calls = 0
        self._last = ""
        self.description = [("c",)]

    def execute(self, sql, params=None):
        self._last = sql
        self.executed.append((sql, params))
        if self._hard_fail_substr and self._hard_fail_substr in sql:
            raise ValueError("hard failure -- not a collision")
        if "UPDATE" in sql and rt.ENTITY_COUNTERS_TABLE in sql:
            self._update_calls += 1
            if self._update_calls <= self._occ_fail_on_update:
                raise RuntimeError("could not serialize access due to concurrent update")
            if self._counter_value is None:
                self._counter_value = self._seed_max
            self._counter_value += 1
        if "INSERT INTO" in sql and rt.ENTITY_COUNTERS_TABLE in sql:
            self._counter_value = params[1] if params else self._seed_max
        return self

    def fetchone(self):
        if "SELECT current_value" in self._last:
            return None if self._counter_value is None else (self._counter_value,)
        if "coalesce(max(CAST(regexp_extract" in self._last:
            return (self._seed_max,)
        return None

    def fetchall(self):
        if "WHERE ulid = ?" in self._last:
            return self._replay_rows
        if self._last.startswith("SELECT created_timestamp"):
            return self._existing_rows
        if "SELECT current_value" in self._last:
            return [] if self._counter_value is None else [(self._counter_value,)]
        return []


def test_file_scd2_allocates_next_id_from_counter():
    con = FileOpsCon(counter_value=2170)
    result = rt.file_scd2(con, ops_rec_fields(), table="ops_recommendations")
    assert result.rec_id == "rec-2171"
    merges = [s for s, _ in con.executed if s.startswith("MERGE INTO")]
    assert len(merges) == 2


def test_file_scd2_missing_counter_is_terminal():
    """The hot path NEVER self-seeds: the concurrent-seed race mints duplicate ids (live 2026-06-11)."""
    con = FileOpsCon(counter_value=None)
    with pytest.raises(rt.DuckLakeRuntimeError, match="run create_ops_tables"):
        rt.file_scd2(con, ops_rec_fields(), table="ops_recommendations")
    assert ("ROLLBACK", None) in con.executed
    assert not any(s.startswith("MERGE INTO") for s, _ in con.executed)


def test_allocate_entity_id_rejects_no_writer_keyspace():
    """_allocate_entity_id's own keyspace guard (defense-in-depth: file_scd2 already refuses
    before ever calling it, but the private helper must not silently mis-mint if reached
    directly)."""
    with pytest.raises(rt.DuckLakeRuntimeError, match="no writer-owned keyspace"):
        rt._allocate_entity_id(FileOpsCon(), rt.resolve_table_spec("ops_priority_queue"))


def test_file_scd2_occ_retry_exhaustion_raises():
    con = FileOpsCon(counter_value=2170, occ_fail_on_update=99)  # always collide
    with pytest.raises(rt.OCCRetryExhaustedError, match="exhausted"):
        rt.file_scd2(con, ops_rec_fields(), table="ops_recommendations", sleep=lambda s: None)


def test_file_scd2_non_occ_error_propagates():
    """A hard (non-collision) failure during file_scd2 rolls back and re-raises unconditionally --
    never retried, never reclassified as an OCC collision."""
    con = FileOpsCon(counter_value=2170, hard_fail_substr="MERGE INTO")
    with pytest.raises(ValueError, match="hard failure"):
        rt.file_scd2(con, ops_rec_fields(), table="ops_recommendations")
    assert any(s == "ROLLBACK" for s, _ in con.executed)


def test_bootstrap_entity_counter_seeds_from_history_max():
    con = FileOpsCon(seed_max=2178)
    seed = rt.bootstrap_entity_counter(con, rt.resolve_table_spec("ops_recommendations"))
    assert seed == 2178
    deletes = [s for s, _ in con.executed if s.startswith("DELETE FROM") and rt.ENTITY_COUNTERS_TABLE in s]
    inserts = [s for s, _ in con.executed if "INSERT INTO" in s and rt.ENTITY_COUNTERS_TABLE in s]
    creates = [s for s, _ in con.executed if s.startswith("CREATE TABLE IF NOT EXISTS")]
    assert deletes and inserts and creates
    assert ("COMMIT", None) in con.executed


def test_bootstrap_entity_counter_rejects_unprefixed_table():
    with pytest.raises(rt.DuckLakeRuntimeError, match="no allocation counter"):
        rt.bootstrap_entity_counter(FileOpsCon(), rt.resolve_table_spec("ops_priority_queue"))


def test_file_scd2_zero_pads_small_ids():
    con = FileOpsCon(counter_value=7)
    result = rt.file_scd2(con, ops_rec_fields(), table="ops_recommendations")
    assert result.rec_id == "rec-008"


def test_file_scd2_replay_returns_original_id_without_allocating():
    ts = datetime(2026, 6, 10, tzinfo=timezone.utc)
    identity = rt.mint_write_identity()
    con = FileOpsCon(replay_rows=[("rec-2160", ts)])
    result = rt.file_scd2(con, ops_rec_fields(), table="ops_recommendations", identity=identity)
    assert result.rec_id == "rec-2160"
    assert result.created_timestamp == ts
    assert not any("UPDATE" in s and rt.ENTITY_COUNTERS_TABLE in s for s, _ in con.executed)
    assert not any(s.startswith("MERGE INTO") for s, _ in con.executed)


def test_file_scd2_rejects_caller_supplied_merge_key():
    with pytest.raises(rt.SchemaGateError, match="must not supply"):
        rt.file_scd2(FileOpsCon(), {"id": "rec-1", "status": "open"}, table="ops_recommendations")


def test_file_scd2_rejects_table_without_prefix():
    con = FileOpsCon()
    with pytest.raises(rt.DuckLakeRuntimeError, match="no writer-owned keyspace"):
        rt.file_scd2(con, {"queue_run_id": "q"}, table="ops_priority_queue")
    assert con.executed == []


def test_file_scd2_rejects_caller_keyspace_table():
    """Decision 84 I-2 exception: dec-NNN follows DECISIONS.md numbering -- file_ops must refuse."""
    con = FileOpsCon()
    with pytest.raises(rt.DuckLakeRuntimeError, match="no writer-owned keyspace"):
        rt.file_scd2(con, {"title": "t", "status": "open"}, table="ops_decisions")
    assert con.executed == []


def test_bootstrap_entity_counter_rejects_caller_keyspace():
    with pytest.raises(rt.DuckLakeRuntimeError, match="no writer-owned keyspace"):
        rt.bootstrap_entity_counter(FileOpsCon(), rt.resolve_table_spec("ops_decisions"))


def test_write_scd2_advances_counter_for_canonical_caller_key():
    """A caller-keyed rec-NNN write_ops (backfill / pre-merge clients) must never strand the counter."""
    con = FileOpsCon(counter_value=2170)
    rt.write_scd2(con, ops_rec_record(id="rec-2200"), table="ops_recommendations")
    advances = [(s, p) for s, p in con.executed if s.startswith("UPDATE") and rt.ENTITY_COUNTERS_TABLE in s]
    assert advances and advances[0][1] == [2200, "ops_recommendations"]


def test_write_scd2_no_counter_advance_for_noncanonical_key():
    con = FileOpsCon(counter_value=2170)
    rt.write_scd2(con, ops_rec_record(id="test-probe-1"), table="ops_recommendations")
    assert not any(s.startswith("UPDATE") and rt.ENTITY_COUNTERS_TABLE in s for s, _ in con.executed)


def test_advance_counter_noop_writes_no_files():
    """A no-op counter advance (candidate <= current) issues no UPDATE at all.

    Red before the fix: the old unconditional `UPDATE ... SET current_value = GREATEST(...)`
    issued a write -- and so minted a new DuckLake data file -- even when the candidate id did
    not exceed the stored value, turning the counter row into a global write-lock/file-churn
    source. Observation surface: the recording connection fake's executed-SQL list (no UPDATE
    issued), per VP step 1 -- a SQL-predicate form cannot be discriminated here because the
    UPDATE would still be issued and merely match zero rows.
    """
    con = FileOpsCon(counter_value=2170)
    rt.write_scd2(con, ops_rec_record(id="rec-2100"), table="ops_recommendations")
    assert not any(s.startswith("UPDATE") and rt.ENTITY_COUNTERS_TABLE in s for s, _ in con.executed)


def test_file_scd2_allocated_collision_is_terminal():
    ts = datetime(2026, 6, 10, tzinfo=timezone.utc)
    con = FileOpsCon(counter_value=2170, existing_rows=[(ts,)])
    with pytest.raises(rt.DuckLakeRuntimeError, match="already exists"):
        rt.file_scd2(con, ops_rec_fields(), table="ops_recommendations")
    assert ("ROLLBACK", None) in con.executed


def test_file_scd2_occ_retry_reallocates():
    """An aborted transaction's counter increment rolls back; the retry re-issues the same number."""
    con = FileOpsCon(counter_value=2170, occ_fail_on_update=1)
    result = rt.file_scd2(con, ops_rec_fields(), table="ops_recommendations", sleep=lambda s: None)
    assert result.occ_retries == 1
    assert result.rec_id == "rec-2171"
    assert ("ROLLBACK", None) in con.executed


def test_file_scd2_gate_blocks_before_catalog():
    con = FileOpsCon()
    with pytest.raises(rt.SchemaGateError):
        rt.file_scd2(con, ops_rec_fields(bogus="x"), table="ops_recommendations")
    assert con.executed == []


def test_file_scd2_sequential_allocations_are_distinct_and_monotonic():
    """c2 distinct-id lock (T2.28): sequential file_scd2 allocations yield distinct, monotonic rec-NNN ids."""
    con = FileOpsCon(counter_value=2170)
    r1 = rt.file_scd2(con, ops_rec_fields(title="first recommendation"), table="ops_recommendations")
    r2 = rt.file_scd2(con, ops_rec_fields(title="second recommendation"), table="ops_recommendations")
    assert r1.rec_id != r2.rec_id, "sequential allocations must be distinct"
    n1 = int(r1.rec_id.split("-")[1])
    n2 = int(r2.rec_id.split("-")[1])
    assert n2 > n1, f"allocations must be monotonic: {r1.rec_id} then {r2.rec_id}"


_APPEND_ONLY_RT_SEMANTICS: dict = {
    "fields": {
        "ulid": {"role": "derived", "sql_type": "VARCHAR", "nullable": False},
        "rec_id": {"role": "input", "sql_type": "VARCHAR", "nullable": False},
        "created_timestamp": {"role": "derived", "sql_type": "TIMESTAMP WITH TIME ZONE", "nullable": False},
        "last_updated_timestamp": {"role": "derived", "sql_type": "TIMESTAMP WITH TIME ZONE", "nullable": False},
    },
    "ops_tables": {
        "ops_smoke_events": {
            "write_mode": "append_only",
            "status": "smoke",
            "merge_key": "event_id",
            "history_table": "ops_smoke_events_history",
            "partition": {"history": "year(created_timestamp), month(created_timestamp), day(created_timestamp)"},
            "columns": {
                "ulid": {"role": "derived", "sql_type": "VARCHAR", "nullable": False},
                "event_id": {"role": "input", "sql_type": "VARCHAR", "nullable": False},
                "event_type": {"role": "input", "sql_type": "VARCHAR", "nullable": True},
                "created_timestamp": {"role": "derived", "sql_type": "TIMESTAMP WITH TIME ZONE", "nullable": False},
                "last_updated_timestamp": {"role": "derived", "sql_type": "TIMESTAMP WITH TIME ZONE", "nullable": False},
            },
        }
    },
}


def test_write_scd2_append_only_skips_current_merge():
    """append_only write issues history MERGE only; no current write-through MERGE or SELECT existing."""
    con = FakeCon()
    rt.write_scd2(
        con,
        {"event_id": "ev-001", "event_type": "smoke"},
        table="ops_smoke_events",
        semantics=_APPEND_ONLY_RT_SEMANTICS,
    )
    sql_stmts = [sql for sql, _ in con.executed]
    assert any("ops_smoke_events_history" in s and s.startswith("MERGE INTO") for s in sql_stmts)
    assert not any("ops_smoke_events_current" in s for s in sql_stmts)
    # No SELECT created_timestamp (no existing-row lookup on append_only path)
    assert not any(s.startswith("SELECT created_timestamp") for s in sql_stmts)


def test_write_scd2_append_only_require_exists_raises():
    """write_scd2 with require_exists=True on an append_only table raises AppendOnlyUpdateError before SQL."""
    con = FakeCon()
    with pytest.raises(rt.AppendOnlyUpdateError, match="append_only"):
        rt.write_scd2(
            con,
            {"event_id": "ev-002", "event_type": "smoke"},
            table="ops_smoke_events",
            semantics=_APPEND_ONLY_RT_SEMANTICS,
            require_exists=True,
        )
    # Guard fires before any catalog work
    executed_sql = [sql for sql, _ in con.executed]
    assert not any(s.startswith("BEGIN") for s in executed_sql)


def test_write_scd2_append_only_ulid_idempotency():
    """Retrying write_scd2 with the same WriteIdentity produces identical ULID in both MERGEs.
    DuckLake MERGE-on-ULID (WHEN NOT MATCHED only) makes the second MERGE a no-op -- one row."""
    fixed_identity = rt.mint_write_identity(now=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc))
    con1, con2 = FakeCon(), FakeCon()
    rt.write_scd2(
        con1,
        {"event_id": "ev-idem", "event_type": "t"},
        table="ops_smoke_events",
        semantics=_APPEND_ONLY_RT_SEMANTICS,
        identity=fixed_identity,
    )
    rt.write_scd2(
        con2,
        {"event_id": "ev-idem", "event_type": "t"},
        table="ops_smoke_events",
        semantics=_APPEND_ONLY_RT_SEMANTICS,
        identity=fixed_identity,
    )

    def _merge_ulid(con: FakeCon) -> str | None:
        for sql, params in con.executed:
            if sql.startswith("MERGE INTO") and "ops_smoke_events_history" in sql:
                return params[0] if params else None  # ulid is ordered first in _write_params
        return None

    assert _merge_ulid(con1) == _merge_ulid(con2) == fixed_identity.ulid


def test_write_scd2_append_only_second_distinct_event_appends():
    """Two distinct event_ids each generate a separate history MERGE; neither collapses into the other."""
    con = FakeCon()
    rt.write_scd2(
        con,
        {"event_id": "ev-first", "event_type": "a"},
        table="ops_smoke_events",
        semantics=_APPEND_ONLY_RT_SEMANTICS,
    )
    rt.write_scd2(
        con,
        {"event_id": "ev-second", "event_type": "b"},
        table="ops_smoke_events",
        semantics=_APPEND_ONLY_RT_SEMANTICS,
    )
    history_merges = [sql for sql, _ in con.executed if sql.startswith("MERGE INTO") and "_history" in sql]
    current_merges = [sql for sql, _ in con.executed if sql.startswith("MERGE INTO") and "_current" in sql]
    assert len(history_merges) == 2, f"expected 2 history MERGEs; got {len(history_merges)}"
    assert len(current_merges) == 0, "append_only: no current write-through MERGE"
