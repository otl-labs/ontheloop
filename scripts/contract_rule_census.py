"""Live census of the ops contracts' declared rules against the warehouse (rec-4158 plan A).

Compiles every non-exempt rule scripts.contract_rules declares for ops_recommendations and ops_decisions into a DQ
Check (a dq_check kind and bound params; the reader owns the SQL and the created_timestamp >= DATE gate) and runs them
through the DQ harness (scripts.data_quality_execute.run_checks; no new reader client). Read only. Exit 0 only on a
complete all-PASS run with zero violators; exit 1 when any rule has violators; exit 2 for
anything that is not a clean answer (SKIP, UNAVAILABLE, ERROR, an empty compiled set, a count mismatch) -- never a
silent pass (Decision 55). A violator under a declared date is a STOP back to /plan (Decision 210 cl.4).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from typing import Any

from scripts.contract_rules import DeclaredRule, declared_rules
from scripts.data_quality_execute import run_checks
from scripts.data_quality_models import Check, dq_request

TABLES = ("ops_recommendations", "ops_decisions")
EXIT_OK, EXIT_VIOLATIONS, EXIT_UNCLEAN = 0, 1, 2


def _kind_params(rule: DeclaredRule) -> dict[str, Any]:
    """The dq_check params for a declared rule (the reader owns the predicate; a gate rides as exclude_before)."""
    params: dict[str, Any] = {"column": rule.column}
    if rule.kind == "accepted_values":
        params["values"] = [str(v) for v in rule.params["values"]]
    elif rule.kind == "min_length":
        params["value"] = int(rule.params["value"])
    elif rule.kind == "pattern":
        params["value"] = rule.params["value"]
    elif rule.kind == "array_element_format":
        params["pattern"] = rule.params["pattern"]
    elif rule.kind == "not_before":
        params["value"] = rule.params["value"]
    elif rule.kind != "not_null":
        raise ValueError(f"{rule.table}.{rule.column}: rule kind {rule.kind!r} has no census check kind and no exemption")
    if rule.exclude_before:
        params["exclude_before"] = rule.exclude_before
    return params


def compile_rule(rule: DeclaredRule) -> Check:
    return Check(
        table=rule.table,
        column=rule.column,
        test_type=rule.kind,
        kind=rule.kind,
        params=_kind_params(rule),
        description=f"{rule.table}.{rule.column}: {rule.kind}",
        severity="error",
        enforced=True,
        exclude_before=rule.exclude_before,
    )


@dataclass(frozen=True)
class Plan:
    table: str
    checks: list[Check]
    exempt: list[DeclaredRule]
    declared_non_exempt: int


def build_plan(table: str) -> Plan:
    rules = declared_rules(table)
    exempt = [r for r in rules if r.exemption]
    live = [r for r in rules if not r.exemption]
    return Plan(table, [compile_rule(r) for r in live], exempt, len(live))


def _describe(result: Any) -> dict[str, Any]:
    check = result.check
    return {
        "table": check.table,
        "column": check.column,
        "rule": check.test_type,
        "gate": check.exclude_before,
        "verdict": result.verdict,
        "violators": result.violation_count,
    }


def census(tables: tuple[str, ...], *, dry_run: bool = False) -> tuple[int, list[str], list[dict[str, Any]]]:
    plans = [build_plan(t) for t in tables]
    lines: list[str] = []
    rows: list[dict[str, Any]] = []
    for plan in plans:
        for rule in plan.exempt:
            lines.append(f"EXEMPT  {rule.table}.{rule.column} {rule.kind} class={(rule.exemption or {}).get('class')}")
    if dry_run:
        for plan in plans:
            lines.extend(f"-- {c.table}.{c.column} {c.test_type}\n{json.dumps(dq_request(c))}" for c in plan.checks)
        return EXIT_OK, lines, rows
    all_checks = [c for p in plans for c in p.checks]
    run = run_checks(all_checks)
    results = list(run.results)
    rows = [_describe(r) for r in results]
    for r in results:
        lines.append(f"{r.verdict:<8}{r.check.table}.{r.check.column} {r.check.test_type} violators={r.violation_count}")
    violators = sum(1 for r in results if r.violation_count > 0)
    unclean = [r for r in results if r.verdict != "PASS" and r.violation_count == 0]
    mismatch = len(results) != len(all_checks) or any(p.declared_non_exempt != len(p.checks) or not p.checks for p in plans)
    summary = (
        f"SUMMARY compiled={len(all_checks)} declared={sum(p.declared_non_exempt for p in plans)} "
        f"exempt={sum(len(p.exempt) for p in plans)} rules_with_violators={violators} unclean={len(unclean)}"
    )
    lines.append(summary)
    if violators:
        return EXIT_VIOLATIONS, lines, rows
    if unclean or mismatch or not results:
        return EXIT_UNCLEAN, lines, rows
    return EXIT_OK, lines, rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--table", choices=TABLES, help="census one table (default: both)")
    parser.add_argument("--json", action="store_true", help="print the per-rule results as JSON")
    parser.add_argument("--dry-run", action="store_true", help="print the dq_check requests; no reader is contacted")
    args = parser.parse_args(argv)
    tables = (args.table,) if args.table else TABLES
    try:
        code, lines, rows = census(tables, dry_run=args.dry_run)
    except ValueError as exc:
        print(f"census could not compile: {exc}", file=sys.stderr)
        return EXIT_UNCLEAN
    print("\n".join(lines))
    if args.json:
        print(json.dumps(rows, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
