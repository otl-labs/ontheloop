"""Data quality execution + aggregation over the DuckLake closed reader."""

from __future__ import annotations

import logging
import time
from typing import Any

import yaml

from scripts.data_quality_compile import build_clause8_checks
from scripts.data_quality_models import _DQ_DIR, Check, CheckResult, RunResult
from scripts.ops_portal.reader_transient import is_reader_unavailable as _is_reader_unavailable

logger = logging.getLogger(__name__)

# Ops governance tables on the DuckLake closed boundary (Decision 84 I-1): their checks route
# to the reader, which is the sole backend.
_OPS_TABLES: frozenset[str] = frozenset({"ops_recommendations", "ops_decisions", "ops_priority_queue", "ops_execution_plans"})


def _ops_backend() -> str:
    """DuckLake is the sole ops backend (Decision 84 I-1; the rollback flag is retired)."""
    return "ducklake"


def _verdict_for(check: Check, violation_count: int, duration: float) -> CheckResult:
    """Map a violation count to a verdict."""
    if violation_count == 0:
        return CheckResult(check=check, verdict="PASS", violation_count=0, duration_seconds=duration)
    # Tombstone resurrection is always HARD_GATE regardless of severity.
    if check.test_type == "tombstone_resurrection":
        return CheckResult(
            check=check,
            verdict="HARD_GATE",
            violation_count=violation_count,
            detail=f"resurrected tombstoned record ({violation_count} row(s))",
            duration_seconds=duration,
        )
    if not check.enforced:
        verdict = "UNENFORCED_FAIL" if check.severity == "error" else "WARN"
    else:
        verdict = "FAIL" if check.severity == "error" else "WARN"
    return CheckResult(
        check=check,
        verdict=verdict,
        violation_count=violation_count,
        detail=f"{violation_count} violation(s)",
        duration_seconds=duration,
    )


def _execute_check_ducklake(check: Check, reader: Any) -> CheckResult:
    """Execute a single ops-table check through the closed reader's dq_check action (server-owned SQL, counts only).

    A check with no kind (a cross-table `relationships` FK, dormant and unenforced) is SKIPPED. A response whose
    check_version differs from this repository's is UNAVAILABLE ("deployed reader not at this code"), never a silent pass
    and never a data FAIL; the persistent-unavailability alarm is T1.6 c5.
    """
    from src.common.ducklake_dq_checks import check_version  # noqa: PLC0415

    start = time.time()
    if check.kind is None:
        return CheckResult(
            check=check,
            verdict="SKIP",
            detail="cross-table FK not run on ducklake backend (dormant/unenforced)",
            duration_seconds=0.0,
        )
    try:
        body = reader.dq_check(check.kind, check.table, check.params)
        if body.get("check_version") != check_version(check.kind):
            return CheckResult(
                check=check,
                verdict="UNAVAILABLE",
                detail="deployed reader not at this code (check_version differs)",
                duration_seconds=time.time() - start,
            )
        violation_count = int(body["violation_count"])
    except Exception as exc:  # noqa: BLE001
        verdict = "UNAVAILABLE" if _is_reader_unavailable(exc) else "ERROR"
        return CheckResult(
            check=check, verdict=verdict, detail=f"ducklake query failed: {exc}", duration_seconds=time.time() - start
        )
    return _verdict_for(check, violation_count, time.time() - start)


def apply_backend_routing(all_checks: list[Check], database: str, *, table_filter: str | None = None) -> list[Check]:
    """Append the CD.33 clause-8 checks and pin every ops-table check to the DuckLake reader (sole backend, Decision 84 I-1).

    Shared by main() and the DQ scaffold route so both go through the closed reader. Mutates and returns *all_checks*.
    """
    for c in all_checks:
        if c.table in _OPS_TABLES:
            c.backend = "ducklake"
    ops_spec_yaml = yaml.safe_load((_DQ_DIR / "ops.yaml").read_text(encoding="utf-8")) or {}
    all_checks.extend(build_clause8_checks(ops_spec_yaml, database, table_filter=table_filter))
    return all_checks


def run_checks(
    checks: list[Check],
    *,
    dry_run: bool = False,
    profile_name: str | None = None,
) -> RunResult:
    """Execute all checks against the DuckLake reader and return the aggregate result."""
    run_start = time.time()

    if dry_run:
        skipped = [CheckResult(check=c, verdict="SKIP", detail="dry-run") for c in checks]
        return RunResult(
            results=skipped,
            verdict="SKIP",
            duration_seconds=time.time() - run_start,
        )

    reader = None
    if checks:
        try:
            from src.common.ducklake_reader_client import DuckLakeReader  # noqa: PLC0415

            reader = DuckLakeReader(profile=profile_name)
        except ImportError:
            logger.error("DuckLake reader client unavailable")
            return RunResult(
                results=[CheckResult(check=c, verdict="SKIP", detail="ducklake reader unavailable") for c in checks],
                verdict="SKIP",
                duration_seconds=0.0,
            )

    results: list[CheckResult] = []
    for check in checks:
        result = _execute_check_ducklake(check, reader)
        results.append(result)
        # Log as we go
        symbol = {
            "PASS": ".",
            "FAIL": "F",
            "UNENFORCED_FAIL": "U",
            "WARN": "W",
            "ERROR": "E",
            "SKIP": "S",
            "HARD_GATE": "G",
            "UNAVAILABLE": "A",
        }
        print(symbol.get(result.verdict, "?"), end="", flush=True)

    print()  # newline after progress dots

    # Aggregate verdict
    if not results:
        verdict = "ERROR"
    else:
        has_hard_gate = any(r.verdict == "HARD_GATE" for r in results)
        has_fail = any(r.verdict == "FAIL" and r.check.enforced for r in results)
        has_error = any(r.verdict == "ERROR" for r in results)
        has_unavailable = any(r.verdict == "UNAVAILABLE" for r in results)
        verdict = (
            "HARD_GATE" if has_hard_gate else "FAIL" if (has_fail or has_error) else "DEGRADED" if has_unavailable else "PASS"
        )

    return RunResult(
        results=results,
        verdict=verdict,
        duration_seconds=time.time() - run_start,
    )
