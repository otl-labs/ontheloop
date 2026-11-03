"""DuckLake writer-owned SCD2 write path (T2.17 / CD.33, Decision 81; split from ducklake_runtime).

Owner concern: the write primitives (write_scd2, file_scd2) plus everything they call
intra-module -- OCC retry/backoff, rollback, metric emission, and the writer-owned
entity-id allocation cluster (Decision 84 I-2). Entity-allocation is co-located here
(rather than with the table-DDL concern in ducklake_tables) so write_scd2 ->
_advance_entity_counter and file_scd2 -> _allocate_entity_id stay intra-module calls --
no writes<->tables import cycle.

Dependency is strictly one-directional: this module imports shared schema symbols FROM
ducklake_scd2_schema (+ stdlib) only, and NEVER from the ducklake_runtime facade (Decision 80
acyclic-import discipline). Net DAG: ducklake_scd2_schema <- ducklake_writes <- ducklake_runtime.
"""

from __future__ import annotations

import random
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from src.common.ducklake_scd2_schema import (
    CATALOG_ALIAS,
    STATUS_TRANSITIONS,
    AppendOnlyUpdateError,
    DuckLakeRuntimeError,
    ReferentialError,
    SchemaGateError,
    StatusTransitionError,
    WriteIdentity,
    WriteResult,
    _build_merge_current_sql,
    _build_merge_history_sql,
    _write_params,
    check_append_only_guard,
    check_rec_status_transition,
    load_field_semantics,
    resolve_table_spec,
    schema_gate,
)
from src.common.ducklake_write_rules import RowRuleViolationError, check_write, existing_row_select, rules_for

# ---------------------------------------------------------------------------
# OCC retry budget (CD.33): bounded application-level retry with backoff + jitter, loud-fail on
# exhaustion. NOT a knob to loosen so a gate passes (Decision 55).
# ---------------------------------------------------------------------------

OCC_MAX_ATTEMPTS = 5
OCC_BASE_BACKOFF_S = 0.05
OCC_MAX_BACKOFF_S = 1.0

# Substrings of a Postgres/DuckLake error that indicate an optimistic-concurrency / serialization
# collision (the expected, retryable contention signal) rather than a hard failure.
_OCC_COLLISION_MARKERS = (
    "could not serialize",
    "deadlock detected",
    "concurrent update",
    "conflict",
    "transaction conflict",
    "write-write",
)

# CD.33 churn gate budget (single source -- rec-2091; ducklake_writer and smoke test import from here).
# Values are CD.33 / Decision 55 / Decision 81 invariants -- never relax without a Decision superseding CD.33.
COMMIT_LATENCY_BUDGET_MS = 2000.0  # p95 commit latency ceiling (in-Lambda, DIRECT endpoint)
OCC_COLLISION_RATE_BUDGET = 0.20  # max fraction of churn writers that hit an OCC collision
CHURN_WRITERS = 4  # concurrent invocation fan-out for EC8 (Decision 82: N steered 8->4; budget VALUES above unchanged)
CHURN_WRITES_PER_WRITER = 5  # writes per writer per churn iteration


# ---------------------------------------------------------------------------
# Exceptions -- loud-fail conditions specific to the write path
# ---------------------------------------------------------------------------


class OCCRetryExhaustedError(DuckLakeRuntimeError):
    """Raised when the bounded OCC-retry budget is exhausted (CD.33). Stop-and-RCA, never relax."""


# ---------------------------------------------------------------------------
# Write identity (minted once, outside the OCC-retry loop)
# ---------------------------------------------------------------------------


def mint_write_identity(*, now: datetime | None = None) -> WriteIdentity:
    """Mint the ULID + timestamp ONCE for a write op. Call OUTSIDE the OCC-retry loop (CD.33).

    The ULID embeds the same instant as `timestamp` (ULID.from_datetime), so the history PK and
    the SCD2 ordering key are coherent. On retry the SAME WriteIdentity is reused -- never re-minted.
    """
    from ulid import ULID  # noqa: PLC0415

    moment = now or datetime.now(timezone.utc)
    return WriteIdentity(ulid=str(ULID.from_datetime(moment)), timestamp=moment)


# ---------------------------------------------------------------------------
# The shared write primitive -- history MERGE-on-ULID + current write-through, bounded OCC retry
# ---------------------------------------------------------------------------


def is_occ_collision(exc: Exception) -> bool:
    """True if *exc* looks like an optimistic-concurrency / serialization collision (retryable)."""
    msg = str(exc).lower()
    return any(marker in msg for marker in _OCC_COLLISION_MARKERS)


def _occ_backoff(attempt: int, *, sleep: Callable[[float], None] = time.sleep) -> None:
    """Sleep with exponential backoff + full jitter before the next OCC retry."""
    ceiling = min(OCC_MAX_BACKOFF_S, OCC_BASE_BACKOFF_S * (2 ** (attempt - 1)))
    sleep(random.uniform(0.0, ceiling))


def write_scd2(
    con: Any,
    record: dict[str, Any],
    *,
    table: str | None = None,
    identity: WriteIdentity | None = None,
    semantics: dict[str, Any] | None = None,
    require_exists: bool = False,
    created_override: datetime | None = None,
    max_attempts: int = OCC_MAX_ATTEMPTS,
    metric_sink: Optional[Callable[[str, float], None]] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> WriteResult:
    """Write one SCD2 record: history MERGE-on-ULID append + current write-through, one transaction.

    `table=None` writes the smoke pair (T2.17); a name selects an ops_tables entry (T2.19). The
    merge key, column order, and MERGE SQL are resolved from the spec, so this single primitive
    drives every governance table.

    Idempotency (CD.33 D-2): the ULID + timestamp are minted ONCE here, OUTSIDE the retry loop, and
    reused on every attempt. MERGE-on-ULID de-duplicates the history append, so a retried write
    never double-appends. `created_timestamp` is carried unchanged from the existing current row on
    update, and minted (= identity.timestamp) on first insert -- never re-stamped.

    Referential gate (CD.33 cl.8 / D-5): with `require_exists=True` (the update path), the
    in-transaction existing-row lookup must be non-empty -- an absent merge key raises ReferentialError
    BEFORE any MERGE, replacing the prior permissive upsert-on-absent.

    Status-DAG gate (Decision 103 / T1.16 c3): when *table* declares a DAG (STATUS_TRANSITIONS) and
    `require_exists=True`, the SAME existing-row fetch above also reads the current `status` (no
    second Neon round-trip) and calls check_rec_status_transition before the MERGE -- a resolved rec
    (closed/declined/superseded) silently reactivated to `open` raises StatusTransitionError. This
    check is OUTSIDE the OCC-collision retry path (Decision 82 / Decision 88): it is terminal, never
    retried, mirroring ReferentialError handling.

    `created_override` (operational backfill/re-seed path ONLY): when set AND the row is a fresh
    insert, it supplies the historical `created_timestamp` instead of `identity.timestamp`, so a
    migration/re-seed preserves each rec's ORIGINAL created time (Decision-64 anchor) while
    `identity.timestamp` carries the original last_updated. It has NO effect on the agent write path
    (always None there). The maintenance `seed_ops_recommendations` action that used this was removed
    at the 2026-06-09 recs sign-off; the parameter is retained for any future break-glass re-seed.

    Concurrency (CD.33): a serialization collision is retried with bounded backoff+jitter up to
    `max_attempts`; exhaustion raises OCCRetryExhaustedError (loud-fail, Decision 55). A non-OCC
    error propagates immediately -- never swallowed.

    Emits OccRetryCount + CommitLatencyMs via `metric_sink` (EC9) when provided.
    """
    semantics = semantics if semantics is not None else load_field_semantics()
    schema_gate(record, semantics, table=table)  # loud-fail before any catalog work

    spec = resolve_table_spec(table, semantics)
    check_append_only_guard(spec, require_exists)  # loud-fail before any catalog work (Decision 70)
    has_status_dag = table in STATUS_TRANSITIONS
    merge_history_sql = _build_merge_history_sql(spec)
    merge_current_sql = None if spec.write_mode == "append_only" else _build_merge_current_sql(spec)
    rules = rules_for(spec)
    select_existing_sql, existing_columns = (
        (None, ()) if spec.write_mode == "append_only" else existing_row_select(spec, rules, has_status_dag)
    )

    identity = identity if identity is not None else mint_write_identity()
    key = record[spec.merge_key]

    occ_retries = 0
    start = time.perf_counter()
    attempt = 0
    created_ts: datetime = identity.timestamp

    while True:
        attempt += 1
        try:
            con.execute("BEGIN TRANSACTION")
            prior: dict[str, Any] | None = None
            if spec.write_mode == "append_only":
                created_ts = created_override if created_override is not None else identity.timestamp
            else:
                existing = con.execute(select_existing_sql, [key]).fetchall()
                prior = dict(zip(existing_columns, existing[0], strict=True)) if existing else None
                if require_exists and not existing:
                    raise ReferentialError(
                        f"update of absent {spec.merge_key}={key!r} in {spec.current_table}: the record does "
                        "not exist (CD.33 cl.8 / D-5). An absent rec loud-fails -- it is not silently created."
                    )
                if require_exists and has_status_dag and prior is not None:
                    check_rec_status_transition(table, prior["status"], record.get("status"))
                created_ts = (
                    prior["created_timestamp"]
                    if prior is not None
                    else (created_override if created_override is not None else identity.timestamp)
                )
            check_write(spec, rules, record, created_ts, identity.timestamp, identity.ulid, prior)
            params = _write_params(spec, record, identity, created_ts)
            con.execute(merge_history_sql, params)
            if merge_current_sql is not None:
                con.execute(merge_current_sql, params)
            _advance_entity_counter(con, spec, key)
            con.execute("COMMIT")
            break
        except (ReferentialError, AppendOnlyUpdateError, StatusTransitionError, RowRuleViolationError):
            _safe_rollback(con)
            raise  # terminal failures, never retried
        except Exception as exc:  # noqa: BLE001 -- classify, then retry-or-raise
            _safe_rollback(con)
            if is_occ_collision(exc):
                if attempt < max_attempts:
                    occ_retries += 1
                    _occ_backoff(attempt, sleep=sleep)
                    continue
                commit_ms = (time.perf_counter() - start) * 1000.0
                _emit_write_metrics(metric_sink, occ_retries, commit_ms)
                raise OCCRetryExhaustedError(
                    f"OCC retry budget exhausted after {attempt} attempts for {spec.merge_key}={key!r} "
                    f"(ulid={identity.ulid}). Stop and RCA the contention (Decision 55) -- do NOT "
                    "relax the budget."
                ) from exc
            raise  # non-OCC hard failure: loud-fail immediately

    commit_ms = (time.perf_counter() - start) * 1000.0
    _emit_write_metrics(metric_sink, occ_retries, commit_ms)
    return WriteResult(
        ulid=identity.ulid,
        rec_id=key,
        occ_retries=occ_retries,
        commit_ms=commit_ms,
        created_timestamp=created_ts,
        last_updated_timestamp=identity.timestamp,
    )


# ---------------------------------------------------------------------------
# Writer-owned entity-id allocation (Decision 84 I-2)
# ---------------------------------------------------------------------------

# Plain (non-SCD2) DuckLake bookkeeping table. The counter row is the keyspace serialization
# point: a concurrent file_scd2 pair both UPDATE the same row, which is a guaranteed write-write
# catalog conflict -- one commits, the other OCC-retries and re-allocates. Internal to the writer;
# never exposed through the read boundary. The table itself is a governed control-class table
# (T2.26): its DDL, partitioning, and provisioning are WHOLLY owned by
# src/common/ducklake_control_tables.py (bootstrap_entity_counter and ensure_entity_counters_table
# moved there with it) -- this module keeps only the intra-transaction allocate/advance primitives
# write_scd2/file_scd2 call directly, so the table NAME below is a local literal (not imported),
# keeping this module's dependency on ducklake_scd2_schema (+ stdlib) only, unchanged.
ENTITY_COUNTERS_TABLE = "ops_entity_counters"


def _allocate_entity_id(con: Any, spec: Any) -> str:
    """Allocate the next <prefix>NNN id for *spec* INSIDE the caller's open transaction.

    Requires the counter row to exist (bootstrap_entity_counter): every allocating transaction
    then UPDATEs the SAME shared row, which is the write-write conflict DuckLake's OCC detects --
    one committer wins, the rest retry and re-read. A missing or duplicated counter row is a
    terminal loud-fail (never self-seeded here; see bootstrap_entity_counter for why).
    """
    prefix = spec.entity_id_prefix
    if not prefix or spec.id_keyspace != "writer":
        raise DuckLakeRuntimeError(
            f"table {spec.table!r} has no writer-owned keyspace (id_keyspace={spec.id_keyspace!r}): "
            "file_ops allocation is not enabled for it (Decision 84 I-2)"
        )
    counter = f"{spec.table}"
    rows = con.execute(
        f"SELECT current_value FROM {CATALOG_ALIAS}.{ENTITY_COUNTERS_TABLE} WHERE counter_name = ?", [counter]
    ).fetchall()
    if len(rows) != 1:
        raise DuckLakeRuntimeError(
            f"entity counter for {counter!r} has {len(rows)} rows (expected exactly 1): "
            "run create_ops_tables to bootstrap/repair the counter (Decision 84 I-2). "
            "Allocation never self-seeds -- the concurrent-seed race mints duplicate ids."
        )
    # DuckLake does not support UPDATE ... RETURNING; the UPDATE is the write-write conflict
    # point and the follow-up SELECT reads our own uncommitted increment inside the transaction.
    con.execute(
        f"UPDATE {CATALOG_ALIAS}.{ENTITY_COUNTERS_TABLE} SET current_value = current_value + 1 WHERE counter_name = ?",
        [counter],
    )
    allocated = con.execute(
        f"SELECT current_value FROM {CATALOG_ALIAS}.{ENTITY_COUNTERS_TABLE} WHERE counter_name = ?",
        [counter],
    ).fetchone()
    n = int(allocated[0])
    return f"{prefix}{n:03d}"


def _advance_entity_counter(con: Any, spec: Any, key: Any) -> None:
    """Advance the allocation counter to cover a caller-keyed canonical id, in the open transaction.

    write_ops accepts caller-keyed <prefix>NNN ids (backfill; pre-merge main clients on the old
    allocator). Without this, any such id above the counter strands file_ops on the terminal
    "counter behind table max" guard until an operator re-bootstraps. No-op when the table has no
    writer-owned keyspace, the key is non-canonical, the counter row is absent (not bootstrapped),
    or the candidate does not exceed the stored value.

    No-op-free (Decision 55): read-then-conditional-write, deliberately. An earlier unconditional
    `UPDATE ... SET current_value = GREATEST(...)` issued a write -- and so minted a new DuckLake
    data file -- on EVERY write_scd2 call regardless of whether the counter needed to move,
    turning the counter row into a serialization point for writes that touched it in name only.
    Reading first and skipping the UPDATE when the candidate does not exceed the stored value
    removes that write entirely on the (overwhelmingly common) no-op path.
    """
    if spec.id_keyspace != "writer" or not spec.entity_id_prefix:
        return
    m = re.fullmatch(re.escape(spec.entity_id_prefix) + r"([0-9]+)", str(key))
    if not m:
        return
    candidate = int(m.group(1))
    rows = con.execute(
        f"SELECT current_value FROM {CATALOG_ALIAS}.{ENTITY_COUNTERS_TABLE} WHERE counter_name = ?",
        [spec.table],
    ).fetchall()
    if not rows or candidate <= rows[0][0]:
        return
    con.execute(
        f"UPDATE {CATALOG_ALIAS}.{ENTITY_COUNTERS_TABLE} SET current_value = ? WHERE counter_name = ?",
        [candidate, spec.table],
    )


def file_scd2(
    con: Any,
    record: dict[str, Any],
    *,
    table: str,
    identity: WriteIdentity | None = None,
    semantics: dict[str, Any] | None = None,
    max_attempts: int = OCC_MAX_ATTEMPTS,
    metric_sink: Optional[Callable[[str, float], None]] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> WriteResult:
    """Create one record, allocating its merge key INSIDE the write transaction (Decision 84 I-2).

    The record arrives WITHOUT the merge key; allocation, the require-absent check, and both MERGEs
    commit atomically. An OCC retry re-runs the whole transaction including allocation, so the
    retried attempt picks up a fresh max -- the counter-row UPDATE is the serialization point.

    Invocation idempotency (response-lost retry): `identity` is minted by the CALLER per logical
    file operation and replayed unchanged on retry. The in-transaction replay check (history ULID
    lookup) returns the originally allocated id instead of allocating anew, so a client retry after
    a lost response never double-files.
    """
    semantics = semantics if semantics is not None else load_field_semantics()
    spec = resolve_table_spec(table, semantics)
    if not spec.entity_id_prefix or spec.id_keyspace != "writer":
        raise DuckLakeRuntimeError(
            f"table {table!r} has no writer-owned keyspace (id_keyspace={spec.id_keyspace!r}): "
            "file_ops allocation is not enabled for it (Decision 84 I-2)"
        )
    if spec.merge_key in record:
        raise SchemaGateError(f"file operation must not supply {spec.merge_key!r}: the writer allocates it (Decision 84 I-2)")
    # Fail fast on every OTHER contract violation before touching the catalog: gate a copy with a
    # syntactically-valid placeholder key (the real key does not exist yet).
    placeholder = {**record, spec.merge_key: f"{spec.entity_id_prefix or 'x-'}0"}
    schema_gate(placeholder, semantics, table=table)
    identity = identity if identity is not None else mint_write_identity()
    check_write(spec, rules_for(spec), placeholder, identity.timestamp, identity.timestamp, identity.ulid, None)

    merge_history_sql = _build_merge_history_sql(spec)
    merge_current_sql = _build_merge_current_sql(spec)
    select_existing_sql, _ = existing_row_select(spec, None, False)

    occ_retries = 0
    start = time.perf_counter()
    attempt = 0

    while True:
        attempt += 1
        try:
            con.execute("BEGIN TRANSACTION")
            # Replay check: a retried invocation carries the same ULID; return the original allocation.
            replay = con.execute(
                f"SELECT {spec.merge_key}, created_timestamp FROM {CATALOG_ALIAS}.{spec.history_table} WHERE ulid = ?",
                [identity.ulid],
            ).fetchall()
            if replay:
                con.execute("COMMIT")
                commit_ms = (time.perf_counter() - start) * 1000.0
                _emit_write_metrics(metric_sink, occ_retries, commit_ms)
                return WriteResult(
                    ulid=identity.ulid,
                    rec_id=replay[0][0],
                    occ_retries=occ_retries,
                    commit_ms=commit_ms,
                    created_timestamp=replay[0][1],
                    last_updated_timestamp=identity.timestamp,
                )
            key = _allocate_entity_id(con, spec)
            existing = con.execute(select_existing_sql, [key]).fetchall()
            if existing:
                raise DuckLakeRuntimeError(
                    f"allocated {spec.merge_key}={key!r} already exists in {spec.current_table}: "
                    "counter behind table max -- stop and RCA (Decision 55), do not retry past it"
                )
            params = _write_params(spec, {**record, spec.merge_key: key}, identity, identity.timestamp)
            con.execute(merge_history_sql, params)
            con.execute(merge_current_sql, params)
            con.execute("COMMIT")
            commit_ms = (time.perf_counter() - start) * 1000.0
            _emit_write_metrics(metric_sink, occ_retries, commit_ms)
            return WriteResult(
                ulid=identity.ulid,
                rec_id=key,
                occ_retries=occ_retries,
                commit_ms=commit_ms,
                created_timestamp=identity.timestamp,
                last_updated_timestamp=identity.timestamp,
            )
        except DuckLakeRuntimeError:
            _safe_rollback(con)
            raise  # contract/keyspace failures are terminal, never retried
        except Exception as exc:  # noqa: BLE001 -- classify, then retry-or-raise
            _safe_rollback(con)
            if is_occ_collision(exc):
                if attempt < max_attempts:
                    occ_retries += 1
                    _occ_backoff(attempt, sleep=sleep)
                    continue
                commit_ms = (time.perf_counter() - start) * 1000.0
                _emit_write_metrics(metric_sink, occ_retries, commit_ms)
                raise OCCRetryExhaustedError(
                    f"OCC retry budget exhausted after {attempt} attempts for file_ops on {table} "
                    f"(ulid={identity.ulid}). Stop and RCA the contention (Decision 55)."
                ) from exc
            raise


def _safe_rollback(con: Any) -> None:
    """Roll back the current transaction, swallowing a 'no active transaction' error only."""
    try:
        con.execute("ROLLBACK")
    except Exception:  # noqa: BLE001 -- rollback failure must not mask the original error
        pass


def _emit_write_metrics(metric_sink: Optional[Callable[[str, float], None]], occ_retries: int, commit_ms: float) -> None:
    """Emit the OccRetryCount + CommitLatencyMs metrics through the sink, if provided."""
    if metric_sink is None:
        return
    metric_sink("OccRetryCount", float(occ_retries))
    metric_sink("CommitLatencyMs", commit_ms)
