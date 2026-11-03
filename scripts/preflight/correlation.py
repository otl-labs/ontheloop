# complexity-waiver: decision-43
"""Rec/commit correlation concern for session_preflight.

correlate_recs_with_commits carries the Decision 43 CC waiver (31 branches): a single
per-rec classification pass over commits with several independent short-circuit signals
(id-mention, file-path match, closed-sibling cluster) -- this is the same waiver the
pre-decomposition scripts/session/preflight.py carried at file top for this function.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from scripts.preflight import _common

_CI_TITLE_STOPWORDS: frozenset[str] = frozenset({"ci", "failure", "failed", "error", "lint", "test", "fix", "rca"})


def _title_jaccard(title_a: str, title_b: str) -> float:
    """Lowercased alphanumeric-token Jaccard between two titles, with common CI stopwords removed."""

    def _tokens(s: str) -> set[str]:
        return {t for t in re.findall(r"[a-z0-9]+", s.lower()) if t not in _CI_TITLE_STOPWORDS}

    set_a = _tokens(title_a)
    set_b = _tokens(title_b)
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / len(union)


def _file_paths_correlate(rec_file: str, changed: str) -> bool:
    """Return True when rec_file and changed refer to the same repository file.

    Matches on exact equality or a trailing path-component suffix match: split
    both on "/" and compare the last N components (N = min of the two lengths).
    A bare basename rec file therefore matches any changed path that ends with
    that basename, but a substring that crosses a component boundary does not.
    """
    if rec_file == changed:
        return True
    parts_rec = rec_file.split("/")
    parts_changed = changed.split("/")
    n = min(len(parts_rec), len(parts_changed))
    return parts_rec[-n:] == parts_changed[-n:]


def correlate_recs_with_commits(
    recs: list[dict],
    commits: list[dict],
    closed_recs: list[dict] | None = None,
) -> dict[str, list[dict]]:
    """Classify open recs as LIKELY-RESOLVED or UNRESOLVED from commit history.

    General engine for all rec sources (generalised from the ci_rca path, T3.8).
    Served from the already-warmed read-cache; never performs a warehouse re-fetch
    or acceptance-command execution (Decision 88).

    A rec is LIKELY-RESOLVED when any commit on origin/main whose date is
    AFTER the rec's created_timestamp either:
      - modified the rec's ``file`` field (path-suffix / basename match), or
      - mentions the rec's ``id`` in its subject line.

    When ``closed_recs`` is provided (cache path only), a rec that is
    still uncorrelated after the commit loop is also classified LIKELY-RESOLVED
    when a closed sibling on the same file has a sufficiently similar
    title and was closed at/after this rec's creation (window-independent
    closed-sibling cluster signal). The matched sibling id is recorded on the
    rec as ``_resolved_reason`` for the operator's verify-and-close prompt.

    Args:
        recs:         Open recs to classify.
        commits:      Recent main commits from _get_recent_main_commits().
        closed_recs:  Closed recs (cache path), or None to skip cluster detection.

    Returns:
        Dict with keys ``likely_resolved`` and ``unresolved``, each a list of recs.
    """
    if not recs:
        return {"likely_resolved": [], "unresolved": []}

    likely_resolved: list[dict] = []
    unresolved: list[dict] = []

    for rec in recs:
        rec_id = (rec.get("id") or "").lower()
        rec_file = (rec.get("file") or "").strip()
        rec_created_dt = _common._parse_ts_utc(rec.get("created_timestamp") or "")

        correlated = False
        for commit in commits:
            commit_dt = _common._parse_ts_utc(commit.get("date") or "")

            if rec_created_dt is not None and commit_dt is not None:
                if commit_dt <= rec_created_dt:
                    continue

            subject_lower = (commit.get("subject") or "").lower()
            if rec_id and rec_id in subject_lower:
                correlated = True
                break

            if rec_file:
                for changed in commit.get("files") or []:
                    if _file_paths_correlate(rec_file, changed):
                        correlated = True
                        break
            if correlated:
                break

        # Closed-sibling cluster: window-independent signal (Decision 88 invariant ii --
        # served from the already-pulled cache, never a fresh reader call).
        if not correlated and closed_recs and rec_file:
            for sibling in closed_recs:
                sib_file = (sibling.get("file") or "").strip()
                if not sib_file:
                    continue
                if not _file_paths_correlate(rec_file, sib_file):
                    continue
                if _title_jaccard(rec.get("title") or "", sibling.get("title") or "") < 0.5:
                    continue
                sib_closed_dt = _common._row_ts(sibling, "last_updated_timestamp")
                if sib_closed_dt is None:
                    continue
                if rec_created_dt is not None and sib_closed_dt < rec_created_dt:
                    continue
                rec = {**rec, "_resolved_reason": f"likely resolved by sibling {sibling.get('id', '')}"}
                correlated = True
                break

        if correlated:
            likely_resolved.append(rec)
        else:
            unresolved.append(rec)

    return {"likely_resolved": likely_resolved, "unresolved": unresolved}


def correlate_ci_rca_with_main(
    recs: list[dict],
    commits: list[dict],
    closed_ci_rca_recs: list[dict] | None = None,
) -> dict[str, list[dict]]:
    """Classify open ci_rca recs as LIKELY-RESOLVED or UNRESOLVED.

    Thin wrapper around correlate_recs_with_commits() for backward compatibility.
    See that function for signal documentation.

    Args:
        recs:              Open ci_rca recs from _fetch_ci_rca_recs().
        commits:           Recent main commits from _get_recent_main_commits().
        closed_ci_rca_recs: Closed ci_rca recs from _derive_ci_rca_closed() (cache path).

    Returns:
        Dict with keys ``likely_resolved`` and ``unresolved``, each a list of recs.
    """
    return correlate_recs_with_commits(recs, commits, closed_recs=closed_ci_rca_recs)


def surface_queue_relevance_triage(
    cache_rows: list[dict],
    commits: list[dict],
    *,
    exclude_sources: frozenset[str] = frozenset({"ci_rca"}),
    cap: int = 10,
) -> list[dict]:
    """Return queue-wide likely-resolved recs for operator triage (read-cache only, Decision 88).

    Runs cheap commit-file correlation on all open recs EXCEPT those in ``exclude_sources``
    (ci_rca has its own dedicated triage block). Never calls the warehouse reader and never
    executes acceptance probes -- surfacing-only per Decision 55.

    Args:
        cache_rows:      Caller-supplied recommendation rows (not a local read cache; Decision 215).
        commits:         Recent main commits already fetched by the caller.
        exclude_sources: Source tags that have their own triage block (default: ci_rca).
        cap:             Maximum recs returned.

    Returns:
        Likely-resolved recs (up to ``cap``), newest first.
    """
    open_non_ci = [r for r in cache_rows if r.get("status") == "open" and (r.get("source") or "") not in exclude_sources]
    closed_recs = [
        {
            "id": r.get("id", ""),
            "file": r.get("file", ""),
            "title": r.get("title", ""),
            "last_updated_timestamp": r.get("last_updated_timestamp"),
        }
        for r in cache_rows
        if r.get("status") == "closed" and (r.get("source") or "") not in exclude_sources
    ]
    result = correlate_recs_with_commits(open_non_ci, commits, closed_recs=closed_recs)
    likely = result.get("likely_resolved") or []
    likely.sort(key=lambda r: _common._row_ts(r) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return likely[:cap]
