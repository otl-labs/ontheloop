"""The begin_blobs verb core and the append hook of the claim-check route (T2.36, slice 2a-2, plan 4).

begin_blobs answers each content-addressed item with `existed` (a verified object under the catalog-scoped blob root) or a
writer-issued presigned PUT, after the writer resolved the tenant and project and derived the key. Append derives
content_uri itself (attach_blob_uris), HEADs every blob row only after the batch has been prepared (verify_blob_rows), and
inside the one transaction lets a missing or stale object commit ONLY as the no-op of an identical stored row
(require_stored; Decision 210 cl.1, Decision 207), so an identical re-send never demands a re-upload.

Never logs, returns or raises a key, digest, URL or row value (Decision 101). Imported only by the telemetry writer's edge.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from src.common import ducklake_telemetry_registration as reg
from src.common.ducklake_telemetry_blobstore import (
    MISMATCH,
    MISSING,
    SIZE_MISMATCH,
    STALE,
    VERIFIED,
    BlobIntegrityError,
    BlobStoreUnavailableError,
    Grant,
)
from src.common.ducklake_telemetry_txn import TelemetryRequestError, run_telemetry_txn
from src.common.ducklake_writes import mint_write_identity
from src.telemetry.blobs import MAX_BLOB_ITEMS, TOO_MANY_BLOBS, BeginBlobsRequest, blob_row
from src.telemetry.identity import KEY_PLANS, IdentityError, canonical_ref, derive_entity_key

TRANSCRIPTS = "telemetry_transcripts"
HEAD_WORKERS = 16
_SESSION_PLAN = KEY_PLANS[TRANSCRIPTS]["session_id"]


@dataclass(frozen=True)
class BeginBlobsItem:
    existed: bool
    grant: Grant | None
    duplicate_of: int | None


@dataclass(frozen=True)
class BeginBlobsOutcome:
    project_id: str
    project_registration: str  # created | existing
    items: tuple[BeginBlobsItem, ...]
    counts: dict[str, int]


@dataclass(frozen=True)
class Unverified:
    row: int  # index within the request's telemetry_transcripts rows
    reason: str  # missing | stale


def content_uri_for(tenant_id: str, project_id: str, session_ref: str, session_started_at: datetime, sha: str) -> str:
    """'<tenant>/<project>/<session_id>/<sha256>': the session segment equals the session_id prepare_batch derives."""
    session_id = derive_entity_key(_SESSION_PLAN.domain_tag, tenant_id, project_id, session_ref, session_started_at)
    return f"{tenant_id}/{project_id}/{session_id}/{sha}"


def session_prefix(tenant_id: str, project_id: str, session_ref: str, session_started_at: datetime) -> str:
    """The blob-root-relative prefix of one session's objects (the probe's List scope)."""
    session_id = derive_entity_key(_SESSION_PLAN.domain_tag, tenant_id, project_id, session_ref, session_started_at)
    return f"{tenant_id}/{project_id}/{session_id}/"


def known_project_id(con: Any, tenant_id: str, project_ref: str, semantics: dict[str, Any]) -> str | None:
    """The project id from part 1's binding cache, else a read-only lookup; None for a real, unregistered ref.

    A miss on a reserved ref (drill:, synthetic:) is refused 403 project_unregistered before anything else happens.
    """
    cached = reg.cached_project_id(tenant_id, project_ref)
    if cached is not None:
        return cached
    found = reg.lookup_project(con, tenant_id, project_ref, semantics=semantics)
    if found is not None:
        reg._PROJECT_CACHE[(tenant_id, project_ref)] = found[0]
        return found[0]
    if reg.purpose_for(project_ref, reg.purpose_derivation(semantics)) != "real":
        raise TelemetryRequestError(403, "project_unregistered", {"remedy": "register_project"})
    return None


def _verify_all(store: Any, wanted: Sequence[tuple[str, str, int]], now: datetime) -> list[str]:
    """verify() for each (content_uri, sha, size), at most HEAD_WORKERS in flight; an unavailable store answers 503."""
    if not wanted:
        return []
    try:
        with ThreadPoolExecutor(max_workers=min(HEAD_WORKERS, len(wanted))) as pool:
            return list(pool.map(lambda w: store.verify(w[0], w[1], w[2], now=now), wanted))
    except BlobStoreUnavailableError:
        raise TelemetryRequestError(503, "blob_store_unavailable", {}, "blob store unavailable") from None


def _size_mismatch(fields: dict[str, Any]) -> TelemetryRequestError:
    return TelemetryRequestError(422, "blob_not_verified", {**fields, "reason": SIZE_MISMATCH}, "blob not verified")


def _settle_heads(states: Sequence[str], indexes: Sequence[int], fields: Callable[[int], dict[str, Any]]) -> None:
    """A checksum fault (BlobIntegrityError, pages) outranks a HEAD-detected size mismatch (422 naming the first index)."""
    if MISMATCH in states:
        raise BlobIntegrityError("a stored object does not match its content-addressed key")
    for index, state in zip(indexes, states, strict=True):
        if state == SIZE_MISMATCH:
            raise _size_mismatch(fields(index))


def begin_blobs(
    con: Any,
    request: BeginBlobsRequest,
    *,
    tenant_id: str,
    store: Any,
    semantics: dict[str, Any],
    metric_sink: Callable[[str, float], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: datetime | None = None,
) -> BeginBlobsOutcome:
    declared: dict[tuple[str, datetime, str], int] = {}
    for index, item in enumerate(request.items):
        try:
            ref = canonical_ref(item.session_ref)
        except IdentityError:
            raise TelemetryRequestError(
                400, "bad_request", {"reason": "session_ref", "row": index, "column": "session_ref"}
            ) from None
        if declared.setdefault((ref, item.session_started_at, item.content_sha256), item.content_bytes) != item.content_bytes:
            raise _size_mismatch({"row": index})
    project_id = known_project_id(con, tenant_id, request.project_ref, semantics)
    created = False
    if project_id is None:
        identity, candidate = mint_write_identity(), reg.new_entity_id()
        outcome = run_telemetry_txn(
            con,
            lambda c: reg.resolve_or_register_project(
                c,
                tenant_id,
                request.project_ref,
                explicit=False,
                attributes={},
                identity=identity,
                semantics=semantics,
                candidate_id=candidate,
            ),
            metric_sink=metric_sink,
            sleep=sleep,
        )
        project_id, created = outcome.result
        reg._PROJECT_CACHE[(tenant_id, request.project_ref)] = project_id
    moment = now or datetime.now(timezone.utc)
    uris = [
        content_uri_for(tenant_id, project_id, i.session_ref, i.session_started_at, i.content_sha256) for i in request.items
    ]
    first: dict[str, int] = {}
    for index, uri in enumerate(uris):
        first.setdefault(uri, index)
    distinct = list(first.values())
    started = time.perf_counter()
    states = _verify_all(
        store, [(uris[i], request.items[i].content_sha256, request.items[i].content_bytes) for i in distinct], moment
    )
    head_ms = int(round((time.perf_counter() - started) * 1000))
    _settle_heads(states, distinct, lambda index: {"row": index})
    by_index = dict(zip(distinct, states, strict=True))
    answers: list[BeginBlobsItem] = []
    for index, uri in enumerate(uris):
        if first[uri] != index:
            answers.append(BeginBlobsItem(False, None, first[uri]))
        elif by_index[index] == VERIFIED:
            answers.append(BeginBlobsItem(True, None, None))
        else:
            item = request.items[index]
            answers.append(
                BeginBlobsItem(False, store.grant_put(uri, item.content_sha256, item.content_bytes, now=moment), None)
            )
    sizes = [request.items[i].content_bytes for i in distinct]
    counts = {
        "items": len(request.items),
        "distinct_keys": len(distinct),
        "existed": sum(1 for s in states if s == VERIFIED),
        "granted": sum(1 for s in states if s != VERIFIED),
        "stale": sum(1 for s in states if s == STALE),
        "declared_bytes_total": sum(sizes),
        "declared_bytes_max": max(sizes),
        "head_ms": head_ms,
    }
    return BeginBlobsOutcome(project_id, "created" if created else "existing", tuple(answers), counts)


def blob_row_indexes(rows: Sequence[Mapping[str, Any]], threshold: int, cap: int) -> list[int]:
    return [index for index, row in enumerate(rows) if blob_row(row, threshold, cap)]


def check_blob_cap(count: int) -> None:
    if count > MAX_BLOB_ITEMS:
        raise TelemetryRequestError(
            413,
            TOO_MANY_BLOBS,
            {"measure": "blob_rows_per_request", "max": MAX_BLOB_ITEMS},
            "too many blob rows in the request",
        )


def attach_blob_uris(
    rows: Sequence[Mapping[str, Any]], *, tenant_id: str, project_id: str, threshold: int, cap: int
) -> list[dict[str, Any]]:
    """Pure: new row dicts, content_uri set on every blob row from the writer-derived key."""
    out: list[dict[str, Any]] = []
    for row in rows:
        fresh = dict(row)
        if blob_row(row, threshold, cap):
            try:
                fresh["content_uri"] = content_uri_for(
                    tenant_id, project_id, row["session_ref"], row["session_started_at"], row["content_sha256"]
                )
            except (IdentityError, KeyError, TypeError):
                raise TelemetryRequestError(400, "bad_request", {"reason": "session_ref", "column": "session_ref"}) from None
        out.append(fresh)
    return out


def verify_blob_rows(
    rows: Sequence[Mapping[str, Any]], indexes: Sequence[int], *, store: Any, now: datetime | None = None
) -> list[Unverified]:
    """HEAD each distinct object of the blob rows (bounded parallel); the rows whose object is missing or stale.

    Each row is judged on its own declared content_bytes. Before any HEAD, a later row that names an earlier row's object
    with another content_bytes is 422 blob_not_verified (size_mismatch), naming that row. After the HEADs, a checksum
    mismatch raises BlobIntegrityError (a deploy or out-of-band-writer defect, never a caller fault) and outranks a
    HEAD-detected size_mismatch, which is the same 422 naming the first such row.
    """
    moment = now or datetime.now(timezone.utc)
    distinct: dict[str, int] = {}
    for index in indexes:
        first = distinct.setdefault(rows[index]["content_uri"], index)
        if rows[first]["content_bytes"] != rows[index]["content_bytes"]:
            raise _size_mismatch({"table": TRANSCRIPTS, "row": index})
    order = list(distinct.items())
    states = _verify_all(store, [(uri, rows[i]["content_sha256"], rows[i]["content_bytes"]) for uri, i in order], moment)
    state_of = {uri: state for (uri, _), state in zip(order, states, strict=True)}
    _settle_heads(states, [i for _, i in order], lambda index: {"table": TRANSCRIPTS, "row": index})
    return [
        Unverified(i, state_of[rows[i]["content_uri"]])
        for i in indexes
        if state_of[rows[i]["content_uri"]] in (MISSING, STALE)
    ]


def require_stored(con: Any, catalog: str, prepared: Any, unverified: Sequence[Unverified]) -> None:
    """Inside the transaction: every unverified blob row must already be stored (its grain key), else 422 blob_not_verified.

    One partition-bounded read. An object is therefore newly referenced only when present and young, while an identical
    re-send of a stored row stays a no-op whatever the object's age or presence.
    """
    reasons = {u.row: u.reason for u in unverified}
    first_of = {index: event_id for event_id, index in prepared.source_index.items()}
    wanted = {index: first_of[index] for index in reasons if index in first_of}
    rows = {r["event_id"]: r for r in prepared.rows}
    if wanted:
        lows = [rows[e]["session_started_at"].replace(hour=0, minute=0, second=0, microsecond=0) for e in wanted.values()]
        lower, upper = min(lows), max(lows)
        marks = ", ".join("?" for _ in wanted)
        sql = (
            f"SELECT producer, event_id, parser_version FROM {catalog}.{TRANSCRIPTS} "
            f"WHERE session_started_at >= ? AND session_started_at < ? + INTERVAL 1 DAY AND event_id IN ({marks})"
        )
        stored = {(p, e, v) for p, e, v in con.execute(sql, [lower, upper, *wanted.values()]).fetchall()}
    else:
        stored = set()
    for index in sorted(reasons):
        event_id = first_of.get(index)
        row = rows.get(event_id) if event_id is not None else None
        if row is None:
            continue  # a collapsed duplicate: its first occurrence carries the same object and is judged there
        if (row["producer"], event_id, row["parser_version"]) not in stored:
            raise TelemetryRequestError(
                422, "blob_not_verified", {"table": TRANSCRIPTS, "row": index, "reason": reasons[index]}, "blob not verified"
            )


__all__ = [
    "BeginBlobsItem",
    "BeginBlobsOutcome",
    "HEAD_WORKERS",
    "TRANSCRIPTS",
    "Unverified",
    "attach_blob_uris",
    "begin_blobs",
    "blob_row_indexes",
    "check_blob_cap",
    "content_uri_for",
    "known_project_id",
    "require_stored",
    "session_prefix",
    "verify_blob_rows",
]
