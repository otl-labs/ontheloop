"""Current-table delete compaction for the 6-hourly merge_ops pass (Decision 88 amended, rec-5179).

A Type-1 current projection is MERGE-maintained, so its live data files accumulate delete files that
the engine's default rewrite threshold (0.95) never reaches; a fresh reader connection then opens
every one of them. compact_current_tables rewrites each in-scope current table's delete-bearing files
at an explicit threshold (0.0) before the merge consolidates them, proving the rewrite row- and
digest-preserving at the commit's immediate predecessor snapshot and re-proving each table's previous
rewrite commit on every pass while that predecessor still exists.

Failure split: a per-table error, a refusal or a catalog-level error is RECORDED (the caller still
merges every table and fails the pass); a proof mismatch, or a rewrite that processed files but whose
token resolves no snapshot, raises CurrentCompactionProofError at once (the breaker 500). Never
auto-restores: current rebuilds from history (Decision 81 cl.8).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Callable

from src.common import ducklake_maintenance as maint
from src.common import ducklake_maintenance_scope as scope
from src.common.ducklake_partition_layout import TableLayout, read_partition_layout
from src.common.ducklake_partition_rewrite import _row_proof
from src.common.ducklake_scd2_schema import CATALOG_ALIAS
from src.common.ducklake_writes import OCC_MAX_ATTEMPTS, _occ_backoff, is_occ_collision

logger = logging.getLogger(__name__)

VERB = "rewrite_current_deletes"
CURRENT_REWRITE_DELETE_THRESHOLD = 0.0
COMPACTION_TIME_BUDGET_S = 450.0
LINE_PREFIX = "CURRENT_COMPACTION"


class CurrentCompactionProofError(maint.DuckLakeMaintenanceError):
    """A rewrite whose row count or full-row digest differs across its commit, or that processed files
    yet left no snapshot for its token. Raised at once: an unproven rewrite is never superseded."""


def _safe_rollback(con: Any) -> None:
    try:
        con.execute("ROLLBACK")
    except Exception:  # noqa: BLE001 -- rollback failure must not mask the original error
        pass


def _token_prefix(table: str) -> str:
    return f"{VERB}:{table}:"


def _latest_commit(con: Any, catalog: str, table: str) -> int | None:
    """Latest snapshot committed under this table's token prefix. starts_with, never LIKE: an
    underscore in a table name is a LIKE wildcard."""
    row = con.execute(
        f"SELECT snapshot_id FROM ducklake_snapshots('{catalog}') WHERE starts_with(commit_message, ?) "
        "ORDER BY snapshot_id DESC LIMIT 1",
        [_token_prefix(table)],
    ).fetchone()
    return int(row[0]) if row else None


def _snapshot_exists(con: Any, catalog: str, snapshot_id: int) -> bool:
    row = con.execute(f"SELECT count(*) FROM ducklake_snapshots('{catalog}') WHERE snapshot_id = ?", [snapshot_id]).fetchone()
    return bool(row and int(row[0]) > 0)


def _prove(con: Any, fq: str, commit_snapshot_id: int) -> bool:
    """True when row count and full-row digest are equal at the commit's immediate predecessor and the commit."""
    before = _row_proof(con, f"{fq} AT (VERSION => {commit_snapshot_id - 1})")
    after = _row_proof(con, f"{fq} AT (VERSION => {commit_snapshot_id})")
    return before == after


def _delete_file_counts(con: Any, catalog: str) -> dict[str, int]:
    meta = f"__ducklake_metadata_{catalog}"
    rows = con.execute(
        f"SELECT t.table_name, count(d.delete_file_id) FROM {meta}.ducklake_table t "
        f"LEFT JOIN {meta}.ducklake_delete_file d ON d.table_id = t.table_id AND d.end_snapshot IS NULL "
        "WHERE t.end_snapshot IS NULL GROUP BY t.table_name"
    ).fetchall()
    return {r[0]: int(r[1]) for r in rows}


def _rewrite_table(
    con: Any,
    table: str,
    *,
    catalog: str,
    over_budget: Callable[[], bool],
    max_attempts: int,
    sleep: Callable[[float], None],
    uuid_factory: Callable[[], str],
) -> dict[str, Any] | None:
    """One transaction under a unique token: maint.rewrite at the explicit threshold, under the writer's
    bounded OCC policy. Returns attempts, files_processed, pre_snapshot_id, token; None when the budget
    passed before any attempt committed (the table is deferred). Raises the last error otherwise."""
    attempts = 0
    while True:
        if over_budget():
            return None
        attempts += 1
        token = f"{_token_prefix(table)}{uuid_factory()}"
        try:
            con.execute("BEGIN TRANSACTION")
            pre_snapshot_id = int(con.execute(f"SELECT id FROM {catalog}.current_snapshot()").fetchone()[0])
            con.execute(f"CALL {catalog}.set_commit_message(?, ?)", [VERB, token])
            processed = maint.rewrite(con, [table], delete_threshold=CURRENT_REWRITE_DELETE_THRESHOLD, catalog=catalog)
            con.execute("COMMIT")
            return {"attempts": attempts, "files_processed": processed, "pre_snapshot_id": pre_snapshot_id, "token": token}
        except Exception as exc:  # noqa: BLE001 -- classify, then retry-or-raise
            _safe_rollback(con)
            if is_occ_collision(exc) and attempts < max_attempts:
                _occ_backoff(attempts, sleep=sleep)
                continue
            raise


def compact_current_tables(
    con: Any,
    discovered: list[str],
    *,
    semantics: dict[str, Any],
    registry: dict[str, scope.ClassifiedTable],
    policy: dict[str, dict[str, dict[str, Any]]],
    catalog: str = CATALOG_ALIAS,
    meta_schema: str,
    emit: Callable[[str, float], None],
    budget_s: float = COMPACTION_TIME_BUDGET_S,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    uuid_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
    max_attempts: int = OCC_MAX_ATTEMPTS,
) -> dict[str, Any]:
    start = clock()
    per_table: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    refused: list[dict[str, Any]] = []
    deferred: list[str] = []
    failures: list[dict[str, Any]] = []

    def over_budget() -> bool:
        return clock() - start >= budget_s

    def finish() -> dict[str, Any]:
        elapsed_ms = round((clock() - start) * 1000.0, 2)
        result = {
            "meta_schema": meta_schema,
            "per_table": per_table,
            "skipped": skipped,
            "refused": refused,
            "deferred": deferred,
            "failures": failures,
            "elapsed_ms": elapsed_ms,
        }
        emit("CurrentCompactionFilesRewritten", float(sum(r["files_processed"] for r in per_table)))
        emit("CurrentCompactionFailures", float(len(failures)))
        emit("CurrentCompactionRefused", float(len(refused)))
        emit("CurrentCompactionDeferred", float(len(deferred)))
        emit("CurrentCompactionDurationMs", elapsed_ms)
        print(f"{LINE_PREFIX} {json.dumps(result, sort_keys=True)}", flush=True)
        return result

    try:
        resolution = scope.resolve_scope(discovered, verb=VERB, policy=policy, registry=registry)
        scoped = list(resolution.to_merge)
        current_side = [t for t in scoped if registry[t].side == "current"]
        for table in scoped:
            if registry[table].side != "current":
                skipped.append({"table": table, "reason": "history_side"})
        layouts: dict[str, TableLayout] = {}
        delete_counts: dict[str, int] = {}
        if current_side:
            layouts = read_partition_layout(con, catalog_alias=catalog, semantics=semantics)
            delete_counts = _delete_file_counts(con, catalog)
    except Exception as exc:  # noqa: BLE001 -- catalog-level: recorded, the merge still runs
        logger.warning("current compaction catalog-level failure: %s", exc)
        failures.append({"table": None, "error_class": type(exc).__name__})
        return finish()

    work: list[str] = []
    for table in current_side:
        layout = layouts[table]
        if layout.active_scheme_id is not None and layout.legacy_scheme_files > 0:
            refused.append({"table": table, "legacy_scheme_files": layout.legacy_scheme_files})
        else:
            work.append(table)
    work.sort(
        key=lambda t: (registry[t].table_class != scope.SMOKE_HARNESS_CLASS, -delete_counts.get(t, 0), t),
    )

    for table in work:
        if over_budget():
            deferred.append(table)
            continue
        layout = layouts[table]
        fq = f"{catalog}.{table}"
        t_start = clock()
        entry: dict[str, Any] = {
            "table": table,
            "files_before": layout.live_files,
            "delete_files_before": delete_counts.get(table, 0),
            "files_processed": 0,
            "files_after": None,
            "attempts": 0,
            "duration_ms": 0.0,
            "pre_snapshot_id": None,
            "commit_snapshot_id": None,
            "reproof": "none",
            "proof": "none",
        }
        try:
            prior = _latest_commit(con, catalog, table)
            if prior is not None:
                if not _snapshot_exists(con, catalog, prior - 1):
                    entry["reproof"] = "expired"
                    skipped.append({"table": table, "reason": "reproof_expired"})
                elif _prove(con, fq, prior):
                    entry["reproof"] = "equal"
                else:
                    entry["reproof"] = "mismatch"
                    entry["commit_snapshot_id"] = prior
                    per_table.append(entry)
                    finish()
                    raise CurrentCompactionProofError(
                        f"{table!r}: re-proof of the earlier rewrite_current_deletes commit {prior} differs from its "
                        f"predecessor snapshot {prior - 1} -- STOP: never auto-restore, current rebuilds from history"
                    )
            outcome = _rewrite_table(
                con,
                table,
                catalog=catalog,
                over_budget=over_budget,
                max_attempts=max_attempts,
                sleep=sleep,
                uuid_factory=uuid_factory,
            )
            if outcome is None:
                deferred.append(table)
                continue
            entry["attempts"] = outcome["attempts"]
            entry["files_processed"] = outcome["files_processed"]
            entry["pre_snapshot_id"] = outcome["pre_snapshot_id"]
            row = con.execute(
                f"SELECT snapshot_id FROM ducklake_snapshots('{catalog}') WHERE commit_message = ?", [outcome["token"]]
            ).fetchone()
            if row is None:
                if outcome["files_processed"] > 0:
                    entry["proof"] = "unresolved"
                    per_table.append(entry)
                    finish()
                    raise CurrentCompactionProofError(
                        f"{table!r}: rewrite processed {outcome['files_processed']} files but token resolves no "
                        f"snapshot (pre_snapshot_id={outcome['pre_snapshot_id']}) -- STOP, file a Critical rec"
                    )
            else:
                commit = int(row[0])
                entry["commit_snapshot_id"] = commit
                if _prove(con, fq, commit):
                    entry["proof"] = "equal"
                else:
                    entry["proof"] = "mismatch"
                    per_table.append(entry)
                    finish()
                    raise CurrentCompactionProofError(
                        f"{table!r}: rewrite commit {commit} differs from its predecessor snapshot {commit - 1} in "
                        "row count or full-row digest -- STOP: never auto-restore, current rebuilds from history"
                    )
            entry["files_after"] = maint._count_files(con, catalog, table)
            entry["duration_ms"] = round((clock() - t_start) * 1000.0, 2)
            per_table.append(entry)
        except CurrentCompactionProofError:
            raise
        except Exception as exc:  # noqa: BLE001 -- one table's failure isolates to that table
            logger.warning("current compaction failure on %s: %s", table, exc)
            failures.append({"table": table, "error_class": type(exc).__name__})

    return finish()
