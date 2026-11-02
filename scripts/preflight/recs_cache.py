"""Open-recommendation tallies over the warm cache for session_preflight."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import yaml

from scripts.preflight import _common
from src.common.ducklake_reader_client import DuckLakeReader


def _oldest_first_sort_key(row: dict) -> tuple[int, datetime, str]:
    """Sort key: oldest `created_timestamp` first, rows with an unparseable timestamp last, ties
    broken by id. Shared by `_derive_followon_recs` and `_derive_open_critical_recs`: readiness is
    the membership predicate, this is only a deterministic tie-break under the /orient cap, not an
    urgency rank (audit B1-R4 (c) governs the bucket RANK, not this age ordering)."""
    ts = _common._row_ts(row)
    if ts is None:
        return (1, datetime.max.replace(tzinfo=timezone.utc), row.get("id") or "")
    return (0, ts, row.get("id") or "")


def _landed_followon_rec_ids(plans_dir: Path | None = None) -> dict[str, str]:
    """Map each rec id named in a LANDED plan's `followon_recs` to that plan's slug.

    Walks docs/plans/PLAN-*.yaml under the T-1.23 substring gate (scripts/platform_roadmap_state.py
    ::compute_followon_state precedent): a document whose text never mentions the key cannot
    declare one, so it is skipped before yaml.safe_load ever runs -- zero reader call (Decision 88
    invariant ii). Only a plan carrying `implementation_declared: true` contributes (Fork 1: this
    is a DELIBERATE NARROWING of the adjudicated remedy text, pinned by Decision 148's
    implementation_declared resolution signal) -- a plan whose implementation has not landed marks
    its half not-yet-ready, so its rec ids are withheld from the map entirely.

    Exception-contained (a per-file OSError or yaml.YAMLError is skipped, never raised). The first
    plan a sorted walk finds naming a given rec id wins that id's entry.
    """
    resolved_dir = plans_dir if plans_dir is not None else _common.ROOT / "docs" / "plans"
    result: dict[str, str] = {}
    if not resolved_dir.is_dir():
        return result

    for plan_file in sorted(resolved_dir.glob("PLAN-*.yaml")):
        try:
            text = plan_file.read_text(encoding="utf-8")
        except OSError:
            continue
        if "followon_recs" not in text:
            continue
        try:
            plan_data = yaml.safe_load(text)
        except yaml.YAMLError:
            continue
        if not isinstance(plan_data, dict):
            continue
        if plan_data.get("implementation_declared") is not True:
            continue
        recs = plan_data.get("followon_recs")
        if not isinstance(recs, list):
            continue
        slug = plan_file.stem[len("PLAN-") :]
        for rec_id in recs:
            if isinstance(rec_id, str) and rec_id not in result:
                result[rec_id] = slug
    return result


def _derive_followon_recs(
    rows: list[dict] | None, plans_dir: Path | None = None, landed: dict[str, str] | None = None
) -> list[dict] | None:
    """Client-side `followon_recs` /orient key: OPEN recs named in a landed plan's followon_recs.

    Intersects `_landed_followon_rec_ids`'s map with the OPEN rows in *rows* -- both derivations
    take the bounded preflight signal rows as an argument, never a reader call; None (signal rows
    unavailable) stays None. Ordered oldest-created-first via `_oldest_first_sort_key`.
    """
    if rows is None:
        return None
    landed = _landed_followon_rec_ids(plans_dir) if landed is None else landed
    if not landed:
        return []
    matched = [r for r in rows if r.get("status") == "open" and r.get("id") in landed]
    matched.sort(key=_oldest_first_sort_key)
    return [
        {
            "id": r.get("id", ""),
            "title": r.get("title", ""),
            "parent_plan": landed[r.get("id", "")],
        }
        for r in matched
    ]


def _derive_open_critical_recs(rows: list[dict] | None) -> list[dict] | None:
    """Client-side `open_critical_recs` /orient key: open Critical recs whose source is not the
    EXACT string 'ci_rca' (Fork 3) -- an open Critical `ci_rca_evidence_dispute` row IS surfaced,
    because every ci_rca preflight predicate and the Decision 73 halt match the exact source only,
    so such a row would otherwise have no lane at all (rec-3054's own complaint; audit B4-O1).
    Ordered oldest-created-first via `_oldest_first_sort_key` (a tie-break, not an urgency rank).
    None (signal rows unavailable) stays None.
    """
    if rows is None:
        return None
    matched = [
        r
        for r in rows
        if r.get("status") == "open"
        and str(r.get("priority", "")).lower() == "critical"
        and str(r.get("source", "")) != "ci_rca"
    ]
    matched.sort(key=_oldest_first_sort_key)
    return [
        {
            "id": r.get("id", ""),
            "title": r.get("title", ""),
            "source": r.get("source", ""),
        }
        for r in matched
    ]


def _derive_decisions_max_updated(rows: list[dict]) -> list[dict]:
    """Client-side `decisions_max_updated` verb: [{ts: max(last_updated_timestamp)}] (mirrors the verb row)."""
    raw_stamps = [_common._row_ts(r, "last_updated_timestamp") for r in rows]
    stamps = [s for s in raw_stamps if s is not None]
    if not stamps:
        return [{"ts": None}]
    newest = max(stamps)
    # Echo the original string form when present (the verb returns the stored value, not a re-format).
    for r in rows:
        if _common._row_ts(r, "last_updated_timestamp") == newest:
            return [{"ts": r.get("last_updated_timestamp")}]
    return [{"ts": newest.isoformat()}]


def _tally_rec_counts(
    rows: list[dict],
    source: str = "reader",
) -> tuple[int, int, int, list[dict]]:
    """Compute (open_count, aging_count, non_auto_count, non_auto_details) from a row list."""
    now = datetime.now(timezone.utc)
    open_count = len(rows)
    aging_count = 0
    non_auto_count = 0
    non_auto_details: list[dict] = []

    for entry in rows:
        date_str = entry.get("created_timestamp", "")
        if date_str:
            try:
                if isinstance(date_str, str):
                    entry_date = datetime.fromisoformat(date_str.replace("Z", "+00:00")).replace(tzinfo=timezone.utc)
                else:
                    import datetime as dt  # noqa: PLC0415

                    if hasattr(date_str, "timestamp"):
                        entry_date = dt.datetime.fromtimestamp(date_str.timestamp(), tz=timezone.utc)
                    else:
                        entry_date = now
                if (now - entry_date).days > 30:
                    aging_count += 1
            except (ValueError, AttributeError, TypeError):
                pass
        # Reader rows carry native Python bools; rows migrated from the legacy path may
        # still serialise booleans as the strings "true"/"false" -- normalise both forms.
        raw_auto = entry.get("automatable", True)
        if isinstance(raw_auto, bool):
            automatable = raw_auto
        else:
            automatable = str(raw_auto).lower() != "false"
        if not automatable:
            non_auto_count += 1
            if len(non_auto_details) < 10:
                context = entry.get("context", "")
                non_auto_details.append(
                    {
                        "id": entry.get("id", ""),
                        "title": entry.get("title", ""),
                        "context_excerpt": (context[:200] if isinstance(context, str) else ""),
                    }
                )

    return open_count, aging_count, non_auto_count, non_auto_details


def _count_recommendations_reader(tally: object = _common._READER_SENTINEL) -> tuple[int, int, int, list[dict]] | str:
    """Return (open, aging, non_automatable, []) from the open_recs_tally row preflight fetched.

    None (the tally read failed) -> "reader_unreachable": Decision 55's loud degraded signal, never a
    false zero. There is no reader fallback here; the details list is empty (Decision 215).
    """
    if not isinstance(tally, dict):
        return "reader_unreachable"
    return (
        int(tally.get("open_count") or 0),
        int(tally.get("aging_count") or 0),
        int(tally.get("non_automatable_count") or 0),
        [],
    )


def _check_non_automatable_softcap(non_auto_count: int) -> bool:
    """Return True when non-automatable rec count exceeds the soft cap."""
    return non_auto_count > _common._NON_AUTOMATABLE_SOFTCAP


def _get_latest_decision_ts(cache_rows: object = _common._READER_SENTINEL) -> str | None:
    """Return the max decision last_updated_timestamp -- from the warm-pulled rows, else the verb.

    cache_rows (neon-egress-reduction D4): a supplied row list is served via
    _derive_decisions_max_updated (zero reader call); a supplied None -> None. Omitted (sentinel) ->
    decisions_max_updated verb (back-compat / tests).
    """
    if cache_rows is not _common._READER_SENTINEL:
        if cache_rows is None:
            return None
        rows = _derive_decisions_max_updated(cache_rows)  # type: ignore[arg-type]
    else:
        try:
            reader = cast(DuckLakeReader, _common._make_reader(table="ops_decisions"))
            rows = reader.named("decisions_max_updated")
        except Exception:  # noqa: BLE001
            return None
    if not rows:
        return None
    ts = rows[0].get("ts") or ""
    return str(ts) if ts else None
