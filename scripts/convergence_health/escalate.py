"""Convergence-stale idempotent escalation (CD.35 Wave 6 / T2.35).

Provides an idempotent escalation path: files OR updates a single
tf_convergence_stale rec per red-episode via scripts.ops_data_portal
(file_rec / update_rec). Never writes the convergence record; never runs
terraform apply; never dispatches terraform-apply-sandbox. Part of the
scripts.convergence_health package -- see scripts/convergence_health/__init__.py
for the full public surface.

The file/update/close orchestration below is scripts.rec_episode.run_episode (rec-3291 /
rec-3563): this module used to bulk-fetch every open rec via the `open_recs` named verb and
filter the result on `source`/`status` -- two columns that verb never projects, so the filter
always matched nothing and every red episode re-filed a duplicate rec instead of updating the one
already open. run_episode's find_rec does a source-scoped structural read instead, and raises
rather than silently returning None if a row is ever missing a key this lookup filters on.
"""

from __future__ import annotations

from typing import Any, Optional

from scripts.convergence_health.assess import (
    RED_AGE_THRESHOLD_HOURS,
    STALE_GREEN_BACKLOG_THRESHOLD_HOURS,
    HealthVerdict,
)
from scripts.rec_episode import run_episode

# Three mutually-branched escalation conditions (T2.35 hardening). A stuck gated-apply approval
# takes priority in the title/context even when it co-occurs with a persistently-red record --
# it is the more directly actionable signal (approve/cancel the run). stale_green_backlog and
# persistently_red are mutually exclusive by construction (they require status=="green" and
# status=="red" respectively).
_TITLE_STUCK_APPROVAL = "Gated-apply approval stuck -- staleness escalation"
_TITLE_STALE_GREEN_BACKLOG = "Sandbox convergence green with stale unapplied backlog -- staleness escalation"
_TITLE_PERSISTENTLY_RED = "Sandbox convergence record persistently red -- staleness escalation"
# Decision 190: a FOURTH, orthogonal condition -- a benign code-behind-state delta the drift
# classifier measured (resource_changes with no resource_drift), tracked separately from the
# status-red/status-green conditions above since the marker leaves status at its PRIOR value.
_TITLE_PENDING_CODIFICATION = (
    "Sandbox convergence shows a pending-codification delta (state ahead of code, not drift) -- staleness escalation"
)

_RESOLUTION_STUCK_APPROVAL = "Gated-apply approval cleared (approved or cancelled); staleness episode resolved."
_RESOLUTION_STALE_GREEN_BACKLOG = "Unapplied terraform/personal/ backlog drained; staleness episode resolved."
_RESOLUTION_PERSISTENTLY_RED = "Convergence record returned to green; staleness episode resolved."
# Decision 55 anti-hiding: the durable closure stamp for a self-cleared pending_codification
# episode -- names WHICH of the two possible outcomes happened (code caught up, or the marker
# escalated past the bound), never a bare "cleared" that could hide the escalate branch's harm.
_RESOLUTION_PENDING_CODIFICATION = (
    "pending_codification marker cleared: either the code caught up (a plan_ec==0 self-clear) or "
    "the marker escalated to a genuine red past its bounded age; episode resolved either way."
)

# Per-condition lint-valid acceptance probes (Decision 103 [NOTE]: these are live AWS/gh probes
# because no repo-local command can express "the convergence record returned to green"). Each
# is a static shell command -- credentials-bearing but syntactically real, never prose.
_ACCEPTANCE_STUCK_APPROVAL = (
    "gh api 'repos/otl-labs/ontheloop/actions/workflows/terraform-apply-sandbox.yml/runs"
    "?status=waiting' --jq '.total_count' | grep -qx 0"
)
_ACCEPTANCE_STALE_GREEN_BACKLOG = (
    "aws s3 cp s3://agent-platform-data-lake/convergence/personal/sandbox.json - --profile agent_platform "
    '| grep -q "$(git rev-parse origin/main)"'
)
_ACCEPTANCE_PERSISTENTLY_RED = (
    "aws s3 cp s3://agent-platform-data-lake/convergence/personal/sandbox.json - --profile agent_platform "
    '| grep -q \'"status": "green"\''
)
_ACCEPTANCE_PENDING_CODIFICATION = (
    "aws s3 cp s3://agent-platform-data-lake/convergence/personal/sandbox.json - --profile agent_platform "
    "| grep -qv pending_codification"
)


def _condition_for_verdict(verdict: HealthVerdict) -> str:
    """Classify which of the FOUR escalation conditions this verdict represents.

    pending_codification is checked BEFORE stale_green_backlog/persistently_red: it is orthogonal
    to status (like pending_gated/infra_error, the marker leaves status at its PRIOR value), so it
    must not be shadowed by whichever status-keyed condition the record happens to carry.
    """
    if verdict.stuck_approvals:
        return "stuck_approval"
    if verdict.pending_codification:
        return "pending_codification"
    if verdict.status == "green" and verdict.unapplied_backlog > 0:
        return "stale_green_backlog"
    return "persistently_red"


def _condition_from_existing_rec(existing: dict[str, Any]) -> str:
    """Recover the escalation condition an open rec was filed for, from its title.

    Needed at close time: by definition every trigger has cleared (over_threshold is False),
    so the current verdict can no longer tell us which condition the rec was tracking.
    """
    title = existing.get("title", "")
    if title == _TITLE_STUCK_APPROVAL:
        return "stuck_approval"
    if title == _TITLE_STALE_GREEN_BACKLOG:
        return "stale_green_backlog"
    if title == _TITLE_PENDING_CODIFICATION:
        return "pending_codification"
    return "persistently_red"


def _build_context(verdict: HealthVerdict, condition: str) -> str:
    if condition == "stuck_approval":
        parts = [
            (
                f"{len(verdict.stuck_approvals)} terraform-apply-sandbox run(s) are waiting on "
                "the tf-gated-apply Environment approval (stuck > threshold), independent of the "
                f"convergence record's own status ({verdict.status})."
            )
        ]
        parts.append(
            "Resolve via: approve or cancel the pending gated-apply run in GitHub Actions -> "
            "Review pending deployments. This rec closes automatically on the next sensor "
            "tick once no stuck approvals remain."
        )
    elif condition == "stale_green_backlog":
        parts = [
            (
                f"The sandbox convergence record is green, but {verdict.unapplied_backlog} merged "
                "terraform/personal/ commit(s) have been pending application for "
                f"{verdict.record_age_hours:.1f} hours -- past the "
                f"{STALE_GREEN_BACKLOG_THRESHOLD_HOURS:.1f}h stale-green-backlog threshold."
            )
        ]
        parts.append(
            "Resolve via: run terraform-apply-sandbox workflow_dispatch (or land a "
            "terraform/personal/ change) to apply the pending backlog. This rec closes "
            "automatically on the next sensor tick once the backlog drains."
        )
    elif condition == "pending_codification":
        marker = verdict.pending_codification or {}
        parts = [
            (
                "The sandbox convergence record carries a pending_codification marker -- the drift "
                "classifier measured a state-vs-code delta with resource_changes but NO resource_drift "
                f"(status stays at its prior value, {verdict.status}; this is not out-of-band infra "
                f"drift). First observed: {marker.get('first_seen', 'unknown')}."
            )
        ]
        gated = verdict.pending_gated if verdict.status == "green" else None
        if isinstance(gated, dict) and gated:
            parts = [
                (
                    "The sandbox convergence record carries a pending_codification marker while a routed gated "
                    f"change awaits approval (commit {gated.get('commit_sha', 'unknown')}, run "
                    f"{gated.get('run_url', 'unknown')}); the delta is that unapproved gated change, not out-of-band "
                    f"infra drift. First observed: {marker.get('first_seen', 'unknown')}."
                ),
                (
                    "Resolve via: approve or reject the waiting tf-gated-apply run; left unapproved past the bounded "
                    "age the record turns red (unapproved gated change). This rec closes automatically on the next "
                    "sensor tick once the marker clears."
                ),
            ]
        else:
            parts.append(
                "Resolve via: land the codifying terraform/personal/ change (a plan_ec==0 cycle "
                "self-clears the marker), or wait for the scheduled drift check -- a marker left "
                "unresolved past its bounded age escalates to a genuine red automatically. This rec "
                "closes automatically on the next sensor tick once the marker clears."
            )
    else:
        parts = [
            f"The sandbox convergence record has been red for {verdict.red_age_hours:.1f} hours.",
        ]
        if verdict.unapplied_backlog:
            parts.append(
                f"{verdict.unapplied_backlog} merged terraform/personal/ commit(s) are pending "
                "application since the last green convergence commit."
            )
        parts.append(
            "Resolve via: (a) approve the pending gated-apply run in GitHub Actions, or "
            "(b) run terraform-apply-sandbox workflow_dispatch with acknowledge_red_commit "
            "naming the red commit SHA. This rec closes automatically on the next sensor "
            "tick once the convergence record returns to green."
        )
    return " ".join(parts)


def _build_rec_fields(verdict: HealthVerdict, condition: str) -> dict[str, Any]:
    title = {
        "stuck_approval": _TITLE_STUCK_APPROVAL,
        "stale_green_backlog": _TITLE_STALE_GREEN_BACKLOG,
        "pending_codification": _TITLE_PENDING_CODIFICATION,
    }.get(condition, _TITLE_PERSISTENTLY_RED)
    acceptance = {
        "stuck_approval": _ACCEPTANCE_STUCK_APPROVAL,
        "stale_green_backlog": _ACCEPTANCE_STALE_GREEN_BACKLOG,
        "pending_codification": _ACCEPTANCE_PENDING_CODIFICATION,
    }.get(condition, _ACCEPTANCE_PERSISTENTLY_RED)
    return {
        "title": title,
        "file": ".github/workflows/convergence-health.yml",
        "status": "open",
        "source": "tf_convergence_stale",
        "priority": "High",
        "effort": "S",
        "risk": "medium",
        "verification_tier": "V2",
        "context": _build_context(verdict, condition),
        "acceptance": acceptance,
    }


def escalate(
    verdict: HealthVerdict,
    portal_caller: Optional[Any] = None,
    open_recs: Optional[list[dict[str, Any]]] = None,
    threshold_hours: float = RED_AGE_THRESHOLD_HOURS,
    profile: Optional[str] = None,
    reconcile_in_flight: bool = False,
) -> dict[str, Any]:
    """Idempotent escalation: file/update/close exactly one tf_convergence_stale rec per episode.

    Args:
        verdict:       HealthVerdict from assess_health.
        portal_caller: Injected callable(action, fields) for testability. When None,
                       uses scripts.ops_data_portal.file_rec / update_rec directly.
        open_recs:     Pre-fetched rows (for testing) -- treated as if they were already the
                       result of the scoped tf_convergence_stale read. When None, fetches live
                       via current_state("ops_recommendations", row_filter="source = "
                       "'tf_convergence_stale'") (not the JSONL cache, and never a bulk fetch of
                       every open rec).
        threshold_hours: Red-age threshold triggering escalation.
        profile:       AWS profile for the reader / portal.
        reconcile_in_flight: T2.37 c4 -- True when a reconcile.yml Actions run has already
                       started (or completed) during the current red episode (see
                       has_in_flight_reconcile_for_episode). Suppresses ONLY a fresh "file"
                       action -- an already-open rec still updates/closes normally, since
                       refreshing an existing rec's context is not a double-file.

    Returns:
        {"action": "file"|"update"|"unchanged"|"close"|"none"|"skipped"|"skipped_suppressed",
         "rec_id": str|None}
    """
    stuck_approval_trigger = bool(verdict.stuck_approvals)
    red_age_trigger = verdict.status == "red" and verdict.red_age_hours >= threshold_hours
    # Decision 190: a RED record with a non-zero unapplied backlog trips over_threshold on IMPACT,
    # not only on age -- assess.py already raises severity for this shape, but Decision 154 point
    # 7 states severity "files nothing on its own" (escalate.py had zero severity references
    # before this). Without this trigger the 2026-09-13 incident's real harm (three applies
    # refused, 2.6h, zero recs filed) stays unfixed even after assess.py's severity fix.
    red_blocked_apply_backlog_trigger = verdict.status == "red" and verdict.unapplied_backlog > 0
    stale_green_backlog_trigger = (
        verdict.status == "green"
        and verdict.unapplied_backlog > 0
        and verdict.record_age_hours >= STALE_GREEN_BACKLOG_THRESHOLD_HOURS
    )
    # Decision 190: unconditional -- the marker's OWN bounded-age escalation lives in
    # scripts.ci.convergence_classify (the drift workflow's stdlib delegate), not here. This
    # ticket only tracks operator visibility of the episode while it is open.
    pending_codification_trigger = bool(verdict.pending_codification)
    over_threshold = (
        stuck_approval_trigger
        or red_age_trigger
        or red_blocked_apply_backlog_trigger
        or stale_green_backlog_trigger
        or pending_codification_trigger
    )

    def _build_fields() -> dict[str, Any]:
        condition = _condition_for_verdict(verdict)
        return _build_rec_fields(verdict, condition)

    def _build_update(existing: dict[str, Any]) -> dict[str, Any]:
        condition = _condition_for_verdict(verdict)
        return {"context": _build_context(verdict, condition)}

    def _build_close(existing: dict[str, Any]) -> dict[str, Any]:
        condition = _condition_from_existing_rec(existing)
        resolution = {
            "stuck_approval": _RESOLUTION_STUCK_APPROVAL,
            "stale_green_backlog": _RESOLUTION_STALE_GREEN_BACKLOG,
            "pending_codification": _RESOLUTION_PENDING_CODIFICATION,
        }.get(condition, _RESOLUTION_PERSISTENTLY_RED)
        return {"status": "closed", "resolution": resolution}

    return run_episode(
        source="tf_convergence_stale",
        over_threshold=over_threshold,
        build_fields=_build_fields,
        build_update=_build_update,
        build_close=_build_close,
        suppress_file=reconcile_in_flight,
        portal_caller=portal_caller,
        rows=open_recs,
        profile=profile,
    )
