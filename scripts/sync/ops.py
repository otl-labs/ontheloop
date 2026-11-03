# complexity-waiver: decision-43
"""sync_ops -- one-way refresh of the local JSONL read caches from the DuckLake ops tables.

Read path: the DuckLake closed reader, for every migrated table. It is the sole backend
(Decision 84 I-1); on reader failure the cache is left untouched with a loud warning.
ops_recommendations has no local cache (Decision 215): it is never pulled, and any stale
logs/.recommendations-log.jsonl is deleted by sync and warm_sync.

Provides one CLI subcommand:
  sync   -- pull decisions, priority queue and execution plans from the reader; exits non-zero if any pull failed

Internal helpers (not for direct agent use):
  _rebuild_local_cache -- read current-state rows and overwrite local JSONL files
  _pull_single_table -- pull a single table from the DuckLake reader
  warm_sync          -- pull all migrated tables in one warm-up pass, returning the
                        pulled rows in-memory plus per-table reader reachability (the preflight
                        serves its Phase-B signals from these rows -- zero additional reader calls,
                        neon-egress-reduction D4). The disk caches are written as a side effect.
  upsert_cache_row   -- incremental single-row upsert into a local JSONL read-cache by merge key.
                        A read-cache refresh DOWNSTREAM of a synchronous ducklake_writer commit
                        (Decision 84 I-4): never a write source, never re-staged to the writer.

Never raises to callers. All functions catch and log exceptions internally.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).parent.parent.parent
_LOGS_DIR = _REPO_ROOT / "logs"

# Maps warehouse table name -> local JSONL file (relative to _LOGS_DIR)
_TABLE_TO_LOCAL: dict[str, str] = {
    "ops_decisions": ".decisions-index.jsonl",
    "ops_priority_queue": "priority-queue/.priority-queue.jsonl",
    "ops_execution_plans": ".execution-plans-index.jsonl",
}

# Retired recs cache (Decision 215): never written; any pre-existing copy is deleted on every sync.
_RETIRED_RECS_CACHE = ".recommendations-log.jsonl"
_SSO_PROFILE = "agent_platform"
_SYNC_REJECTS_LOG = _LOGS_DIR / "debug" / "dq-sync-rejects.jsonl"
_DECISIONS_SYNC_REJECTS_LOG = _LOGS_DIR / "debug" / "decisions-sync-rejects.jsonl"
_REQUIRED_REC_FIELDS = ["title", "source", "effort", "priority"]


def _delete_retired_recs_cache() -> None:
    """Remove a stale full-looking recs cache copy left by a pre-Decision-215 sync."""
    try:
        (_LOGS_DIR / _RETIRED_RECS_CACHE).unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("sync_ops._delete_retired_recs_cache: could not delete stale recs cache: %s", exc)


def _pull_via_reader(table: str) -> list[dict] | None:
    """Return current-state rows for *table* via the DuckLake reader.

    Returns None on any exception so the caller can degrade LOUDLY (warn + cache
    not updated). The reader is the sole backend for every migrated table (Decision 84 I-1).
    """
    try:
        from src.common.ducklake_reader_client import DuckLakeReader, make_reader  # noqa: PLC0415

        # make_reader() is annotated -> Reader (the Protocol, which deliberately does not declare
        # named()) but only ever constructs a DuckLakeReader, which also carries current_state;
        # cast narrows the type here rather than widening the Protocol by omission.
        reader = cast(DuckLakeReader, make_reader(table=table))
        if table == "ops_priority_queue":
            # Decision 70: the queue current state is ALL entries of the LATEST curator run.
            # The generic latest-per-merge-key projection would silently change these semantics,
            # so the verb is the only sanctioned queue read (Decision 84 I-3).
            return reader.named("priority_queue_current")
        return reader.current_state(table)
    except Exception as exc:  # noqa: BLE001
        logger.warning("sync_ops._pull_via_reader: reader failed for %s: %s", table, exc)
        return None


def _write_sync_reject(row: dict, reason: str) -> None:
    """Append a rejected ops_recommendations row to the sync-rejects debug log."""
    try:
        _SYNC_REJECTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {"rejected_at": datetime.now(timezone.utc).isoformat(), "reason": reason, "row": row}
        with _SYNC_REJECTS_LOG.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001
        logger.warning("sync_ops._write_sync_reject: could not write reject log: %s", exc)


def _coerce_array(val: object, *, elem_type: type = str) -> list:
    """Parse a string-serialised array into a typed Python list.

    A legacy string-serialised column arrives as "[elem1, elem2]" with unquoted,
    comma-separated elements. ast.literal_eval is not suitable here because elements
    are not quoted Python string literals. Returns [] for null/empty values.

    Backend-agnostic: the DuckLake reader returns native Python lists (JSON arrays), so an
    already-list value is coerced element-wise and returned as-is rather than re-parsed from str().
    """
    if isinstance(val, list):
        out: list = []
        for elem in val:
            if elem is None:
                continue
            try:
                out.append(elem_type(elem))
            except (ValueError, TypeError):
                pass
        return out
    raw = str(val).strip() if val is not None else ""
    if not raw:
        return []
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        result = []
        for part in inner.split(","):
            part = part.strip()
            if part:
                try:
                    result.append(elem_type(part))
                except (ValueError, TypeError):
                    pass
        return result
    try:
        return [elem_type(raw)]
    except (ValueError, TypeError):
        return []


def _coerce_ops_rec_row(row: dict) -> dict | None:
    """Coerce string-serialised values in an ops_recommendations row to proper Python types.

    Array columns may arrive as "[elem1, elem2]"; null scalars may arrive as "".

    Returns None and writes a reject log entry if the row has an invalid id prefix.
    """
    rec_id = row.get("id", "")
    if not rec_id.startswith(("rec-", "agent-", "test-")):
        _write_sync_reject(row, f"invalid id prefix: {rec_id!r}")
        return None
    for field in ("dependencies", "tags"):
        row[field] = _coerce_array(row.get(field))
    steps = row.get("execution_steps")
    if not isinstance(steps, int):
        try:
            row["execution_steps"] = int(steps) if steps else None
        except (ValueError, TypeError):
            row["execution_steps"] = None
    automatable = row.get("automatable")
    if not isinstance(automatable, bool):
        if automatable == "":
            row["automatable"] = None
        elif isinstance(automatable, str):
            row["automatable"] = {"true": True, "false": False}.get(automatable.lower())
    return row


def _coerce_ops_priority_queue_row(row: dict) -> dict:
    """Coerce string-serialised values in an ops_priority_queue row to proper Python types."""
    for field in ("compound_with", "gates"):
        row[field] = _coerce_array(row.get(field))
    rank = row.get("rank")
    if not isinstance(rank, int):
        try:
            row["rank"] = int(rank) if rank else None
        except (ValueError, TypeError):
            row["rank"] = None
    return row


def _write_decisions_sync_reject(row: dict, reason: str) -> None:
    """Append a rejected ops_decisions row to the decisions sync-rejects debug log."""
    try:
        _DECISIONS_SYNC_REJECTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {"rejected_at": datetime.now(timezone.utc).isoformat(), "reason": reason, "row": row}
        with _DECISIONS_SYNC_REJECTS_LOG.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001
        logger.warning("sync_ops._write_decisions_sync_reject: could not write reject log: %s", exc)


def _coerce_ops_decisions_row(row: dict) -> dict:
    """Coerce string-serialised values in an ops_decisions row to proper Python types.

    Also populates legacy decision_id from id (or vice versa) and logs a
    sync-reject entry when the dual-write invariant is violated.
    """
    decision_id = row.get("decision_id")
    if not isinstance(decision_id, int):
        try:
            row["decision_id"] = int(decision_id) if decision_id else None
        except (ValueError, TypeError):
            row["decision_id"] = None
    row["related_decisions"] = _coerce_array(row.get("related_decisions"), elem_type=int)

    dec_id = row.get("id")
    coerced_did = row.get("decision_id")

    if not dec_id and coerced_did is not None:
        row["id"] = f"dec-{coerced_did:03d}"
    elif dec_id and coerced_did is not None:
        try:
            expected = int(dec_id.split("-")[1])
            if expected != coerced_did:
                _write_decisions_sync_reject(
                    row,
                    f"dual-write invariant: id={dec_id!r} implies decision_id={expected}, got {coerced_did}",
                )
        except (IndexError, ValueError):
            pass

    return row


def _coerce_ops_execution_plans_row(row: dict) -> dict:
    """Decode the JSON-blob VARCHAR columns (steps/critique_history) back to native list objects.

    The DuckLake reader returns BIGINT/VARCHAR scalar columns natively typed, but
    steps/critique_history are VARCHAR columns carrying
    json.dumps'd JSON (scripts/ops_portal/execution_plans.py) -- decode them for local-cache
    consumers so the cache mirrors the pre-serialization ExecutionPlan shape.
    """
    for field_name in ("steps", "critique_history"):
        raw = row.get(field_name)
        if isinstance(raw, str):
            try:
                row[field_name] = json.loads(raw) if raw.strip() else []
            except json.JSONDecodeError:
                pass
    return row


def check_sso(profile: str = _SSO_PROFILE) -> bool:
    """Return True if the given SSO profile has valid credentials."""
    try:
        result = subprocess.run(
            ["aws", "sts", "get-caller-identity", "--profile", profile],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        return result.returncode == 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("sync_ops.check_sso: credential check failed: %s", exc)
        return False


def _pull_single_table_with_rows(table: str, profile: str = _SSO_PROFILE) -> tuple[int, list[dict] | None]:
    """Pull a single ops table from the DuckLake reader; overwrite the local JSONL and return the rows.

    Returns (row_count, rows). On reader failure returns (0, None) -- the second element is None
    (NOT []) so a caller can distinguish "reader unreachable" from "genuinely empty table" without a
    false-zero (Decision 55). The reader is the sole backend for every migrated table
    (Decision 84 I-1): on reader failure the cache is left untouched with a loud warning.
    """
    local_rel = _TABLE_TO_LOCAL.get(table)
    if not local_rel:
        logger.warning("sync_ops._pull_single_table_with_rows: unknown table %r", table)
        return 0, None

    reader_rows = _pull_via_reader(table)
    if reader_rows is not None:
        rows = _coerce_rows_list(table, reader_rows)
        return _write_rows_to_local(table, rows, local_rel), rows

    logger.warning(
        "sync_ops._pull_single_table_with_rows: DuckLake reader unreachable for %s -- it is the "
        "sole backend (Decision 84 I-1). Local cache not updated.",
        table,
    )
    return 0, None


def _pull_single_table(table: str, profile: str = _SSO_PROFILE) -> int:
    """Pull a single ops table from the DuckLake reader and overwrite the local JSONL file.

    The reader is the sole backend for every migrated table (Decision 84 I-1): on reader failure
    the cache is left untouched with a loud warning. Returns number of rows pulled, or 0 on failure.
    """
    count, _rows = _pull_single_table_with_rows(table, profile=profile)
    return count


def upsert_cache_row(table: str, row: dict, *, merge_key: str = "id", path: Path | None = None) -> int:
    """Incrementally upsert ONE row into the local JSONL read-cache by *merge_key*. Returns row count.

    Reads the existing cache, replaces the row whose merge_key matches (last-wins, keeping its
    position) or appends a new one, then atomically rewrites the file (temp + os.replace). The whole
    file is deduplicated by merge_key as a side effect, so repeated portal writes never accumulate
    duplicate rows in the cache.

    This is a refresh of the READ cache DOWNSTREAM of a synchronous ducklake_writer commit
    (Decision 84 I-4 / warehouse-as-source-of-truth): it is NEVER a write source and is NEVER
    re-staged to the writer. The authoritative write already transited ducklake_writer; this
    only keeps the local projection current without a reader round-trip (neon-egress-reduction D4).

    *path* overrides the cache-file location (defaults to _LOGS_DIR/_TABLE_TO_LOCAL[table]); the
    portal passes its own RECS_JSONL/DECISIONS_JSONL symbol so a single cache path is authoritative.

    Returns 0 (no-op) for an unknown table or a row missing the merge key.
    """
    local_rel = _TABLE_TO_LOCAL.get(table)
    if path is None and not local_rel:
        logger.warning("sync_ops.upsert_cache_row: unknown table %r", table)
        return 0
    key_val = row.get(merge_key)
    if not key_val:
        logger.warning("sync_ops.upsert_cache_row: row missing merge key %r; cache not updated", merge_key)
        return 0

    # The guard above already returned 0 when `path` is None and `local_rel` is falsy, so this
    # arm is reached only with a non-empty _TABLE_TO_LOCAL entry.
    local_path = path if path is not None else _LOGS_DIR / cast("str", local_rel)
    # No-op for /dev/null sentinel or character devices -- real-path write errors still propagate (Decision 55).
    if str(local_path) == os.devnull:
        return 0
    try:
        is_chardev = local_path.exists() and stat.S_ISCHR(os.stat(local_path).st_mode)
    except OSError:
        is_chardev = False
    if is_chardev:
        return 0
    by_key: dict[str, dict] = {}
    if local_path.exists():
        for line in local_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                existing = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            existing_key = existing.get(merge_key)
            if existing_key:
                by_key[existing_key] = existing
    by_key[key_val] = row  # replace-in-place (keeps position) or append a new merge key

    local_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = local_path.with_name(local_path.name + ".tmp")
    with tmp_path.open("w", encoding="utf-8", newline="\n") as fh:
        for cached in by_key.values():
            fh.write(json.dumps(cached, ensure_ascii=False) + "\n")
    os.replace(tmp_path, local_path)
    return len(by_key)


def _coerce_rows_list(table: str, raw_rows: list[dict]) -> list[dict]:
    """Apply per-table coercion to a list of rows returned by the reader."""
    rows: list[dict] = []
    rejected_count = 0
    for row in raw_rows:
        row.pop("_rn", None)
        row.pop("row_num", None)
        if table == "ops_recommendations":
            row = _coerce_ops_rec_row(row)  # type: ignore[assignment]
            if row is None:
                rejected_count += 1
                continue
            missing = [f for f in _REQUIRED_REC_FIELDS if not row.get(f) or not str(row[f]).strip()]
            if missing:
                _write_sync_reject(row, f"missing/empty required fields: {missing}")
                rejected_count += 1
                continue
        elif table == "ops_priority_queue":
            row = _coerce_ops_priority_queue_row(row)
        elif table == "ops_decisions":
            row = _coerce_ops_decisions_row(row)
        elif table == "ops_execution_plans":
            row = _coerce_ops_execution_plans_row(row)
        rows.append(row)
    if rejected_count:
        logger.warning(
            "sync_ops._coerce_rows_list: rejected %d invalid rows for %s (see %s)",
            rejected_count,
            table,
            _SYNC_REJECTS_LOG,
        )
    return rows


def _write_rows_to_local(table: str, rows: list[dict], local_rel: str) -> int:
    """Write *rows* to the local JSONL cache for *table*. Returns row count."""
    local_path = _LOGS_DIR / local_rel
    local_path.parent.mkdir(parents=True, exist_ok=True)
    with local_path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    logger.info("sync_ops._write_rows_to_local: wrote %d rows for %s", len(rows), table)
    return len(rows)


def _rebuild_with_failures(profile: str = _SSO_PROFILE) -> tuple[dict[str, int], list[str]]:
    """Pull every migrated table once; return (counts, failed table names).

    A table is failed only when its reader pull returned None (reader unreachable) -- a
    successful pull of zero rows is an empty table, never a failure (Decision 55: no false-zero).
    """
    counts: dict[str, int] = {}
    failed: list[str] = []
    for table in _TABLE_TO_LOCAL:
        counts[table], rows = _pull_single_table_with_rows(table, profile=profile)
        if rows is None:
            failed.append(table)
    return counts, failed


def _rebuild_local_cache(profile: str = _SSO_PROFILE) -> dict[str, int]:
    """Read current-state and overwrite local JSONL files with fresh data.

    DESTRUCTIVE: overwrites local JSONL files with warehouse state. Every migrated
    table pulls from the DuckLake reader, its sole backend (Decision 84 I-1).

    Returns:
        Dict mapping table name to number of rows pulled.
    """
    return _rebuild_with_failures(profile)[0]


def sync(profile: str = _SSO_PROFILE) -> dict[str, object]:
    """Rebuild the local read caches from the DuckLake reader.

    Returns:
        {"pulled": {table: count}, "failed": [table, ...]} -- "failed" lists every table whose
        reader pull failed (T2.19 c9); the CLI exits non-zero when it is non-empty.
    """
    _delete_retired_recs_cache()
    counts, failed = _rebuild_with_failures(profile)
    return {"pulled": counts, "failed": failed}


def warm_sync(profile: str = _SSO_PROFILE) -> dict[str, object]:
    """Single warm-up pass for preflight: pull every migrated table ONCE.

    This is the ONE serial reader touch that absorbs the Neon cold-resume before the preflight
    fan-out. It returns the pulled rows in-memory so the caller can serve every Phase-B signal from
    them WITHOUT issuing a second reader call per signal (neon-egress-reduction D4). The disk caches
    are still written (so degraded fallbacks and other tools see fresh data); returning the rows just
    avoids re-reading the file we literally just wrote.

    Returns:
        {
          "pulled":    {table: count},                 # rows written to the local cache
          "rows":      {table: [rows] | None},         # None => that table's reader pull failed
          "reader_ok": {table: bool},                  # per-table reachability (False on failure)
        }
    """
    _delete_retired_recs_cache()
    pulled: dict[str, int] = {}
    rows: dict[str, list[dict] | None] = {}
    reader_ok: dict[str, bool] = {}
    for table in _TABLE_TO_LOCAL:
        count, table_rows = _pull_single_table_with_rows(table, profile=profile)
        pulled[table] = count
        rows[table] = table_rows
        reader_ok[table] = table_rows is not None
    return {"pulled": pulled, "rows": rows, "reader_ok": reader_ok}


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync ops tables from the DuckLake reader to local JSONL cache")
    parser.add_argument("command", choices=["sync"], help="Subcommand to run")
    parser.add_argument("--profile", default=_SSO_PROFILE, help=f"AWS SSO profile (default: {_SSO_PROFILE})")
    args = parser.parse_args()

    if args.command == "sync":
        result = sync(args.profile)
        print(json.dumps(result, indent=2))
        failed = result.get("failed")
        if failed:
            print(f"sync_ops: reader pull failed for {failed}; local cache is stale for those tables", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
