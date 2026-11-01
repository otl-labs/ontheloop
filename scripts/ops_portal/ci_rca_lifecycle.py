"""source=ci_rca STATUS-AWARE CHAIN + LIFECYCLE (fingerprint v2, Decision 142).

Owner-concern: fingerprint-chain resolution, the regression-vs-drop ancestry classification, the
fix-linked / timestamp-based-inactivity / flake auto-close split, and the escape-attribution
helper. Decomposed out of ci_rca_runtime.py (Decision 128 SLOC budget) -- this module replaces the
rec-2644 close-then-recur revive path (its status-flip mutator and READ-ONLY recency finder, both
now REMOVED from ci_rca_runtime.py) with a chain model where a CLOSED head is NEVER mutated: a
recurrence against a closed head is either dropped (proven stale-code rerun via git ancestry) or
filed as a brand-new REGRESSION record, never a closed->open status flip (Decision 55, citing
Decision 103's closure-proof clause -- a closed rec carries a fixed_by_sha proof; reopening it
would discard that proof without evidence it no longer holds).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from scripts.ops_portal._common import ROOT
from scripts.ops_portal.ci_rca_schema import _PORTAL_OWNED_KEYS, derive_ci_rca_priority, preventive_deficiency
from scripts.ops_portal.closure_gate import changed_files, closure_stamps_applicable, confirmed_repair_track
from scripts.ops_portal.trailer_closure_gate import merge_leg_refuses

logger = logging.getLogger(__name__)

# Decision 155-shaped marker (mirrors decisions.py::_ORPHAN_GUARD_TRANSIENT_MARKER): distinct,
# greppable, and mirrored to $GITHUB_STEP_SUMMARY when set.
_ESCAPE_CLOSURE_REFUSAL_MARKER = "[REC-AUTOCLOSE] Decision 186 closure refusal"

# Merge-leg refusal markers (rec-3775 / rec-3901): two DISTINCT constants, never one shared with
# each other or with _ESCAPE_CLOSURE_REFUSAL_MARKER above, so a future shift to true merge
# commits (which would make EVERY autoclose refuse under the empty/None arm while the workflow
# stays green by design) is greppable apart from a normal plan-leg refusal.
_MERGE_LEG_PLAN_ONLY_REFUSAL_MARKER = "[REC-AUTOCLOSE] merge-leg refusal (plan-only diff)"
_MERGE_LEG_EMPTY_REFUSAL_MARKER = "[REC-AUTOCLOSE] merge-leg refusal (empty/unresolvable diff)"

# Decision 201 acceptance-verdict refusal marker: a THIRD, distinct constant, never shared with
# _ESCAPE_CLOSURE_REFUSAL_MARKER or either merge-leg marker above, formatted PER-REC and CARRYING
# THE REC ID (mirrors the shipped _ESCAPE_CLOSURE_REFUSAL_MARKER precedent) -- the corrected
# post-merge closeout fallback tests for a marker naming that rec id.
_ACCEPTANCE_VERDICT_REFUSAL_MARKER = "[REC-AUTOCLOSE] Decision 201 acceptance-verdict refusal"

# Fail-closed default: a legacy closed head with no fixed_by_sha (every rec closed before this
# change; manual closures) cannot run the ancestry check -- so it always classifies as a
# REGRESSION, never a silent drop (Decision 55).
_CLASSIFY_DROP = "drop"
_CLASSIFY_REGRESSION = "regression"

# Flake escalation: a chain reaching this length is a recurring flake, not a fresh incident.
_FLAKE_CHAIN_LENGTH = 3

# Timestamp-based inactivity-close bounds (purely deterministic; Decision 103 boundary).
_INACTIVITY_WINDOW_DAYS = 30
_INACTIVITY_MIN_AGE_DAYS = 14


@dataclass(frozen=True)
class ChainRecord:
    rec_id: str
    status: str
    fixed_by_sha: Optional[str]
    last_touched: str


def _all_ci_rca_rows(profile: Optional[str] = None) -> list[dict]:
    """Named-verb read (Decision 84 I-3 / Decision 88): every source=ci_rca row (any status),
    context_v2_json parsed into "_ctx". No ad-hoc warehouse re-fetch, no caller SQL beyond the
    single structural source='ci_rca' row_filter."""
    from src.common.ducklake_reader_client import make_reader  # noqa: PLC0415

    rows = make_reader(profile=profile).current_state("ops_recommendations", row_filter="source = 'ci_rca'") or []
    parsed: list[dict] = []
    for row in rows:
        ctx_raw = row.get("context_v2_json")
        ctx: dict = {}
        if ctx_raw:
            try:
                loaded = json.loads(ctx_raw) if isinstance(ctx_raw, str) else ctx_raw
                if isinstance(loaded, dict):
                    ctx = loaded
            except (json.JSONDecodeError, TypeError):
                ctx = {}
        parsed.append({**row, "_ctx": ctx})
    return parsed


def _rows_matching_fingerprint(fingerprint: str, profile: Optional[str] = None) -> list[dict]:
    """Every parsed source=ci_rca row whose context_v2_json.fingerprint matches."""
    return [row for row in _all_ci_rca_rows(profile=profile) if row["_ctx"].get("fingerprint") == fingerprint]


def list_open_ci_rca_recs(profile: Optional[str] = None) -> list[dict]:
    """Every OPEN source=ci_rca row (id, created_timestamp, parsed context_v2_json under "_ctx")
    -- the inactivity sweep's read surface. Named-verb read, same boundary as resolve_chain."""
    return [row for row in _all_ci_rca_rows(profile=profile) if row.get("status") == "open"]


def resolve_chain(fingerprint: str, profile: Optional[str] = None) -> list[ChainRecord]:
    """Every source=ci_rca record matching `fingerprint`, ordered NEWEST FIRST.

    Ordering key: last_updated_timestamp, falling back to created_timestamp -- mirrors the
    retired rec-2644 recency finder's ordering. An empty list means the fingerprint is
    genuinely novel (no chain exists yet).
    """
    rows = _rows_matching_fingerprint(fingerprint, profile=profile)
    records = [
        ChainRecord(
            rec_id=str(row.get("id")),
            status=str(row.get("status") or ""),
            fixed_by_sha=row["_ctx"].get("fixed_by_sha"),
            last_touched=str(row.get("last_updated_timestamp") or row.get("created_timestamp") or ""),
        )
        for row in rows
    ]
    records.sort(key=lambda r: r.last_touched, reverse=True)
    return records


def newest_open_in_chain(fingerprint: str, profile: Optional[str] = None) -> Optional[str]:
    """The chain's newest record's id IFF it is status=open, else None.

    Status-aware replacement for a scan-order-dependent "any open match" lookup: only the
    NEWEST record in a fingerprint's chain is ever eligible to absorb a recurrence."""
    chain = resolve_chain(fingerprint, profile=profile)
    if chain and chain[0].status == "open":
        return chain[0].rec_id
    return None


def closed_head_of_chain(fingerprint: str, profile: Optional[str] = None) -> Optional[ChainRecord]:
    """The chain's newest record IFF it is status=closed, else None."""
    chain = resolve_chain(fingerprint, profile=profile)
    if chain and chain[0].status == "closed":
        return chain[0]
    return None


def _is_ancestor(ancestor_sha: str, descendant_sha: str, cwd: Optional[str] = None) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor_sha, descendant_sha],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
    )
    return result.returncode == 0


def current_commit_sha(cwd: Optional[str] = None) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _commit_present(sha: str, cwd: Optional[str] = None) -> bool:
    probe = subprocess.run(
        ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
    )
    return probe.returncode == 0


def _ensure_ancestry_history(failing_sha: str, fixed_by_sha: str, cwd: Optional[str] = None) -> None:
    """Best-effort: deepen a shallow checkout so `git merge-base --is-ancestor` between
    `failing_sha` and `fixed_by_sha` can actually be resolved (code-level guard-fetch belt).

    A shallow CI checkout (e.g. ci-rca.yml's bounded fetch-depth) can be missing the connecting
    history between an old fix commit and a later failing commit entirely -- `git merge-base
    --is-ancestor` then cannot resolve one side and returns non-zero, which
    `classify_closed_head` reads as "not an ancestor" and misfiles a genuine stale-code rerun
    as a brand-new REGRESSION instead of dropping it. Purely best-effort and silent: if this
    deepen attempt doesn't fully resolve the object graph (offline sandbox, no `origin` remote,
    a fetch failure), `classify_closed_head`'s own fail-closed-to-regression default still
    applies exactly as before -- this never becomes a hard dependency of the classification.
    """
    if not failing_sha or not fixed_by_sha:
        return
    if _commit_present(failing_sha, cwd=cwd) and _commit_present(fixed_by_sha, cwd=cwd):
        return
    subprocess.run(
        ["git", "fetch", "--unshallow", "origin"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
    )


def classify_closed_head(failing_sha: str, head: ChainRecord, cwd: Optional[str] = None) -> str:
    """'drop' when `failing_sha` is an ancestor of head.fixed_by_sha (stale-code rerun of an
    already-fixed commit); 'regression' otherwise.

    FAILS CLOSED (Decision 55): a legacy closed head with NO fixed_by_sha (every rec closed
    before this change; any manual closure) cannot run the ancestry check at all -- `git
    merge-base --is-ancestor <x> <None>` is meaningless -- so it always classifies as a
    REGRESSION rather than silently dropping a possibly-real recurrence.
    """
    if not head.fixed_by_sha:
        return _CLASSIFY_REGRESSION
    if not failing_sha:
        return _CLASSIFY_REGRESSION
    _ensure_ancestry_history(failing_sha, head.fixed_by_sha, cwd=cwd)
    return _CLASSIFY_DROP if _is_ancestor(failing_sha, head.fixed_by_sha, cwd=cwd) else _CLASSIFY_REGRESSION


def file_regression_record(fields: dict, context_v2_json: dict, head_id: str) -> tuple[dict, dict]:
    """Mutate (fields, context_v2_json) so the CALLER's normal insert path files this as a
    REGRESSION of `head_id`, never a closed->open status flip: title gets a 'REGRESSION: '
    prefix, priority is forced to 'critical', and context_v2_json.regression_of is set.

    Returns the mutated (fields, context_v2_json) copies -- callers proceed to insert them via
    the ordinary file_rec() path (a brand-new rec row); this function performs no portal write
    itself.
    """
    new_fields = dict(fields)
    new_ctx = dict(context_v2_json)
    title = new_fields.get("title") or ""
    if not title.startswith("REGRESSION: "):
        new_fields["title"] = f"REGRESSION: {title}" if title else "REGRESSION"
    new_fields["priority"] = "Critical"
    new_ctx["regression_of"] = head_id
    return new_fields, new_ctx


def stamp_fixed_by_sha(rec_id: str, fixed_by_sha: str, profile: Optional[str] = None) -> None:
    """Record the fix commit into a (closed) rec's context_v2_json.fixed_by_sha via the portal.

    The importable helper rec-autoclose.yml calls at close time (Decision 142) --
    that SHA is what powers a future recurrence's regression-vs-drop ancestry check. Portal-only
    write (update_rec); never re-stages from a read cache.
    """
    from scripts.ops_data_portal import _fetch_rec_from_reader, update_rec  # noqa: PLC0415

    existing = _fetch_rec_from_reader(rec_id, profile=profile)
    if existing is None:
        raise RuntimeError(f"stamp_fixed_by_sha: {rec_id} does not exist in the current projection.")
    ctx_raw = existing.get("context_v2_json") or "{}"
    try:
        ctx = json.loads(ctx_raw) if isinstance(ctx_raw, str) else dict(ctx_raw or {})
    except (json.JSONDecodeError, TypeError):
        ctx = {}
    ctx["fixed_by_sha"] = fixed_by_sha
    confirmed = confirmed_repair_track(fixed_by_sha, ROOT)
    if confirmed is not None:
        ctx["repair_track_confirmed"] = confirmed
    update_rec(rec_id, {"context_v2_json": json.dumps(ctx)}, profile=profile)


def correct_rec_context(
    rec_id: str,
    context_updates: dict,
    row_updates: Optional[dict] = None,
    profile: Optional[str] = None,
) -> None:
    """Correct fields within an existing ci_rca rec's context_v2_json, in place, via the portal.

    Mirrors stamp_fixed_by_sha's _fetch_rec_from_reader -> mutate -> update_rec shape, but for
    correcting a factually-wrong narrative rather than stamping a fix commit. `context_updates` is
    shallow-merged into the FULL existing context (never applied as a partial column write --
    update_rec would otherwise replace context_v2_json wholesale, losing sibling keys like
    fingerprint/evidence_bundle_ref). Refuses to write unless the read blob already carries a
    fingerprint (a rec with no fingerprint predates CIRCA-03 dedup metadata and is not a safe
    target for this helper), and refuses when the merged context fails
    _validate_ci_rca_context_v2 -- both refusal paths raise before any write. `row_updates` carries
    row-level fields (e.g. `title`), which live outside context_v2_json and are content-gated by
    update_rec itself; they land in the SAME update_rec call as the corrected blob.
    """
    from scripts.ops_data_portal import _fetch_rec_from_reader, update_rec  # noqa: PLC0415
    from scripts.ops_portal.ci_rca_schema import _validate_ci_rca_context_v2  # noqa: PLC0415

    existing = _fetch_rec_from_reader(rec_id, profile=profile)
    if existing is None:
        raise RuntimeError(f"correct_rec_context: {rec_id} does not exist in the current projection.")
    ctx_raw = existing.get("context_v2_json") or "{}"
    try:
        ctx = json.loads(ctx_raw) if isinstance(ctx_raw, str) else dict(ctx_raw or {})
    except (json.JSONDecodeError, TypeError):
        ctx = {}
    if not ctx.get("fingerprint"):
        raise RuntimeError(f"correct_rec_context: {rec_id}'s context_v2_json carries no fingerprint -- refusing to write.")

    context_updates = {k: v for k, v in context_updates.items() if k not in _PORTAL_OWNED_KEYS}
    if context_updates.get("repair_track") == "test_defect" and ctx.get("repair_track") != "test_defect":
        raise RuntimeError(f"correct_rec_context: {rec_id} can only be re-tracked toward product_regression -- refusing.")
    merged = {**ctx, **context_updates}
    deficiencies = _validate_ci_rca_context_v2(merged) + preventive_deficiency(merged)
    if deficiencies:
        raise RuntimeError(f"correct_rec_context: merged context_v2_json for {rec_id} fails validation: {deficiencies}")

    updates: dict = {"context_v2_json": json.dumps(merged)}
    if row_updates:
        updates.update(row_updates)
    updates["priority"] = derive_ci_rca_priority(merged)
    update_rec(rec_id, updates, profile=profile)


def check_flake_escalation(fingerprint: str, profile: Optional[str] = None) -> bool:
    """True when the fingerprint's chain has reached (or exceeds) the flake-escalation length --
    the caller should tag flaky + quarantine instead of filing another fresh critical."""
    return len(resolve_chain(fingerprint, profile=profile)) >= _FLAKE_CHAIN_LENGTH


def is_inactive(
    context_v2_json: dict,
    created_timestamp: str,
    *,
    inactivity_window_days: int = _INACTIVITY_WINDOW_DAYS,
    min_age_days: int = _INACTIVITY_MIN_AGE_DAYS,
    now: Optional[datetime] = None,
) -> bool:
    """Purely TIMESTAMP-based deterministic inactivity predicate (Decision 103 boundary: a
    recorded proof licenses a direct close, no run-history data source needed).

    True iff BOTH: (a) last_seen (falling back to created_timestamp) is older than
    `inactivity_window_days`, AND (b) the rec's created-age is >= `min_age_days`. Both bounds
    must hold -- a rec that is merely old-since-last-seen but was only just created stays open
    (the age floor guards against closing something that never had a chance to recur yet).
    """
    reference = now if now is not None else datetime.now(timezone.utc)
    last_seen_raw = context_v2_json.get("last_seen") or created_timestamp
    last_seen = _parse_ts(last_seen_raw)
    created = _parse_ts(created_timestamp)
    if last_seen is None or created is None:
        return False
    inactive_enough = (reference - last_seen) > timedelta(days=inactivity_window_days)
    old_enough = (reference - created) >= timedelta(days=min_age_days)
    return inactive_enough and old_enough


def _parse_ts(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# --- escape-attribution (Decision 135 selection-manifest diff) -----------------------------

_ESCAPE_NO_EDGE = "no-edge"
_ESCAPE_CAPPED = "capped"
_ESCAPE_UNKNOWN_DATA_EDGE = "unknown-data-edge"


def _nodeid_test_file(nodeid: str) -> str:
    return nodeid.split("::", 1)[0]


def compute_escape_class(failed_nodeid: str, selection_manifest: dict) -> str:
    """Tag a post-merge full-tier failure with WHY it escaped the --pre affected-set selection
    (Decision 135's selection-manifest, diffed against the failing nodeid's test file):

    - 'capped': the test file was identified as affected but DEFERRED over the cap (present in
      manifest['deferred']) -- --pre knew about it but ran out of budget.
    - 'no-edge': the test file was never selected at all (absent from manifest['selected']) --
      no reverse-dependency edge connected it to the diff, so --pre had no way to know.
    - 'unknown-data-edge': the test file WAS selected (and should have run under --pre) but
      still failed post-merge -- an escape --pre's channels could not explain (e.g. a data-edge
      dependency the precise quoted-token match did not catch, or a genuine environment
      difference); flagged for manual review rather than silently assumed benign.
    """
    test_file = _nodeid_test_file(failed_nodeid)
    deferred = set(selection_manifest.get("deferred", []))
    if test_file in deferred:
        return _ESCAPE_CAPPED
    selected = set(selection_manifest.get("selected", []))
    if test_file not in selected:
        return _ESCAPE_NO_EDGE
    return _ESCAPE_UNKNOWN_DATA_EDGE


def _mirror_refusal_to_step_summary(heading: str, marker: str) -> None:
    """Best-effort mirror to $GITHUB_STEP_SUMMARY (Decision 155 shape) -- never raises; a CI-only
    convenience, absent in a local run where the env var is unset. `heading` is parametrised
    (never hardcoded) so a caller's refusal shape is never mislabelled as another caller's."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as f:  # noqa: PTH123 -- mirrors decisions.py:183's precedent
        f.write(f"\n## {heading}\n\n{marker}\n")


def close_recs_from_trailer(
    ids: list[str],
    commit_sha: str,
    run_url: str,
    recs_cache: dict[str, dict],
    profile: Optional[str] = None,
    *,
    acceptance_verdicts: Optional[dict[str, dict]] = None,
    require_acceptance_verdict: bool = False,
) -> int:
    """Close every rec named in `ids` via the ops portal -- the importable helper ci.yml's
    trailer-closure job delegates its loop to (the stamp_fixed_by_sha / Decision 142 precedent),
    so a Decision 186 refusal cannot redden this ci_rca:watched workflow and the workflow body
    stays under its ratchet ceiling.

    Decision 201/163: when `require_acceptance_verdict` is True (what ci.yml's trailer-closure
    job passes), a rec_id with no entry in `acceptance_verdicts` is NEVER passed to update_rec at
    all -- it is left OPEN with the same loud-not-red marker treatment as an
    AcceptanceVerdictRefused refusal, so the trailer path can never fail open on an absent
    verdict. The default False preserves every existing caller's behaviour byte-for-byte. The
    closing sha handed to update_rec's gate is this function's own `commit_sha` positional,
    passed through unconditionally and independently of the closure_fix_sha stamp path.

    Idempotent: a rec already closed_by the cache is skipped. Passes closure_fix_sha=commit_sha
    into the closing update_rec call, CONDITIONALLY on closure_stamps_applicable over the cached
    row's context_v2_json cell, so the fix-commit binding is satisfiable on the automated route in
    ONE write when applicable, and simply omitted (never threaded) when it is not -- a rec with no
    stampable blob (the common case: 1147 of 1152 open recs carry no context_v2_json) closes
    cleanly instead of hitting update_rec's closure-stamp precondition. A cache miss (rec_id absent
    from recs_cache) is treated the same as an inapplicable blob: the stamp is omitted, and a later
    refusal for a genuinely absent rec is loud-not-red (Decision 186 pt 7), not silently dropped.
    Catches ClosureArtifactRequired (Decision 186): prints _ESCAPE_CLOSURE_REFUSAL_MARKER (mirrored
    to $GITHUB_STEP_SUMMARY when set), skips that rec (left OPEN), and does NOT touch exit_code --
    the refusal exits this loop iteration the same way an already-closed rec does, never as an
    error. RecNotFound (an absent rec) is the ONLY RuntimeError treated as a skip; every other
    RuntimeError (reader-unreachable, a writer-transport failure) falls through to the broad except
    below and sets exit_code=1 -- never discriminated by message substring. The existing post-close
    stamp_fixed_by_sha call is kept for non-escape ci_rca recs and is idempotent where both run.

    MERGE-LEG GATE (rec-3775 / rec-3901): before the per-rec loop, resolves the merge commit's
    changed-file set (closure_gate.changed_files) once and consults merge_leg_refuses over it. If
    the gate refuses -- the diff is None/unresolvable, empty (the true-merge-commit shape), or
    confined to docs/plans/ -- every id in `ids` is left OPEN, a Decision-155-shaped marker is
    printed (mirrored to $GITHUB_STEP_SUMMARY when set) naming which arm fired, and this function
    returns 0 WITHOUT touching exit_code or attempting any write -- rec-autoclose stays green
    (Decision 186 pt 7's loud-not-red posture) for a refusal that is correct behaviour, not a
    failure.

    Returns the exit code sys.exit() should use (0 normally; 1 iff a non-gate exception occurred
    while closing any rec).
    """
    from scripts.ops_data_portal import (  # noqa: PLC0415
        AcceptanceVerdictRefused,
        ClosureArtifactRequired,
        RecNotFound,
        update_rec,
    )

    changed = changed_files(commit_sha, ROOT)
    if merge_leg_refuses(changed):
        heading = _MERGE_LEG_EMPTY_REFUSAL_MARKER if not changed else _MERGE_LEG_PLAN_ONLY_REFUSAL_MARKER
        shape = "empty or unresolvable" if not changed else "confined to docs/plans/"
        marker = f"{heading}: {', '.join(ids)} left OPEN -- merge diff is {shape}, commit {commit_sha}"
        print(marker)
        _mirror_refusal_to_step_summary(heading, marker)
        return 0

    resolution = f"Auto-closed by rec-autoclose workflow: merge commit {commit_sha} (run: {run_url})"
    exit_code = 0

    verdicts = acceptance_verdicts or {}

    for rec_id in ids:
        existing = recs_cache.get(rec_id)
        if existing and existing.get("status") == "closed":
            print(f"rec-autoclose: {rec_id} already closed -- skipping (idempotent)")
            continue
        if require_acceptance_verdict and rec_id not in verdicts:
            marker = f"{_ACCEPTANCE_VERDICT_REFUSAL_MARKER}: {rec_id} left OPEN -- no acceptance verdict"
            print(marker)
            _mirror_refusal_to_step_summary(_ACCEPTANCE_VERDICT_REFUSAL_MARKER, marker)
            continue
        stamp_kwargs: dict = {}
        if closure_stamps_applicable(existing.get("context_v2_json") if existing else None):
            stamp_kwargs["closure_fix_sha"] = commit_sha
        try:
            update_rec(
                rec_id,
                {"status": "closed", "resolution": resolution},
                profile=profile,
                acceptance_verdict=verdicts.get(rec_id),
                closing_sha=commit_sha,
                **stamp_kwargs,
            )
            print(f"rec-autoclose: closed {rec_id}")
        except ClosureArtifactRequired as exc:
            marker = f"{_ESCAPE_CLOSURE_REFUSAL_MARKER}: {rec_id} left OPEN -- {exc}"
            print(marker)
            _mirror_refusal_to_step_summary(_ESCAPE_CLOSURE_REFUSAL_MARKER, marker)
            continue
        except AcceptanceVerdictRefused as exc:
            marker = f"{_ACCEPTANCE_VERDICT_REFUSAL_MARKER}: {rec_id} left OPEN -- {exc}"
            print(marker)
            _mirror_refusal_to_step_summary(_ACCEPTANCE_VERDICT_REFUSAL_MARKER, marker)
            continue
        except RecNotFound as exc:
            print(f"rec-autoclose: WARN {rec_id} not found in portal ({exc}) -- skipping")
            continue
        except Exception as exc:  # noqa: BLE001 -- deliberately broad: any non-gate failure still exits 1
            print(f"rec-autoclose: ERROR closing {rec_id}: {exc}", file=sys.stderr)
            exit_code = 1
            continue

        # ci-rca-identity-lifecycle: record the fix commit for the ancestry check, as before.
        # No-op for non-ci_rca recs; idempotent alongside closure_fix_sha for ci_rca ones.
        if existing and existing.get("source") == "ci_rca":
            try:
                stamp_fixed_by_sha(rec_id, commit_sha, profile=profile)
                print(f"rec-autoclose: stamped fixed_by_sha={commit_sha} on {rec_id}")
            except Exception as exc:  # noqa: BLE001 -- best-effort, mirrors the prior inline behaviour
                print(f"rec-autoclose: WARN could not stamp fixed_by_sha on {rec_id}: {exc}", file=sys.stderr)

    return exit_code
