# complexity-waiver: decision-43
"""Argparse CLI for the ops data portal.

Owner-concern: the argparse surface only. Cross-module verb calls use FUNCTION-LOCAL
imports from scripts.ops_data_portal (the facade) INSIDE main() -- a module-level import
here would bind at load time and defeat facade patches for CLI-driven tests.

Write-fidelity contract (Decision 66/163, ops-portal-write-fidelity): an advertised flag
either reaches the write it names or the CLI loud-fails before any write boundary is
called -- it is never silently accepted and dropped. The sole exception is the
formula-derived field group (risk on --file-rec), which is accepted advisorily because
_derive_computed_fields always overwrites it (Decision 66 Tier B places its value under
the portal's control, not the caller's). --dry-run is honoured only for
--purge-postmortems-for and --repair-dependency-tokens, the two actions that implement it,
and loud-fails for every other action. --update-rec threads every genuinely mutable field
via a module-level dest-to-field map (_UPDATE_REC_FIELD_MAP) and loud-fails the flags that
are not updatable via that path (_UPDATE_REC_REJECTIONS below); Decision 186's four
closure_* flags thread into update_rec's own keyword parameters instead, via a THIRD table
(_UPDATE_REC_KWARG_MAP) -- so every dest registered on the two rec argument groups is
classified by exactly one of the three tables.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

import yaml
from pydantic import ValidationError

from scripts.telemetry_capture.status_line import emit_status

# --update-rec fields that thread straight into the update_rec() payload. Keys are
# argparse dests; values are the corresponding update_rec() field names.
_UPDATE_REC_FIELD_MAP: dict[str, str] = {
    "status": "status",
    "resolution": "resolution",
    "execution_result": "execution_result",
    "execution_date": "execution_date",
    "execution_branch": "execution_branch",
    "execution_pr_url": "execution_pr_url",
    "rec_context": "context",
    "title": "title",
    "acceptance": "acceptance",
    "priority": "priority",
    "tags": "tags",
    "dependencies": "dependencies",
    "verification": "verification",
    "verification_tier": "verification_tier",
}

# --update-rec flags that thread straight into update_rec()'s own keyword-only parameters
# (Decision 186) rather than into the merged rec fields _UPDATE_REC_FIELD_MAP targets. Keys are
# argparse dests; values are the corresponding update_rec() keyword parameter names -- the THIRD
# classification table the write-fidelity contract's docstring paragraph above names.
_UPDATE_REC_KWARG_MAP: dict[str, str] = {
    "closure_artifact": "closure_artifact",
    "closure_waiver_category": "closure_waiver_category",
    "closure_waiver_reason": "closure_waiver_reason",
    "closure_fix_sha": "closure_fix_sha",
}

_DERIVED_FIELD_REASON = (
    "not updatable via the CLI pending re-derivation (config/agent/data_quality/ops.yaml: "
    "derived, never agent-set -- see follow-on rec-3047)"
)

# --update-rec flags that must loud-fail rather than silently thread or drop. Keys are
# argparse dests; values are (flag name for the error message, rejection reason).
_UPDATE_REC_REJECTIONS: dict[str, tuple[str, str]] = {
    "target_file": ("--file", _DERIVED_FIELD_REASON),
    "effort": ("--effort", _DERIVED_FIELD_REASON),
    "risk": ("--risk", _DERIVED_FIELD_REASON),
    "source": ("--source", "not updatable via the CLI -- filing provenance, immutable once the rec is filed"),
    "context_v2_json": (
        "--context-v2-json",
        "not updatable via the CLI -- update_rec has no context_v2_json parameter",
    ),
}


def _build_parser() -> argparse.ArgumentParser:
    """Construct the argparse parser. Behaviour-preserving extraction from main()."""
    from scripts.ops_data_portal import _CI_RCA_BACK_VALIDATE_DEFAULT_SINCE  # noqa: PLC0415

    parser = argparse.ArgumentParser(
        description="Unified gateway for filing and updating recommendations and decisions.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--profile", metavar="AWS_PROFILE", default=None, help="AWS profile override")
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview purge without writing (use with --purge-postmortems-for)"
    )

    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--file-rec", action="store_true", help="File a new recommendation")
    action.add_argument("--update-rec", metavar="REC_ID", help="Update an existing recommendation")
    action.add_argument("--file-decision", action="store_true", help="File a new decision")
    action.add_argument(
        "--update-decision", metavar="DECISION_ID", type=str, help="Update an existing decision (e.g. dec-072)"
    )
    action.add_argument(
        "--purge-postmortems-for", metavar="REC_ID", help="Supersede all executor postmortems for REC_ID (SCD2)"
    )
    action.add_argument(
        "--repair-dependency-tokens",
        action="store_true",
        help="Strip bracket-residue from malformed ops_recommendations.dependencies tokens (one-shot backfill)",
    )
    action.add_argument(
        "--backfill-decisions-md",
        action="store_true",
        help="ETL DECISIONS.md -> ops_decisions on DuckLake (idempotent caller-keyed upsert)",
    )
    action.add_argument(
        "--enqueue-findings",
        metavar="PATH",
        help="Bulk-enqueue findings from a JSONL file into ops_recommendations",
    )
    action.add_argument(
        "--guidance",
        action="store_true",
        help="Print field semantics and registered source values as YAML, then exit",
    )
    action.add_argument(
        "--sync",
        action="store_true",
        help="Refresh the local read-cache from the DuckLake reader",
    )
    action.add_argument(
        "--selftest-read",
        action="store_true",
        help="Read a sample row via the active backend's reader (rollback rehearsal, VP14)",
    )
    action.add_argument(
        "--selftest-roundtrip",
        action="store_true",
        help="Write+read a throwaway test- rec via the active backend (cutover sign-off, VP15)",
    )
    action.add_argument(
        "--back-validate",
        action="store_true",
        help="Re-validate warn-period source=ci_rca recs against the current strict-mode schema (T1.13 c2 enabler)",
    )
    action.add_argument(
        "--find-open-ci-rca-rec",
        action="store_true",
        help="Read-only: print the id of an OPEN source=ci_rca rec matching --fingerprint, or nothing (CIRCA-03(b))",
    )
    action.add_argument(
        "--bump-ci-rca-occurrence",
        metavar="REC_ID",
        help="Bump occurrence_count/last_seen on an existing source=ci_rca rec's context_v2_json (CIRCA-03(b))",
    )

    # --find-open-ci-rca-rec fields
    fp_group = parser.add_argument_group("--find-open-ci-rca-rec fields")
    fp_group.add_argument("--fingerprint", help="sha256 hex fingerprint to match against open source=ci_rca recs")

    # --back-validate fields
    bv = parser.add_argument_group("--back-validate fields")
    bv.add_argument(
        "--since",
        default=_CI_RCA_BACK_VALIDATE_DEFAULT_SINCE,
        help="ISO date/timestamp floor on created_timestamp (inclusive); default is the Phase-1 landing date",
    )
    bv.add_argument(
        "--refile-audit",
        action="store_true",
        default=False,
        help="File non_conformant recs as source=ci_rca_warn_period_audit (capped per invocation); default is report-only",
    )
    bv.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        default=False,
        help="Print the back-validation report as machine-readable JSON instead of text",
    )

    # file-rec fields
    rec = parser.add_argument_group("--file-rec fields")
    rec.add_argument("--title")
    rec.add_argument("--file", dest="target_file")
    rec.add_argument("--context", dest="rec_context", help="Context text; also applies to --update-rec")
    rec.add_argument("--acceptance")
    rec.add_argument("--effort", choices=["XS", "S", "M", "L", "XL"])
    rec.add_argument("--priority", choices=["Critical", "High", "Medium", "Low"])
    rec.add_argument("--source")
    rec.add_argument("--risk", choices=["low", "medium", "high"])
    rec.add_argument("--tags", nargs="*", default=None)
    rec.add_argument("--dependencies", nargs="*", default=None)
    rec.add_argument("--verification")
    rec.add_argument("--verification-tier", choices=["V1", "V2", "V3"], dest="verification_tier")
    rec.add_argument(
        "--context-v2-json",
        dest="context_v2_json",
        default=None,
        help="JSON-encoded structured context dict: CiRcaContext for source=ci_rca recs,"
        " or CiRcaEvidenceDispute for source=ci_rca_evidence_dispute recs",
    )

    # update-rec fields
    upd = parser.add_argument_group("--update-rec fields")
    upd.add_argument("--status", choices=["open", "closed", "failed", "declined", "superseded"])
    upd.add_argument("--execution_result", choices=["success", "failure", "manual", "already_implemented"])
    upd.add_argument("--execution_date")
    upd.add_argument("--execution_branch")
    upd.add_argument("--execution_pr_url")
    upd.add_argument("--resolution")
    upd.add_argument(
        "--closure-artifact",
        help="Decision 186: <kind>:<ref> closure artifact token (shard:/pytest:/check:/fixture:)",
    )
    upd.add_argument(
        "--closure-waiver-category",
        help="Decision 186: closure waiver category (one of scripts.ops_portal.closure_gate.WAIVER_CATEGORIES)",
    )
    upd.add_argument("--closure-waiver-reason", help="Decision 186: free-text proof for --closure-waiver-category")
    upd.add_argument("--closure-fix-sha", help="Decision 186: fix-commit sha (stamps the existing fixed_by_sha key)")

    # file-decision fields
    dec = parser.add_argument_group("--file-decision fields")
    dec.add_argument("--rationale")
    dec.add_argument("--decision-status", choices=["open", "closed", "superseded"], dest="decision_status")
    dec.add_argument(
        "--decision-id",
        type=int,
        dest="decision_arg_id",
        help="DECISIONS.md-assigned integer number (numbering authority is DECISIONS.md, Decision 84)",
    )

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entrypoint for the ops data portal."""
    from scripts.ops_data_portal import (
        _print_ci_rca_back_validation_report,
        back_validate_ci_rca,
        backfill_decisions_from_md,
        bump_ci_rca_occurrence,
        enqueue_findings,
        file_decision,
        file_rec_with_outcome,
        find_open_ci_rca_rec_by_fingerprint,
        purge_postmortems_for,
        repair_dependency_tokens,
        selftest_read,
        selftest_roundtrip,
        sync,
        update_decision,
        update_rec,
    )

    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.dry_run and not (args.purge_postmortems_for or args.repair_dependency_tokens):
        print(
            "ERROR: --dry-run is supported only with --purge-postmortems-for and "
            "--repair-dependency-tokens; every other action writes for real",
            file=sys.stderr,
        )
        return 1

    if args.file_rec:
        required = ["title", "target_file", "rec_context", "acceptance", "effort", "priority", "source"]
        missing = [r for r in required if not getattr(args, r, None)]
        if missing:
            print(f"ERROR: --file-rec requires: {', '.join(missing)}", file=sys.stderr)
            emit_status("ops_portal.file_rec", "failed", "missing_required")
            return 1
        fields: dict = {
            "title": args.title,
            "file": args.target_file,
            "context": args.rec_context,
            "acceptance": args.acceptance,
            "effort": args.effort,
            "priority": args.priority,
            "source": args.source,
            "status": "open",
        }
        if args.risk is not None:
            fields["risk"] = args.risk
        if args.tags is not None:
            fields["tags"] = args.tags
        if args.dependencies is not None:
            fields["dependencies"] = args.dependencies
        if args.verification:
            fields["verification"] = args.verification
        if args.verification_tier:
            fields["verification_tier"] = args.verification_tier
        context_v2_parsed: dict | None = None
        if args.context_v2_json is not None:
            try:
                context_v2_parsed = json.loads(args.context_v2_json)
            except json.JSONDecodeError as exc:
                print(f"ERROR: --context-v2-json is not valid JSON: {exc}", file=sys.stderr)
                emit_status("ops_portal.file_rec", "failed", "invalid_context_v2_json")
                return 1
        try:
            outcome = file_rec_with_outcome(fields, context_v2_json=context_v2_parsed, profile=args.profile)
        except (ValidationError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            emit_status("ops_portal.file_rec", "failed", "invalid_fields")
            return 1
        except Exception:
            emit_status("ops_portal.file_rec", "failed", "writer_error")
            raise
        print(outcome.rec_id)
        if outcome.inserted:
            emit_status("ops_portal.file_rec", "done")
        elif outcome.reason == "ci_rca_open_duplicate":
            emit_status("ops_portal.file_rec", "skipped", "ci_rca_open_duplicate")
        elif outcome.reason == "ci_rca_stale_rerun":
            emit_status("ops_portal.file_rec", "skipped", "ci_rca_stale_rerun")
        else:
            emit_status("ops_portal.file_rec", "failed", "unknown_outcome")
        return 0

    if args.find_open_ci_rca_rec:
        if not args.fingerprint:
            print("ERROR: --find-open-ci-rca-rec requires --fingerprint", file=sys.stderr)
            return 1
        found_rec_id = find_open_ci_rca_rec_by_fingerprint(args.fingerprint, profile=args.profile)
        if found_rec_id:
            print(found_rec_id)
        return 0

    if args.bump_ci_rca_occurrence:
        try:
            new_count = bump_ci_rca_occurrence(args.bump_ci_rca_occurrence, profile=args.profile)
            print(f"Bumped {args.bump_ci_rca_occurrence} occurrence_count={new_count}")
            return 0
        except (ValidationError, ValueError, RuntimeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    if args.update_rec:
        rejected_flags = [
            (flag, reason) for dest, (flag, reason) in _UPDATE_REC_REJECTIONS.items() if getattr(args, dest, None) is not None
        ]
        if rejected_flags:
            for flag, reason in rejected_flags:
                print(f"ERROR: {flag} is {reason}", file=sys.stderr)
            emit_status("ops_portal.update_rec", "failed", "invalid_update")
            return 1
        updates: dict = {
            field: getattr(args, dest)
            for dest, field in _UPDATE_REC_FIELD_MAP.items()
            if getattr(args, dest, None) is not None
        }
        kwargs: dict = {
            kwarg: getattr(args, dest)
            for dest, kwarg in _UPDATE_REC_KWARG_MAP.items()
            if getattr(args, dest, None) is not None
        }
        if not updates and not kwargs:
            print("ERROR: --update-rec requires at least one update field (e.g. --status)", file=sys.stderr)
            emit_status("ops_portal.update_rec", "failed", "invalid_update")
            return 1
        try:
            update_rec(args.update_rec, updates, profile=args.profile, **kwargs)
        except (ValidationError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            emit_status("ops_portal.update_rec", "failed", "invalid_update")
            return 1
        except Exception:
            emit_status("ops_portal.update_rec", "failed", "writer_error")
            raise
        print(f"Updated {args.update_rec}: {', '.join(sorted({*updates, *kwargs}))}")
        emit_status("ops_portal.update_rec", "done")
        return 0

    if args.file_decision:
        required_dec = ["title", "decision_status", "rationale"]
        missing_dec = [r for r in required_dec if not getattr(args, r.replace("-", "_"), None)]
        if missing_dec:
            # Check mapped names
            actually_missing = []
            if not args.title:
                actually_missing.append("--title")
            if not args.decision_status:
                actually_missing.append("--decision-status")
            if not args.rationale:
                actually_missing.append("--rationale")
            if actually_missing:
                print(f"ERROR: --file-decision requires: {', '.join(actually_missing)}", file=sys.stderr)
                return 1
        if not args.decision_arg_id:
            print("ERROR: --file-decision requires --decision-id (DECISIONS.md number)", file=sys.stderr)
            return 1
        dec_fields: dict = {
            "title": args.title,
            "status": args.decision_status,
            "decision_text": args.rationale,
            "decision_id": args.decision_arg_id,
        }
        try:
            decision_id = file_decision(dec_fields, profile=args.profile)
        except (ValidationError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(decision_id)
        return 0

    if args.update_decision is not None:
        dec_updates: dict = {}
        if args.status:
            dec_updates["status"] = args.status
        if args.resolution:
            dec_updates["resolution"] = args.resolution
        if not dec_updates:
            print("ERROR: --update-decision requires at least one update field", file=sys.stderr)
            return 1
        update_decision(args.update_decision, dec_updates, profile=args.profile)
        print(f"Updated decision {args.update_decision}")
        return 0

    if args.backfill_decisions_md:
        result = backfill_decisions_from_md(profile=args.profile)
        print(json.dumps(result))
        return 0 if result.get("failed", 0) == 0 else 1

    if args.purge_postmortems_for:
        result = purge_postmortems_for(args.purge_postmortems_for, dry_run=args.dry_run, profile=args.profile)
        print(json.dumps(result, indent=2))
        return 0

    if args.repair_dependency_tokens:
        result = repair_dependency_tokens(dry_run=args.dry_run, profile=args.profile)
        print(json.dumps(result, indent=2))
        return 0

    if args.enqueue_findings:
        result = enqueue_findings(Path(args.enqueue_findings), profile=args.profile)
        print(f"enqueued: {result['enqueued']}, invalid: {result['invalid']}, skipped: {result['skipped']}")
        return 0

    if args.guidance:
        from scripts.executor.rec_write_guidance import get_rec_write_guidance

        guidance = get_rec_write_guidance(source=args.source)
        print(yaml.dump(guidance, default_flow_style=False, sort_keys=True, allow_unicode=True))
        return 0

    if args.sync:
        result = sync()
        print(json.dumps(result, indent=2))
        return 0

    if args.selftest_read:
        result = selftest_read(profile=args.profile)
        print(json.dumps(result, indent=2))
        return 0

    if args.selftest_roundtrip:
        try:
            result = selftest_roundtrip(profile=args.profile)
        except (RuntimeError, ValueError, ValidationError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(result, indent=2))
        return 0

    if args.back_validate:
        result = back_validate_ci_rca(since=args.since, refile_audit=args.refile_audit, profile=args.profile)
        if args.json_output:
            print(json.dumps(result))
        else:
            _print_ci_rca_back_validation_report(result)
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
