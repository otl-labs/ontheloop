"""Data quality runner: compiles YAML check definitions to dq_check requests on the DuckLake reader.

Modelled after dbt test semantics. Each check is a closed dq_check kind plus bound params whose SQL is owned by the
reader; the answer is a violation count. Zero violations = PASS. Any violations = FAIL (or WARN).

Two-layer health picture (surfaced in preflight's `data_quality` section):
    1. Coverage, read from config/agent/data_quality/*.yaml: how many declarative
       checks (not_null, unique, accepted_values, relationships, row_count,
       recency) are defined across how many tables (checks_defined > 0 means
       there is visibility into data correctness at all).
    2. State, read from logs/debug/dq-latest.json: the verdict, pass/fail/warn
       counts, and timestamp of the most recent run of this module
       (last_run.verdict == PASS means the data was actually correct as of
       that run).
    Coverage without state means checks are defined but have never run.
    checks_defined == 0 means a plan touching data write paths should add YAML
    checks here; a FAIL verdict means the plan should note which tables failed.

Usage:
    # Run all checks for all tables
    AWS_PROFILE=agent_platform python -m scripts.data_quality_runner

    # Run checks for a single table
    AWS_PROFILE=agent_platform python -m scripts.data_quality_runner --table ops_recommendations

    # Run checks from a specific YAML file
    AWS_PROFILE=agent_platform python -m scripts.data_quality_runner --file config/agent/data_quality/ops.yaml

    # Dry-run: print each check's dq_check request without executing
    python -m scripts.data_quality_runner --dry-run

    # JSON output for programmatic consumption
    AWS_PROFILE=agent_platform python -m scripts.data_quality_runner --json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from scripts.data_quality_compile import (
    _compile_column_test,  # noqa: F401
    build_clause8_checks,  # noqa: F401
    build_tombstone_checks,
    load_checks,
    load_tombstones,
)
from scripts.data_quality_execute import (
    _OPS_TABLES,  # noqa: F401
    _execute_check_ducklake,  # noqa: F401
    _is_reader_unavailable,  # noqa: F401
    _ops_backend,  # noqa: F401
    _verdict_for,  # noqa: F401
    apply_backend_routing,
    run_checks,
)
from scripts.data_quality_models import (
    _DQ_DIR,
    _ROOT,
    _TOMBSTONES_PATH,  # noqa: F401
    Check,
    CheckResult,  # noqa: F401
    RunResult,
    dq_request,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_results(run_result: RunResult, as_json: bool = False) -> None:
    """Print results to stdout."""
    if as_json:
        output = {
            "verdict": run_result.verdict,
            "total": len(run_result.results),
            "passed": run_result.passed,
            "failed": run_result.failed,
            "unenforced_fail": run_result.unenforced_fail,
            "warned": run_result.warned,
            "errored": run_result.errored,
            "skipped": run_result.skipped,
            "unavailable": run_result.unavailable,
            "duration_seconds": round(run_result.duration_seconds, 1),
            "checks": [
                {
                    "table": r.check.table,
                    "column": r.check.column,
                    "test": r.check.test_type,
                    "verdict": r.verdict,
                    "violations": r.violation_count,
                    "detail": r.detail,
                    "description": r.check.description,
                }
                for r in run_result.results
            ],
        }
        print(json.dumps(output, indent=2))
        return

    # Human-readable summary
    print(f"\n{'=' * 60}")
    print(f"Data Quality: {run_result.verdict}")
    print(f"{'=' * 60}")
    if run_result.verdict == "DEGRADED":
        print(f"  *** DEGRADED: {run_result.unavailable} check(s) could not reach the DuckLake backend. ***")
        print("  *** Gate not enforced -- infra outage, not a data violation. ***")
    print(
        f"  Passed: {run_result.passed}  "
        f"Failed: {run_result.failed}  "
        f"Unenforced: {run_result.unenforced_fail}  "
        f"Warned: {run_result.warned}  "
        f"Errors: {run_result.errored}  "
        f"Skipped: {run_result.skipped}  "
        f"Unavailable: {run_result.unavailable}"
    )
    print(f"  Duration: {run_result.duration_seconds:.1f}s")
    print()

    # Show failures, warnings, and unavailable checks
    _ISSUE_VERDICTS = ("FAIL", "UNENFORCED_FAIL", "WARN", "ERROR", "HARD_GATE", "UNAVAILABLE")
    issues = [r for r in run_result.results if r.verdict in _ISSUE_VERDICTS]
    if issues:
        print("Issues:")
        for r in issues:
            prefix = {
                "FAIL": "FAIL",
                "UNENFORCED_FAIL": "UENF",
                "WARN": "WARN",
                "ERROR": "ERR ",
                "HARD_GATE": "GATE",
                "UNAVAILABLE": "UNAVL",
            }[r.verdict]
            print(f"  [{prefix}] {r.check.description}")
            if r.detail:
                print(f"         {r.detail}")
        print()


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Run data quality checks against the DuckLake ops tables",
    )
    parser.add_argument(
        "--file",
        "-f",
        help="Specific YAML file to load (default: all files in config/agent/data_quality/)",
    )
    parser.add_argument(
        "--table",
        "-t",
        help="Run checks for a specific table only",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print each check's dq_check request without executing",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )
    parser.add_argument(
        "--severity",
        choices=["error", "warn", "all"],
        default="all",
        help="Filter checks by severity (default: all)",
    )
    parser.add_argument(
        "--checks",
        metavar="TYPE",
        help="Run only checks of this test type (e.g. tombstone_resurrection)",
    )
    parser.add_argument(
        "--profile",
        default=None,
        metavar="PROFILE",
        help="AWS SSO profile name (default: AWS_PROFILE env, then agent_platform)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # Load YAML files
    if args.file:
        yaml_files = [Path(args.file)]
    else:
        yaml_files = sorted(_DQ_DIR.glob("*.yaml"))

    if not yaml_files:
        logger.error("No YAML files found in %s", _DQ_DIR)
        return 1

    all_checks: list[Check] = []
    database: str | None = None

    for yf in yaml_files:
        checks, metadata = load_checks(yf, table_filter=args.table)
        file_db = metadata.get("database", "agent_platform")
        if database is None:
            database = file_db
        elif file_db != database:
            logger.error(
                "Conflicting database in %s (%s) vs previous files (%s). Use --file to run each YAML separately.",
                yf,
                file_db,
                database,
            )
            return 1
        all_checks.extend(checks)

    database = database or "agent_platform"

    # Add tombstone resurrection checks
    tombstones = load_tombstones()
    all_checks.extend(build_tombstone_checks(tombstones, table_filter=args.table, database=database))

    # Decision 84 I-1: every ops-table check runs against the DuckLake closed reader (DuckDB dialect
    # over the `current` TABLE), plus the CD.33 clause-8 checks.
    all_checks = apply_backend_routing(all_checks, database, table_filter=args.table)

    # Filter by check type (e.g. --checks tombstone_resurrection)
    if args.checks:
        all_checks = [c for c in all_checks if c.test_type == args.checks]

    # Filter by severity
    if args.severity == "error":
        all_checks = [c for c in all_checks if c.severity == "error"]
    elif args.severity == "warn":
        all_checks = [c for c in all_checks if c.severity == "warn"]

    if not all_checks:
        logger.info("No checks match filters")
        return 0

    logger.info("Running %d checks against %s", len(all_checks), database)

    if args.dry_run:
        result = run_checks(all_checks, dry_run=True, profile_name=args.profile)
        if args.json:
            _print_results(result, as_json=True)
            _save_latest_result(result)
        else:
            for r in result.results:
                print(f"\n-- [{r.check.severity.upper()}] {r.check.description}")
                print(json.dumps(dq_request(r.check)))
        return 0

    # Execute
    result = run_checks(all_checks, dry_run=False, profile_name=args.profile)
    _print_results(result, as_json=args.json)

    # Save latest result for preflight consumption
    _save_latest_result(result)

    return 0 if result.verdict in {"PASS", "DEGRADED", "SKIP"} else 1


def _save_latest_result(result: RunResult) -> None:
    """Write a summary to logs/debug/dq-latest.json for preflight pickup."""
    from datetime import datetime, timezone  # noqa: PLC0415

    if not result.results:
        return

    out_dir = _ROOT / "logs" / "debug"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "verdict": result.verdict,
        "total": len(result.results),
        "passed": result.passed,
        "failed": result.failed,
        "unenforced_fail": result.unenforced_fail,
        "warned": result.warned,
        "errored": result.errored,
        "hard_gated": result.hard_gated,
        "unavailable": result.unavailable,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": round(result.duration_seconds, 1),
        "checks": [
            {"table": r.check.table, "column": r.check.column, "test": r.check.test_type, "verdict": r.verdict}
            for r in result.results
        ],
    }
    if summary["total"] == 0:
        summary["verdict"] = "ERROR"
    (out_dir / "dq-latest.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    sys.exit(main())
