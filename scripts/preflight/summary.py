"""Stdout presentation for session_preflight: the terse one-line summary plus the small set of
alert print blocks that render a computed alert dict to stderr.

Relocated out of scripts/session/preflight.py (PLAN-convergence-health-prod-drift-red) to create
SLOC headroom for the convergence-sensor-liveness wiring -- preflight.py was at 498 effective
lines against the hard 500 gate. Deliberately NOT folded into scripts/preflight/alerts.py: that
module's docstring scopes it to "Recurrence and bypass alert concern" and every one of its seven
functions is a pure derivation that prints nothing -- mixing presentation into it would blur that
boundary. This module holds ONLY presentation (prints); every alert dict it renders is computed
elsewhere (ci_rca_signals.py, alerts.py, context_docs.py) and passed in.
"""

from __future__ import annotations

import sys
from pathlib import Path


def print_convergence_rca_gap_alert(alert: dict | None) -> None:
    """Print the convergence-RCA-gap alert (see ci_rca_signals._check_convergence_rca_gap)."""
    if alert is not None:
        print(
            f"Convergence RCA gap alert: record red {alert['red_age_hours']}h "
            f"(commit {alert.get('commit_sha', '')[:8]}) with no matching ci_rca rec filed "
            "since the red episode began -- file one manually or dispatch ci-rca.yml.",
            file=sys.stderr,
        )


def print_convergence_sensor_liveness_alert(alert: dict | None) -> None:
    """Print the convergence-sensor-liveness alert (see
    ci_rca_signals._check_convergence_sensor_liveness). Distinct from
    print_convergence_rca_gap_alert -- this fires on the WORKFLOW's own conclusion, independent of
    the convergence record's colour, closing the blind spot where a dead sensor looks healthy
    because the record it never reached stays green."""
    if alert is not None:
        print(
            f"Convergence sensor liveness alert: latest convergence-health.yml run concluded "
            f"'{alert.get('conclusion', '')}' (not success) -- {alert.get('run_url', '')}. "
            "A dead sensor can look healthy if the convergence record stays green while only the "
            "workflow itself fails; investigate the run log.",
            file=sys.stderr,
        )


def print_ci_rca_liveness_alert(alert: dict | None) -> None:
    """Print the ci-rca-liveness alert (see ci_rca_signals._check_ci_rca_liveness).

    Fires when main CI has been red for over 30 minutes with no matching ci_rca rec filed --
    the counterpart to print_convergence_sensor_liveness_alert, but for ci-rca.yml's own
    liveness rather than convergence-health.yml's."""
    if alert is not None:
        print(
            f"CI-RCA liveness alert: main CI run {alert.get('run_url', '')} has been red for "
            f"{alert.get('elapsed_minutes', '')} minutes with no ci_rca rec filed -- "
            "dispatch ci-rca.yml or file one manually.",
            file=sys.stderr,
        )


def print_budget_bypass_alert(alert: dict | None) -> None:
    """Print the fast-tier budget-bypass alert (see alerts._check_budget_bypass_alert)."""
    if alert is not None:
        print(
            f"Budget bypass alert: {alert['count']} --ignore-budget invocations in the last 7 days."
            " Repeated bypass indicates fast-tier drift -- consider a planning session to revisit the budget.",
            file=sys.stderr,
        )


def print_dependabot_stranded_alert(alert: dict | None) -> None:
    """Print the stranded-dependabot-PR alert (see dependabot.check_stranded_prs).

    Silent on a None signal (gh unavailable -- the cache key already records UNKNOWN) and on a
    clean backlog; a saturated ecosystem is called out by name because it, not the individual
    stranded PR, is what stops Dependabot opening anything new."""
    if not alert:
        return
    stranded = alert.get("stranded") or []
    quota_saturated = alert.get("quota_saturated") or []
    if not stranded and not quota_saturated:
        return
    quota_note = (
        f" Quota saturated: {', '.join(quota_saturated)} -- Dependabot opens nothing new for those ecosystems."
        if quota_saturated
        else ""
    )
    print(
        f"Dependabot backlog alert: {len(stranded)} of {alert.get('open_total', 0)} open dependabot PR(s) "
        f"stranded (aged past the sweep threshold, or DIRTY/BLOCKED).{quota_note}"
        " Dispatch the dependabot-stranded sweep to clear them.",
        file=sys.stderr,
    )


def print_budget_breach_summary(summary: dict | None) -> None:
    """Print the fast-tier budget-breach summary (see alerts._check_budget_breach_summary)."""
    if summary is not None:
        dominant_breach_phase = max(summary["by_phase"], key=summary["by_phase"].get)
        print(
            f"Budget breach summary: {summary['count']} fast-tier budget breach(es) in the last "
            f"7 days (dominant phase: {dominant_breach_phase}).",
            file=sys.stderr,
        )


# The two fail-open reasons context_docs._check_endstate_drift returns when it could not compare
# the fingerprint at all. Both used to render as SILENCE, which a consumer reads as "clean".
_ENDSTATE_SKIPPED_REASONS = frozenset({"stamp_absent", "parse_error"})


def print_endstate_drift_advisory(endstate_drift: dict) -> None:
    """Print the Platform End-State fingerprint advisory (see context_docs._check_endstate_drift).

    Always names WHY. The stale line carries its reason and the ref it resolved, so the three
    previously byte-identical stale causes are three distinct lines; both fail-open branches print
    a SKIPPED line saying drift is UNKNOWN, not clean, instead of nothing; and a reason neither
    vocabulary names -- a legacy space-separated note from an older context_docs, or a value that
    is not even a string -- also prints a SKIPPED line, because silence is what a consumer reads
    as clean. SILENCE means IN-SYNC ALONE.

    The reason is resolved through str() BEFORE the _ENDSTATE_SKIPPED_REASONS membership test:
    membership on an unhashable value (a list- or dict-valued reason) raises TypeError, and this
    renderer runs on main()'s thread. Reads every field with .get so a partial dict cannot raise.
    """
    reason = str(endstate_drift.get("reason") or endstate_drift.get("note") or "unspecified")
    if endstate_drift.get("stale"):
        new_ids = endstate_drift.get("new_ids") or []
        ids_note = f" (new ids: {new_ids})" if new_ids else ""
        print(
            "Advisory: Platform End-State fingerprint stale -- roadmap has new tier_item IDs since "
            f"the stamp.{ids_note} reason={reason} stamp_ref={endstate_drift.get('stamp_ref') or 'none'}",
            file=sys.stderr,
        )
        return
    if reason == "ok":
        return
    if reason in _ENDSTATE_SKIPPED_REASONS:
        print(
            f"Advisory: Platform End-State drift check SKIPPED -- reason={reason}; the fingerprint "
            "could not be compared, so drift is UNKNOWN, not clean.",
            file=sys.stderr,
        )
        return
    print(
        f"Advisory: Platform End-State drift check SKIPPED -- unrecognized reason={reason!r}; drift is UNKNOWN, not clean.",
        file=sys.stderr,
    )


def _format_preflight_summary(report: dict, report_path: Path) -> str:
    """One-line summary for stdout. The full JSON is on disk -- duplicating it here
    forces every consuming agent to pay ~12-15k tokens for a payload already
    available via file read."""
    mf = report.get("main_freshness", {}) or {}
    behind = mf.get("commits_behind", "?")
    ahead = mf.get("commits_ahead", "?")
    recs_status = report.get("recs_read_status", "ok")
    recs_status_suffix = "" if recs_status == "ok" else f" [DEGRADED: recs_read_status={recs_status}]"
    unresolved_recs = report.get("ci_rca_unresolved_recs")
    if recs_status != "ok" or unresolved_recs is None:
        recs_summary = "open_recs=UNAVAILABLE non_automatable=UNAVAILABLE ci_rca=UNAVAILABLE"
    else:
        ci_rca_unresolved = len(unresolved_recs)
        ci_rca_likely = len(report.get("ci_rca_likely_resolved_recs") or [])
        if ci_rca_unresolved or ci_rca_likely:
            ci_rca_summary = f"ci_rca_unresolved={ci_rca_unresolved} ci_rca_likely_resolved={ci_rca_likely}"
        else:
            ci_rca_summary = "ci_rca=0"
        recs_summary = (
            f"open_recs={report.get('open_recommendations')} "
            f"non_automatable={report.get('non_automatable_recommendations')} "
            f"{ci_rca_summary} ci_rca_open_total={report.get('ci_rca_open_total')}"
        )
    convergence_rca_gap = report.get("convergence_rca_gap_alert")
    convergence_rca_gap_suffix = (
        f" convergence_rca_gap_alert=red_{convergence_rca_gap['red_age_hours']}h" if convergence_rca_gap else ""
    )
    sensor_liveness = report.get("convergence_sensor_liveness_alert")
    sensor_liveness_suffix = f" convergence_sensor_liveness_alert={sensor_liveness['conclusion']}" if sensor_liveness else ""
    return (
        f"Preflight OK -> {report_path}\n"
        f"  venv={report.get('venv_ok')} creds={report.get('creds_status')} "
        f"branch={report.get('branch')} main=({behind} behind, {ahead} ahead)\n"
        f"  {recs_summary}{recs_status_suffix}{convergence_rca_gap_suffix}{sensor_liveness_suffix}\n"
        f"  Read the report file for full constraint detail."
    )
