"""CI-RCA rec derivation, fetch, print, and liveness concern for session_preflight."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import yaml

from scripts.preflight import _common
from src.common.ducklake_reader_client import DuckLakeReader


def _derive_ci_rca_open(rows: list[dict]) -> list[dict]:
    """Client-side `ci_rca_open` verb: open/in-progress ci_rca recs, newest first, capped at 5."""
    matched = [r for r in rows if r.get("source") == "ci_rca" and r.get("status") in ("open", "in_progress")]
    matched.sort(key=lambda r: _common._row_ts(r) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return [
        {
            "id": r.get("id", ""),
            "title": r.get("title", ""),
            "priority": r.get("priority", ""),
            "created_timestamp": r.get("created_timestamp"),
            "file": r.get("file", ""),
            "acceptance": r.get("acceptance"),
        }
        for r in matched[:5]
    ]


def _derive_ci_rca_dispute_open(rows: list[dict]) -> list[dict]:
    """Client-side derive: open/in-progress ci_rca_evidence_dispute recs, newest first, capped at 5."""
    matched = [r for r in rows if r.get("source") == "ci_rca_evidence_dispute" and r.get("status") in ("open", "in_progress")]
    matched.sort(key=lambda r: _common._row_ts(r) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return [
        {
            "id": r.get("id", ""),
            "title": r.get("title", ""),
            "priority": r.get("priority", ""),
            "created_timestamp": r.get("created_timestamp"),
        }
        for r in matched[:5]
    ]


def _derive_ci_rca_undetermined_open(cache_rows: list[dict]) -> list[dict]:
    """Return ALL open source=ci_rca recs with rca_confidence in {low, undetermined} -- a
    self-rated-low-confidence RCA is not sound either, not just a literal abstention (untruncated;
    CIRCA-10 moved the cap to print time)."""
    import json as _json  # noqa: PLC0415

    results = []
    for row in cache_rows:
        if row.get("source") != "ci_rca":
            continue
        if row.get("status") != "open":
            continue
        ctx_raw = row.get("context_v2_json") or ""
        if not ctx_raw:
            continue
        try:
            ctx = _json.loads(ctx_raw)
        except Exception:
            continue
        if ctx.get("rca_confidence") in ("low", "undetermined"):
            results.append(row)
    return results


def _derive_ci_rca_closed(rows: list[dict]) -> list[dict]:
    """Client-side derive: closed ci_rca recs projected to the sibling-cluster fields."""
    matched = [r for r in rows if r.get("source") == "ci_rca" and r.get("status") == "closed"]
    return [
        {
            "id": r.get("id", ""),
            "file": r.get("file", ""),
            "title": r.get("title", ""),
            "last_updated_timestamp": r.get("last_updated_timestamp"),
        }
        for r in matched
    ]


def _derive_ci_rca_since(rows: list[dict], since_ts: str) -> list[dict]:
    """Client-side `ci_rca_since` verb: ci_rca rec ids created strictly after *since_ts*."""
    cutoff = _common._parse_ts_utc(since_ts)
    if cutoff is None:
        return []
    out: list[dict] = []
    for r in rows:
        if r.get("source") != "ci_rca":
            continue
        ts = _common._row_ts(r)
        if ts is not None and ts > cutoff:
            out.append({"id": r.get("id", "")})
    return out


def _fetch_ci_rca_undetermined_recs(cache_rows: object = _common._READER_SENTINEL) -> list[dict] | None:
    """Open ci_rca recs with rca_confidence in {low, undetermined}; None when the signal rows are unavailable."""
    if cache_rows is not _common._READER_SENTINEL:
        return None if cache_rows is None else _derive_ci_rca_undetermined_open(cache_rows)  # type: ignore[arg-type]
    return []


def print_ci_rca_undetermined_recs(recs: list[dict]) -> None:
    """Print advisory abstention-review section (CIRCA-10): displays <=5, notes overflow past 5."""
    print("\n--- CI-RCA Abstention Review (advisory; rca_confidence in {low, undetermined}) ---")
    if not recs:
        print("  (none)")
        print()
        return
    print("  A self-rated low-confidence or undetermined RCA classification -- review the proximate cause manually.")
    print(
        "  Advisory only: open ci_rca recs are triaged under Decision 73 L5, narrowed by Decision 226, "
        "into an overlap block or a likely-resolved prompt."
    )
    for rec in recs[:5]:
        rec_id = rec.get("id", "unknown")
        title = rec.get("title", "")
        priority = rec.get("priority", "")
        created = rec.get("created_timestamp", "")
        print(f"  {rec_id} [{priority}] {created}: {title}")
    if len(recs) > 5:
        print(f"  ... showing 5 of {len(recs)} open low-confidence/undetermined recs")
    print()


def _fetch_ci_rca_recs(cache_rows: object = _common._READER_SENTINEL) -> list[dict] | None:
    """Up to 5 open CI-RCA recs from the supplied signal rows (None when unavailable), else the reader verb."""
    if cache_rows is not _common._READER_SENTINEL:
        return None if cache_rows is None else _derive_ci_rca_open(cache_rows)  # type: ignore[arg-type]

    _reader_exc: Exception | None = None
    try:
        return cast(DuckLakeReader, _common._make_reader()).named("ci_rca_open")
    except Exception as exc:  # noqa: BLE001
        _reader_exc = exc

    print(
        f"[WARN] preflight: ci_rca recs reader unreachable ({_reader_exc}) -- CI RCA Recs "
        "section degraded (recs_read_status=reader_unreachable). The reader is the sole "
        "backend (Decision 81 cl.7).",
        file=sys.stderr,
    )
    return []


def _fetch_ci_rca_dispute_recs(cache_rows: object = _common._READER_SENTINEL) -> list[dict] | None:
    """Up to 5 open ci_rca_evidence_dispute recs from the supplied signal rows; None when unavailable."""
    if cache_rows is not _common._READER_SENTINEL:
        return None if cache_rows is None else _derive_ci_rca_dispute_open(cache_rows)  # type: ignore[arg-type]
    return []


def print_ci_rca_dispute_recs(recs: list[dict]) -> None:
    """Print the CI-RCA Dispute Recs section to terminal."""
    print("\n--- CI-RCA Dispute Recs (open) ---")
    if not recs:
        print("  (none)")
        print()
        return
    for rec in recs:
        rec_id = rec.get("id", "unknown")
        title = rec.get("title", "")
        priority = rec.get("priority", "")
        created = rec.get("created_timestamp", "")
        print(f"  {rec_id} [{priority}] {created}: {title}")
    print()


def _fetch_ci_rca_recs_since(ts: str, cache_rows: object = _common._READER_SENTINEL) -> list[dict] | None:
    """ci_rca recs created after *ts* from the supplied signal rows (None when unavailable), else the reader verb."""
    if cache_rows is not _common._READER_SENTINEL:
        return None if cache_rows is None else _derive_ci_rca_since(cache_rows, ts)  # type: ignore[arg-type]
    try:
        return cast(DuckLakeReader, _common._make_reader()).named("ci_rca_since", since_ts=ts)
    except Exception:  # noqa: BLE001
        pass
    return []


def _check_ci_rca_liveness(creds_status: str, cache_rows: object = _common._READER_SENTINEL) -> dict | None:
    """Return alert dict when main CI has been red with no ci-rca rec for >30 min.

    Calls `gh run list` to determine the latest push-to-main ci.yml result.
    Returns None when credentials are unavailable, gh call fails, or conditions are not met.

    Unavailable signal rows (cache_rows is None) raise no alert: unavailability is carried by recs_read_status.
    """
    if creds_status != "ok" or cache_rows is None:
        return None
    try:
        result = subprocess.run(
            [
                "gh",
                "run",
                "list",
                "--branch",
                "main",
                "--workflow",
                "ci.yml",
                "--event",
                "push",
                "--limit",
                "1",
                "--json",
                "conclusion,createdAt,url",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        runs = json.loads(result.stdout)
        if not runs:
            return None
        run = runs[0]
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError, IndexError):
        return None

    if run.get("conclusion") != "failure":
        return None

    created_at = run.get("createdAt", "")
    if not created_at:
        return None

    try:
        run_ts = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        elapsed_minutes = (now - run_ts).total_seconds() / 60.0
    except (ValueError, TypeError):
        return None

    if elapsed_minutes <= 30:
        return None

    if _fetch_ci_rca_recs_since(created_at, cache_rows=cache_rows):
        return None

    return {"run_url": run.get("url", ""), "elapsed_minutes": round(elapsed_minutes, 1)}


def _derive_convergence_gap_tracking_since(rows: list[dict], since_ts: str) -> list[dict]:
    """PRIVATE PREDICATE for _check_convergence_rca_gap ONLY (Decision 190): tf_drift rec ids
    created strictly after since_ts, deliberately NEVER source=ci_rca -- a tf_drift-tracked red
    never counted before, while ANY unrelated ci_rca rec (rec-3802/3809/3810/3811) silently
    satisfied the old matcher (ci_rca recs carry no field binding them to a specific episode, and
    Decision 142 forbids a second cause-resolution classifier here). Deliberately NOT a widening
    of _derive_ci_rca_since (Decision 84 I-3 verb equivalence; also reached by
    _check_ci_rca_liveness) -- a separate, narrower derivation with no other caller.
    """
    cutoff = _common._parse_ts_utc(since_ts)
    if cutoff is None:
        return []
    out: list[dict] = []
    for r in rows:
        if r.get("source") != "tf_drift":
            continue
        ts = _common._row_ts(r)
        if ts is not None and ts > cutoff:
            out.append({"id": r.get("id", "")})
    return out


def _fetch_convergence_gap_tracking_recs_since(ts: str, cache_rows: object = _common._READER_SENTINEL) -> list[dict] | None:
    """Supplied-rows-only fetch for the predicate above (never a reader call); None when unavailable."""
    if cache_rows is not _common._READER_SENTINEL:
        return None if cache_rows is None else _derive_convergence_gap_tracking_since(cache_rows, ts)  # type: ignore[arg-type]
    return []


def _check_convergence_sensor_liveness(creds_status: str) -> dict | None:
    """Return alert dict when the latest scheduled convergence-health.yml run did not succeed.

    Modelled directly on _check_ci_rca_liveness: same creds_status guard, same subprocess `gh run
    list` shape with a timeout, same degrade-to-None error handling. Fires on the WORKFLOW's own
    conclusion, independent of the convergence record's colour -- this closes the exact blind spot
    the 2026-08-17 incident exposed: the sensor step raised (an unregistered source) and exited
    non-zero on ~20 consecutive scheduled runs over 22 hours, but the convergence RECORD stayed
    green throughout (only a separate, unrelated writer flips it), so preflight's existing
    convergence_health.status read reported healthy the whole time. Returns its own payload shape
    under its own report key -- never touches _check_convergence_rca_gap's payload contract.

    Returns None when credentials are unavailable, the gh call fails/times out/raises, the
    payload is malformed, or the latest run's conclusion is success or not yet terminal (null --
    e.g. a still-running scheduled tick, which is not evidence of anything wrong yet).
    """
    if creds_status != "ok":
        return None
    try:
        result = subprocess.run(
            [
                "gh",
                "run",
                "list",
                "--branch",
                "main",
                "--workflow",
                "convergence-health.yml",
                "--limit",
                "1",
                "--json",
                "conclusion,createdAt,url",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        runs = json.loads(result.stdout)
        if not runs:
            return None
        run = runs[0]
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError, IndexError):
        return None

    conclusion = run.get("conclusion")
    if not conclusion or conclusion == "success":
        return None

    return {
        "run_url": run.get("url", ""),
        "conclusion": conclusion,
        "created_at": run.get("createdAt", ""),
    }


_CONVERGENCE_RCA_GAP_GRACE_MINUTES = 30


def _check_convergence_rca_gap(convergence_health: dict | None, cache_rows: object = _common._READER_SENTINEL) -> dict | None:
    """Return alert dict when the convergence record is red beyond grace with no tracking rec since.

    Generalises _check_ci_rca_liveness (ci.yml push-to-main only) to the convergence-record
    surface: PLAN-gated-apply-rca-trigger's confirmed gap (run 28379330706) wrote a red record
    with zero RCA signal, invisible to that check. Matches on red_since vs a tf_drift rec's
    creation time (Decision 190 -- see _derive_convergence_gap_tracking_since for why ci_rca is
    excluded, not added alongside it), never commit_sha (no structured field carries it; a commit
    match would false-positive forever after a valid rec is filed). Degrades to None on any error
    or missing data (rec-2027 pattern -- never crashes preflight).
    """
    try:
        if cache_rows is None or not convergence_health or convergence_health.get("status") != "red":
            return None

        red_since = convergence_health.get("red_since")
        if not red_since:
            return None

        red_age_hours = convergence_health.get("red_age_hours") or 0.0
        if (red_age_hours * 60.0) <= _CONVERGENCE_RCA_GAP_GRACE_MINUTES:
            return None

        if _fetch_convergence_gap_tracking_recs_since(red_since, cache_rows=cache_rows):
            return None

        return {
            "commit_sha": convergence_health.get("commit_sha", ""),
            "run_url": convergence_health.get("run_url", ""),
            "red_age_hours": round(red_age_hours, 2),
            "red_since": red_since,
        }
    except Exception:  # noqa: BLE001
        return None


_OWNER_NAMED_RE = re.compile(
    r"another agent|operator-direct(?:ed|ion)|human-direct(?:ed|ion)|human deferred|operator ruling",
    re.IGNORECASE,
)

# code-review round 1 High finding (ci_rca_signals.py:434): searching the WHOLE qualifying entry
# for an owner phrase fabricates an attribution when the entry merely *discusses* a rec near the
# word "defer" rather than recording a deferral decision for it -- reproduced live against this
# plan's own corpus entry, an 851-char census-methodology note whose rec-id mentions sit
# 187-247 chars from an unrelated "operator-directed" example quote. The one plan this scan's
# owner leg is ever graded against (docs/plans/PLAN-ambient-prose-contract-relocation.yaml, the
# earliest-sorting owner-bearing plan for the corpus's two audit-pinned ids) places its owner
# phrase within 53 chars of the rec id it deferred. 100 sits with margin on both sides of that
# measured split (53 genuine / 187 fabricated) -- bounding the owner search to a window around
# the SPECIFIC rec id's own mention, not the entry as a whole.
_OWNER_PROXIMITY_CHARS = 100


def _owner_named_near(entry_lower: str, rec_id: str) -> str | None:
    """Return the lower-cased owner phrase within `_OWNER_PROXIMITY_CHARS` of *rec_id*'s first
    mention in *entry_lower*, or None when no owner phrase sits that close. Never widened to a
    whole-entry search -- see the fabrication note on `_OWNER_PROXIMITY_CHARS` above."""
    rec_idx = entry_lower.find(rec_id)
    if rec_idx == -1:
        return None
    window_start = max(0, rec_idx - _OWNER_PROXIMITY_CHARS)
    window_end = rec_idx + len(rec_id) + _OWNER_PROXIMITY_CHARS
    owner_match = _OWNER_NAMED_RE.search(entry_lower, window_start, window_end)
    return owner_match.group().lower() if owner_match else None


def _scan_prior_deferrals(rec_ids: Iterable[str], plans_dir: Path) -> dict[str, dict]:
    """Scan docs/plans for a prior deferral rationale naming each of *rec_ids*.

    Mirrors the SORTED substring-prefilter-then-YAML-parse walk of
    scripts/platform_roadmap_state.py:101 (compute_followon_state) -- full-parsing the whole
    docs/plans corpus to read one optional field dominates runtime, so every plan whose text
    lacks a requested rec id (matched EXACTLY) or whose LOWER-CASED text lacks the substring
    "defer" is skipped before it is ever YAML-parsed.

    A plan counts toward a rec's `count` and `plan_slugs` at most once, and only when a SINGLE
    top-level `context` list entry contains BOTH the exact rec id and the case-insensitive
    substring "defer". `owner_named` is the lower-cased phrase matched by `_OWNER_NAMED_RE` within
    `_OWNER_PROXIMITY_CHARS` of the rec id's own mention in that same qualifying entry (never the
    whole entry -- see `_owner_named_near`), taken from the EARLIEST-SORTING counted plan that
    supplies one that close -- a counted plan naming no owner nearby is passed over, never treated
    as "owner_named: none" for the whole rec. The walk MUST iterate
    `sorted(plans_dir.glob("PLAN-*.yaml"))`, never a bare `.glob`, which yields filesystem order:
    against this repo's own corpus an unsorted walk answers "another agent"
    (PLAN-cfg-migration-closeout) where the sorted walk answers "operator-directed"
    (PLAN-ambient-prose-contract-relocation) -- sorting is the only reason the earliest-sorting
    rule is well defined.

    Returns a zeroed {count: 0, plan_slugs: [], owner_named: None} entry for every id in
    *rec_ids* up front, so a rec with no matching plan still round-trips a well-shaped payload.
    Returns immediately (all zeroed) when `plans_dir` is not a directory. Swallows a per-file
    OSError (unreadable) or yaml.YAMLError (malformed) and skips that file -- never raises.
    """
    result: dict[str, dict] = {rec_id: {"count": 0, "plan_slugs": [], "owner_named": None} for rec_id in rec_ids}
    if not result or not plans_dir.is_dir():
        return result

    for plan_file in sorted(plans_dir.glob("PLAN-*.yaml")):
        try:
            text = plan_file.read_text(encoding="utf-8")
        except OSError:
            continue
        if "defer" not in text.lower():
            continue
        candidate_ids = [rec_id for rec_id in result if rec_id in text]
        if not candidate_ids:
            continue
        try:
            plan_data = yaml.safe_load(text)
        except yaml.YAMLError:
            continue
        if not isinstance(plan_data, dict):
            continue
        context = plan_data.get("context")
        if not isinstance(context, list):
            continue
        slug = plan_file.stem[len("PLAN-") :]
        for rec_id in candidate_ids:
            for entry in context:
                if not isinstance(entry, str) or rec_id not in entry:
                    continue
                entry_lower = entry.lower()
                if "defer" not in entry_lower:
                    continue
                bucket = result[rec_id]
                if slug in bucket["plan_slugs"]:
                    break
                bucket["plan_slugs"].append(slug)
                bucket["count"] += 1
                if bucket["owner_named"] is None:
                    owner_named = _owner_named_near(entry_lower, rec_id)
                    if owner_named is not None:
                        bucket["owner_named"] = owner_named
                break
    return result


def annotate_prior_deferrals(correlation: dict[str, list[dict]], plans_dir: Path | None = None) -> dict[str, list[dict]]:
    """Attach `prior_deferrals` ({count, plan_slugs, owner_named}) to each UNRESOLVED rec dict.

    Short-circuits to a no-op (zero docs/plans reads) when `correlation["unresolved"]` is empty.
    `plans_dir=None` -- the sole production call shape, since scripts/session/preflight.py passes
    no directory -- resolves to `_common.ROOT / "docs" / "plans"`, an absolute, cwd-INDEPENDENT
    path identical to the one callers pass explicitly; `_common` is already imported by this
    module, so no new import is needed. Mutates and returns the SAME correlation dict in place:
    `correlate_recs_with_commits` (scripts/preflight/correlation.py:134 and :136, the append
    sites for the `rec = {**rec, ...}` sibling-cluster branch built at :129) appends the SAME
    dict objects to `unresolved` and to the full rec list, so this in-place annotation is also
    visible through `report["ci_rca_recs"]` -- additive and benign, not a copy. Degrades to a
    zeroed payload per rec on any scan error and never raises (the `_check_convergence_rca_gap`
    degrade posture in this module).
    """
    unresolved = correlation.get("unresolved") or []
    if not unresolved:
        return correlation

    resolved_dir = plans_dir if plans_dir is not None else _common.ROOT / "docs" / "plans"
    rec_ids = {rec.get("id", "") for rec in unresolved if isinstance(rec, dict) and rec.get("id")}
    try:
        deferrals = _scan_prior_deferrals(rec_ids, resolved_dir)
    except Exception:  # noqa: BLE001
        deferrals = {}

    for rec in unresolved:
        if not isinstance(rec, dict):
            continue
        rec_id = rec.get("id", "")
        rec["prior_deferrals"] = deferrals.get(rec_id) or {"count": 0, "plan_slugs": [], "owner_named": None}
    return correlation


def print_ci_rca_recs(recs: list[dict], correlation: dict[str, list[dict]] | None = None) -> None:
    """Print the CI RCA Recs section to terminal.

    When ``correlation`` is provided, recs are split into LIKELY-RESOLVED
    (soft verify+close prompt) and UNRESOLVED (HARD BLOCK retained).
    When ``correlation`` is None all recs are treated as UNRESOLVED (backward compat).
    """
    print("\n--- CI RCA Recs (open) ---")
    if not recs:
        print("  (none)")
        print()
        return

    if correlation is None:
        # Backward-compat path: all recs are HARD BLOCK.
        print(
            "  [HARD BLOCK] overlapping work blocks, and all new work once a red is past its SLO: run "
            "bin/venv-python -m scripts.ci_rca.red_overlap --plan <plan> (Decision 226)."
        )
        for rec in recs:
            rec_id = rec.get("id", "unknown")
            title = rec.get("title", "")
            priority = rec.get("priority", "")
            created = rec.get("created_timestamp", "")
            print(f"  {rec_id} [{priority}] {created}: {title}")
        print()
        return

    likely_resolved = correlation.get("likely_resolved") or []
    unresolved = correlation.get("unresolved") or []

    if likely_resolved:
        print(
            "  [SOFT -- LIKELY RESOLVED] A recent main commit appears to have fixed these recs. Verify and close before /plan:"
        )
        for rec in likely_resolved:
            rec_id = rec.get("id", "unknown")
            title = rec.get("title", "")
            priority = rec.get("priority", "")
            created = rec.get("created_timestamp", "")
            print(f"  {rec_id} [{priority}] {created}: {title}")
            print(
                f"    -> bin/venv-python -m scripts.ops_data_portal --update-rec {rec_id}"
                ' --status closed --resolution "Verified resolved by main commit"'
            )

    if unresolved:
        print(
            "  [HARD BLOCK] overlapping work blocks, and all new work once a red is past its SLO: run "
            "bin/venv-python -m scripts.ci_rca.red_overlap --plan <plan> (Decision 226)."
        )
        for rec in unresolved:
            rec_id = rec.get("id", "unknown")
            title = rec.get("title", "")
            priority = rec.get("priority", "")
            created = rec.get("created_timestamp", "")
            print(f"  {rec_id} [{priority}] {created}: {title}")
            deferral = rec.get("prior_deferrals")
            if deferral and deferral.get("count"):
                slugs = deferral.get("plan_slugs") or []
                shown = ", ".join(slugs[:5])
                if len(slugs) > 5:
                    shown += f", +{len(slugs) - 5} more"
                owner = deferral.get("owner_named") or "none"
                print(f"    deferred {deferral['count']} times (plans: {shown}; owner named: {owner})")

    print()
