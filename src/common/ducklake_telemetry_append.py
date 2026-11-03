"""The append adapter of the telemetry writer (T2.36, slice 2a-2, plan 3 part 2; Decisions 199, 200, 207, 210).

append_request writes one validated request (src.telemetry.request.AppendRequest) in ONE transaction: a first-sight
project is registered under part 1's counter-row lock in the same transaction, then every batch is written with one
MERGE (src.telemetry.append.execute_prepared), so an identical re-send is a no-op and a differing row is refused with
nothing written.

Order: the project id comes from part 1's binding cache, else a read-only lookup, both before any transaction; a miss
whose purpose is reserved (drill:, synthetic:) is refused 403 project_unregistered before any transaction. A blob row (a
transcript above the inline threshold, delivered through begin_blobs) needs a known project (else 422 blob_not_verified,
unknown_project, before any S3 call) and gets its content_uri from the writer. Every batch is
prepared (gate, rules, key derivation, intra-batch collapse) BEFORE BEGIN with the known id or a sentinel, so a 422 or an
in-batch 409 never opens a transaction or an S3 call; blob objects are HEAD-verified after prepare and before BEGIN;
the sentinel id is re-derived inside the transaction only when registration resolves a different id, and no row
carrying the sentinel ever reaches a MERGE. `now` and the registration identity are minted once, outside the retry loop
(Decision 81 cl.3). The binding enters part 1's cache only after the transaction returns. Never logs or returns a row
value or principal.ref (Decision 101).

Imported only by the telemetry writer's edge: nothing in ducklake_runtime's closure and nothing under scripts/ imports it
(the scripts-never-import-telemetry-append fence).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from src.common import ducklake_telemetry_blobs as blobs
from src.common import ducklake_telemetry_registration as reg
from src.common.ducklake_scd2_schema import CATALOG_ALIAS, DuckLakeRuntimeError
from src.common.ducklake_telemetry_txn import TelemetryRequestError, run_telemetry_txn
from src.common.ducklake_writes import mint_write_identity
from src.telemetry.append import AppendResult, EventTableSpec, PreparedBatch, execute_prepared, prepare_batch
from src.telemetry.gate import GrainConflictError
from src.telemetry.request import AppendRequest

SENTINEL_PROJECT_ID = "0" * 26


@dataclass(frozen=True)
class AppendOutcome:
    tenant_id: str
    project_id: str
    project_registration: str  # created | existing
    tables: dict[str, AppendResult]
    occ_retries: int
    commit_ms: float
    masked_commit_retries: int = 0
    blob_rows: int = 0
    blob_head_ms: int = 0
    blobs_verified: int = 0


def _prepare_all(
    request: AppendRequest,
    specs: Mapping[str, EventTableSpec],
    *,
    tenant_id: str,
    project_id: str,
    now: datetime,
) -> list[PreparedBatch]:
    prepared: list[PreparedBatch] = []
    for table, rows in request.batches:
        try:
            prepared.append(prepare_batch(specs[table], rows, tenant_id=tenant_id, project_id=project_id, now=now))
        except GrainConflictError as exc:
            if project_id == SENTINEL_PROJECT_ID:
                exc.event_id = None  # derived under the sentinel: it can never exist in storage; the row index locates
            raise
    return prepared


def _with_blob_uris(
    request: AppendRequest, specs: Mapping[str, EventTableSpec], *, tenant_id: str, known: str | None
) -> tuple[AppendRequest, list[int]]:
    """The request with writer-derived content_uri on its blob rows, and those rows' indexes in the transcripts batch."""
    content = specs[blobs.TRANSCRIPTS].rules.content if blobs.TRANSCRIPTS in specs else None
    position = next((i for i, (table, _) in enumerate(request.batches) if table == blobs.TRANSCRIPTS), None)
    if content is None or position is None or content.cap is None:
        return request, []
    rows = request.batches[position][1]
    indexes = blobs.blob_row_indexes(rows, content.threshold, content.cap)
    if not indexes:
        return request, []
    if known is None:
        raise TelemetryRequestError(
            422, "blob_not_verified", {"table": blobs.TRANSCRIPTS, "row": indexes[0], "reason": "unknown_project"}
        )
    blobs.check_blob_cap(len(indexes))
    attached = blobs.attach_blob_uris(
        rows, tenant_id=tenant_id, project_id=known, threshold=content.threshold, cap=content.cap
    )
    batches = list(request.batches)
    batches[position] = (blobs.TRANSCRIPTS, attached)
    return replace(request, batches=tuple(batches)), indexes


def append_request(
    con: Any,
    request: AppendRequest,
    *,
    tenant_id: str,
    specs: Mapping[str, EventTableSpec],
    semantics: dict[str, Any],
    store: Any = None,
    now: datetime | None = None,
    metric_sink: Callable[[str, float], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> AppendOutcome:
    known = blobs.known_project_id(con, tenant_id, request.project_ref, semantics)
    request, blob_indexes = _with_blob_uris(request, specs, tenant_id=tenant_id, known=known)
    moment = now or datetime.now(timezone.utc)
    identity, candidate = mint_write_identity(), reg.new_entity_id()
    prepared_id = known or SENTINEL_PROJECT_ID
    prepared = _prepare_all(request, specs, tenant_id=tenant_id, project_id=prepared_id, now=moment)
    unverified: list[blobs.Unverified] = []
    head_ms = 0
    if blob_indexes:
        if store is None:
            raise DuckLakeRuntimeError("a blob row needs the blob store, which is not configured")
        rows = next(r for table, r in request.batches if table == blobs.TRANSCRIPTS)
        started = time.perf_counter()
        unverified = blobs.verify_blob_rows(rows, blob_indexes, store=store, now=moment)
        head_ms = int(round((time.perf_counter() - started) * 1000))

    def body(c: Any) -> tuple[str, bool, dict[str, AppendResult]]:
        nonlocal prepared, prepared_id
        if unverified:
            transcripts = next(b for b in prepared if b.table_spec.table == blobs.TRANSCRIPTS)
            blobs.require_stored(c, CATALOG_ALIAS, transcripts, unverified)
        project_id, created = known, False
        if project_id is None:
            project_id, created = reg.resolve_or_register_project(
                c,
                tenant_id,
                request.project_ref,
                explicit=False,
                attributes={},
                identity=identity,
                semantics=semantics,
                candidate_id=candidate,
            )
        if project_id != prepared_id:
            prepared = _prepare_all(request, specs, tenant_id=tenant_id, project_id=project_id, now=moment)
            prepared_id = project_id
        if any(row["project_id"] != project_id for batch in prepared for row in batch.rows):
            raise DuckLakeRuntimeError("a prepared row does not carry the resolved project id")
        results = {batch.table_spec.table: execute_prepared(c, batch, catalog=CATALOG_ALIAS) for batch in prepared}
        return project_id, created, results

    outcome = run_telemetry_txn(con, body, metric_sink=metric_sink, sleep=sleep)
    project_id, created, results = outcome.result
    reg._PROJECT_CACHE[(tenant_id, request.project_ref)] = project_id
    return AppendOutcome(
        tenant_id=tenant_id,
        project_id=project_id,
        project_registration="created" if created else "existing",
        tables=results,
        occ_retries=outcome.occ_retries,
        commit_ms=outcome.commit_ms,
        masked_commit_retries=outcome.masked_commit_retries,
        blob_rows=len(blob_indexes),
        blob_head_ms=head_ms,
        blobs_verified=len(blob_indexes) - len(unverified),
    )
