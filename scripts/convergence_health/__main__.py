"""CLI entry point for the convergence-health sensor (called by convergence-health.yml).

Preserves `python -m scripts.convergence_health [--ducklake-drift|--prod-drift|--budget-ingest
[--dry-run]|--sensor-liveness|--liveness-probe [workflow]|--dispatch-integrity-probe [check]]`. Part of the
scripts.convergence_health package -- see scripts/convergence_health/__init__.py for the full
public surface.
"""

from __future__ import annotations

from typing import Optional

from scripts.convergence_health import dispatch_integrity
from scripts.convergence_health.approvals import (
    diagnose_stuck_approvals,
    find_reconcile_runs_since,
    find_stuck_gated_approvals,
    has_in_flight_reconcile_for_episode,
)
from scripts.convergence_health.assess import assess_health
from scripts.convergence_health.budget_ingest import ingest_budget_breaches
from scripts.convergence_health.code_drift import detect_ducklake_code_drift, detect_prod_code_drift
from scripts.convergence_health.escalate import escalate
from scripts.convergence_health.record import derive_red_since, read_convergence_record
from scripts.convergence_health.sensor_liveness import detect_stale_loops, stale_peers_for_probe


def main(profile: Optional[str] = None) -> int:
    """Assess convergence health and escalate if warranted. Returns exit code."""
    import json  # noqa: PLC0415
    import os  # noqa: PLC0415

    try:
        import boto3  # noqa: PLC0415

        session = boto3.Session(profile_name=profile)
        s3 = session.client("s3")
    except Exception as exc:  # noqa: BLE001
        print(f"[convergence_health] S3 client init failed: {exc}")
        return 1

    record = read_convergence_record(s3)
    diagnose_mode = bool(os.environ.get("CONVERGENCE_HEALTH_DIAGNOSE"))

    if diagnose_mode:
        diagnose_out = diagnose_stuck_approvals()
        verdict = assess_health(record, stuck_approvals=diagnose_out)
    else:
        stuck = find_stuck_gated_approvals()
        verdict = assess_health(record, stuck_approvals=stuck)

    print(
        f"[convergence_health] HealthVerdict: {
            json.dumps(
                {
                    'status': verdict.status,
                    'red_age_hours': verdict.red_age_hours,
                    'unapplied_backlog': verdict.unapplied_backlog,
                    'stuck_approvals': len(verdict.stuck_approvals),
                    'severity': verdict.severity,
                    'pending_gated': verdict.pending_gated,
                }
            )
        }"
    )

    if diagnose_mode:
        print(f"[convergence_health] diagnose_stuck_approvals: {diagnose_out}")
        return 0  # read-only; do not escalate

    reconcile_in_flight = False
    if record is not None and record.get("status") == "red":
        # T2.37 c4: only worth the extra API call when there is a red episode to potentially
        # double-file against.
        reconcile_runs = find_reconcile_runs_since()
        reconcile_in_flight = has_in_flight_reconcile_for_episode(reconcile_runs, red_since=derive_red_since(record))

    result = escalate(verdict, profile=profile, reconcile_in_flight=reconcile_in_flight)
    print(f"[convergence_health] escalation result: {result}")
    return 0


def main_ducklake_drift(profile: Optional[str] = None) -> int:
    """Run the DuckLake code-drift sensor (T2.38) and escalate if warranted. Returns exit code."""
    try:
        result = detect_ducklake_code_drift(profile=profile)
    except Exception as exc:  # noqa: BLE001
        print(f"[convergence_health] ducklake_code_drift failed: {exc}")
        return 1
    print(f"[convergence_health] ducklake_code_drift result: {result}")
    return 0


def main_prod_drift(profile: Optional[str] = None) -> int:
    """Run the prod-class code-drift sensor (T2.43) and escalate if warranted. Returns exit code."""
    try:
        result = detect_prod_code_drift(profile=profile)
    except Exception as exc:  # noqa: BLE001
        print(f"[convergence_health] prod_code_drift failed: {exc}")
        return 1
    print(f"[convergence_health] prod_code_drift result: {result}")
    return 0


def main_budget_ingest(profile: Optional[str] = None, dry_run: bool = False) -> int:
    """Ingest the CI fast-tier budget-block population (rec-3288). Returns exit code.

    Fails LOUDLY on a portal or GitHub-API failure: the exception is reported and the exit code
    is 1, so the workflow step goes red. Nothing is buffered for later delivery (Decision 84 I-4
    -- there is no outbox); the next hourly tick re-derives the population from the artifact
    window, which outlives the cron interval by two weeks of retention.
    """
    try:
        result = ingest_budget_breaches(profile=profile, dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001
        print(
            "[convergence_health] budget_ingest FAILED (nothing buffered -- Decision 84 I-4 has no "
            f"outbox; the next tick re-derives from the artifact window): {exc}"
        )
        return 1
    print(f"[convergence_health] budget_ingest result: {result}")
    return 0


def main_sensor_liveness(profile: Optional[str] = None) -> int:
    """Run the scheduled-loop liveness sensor (audit finding LSA-02) and file/update/drop
    loop_liveness_stale alarms. Returns exit code.

    Fails LOUDLY on a portal or GitHub-API failure, same shape as main_budget_ingest: nothing is
    buffered (Decision 84 I-4 -- there is no outbox), and the next hourly tick re-derives the
    peer set and every peer's staleness from the live workflow tree and the GitHub Actions API.
    """
    try:
        result = detect_stale_loops(profile=profile)
    except Exception as exc:  # noqa: BLE001
        print(
            "[convergence_health] sensor_liveness FAILED (nothing buffered -- Decision 84 I-4 has no "
            f"outbox; the next tick re-derives from the live workflow tree): {exc}"
        )
        failed = True
    else:
        failed = False
        print(f"[convergence_health] sensor_liveness result: {result}")
    try:
        integrity = dispatch_integrity.tick(profile=profile)
    except Exception as exc:  # noqa: BLE001
        print(f"[convergence_health] dispatch_integrity FAILED (Decision 84 I-4, nothing buffered): {exc}")
        return 1
    print(f"[convergence_health] dispatch_integrity result: {integrity}")
    return 1 if failed else 0


def main_liveness_probe(workflow: Optional[str] = None, leg: str = "cadence") -> int:
    """`--liveness-probe [workflow] [leg]` acceptance oracle. Returns exit code: 0 iff the named
    peer (or every derived peer, when `workflow` is omitted) is within its derived threshold on
    `leg` ("cadence", the default and every already-filed cadence rec's bare stored form, or
    "success"); 1 if any is stale, the named workflow is not a derived peer, or the GitHub query
    fails. This is the discriminating, repo-local, gh-free command every filed loop_liveness_stale
    rec's `acceptance` names -- never `gh`, which is deliberately not installed in either container.
    """
    try:
        stale = stale_peers_for_probe(workflow, leg=leg)
    except Exception as exc:  # noqa: BLE001
        print(f"[convergence_health] liveness_probe FAILED: {exc}")
        return 1
    if stale:
        print(f"[convergence_health] liveness_probe STALE ({leg}): {stale}")
        return 1
    print(f"[convergence_health] liveness_probe OK ({leg}): {workflow or 'all derived peers'}")
    return 0


def _dispatch(argv: list[str]) -> int:
    """Route a bare argv (flags only, no argparse) to the matching sensor entry point."""
    if "--ducklake-drift" in argv:
        return main_ducklake_drift()
    if "--prod-drift" in argv:
        return main_prod_drift()
    if "--budget-ingest" in argv:
        return main_budget_ingest(dry_run="--dry-run" in argv)
    if "--sensor-liveness" in argv:
        return main_sensor_liveness()
    if "--dispatch-integrity-probe" in argv:
        idx = argv.index("--dispatch-integrity-probe")
        rest = [a for a in argv[idx + 1 :] if not a.startswith("--")]
        return dispatch_integrity.probe(rest[0] if rest else None)
    if "--liveness-probe" in argv:
        idx = argv.index("--liveness-probe")
        # Bare positional tokens after the flag: an optional workflow name and an optional leg
        # selector, in either order. No real workflow filename is literally "cadence" or
        # "success", so the two are unambiguous; a bare "cadence" or omitted selector means the
        # cadence leg -- every already-filed cadence rec's stored acceptance carries no selector.
        tokens = [a for a in argv[idx + 1 :] if not a.startswith("--")]
        workflow: Optional[str] = None
        leg = "cadence"
        for token in tokens:
            if token in ("cadence", "success"):
                leg = token
            else:
                workflow = token
        return main_liveness_probe(workflow, leg=leg)
    return main()


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(_dispatch(sys.argv[1:]))
