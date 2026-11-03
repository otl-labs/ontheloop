"""Local READ-cache refresh, downstream of the writer -- NEVER a write source (Decision 88 /
warehouse-as-source-of-truth).

Owner-concern: keeping the local DECISIONS_JSONL / execution-plans read-cache files in sync (no recs cache, Decision 215) with
the DuckLake writer's committed rows, either via a full reader pull (_sync_table) or an
incremental single-row upsert of the just-committed row (_refresh_cache_after_write,
neon-egress-reduction D4).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def _sync_table(table: str) -> None:
    """Full-pull refresh of the local read-cache for one ops table from the DuckLake reader.

    The atomic catalog commit means there is no compaction/view-refresh step (Decision 81 cl.4) --
    the write already landed in `current`, so a cache-pull from the reader suffices for every
    migrated table (Decision 84 I-1). Raises on infrastructure failure.

    This is the EXPLICIT full-table reconciliation primitive, retained for the bulk-backfill
    post-loop sync and the `sync()` fallback. The per-write path no longer calls it -- it uses
    _refresh_cache_after_write (incremental upsert, no reader round-trip; neon-egress-reduction D4).
    """
    from scripts.sync.ops import _pull_single_table  # noqa: PLC0415

    _pull_single_table(table)


def _refresh_cache_after_write(
    table: str,
    record: dict,
    response: dict,
    jsonl_path: Path,
    *,
    append_only: bool = False,
) -> None:
    """Refresh the local READ cache after a synchronous ducklake_writer commit -- no reader round-trip.

    Replaces the prior per-write full-table resync (_sync_table -> _pull_single_table, one reader
    invocation per file_rec/update_rec) with an incremental single-row upsert of the just-committed
    row (neon-egress-reduction D4). The write itself already transited ducklake_writer synchronously;
    this is a downstream refresh of the READ cache (Decision 84 I-4 / warehouse-as-source-of-truth):
    NEVER a write source, NEVER re-staged to S3/the writer.

    The committed `record` is enriched from the writer's authoritative `response`: the minted ULID
    (when returned) and the SCD2 timestamps. created_timestamp is set only if absent (carried
    unchanged on update, matching the runtime's SCD2 derivation); last_updated_timestamp is stamped
    now (the writer minted it at ~this instant; the next full `sync` reconciles any sub-second skew).

    append_only=True (bulk-import `_skip_sync` path) keeps the historical append-then-final-sync
    behaviour: the caller runs ONE explicit _sync_table after the loop, which dedups via full pull.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    record.setdefault("created_timestamp", now_iso)
    record["last_updated_timestamp"] = now_iso
    ulid = response.get("ulid") if isinstance(response, dict) else None
    if ulid:
        record["ulid"] = ulid

    if append_only:
        _append_to_local_jsonl(jsonl_path, record)
        return

    from scripts.sync.ops import upsert_cache_row  # noqa: PLC0415

    upsert_cache_row(table, record, path=jsonl_path)


def _sanitize_record(record: dict) -> dict:
    """Replace empty strings with None for fields whose NULL arrives serialised as ''."""
    result = dict(record)
    for key, value in result.items():
        if value == "":
            result[key] = None
    return result


def _append_to_local_jsonl(path: Path, record: dict) -> None:
    """Append a JSON record to the local JSONL file (write-through cache update).

    Creates the file if it does not exist. Uses explicit newline='\n' to
    prevent CRLF on Windows.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError as exc:
        logger.warning("[PORTAL] Write-through to %s failed: %s", path, exc)
