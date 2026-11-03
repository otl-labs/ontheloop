"""ops_decisions CRUD + DECISIONS.md ETL.

Owner-concern: filing/updating decision rows (numbering authority is DECISIONS.md; the
caller supplies decision_id, Decision 84 I-2 exception) and the idempotent backfill ETL
that rebuilds ops_decisions from DECISIONS.md. Preserves Decision 91 verb routing
(file_decision -> write_ops).

DAF-01 parity pass (PLAN-daf-etl-parity-fidelity, Decision 134 cl.4): the backfill carries
four new parity-backstop columns (raw_block, reversal_conditions, superseded_by,
content_hash), a client-side content_hash skip gate (reads the current row's hash via the
ducklake_reader boundary to decide whether a write is needed -- the write source stays
DECISIONS.md, never a read cache), and a per-run fidelity tripwire that fails loudly on a
NEW live-h2 entry with an empty decision_text or a non-ISO decided_date, checked against the
checked-in allowlist at config/agent/data_quality/decisions/fidelity_baseline.yaml.

DCG-03 divergence guard (PLAN-dcg-compaction-lifecycle, Decision 149): the backfill also
asserts every ops_decisions current-projection id has a matching '## Decision N:' header in
docs/DECISIONS.md or docs/DECISIONS_ARCHIVE.md (scripts.decisions_md.decision_header_numbers()),
via an allowlist-diff baseline at config/agent/data_quality/decisions/orphan_baseline.yaml
(mirrors the fidelity_baseline.yaml pattern). Raises RuntimeError loudly (Decision 55) on any
NEW orphan -- a warehouse current row with no backing header and not in the baseline.

DCG-06 intent capture (PLAN-dcg-intent-capture, Decision 151): _DECISION_BACKFILL_COLS carries
"intent" so the backfill threads the parser's new intent key to the writer; the existing
`k in _DECISION_BACKFILL_COLS and v not in (None, "")` field-selection filter drops a
markerless entry's intent="" the same way it already drops any other empty optional field.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional, cast

import yaml

from scripts.executor.jsonl_store import DECISIONS_JSONL, Decision
from scripts.ops_portal._common import ROOT
from scripts.ops_portal.cache import _refresh_cache_after_write, _sanitize_record, _sync_table
from scripts.ops_portal.reader_transient import is_reader_unavailable
from scripts.ops_portal.write_validators import gate_write
from scripts.ops_portal.writer_transport import _ducklake_write

if TYPE_CHECKING:
    from src.common.ducklake_reader_client import Reader

logger = logging.getLogger(__name__)

# DECISIONS.md columns carried by the backfill ETL. Excludes id + decision_id (passed via
# _migration_int_id) and the timestamps (portal/runtime stamp them; the store is recreatable).
_DECISION_BACKFILL_COLS = (
    "title",
    "status",
    "problem",
    "decision_text",
    "context",
    "decided_date",
    "related_decisions",
    "raw_block",
    "reversal_conditions",
    "superseded_by",
    "content_hash",
    "intent",
)

_FIDELITY_BASELINE_PATH = ROOT / "config" / "agent" / "data_quality" / "decisions" / "fidelity_baseline.yaml"
_ORPHAN_BASELINE_PATH = ROOT / "config" / "agent" / "data_quality" / "decisions" / "orphan_baseline.yaml"

_ISO_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}(?:-\d{2})?")


def _load_fidelity_baseline() -> set[int]:
    """Load the checked-in known-bad live-entry allowlist (Decision 134 cl.4 / DAF-01).

    Allowlist-diff, not a zero-assertion: baselined pre-77-band entries (e.g. dec-067) are
    known-unrecoverable at the parser's current fidelity and never trip the tripwire.
    """
    if not _FIDELITY_BASELINE_PATH.exists():
        return set()
    doc = yaml.safe_load(_FIDELITY_BASELINE_PATH.read_text(encoding="utf-8")) or {}
    return {int(e["decision_id"]) for e in doc.get("known_bad_live_entries", [])}


def _live_decision_ids() -> set[int]:
    """Decision numbers headed in the LIVE docs/DECISIONS.md file only (not the archive).

    The fidelity tripwire scopes to live-h2 entries -- the archive is historical-band prose
    that predates the bold-marker authoring convention and is not held to the same bar.
    """
    from scripts.decisions_md import _DECISION_HEADING_RE, _DECISIONS_MD_PATHS  # noqa: PLC0415

    live_path = next(p for p in _DECISIONS_MD_PATHS if p.name == "DECISIONS.md")
    assert live_path.name == "DECISIONS.md"
    if not live_path.exists():
        return set()
    content = live_path.read_text(encoding="utf-8", errors="replace")
    return {int(m.group(1)) for m in _DECISION_HEADING_RE.finditer(content)}


def _fidelity_issue(entry: dict) -> Optional[str]:
    """Return a fidelity-issue label for a parsed live entry, or None if it is clean."""
    if not entry.get("decision_text"):
        return "empty_decision_text"
    decided_date = entry.get("decided_date") or ""
    if decided_date and not _ISO_DATE_PREFIX_RE.match(decided_date):
        return "non_iso_decided_date"
    return None


# DCG-03 transient-tolerance marker (Decision 155): distinct and greppable, mirrored to
# GITHUB_STEP_SUMMARY when set, so an accumulating skip stays observable rather than silent.
_ORPHAN_GUARD_TRANSIENT_MARKER = "[PORTAL] DCG-03 orphan guard SKIPPED (transient reader outage)"


def _load_orphan_baseline() -> set[str]:
    """Load the checked-in known-orphaned current-row allowlist (DCG-03 / Decision 149).

    Allowlist-diff, not a zero-assertion: a pre-existing, investigated, disposed orphan (e.g.
    dec-010, a leaked test row with no header ever written) never trips the divergence guard.
    Mirrors _load_fidelity_baseline's shape and read-then-set-comprehension pattern.
    """
    if not _ORPHAN_BASELINE_PATH.exists():
        return set()
    doc = yaml.safe_load(_ORPHAN_BASELINE_PATH.read_text(encoding="utf-8")) or {}
    return {str(e["decision_id"]) for e in doc.get("known_orphaned_current_rows", [])}


def _orphaned_current_ids(current_ids: set[str], header_numbers: set[int], baseline: set[str]) -> set[str]:
    """Pure DCG-03 diff: current-projection ids with no matching header, minus the baseline.

    Args:
        current_ids: 'dec-NNN' ids present in the ops_decisions current projection.
        header_numbers: bare ints from scripts.decisions_md.decision_header_numbers() (every
            '## Decision N:' header across docs/DECISIONS.md + docs/DECISIONS_ARCHIVE.md).
        baseline: the checked-in allowlist from _load_orphan_baseline().

    Never mutates its inputs. Returns exactly the NEW-orphan set (headered or baselined ids are
    never included, regardless of which set(s) they appear in).
    """
    headered_ids = {f"dec-{n:03d}" for n in header_numbers}
    return (set(current_ids) - headered_ids) - set(baseline)


def _assert_no_orphaned_current_rows(profile: Optional[str] = None, reader: Optional[Reader] = None) -> None:
    """DCG-03 divergence guard: raise RuntimeError loudly on any NEW orphaned current row.

    An "orphan" is an ops_decisions current-projection row (identified by its 'id' column) with
    no matching '## Decision N:' header in docs/DECISIONS.md or docs/DECISIONS_ARCHIVE.md, and
    not present in the checked-in config/agent/data_quality/decisions/orphan_baseline.yaml
    allowlist. If a header were ever removed from both files without a replacement, its
    warehouse current row would otherwise be served forever in its last state with no signal it
    was retired -- this guard makes that latent hazard loud instead (Decision 55).

    reader is an injectable seam (mirrors _load_fidelity_baseline's baseline_reader /
    validate_decision_entry_conformance's root+baseline_reader precedent) so a synthetic test
    can inject a fake reader without live DuckLake credentials. Defaults to
    make_reader(profile=profile) -- the closed Decision 84 I-3 named-verb boundary
    (reader.current_state, no caller SQL, no new Lambda verb).
    """
    from scripts.decisions_md import decision_header_numbers  # noqa: PLC0415

    active_reader = reader
    if active_reader is None:
        from src.common.ducklake_reader_client import make_reader  # noqa: PLC0415

        active_reader = make_reader(profile=profile)

    try:
        current_rows = active_reader.current_state("ops_decisions")
    except Exception as exc:  # noqa: BLE001
        if not is_reader_unavailable(exc):
            raise
        message = (
            f"{_ORPHAN_GUARD_TRANSIENT_MARKER}: current_state('ops_decisions') raised {exc!r}. "
            "A 502/503/504 here indicates transient DuckLake reader unavailability, NOT a reliable "
            "cold-resume signal -- the orphan comparison did not run this backfill. The independent "
            "per-row backfill loop still exits non-zero on any failed row (Decision 155)."
        )
        logger.warning(message)
        if summary_path := os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(summary_path, "a", encoding="utf-8") as f:
                f.write(f"\n## DCG-03 orphan guard skipped (transient)\n\n{message}\n")
        return

    current_ids = {row["id"] for row in current_rows if row.get("id")}

    baseline = _load_orphan_baseline()
    header_numbers = decision_header_numbers()

    orphans = _orphaned_current_ids(current_ids, header_numbers, baseline)
    if orphans:
        raise RuntimeError(
            f"[PORTAL] DCG-03 divergence guard: {len(orphans)} NEW orphaned ops_decisions "
            f"current-projection row(s) with no matching DECISIONS.md/DECISIONS_ARCHIVE.md header "
            f"and absent from the checked-in baseline ({_ORPHAN_BASELINE_PATH}): {sorted(orphans)}. "
            "Investigate before allowlisting -- a genuine orphan is never silently baselined "
            "(Decision 55)."
        )


def file_decision(
    fields: dict,
    profile: Optional[str] = None,
    _migration_int_id: Optional[int] = None,
    _skip_sync: bool = False,
) -> str:
    """File a decision row for a DECISIONS.md entry (numbering authority: DECISIONS.md).

    Decision 84 I-2 exception: decision numbers are human-assigned in DECISIONS.md before
    any write, so the caller supplies the integer number via fields['decision_id'] (the
    backfill path passes _migration_int_id). The id is formed as dec-{n:03d}. The write is
    a caller-keyed write_ops upsert, so re-running the backfill refreshes the same id
    rather than duplicating it.

    Returns:
        The decision ID string (e.g. 'dec-084'). Raises LOUDLY on any failure (no outbox).
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    merged = dict(fields)

    n = _migration_int_id if _migration_int_id is not None else merged.get("decision_id")
    if not isinstance(n, int) or n <= 0:
        raise ValueError(
            "file_decision requires the DECISIONS.md-assigned integer decision number "
            "(fields['decision_id'] or _migration_int_id): decisions are authored in "
            "DECISIONS.md FIRST (Decision 84 I-2 exception)"
        )

    dec_id = f"dec-{n:03d}"
    merged["id"] = dec_id
    merged["decision_id"] = n
    merged.setdefault("created_timestamp", now_iso)
    merged["last_updated_timestamp"] = now_iso

    gate_write("ops_decisions", merged, insert=False)

    Decision.model_validate(merged)

    response = _ducklake_write("ops_decisions", merged, action="write_ops", profile=profile)
    logger.info("[PORTAL] Filed decision %s: %s", dec_id, merged.get("title", ""))
    _refresh_cache_after_write("ops_decisions", merged, response, DECISIONS_JSONL, append_only=_skip_sync)
    return dec_id


def _fetch_decision_from_reader(decision_id: str, profile: Optional[str] = None) -> Optional[dict]:
    """Fetch a single ops_decisions record by id via the decision_by_id read verb.

    Closed boundary (Decision 84 I-1/I-3): decisions read from DuckLake like every migrated
    ops table, its sole backend. Decision 69: raises on reader failure; never returns cache.
    Returns the coerced record dict or None if not found.
    """
    if not re.fullmatch(r"dec-\d+", decision_id):
        raise ValueError(f"_fetch_decision_from_reader: invalid decision_id: {decision_id!r}")

    from scripts.sync.ops import _coerce_ops_decisions_row  # noqa: PLC0415
    from src.common.ducklake_reader_client import DuckLakeReader, make_reader  # noqa: PLC0415

    # make_reader() is annotated -> Reader (the Protocol, which deliberately does not declare
    # named()) but only ever constructs a DuckLakeReader; cast narrows the type at this one call
    # site rather than widening the Protocol by omission.
    reader = cast(DuckLakeReader, make_reader(profile=profile))
    rows = reader.named("decision_by_id", id=decision_id)
    if not rows:
        return None
    rec = _coerce_ops_decisions_row(dict(rows[0]))
    return _sanitize_record(rec) if rec is not None else None


def update_decision(decision_id: str, updates: dict, profile: Optional[str] = None) -> bool:
    """Merge update fields into an existing decision via the DuckLake writer.

    Reads the current record through the decision_by_id verb, merges updates,
    validates, and writes via update_ops (in-transaction referential check).

    Args:
        decision_id: Decision ID string to update (e.g. 'dec-072').
        updates: Fields to merge into the existing record.
        profile: Optional AWS profile override.

    Returns:
        True on success.

    Raises:
        RuntimeError: If the warehouse is unreachable.
        ValidationError: If the merged record fails schema validation.
    """
    existing = _fetch_decision_from_reader(decision_id, profile=profile)
    if existing is None:
        raise RuntimeError(
            f"update_decision: {decision_id} does not exist in the current projection -- an absent decision "
            "cannot be updated (referential, CD.33 cl.8 / D-5). File it first via file_decision."
        )
    merged = {**existing, **updates}
    merged["id"] = decision_id

    gate_write("ops_decisions", merged, insert=False, existing=existing)
    Decision.model_validate(merged)

    response = _ducklake_write("ops_decisions", merged, action="update_ops", profile=profile)
    logger.info("[PORTAL] Updated %s: %s", decision_id, list(updates.keys()))
    _refresh_cache_after_write("ops_decisions", merged, response, DECISIONS_JSONL)
    return True


def backfill_decisions_from_md(profile: Optional[str] = None, orphan_reader: Optional[Reader] = None) -> dict:
    """ETL DECISIONS.md -> ops_decisions (premise P3: the markdown is the source of truth).

    Idempotent: each entry is a caller-keyed write_ops upsert on dec-{n:03d}. Differential
    since the content_hash skip gate (DAF-01): an entry whose parser-normalized content_hash
    is unchanged from the current warehouse row is skipped rather than re-versioned.

    Raises RuntimeError (fidelity tripwire) if a NEW live-h2 entry (not in the checked-in
    fidelity_baseline.yaml allowlist) parses to an empty decision_text or a non-ISO
    decided_date -- this is a loud-fail check, not a silent counter (Decision 55).

    Also raises RuntimeError (DCG-03 divergence guard, Decision 149) if the ops_decisions
    current projection contains a NEW orphan -- a row with no matching header in either
    DECISIONS.md or DECISIONS_ARCHIVE.md and absent from the checked-in
    orphan_baseline.yaml allowlist -- via _assert_no_orphaned_current_rows(). orphan_reader
    is an injectable seam for that guard (see its docstring); None uses the live DuckLake
    reader.

    Returns:
        {"written": N, "failed": M, "skipped": K}
    """
    from scripts.decisions_md import parse_decisions_md  # noqa: PLC0415
    from scripts.sync.ops import _coerce_array  # noqa: PLC0415

    baseline = _load_fidelity_baseline()
    live_ids = _live_decision_ids()

    written = failed = skipped = 0
    regressions: list[dict] = []

    for entry in parse_decisions_md():
        try:
            n = int(str(entry.get("decision_id", "")).strip())
        except ValueError:
            n = 0
        if n <= 0:
            skipped += 1
            continue

        if n in live_ids and n not in baseline:
            issue = _fidelity_issue(entry)
            if issue is not None:
                regressions.append({"decision_id": n, "issue": issue})

        # Archive entries may carry no status marker; the column is non-nullable, so be honest.
        fields = {k: v for k, v in entry.items() if k in _DECISION_BACKFILL_COLS and v not in (None, "")}
        fields.setdefault("status", "unspecified")
        if "related_decisions" in fields:
            fields["related_decisions"] = _coerce_array(fields["related_decisions"], elem_type=int)

        dec_id = f"dec-{n:03d}"
        try:
            existing = _fetch_decision_from_reader(dec_id, profile=profile)
            if existing is not None and existing.get("content_hash") and existing["content_hash"] == entry.get("content_hash"):
                skipped += 1
                continue
            file_decision(fields, profile=profile, _migration_int_id=n, _skip_sync=True)
            written += 1
        except Exception as exc:  # noqa: BLE001 -- per-row isolation; the summary surfaces failures
            logger.warning("[PORTAL] backfill_decisions_from_md: dec-%03d failed: %s", n, exc)
            failed += 1

    if written:
        _sync_table("ops_decisions")

    logger.info(
        "[PORTAL] backfill_decisions_from_md fidelity report: written=%d failed=%d skipped=%d "
        "live_entries_checked=%d baseline_size=%d new_regressions=%d",
        written,
        failed,
        skipped,
        len(live_ids),
        len(baseline),
        len(regressions),
    )

    if regressions:
        raise RuntimeError(
            f"[PORTAL] backfill_decisions_from_md fidelity tripwire: {len(regressions)} NEW live-entry "
            f"regression(s) not present in the checked-in baseline ({_FIDELITY_BASELINE_PATH}): "
            f"{regressions}. written={written} failed={failed} skipped={skipped}"
        )

    _assert_no_orphaned_current_rows(profile=profile, reader=orphan_reader)

    return {"written": written, "failed": failed, "skipped": skipped}
