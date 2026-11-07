"""Arrival-expectation declarations for every registered warehouse table (Decision 250, rec-4949).

The grammar and the per-table declarations live in docs/contracts/data-modeling-standard.yaml under
arrival_expectations. The population is the generated table registry (never a glob of contract files, Decision 191); the
check fails a registry table with no declaration, a declaration naming no registry table, and any entry that breaks its
kind's grammar or whose enforcing workflow is not scheduled and wired to its alarm route. It reads no warehouse and no
network. validate_arrival_residual_review is the full-tier-only calendar leg, so no PR's merge depends on the date.
"""

from __future__ import annotations

import datetime as dt
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from scripts.checks import _common, registry
from scripts.convergence_health import sensor_liveness
from scripts.telemetry_verify import session_presence

_STANDARD = "docs/contracts/data-modeling-standard.yaml"
_TAXONOMY = "config/ci_rca_taxonomy.yaml"
_CI_RCA = ".github/workflows/ci-rca.yml"
_WORKFLOWS = ".github/workflows"
_ORACLE = "scripts/telemetry_verify/session_presence.py"
_OWNER = re.compile(r"^rec-[0-9]{4,}$")
_AUTHORIZED = re.compile(r"^(Decision [0-9]+|rec-[0-9]{4,})$")
_SEMANTICS = {"set_inclusion", "count"}
_ROUTES = {"ci_rca", "self_filed"}
_KIND_FIELDS = ("required", "optional")


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _grammar(standard: Any) -> tuple[dict[str, Any], dict[str, Any], str | None]:
    block = standard.get("arrival_expectations") if isinstance(standard, dict) else None
    if not isinstance(block, dict):
        return {}, {}, "arrival_expectations block is absent or not a mapping"
    kinds, tables = block.get("kinds"), block.get("tables")
    if not isinstance(kinds, dict) or not kinds:
        return {}, {}, "arrival_expectations.kinds is absent or empty"
    if not isinstance(tables, dict):
        return {}, {}, "arrival_expectations.tables is absent or not a mapping"
    for name, spec in kinds.items():
        if not isinstance(spec, dict) or not isinstance(spec.get("required"), list):
            return {}, {}, f"arrival_expectations.kinds.{name} lacks a required list"
    return kinds, tables, None


def _workflow_doc(root: Path, stem: str) -> tuple[dict[str, Any] | None, str | None]:
    path = root / _WORKFLOWS / f"{stem}.yml"
    if not path.is_file():
        return None, f"workflow {stem}.yml does not exist"
    try:
        doc = _load(path)
    except yaml.YAMLError:
        return None, f"workflow {stem}.yml does not parse"
    return (doc, None) if isinstance(doc, dict) else (None, f"workflow {stem}.yml is not a mapping")


def _cron_errors(doc: dict[str, Any], stem: str) -> list[str]:
    on_block = doc.get("on", doc.get(True, {}))
    schedule = on_block.get("schedule") if isinstance(on_block, dict) else None
    crons = [e["cron"] for e in schedule if isinstance(e, dict) and e.get("cron")] if isinstance(schedule, list) else []
    if not crons:
        return [f"workflow {stem}.yml has no on.schedule cron"]
    errors = []
    for cron in crons:
        try:
            sensor_liveness.cron_period_seconds(cron)
        except ValueError:
            errors.append(f"workflow {stem}.yml cron {cron!r} is not one the liveness sweep accepts")
    return errors


def _route_errors(root: Path, doc: dict[str, Any], route: str) -> list[str]:
    name = doc.get("name")
    try:
        rows = (_load(root / _TAXONOMY) or {}).get("workflows") or {}
    except (OSError, yaml.YAMLError):
        return [f"{_TAXONOMY} is unreadable"]
    row = rows.get(name) if isinstance(rows, dict) else None
    if not isinstance(row, dict):
        return [f"workflow name {name!r} is not a {_TAXONOMY} workflows key"]
    if route == "self_filed":
        return [] if row.get("ci_rca") == "excluded" else [f"workflow {name!r} must be ci_rca: excluded for self_filed"]
    errors = [] if row.get("ci_rca") == "watched" else [f"workflow {name!r} must be ci_rca: watched for ci_rca"]
    try:
        ci_rca = _load(root / _CI_RCA)
    except (OSError, yaml.YAMLError):
        return errors + [f"{_CI_RCA} is unreadable"]
    on_block = ci_rca.get("on", ci_rca.get(True, {})) if isinstance(ci_rca, dict) else {}
    listed = (on_block.get("workflow_run") or {}).get("workflows") if isinstance(on_block, dict) else None
    if not isinstance(listed, list) or name not in listed:
        errors.append(f"workflow name {name!r} is not in {_CI_RCA} on.workflow_run.workflows")
    return errors


def _enforced_errors(root: Path, entry: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if entry.get("semantics") not in _SEMANTICS:
        errors.append(f"semantics must be one of {sorted(_SEMANTICS)}")
    try:
        session_presence.parse_sla(entry.get("sla"))
    except session_presence.Unreadable:
        errors.append("sla must match ^[1-9][0-9]*[mhd]$")
    route = entry.get("alarm_route")
    if route not in _ROUTES:
        errors.append(f"alarm_route must be one of {sorted(_ROUTES)}")
    check = entry.get("check")
    if not isinstance(check, dict) or set(check) != {"workflow", "module"}:
        return errors + ["check must be a mapping of exactly workflow and module"]
    if not (root / str(check["module"])).is_file():
        errors.append(f"module {check['module']} does not exist")
    doc, err = _workflow_doc(root, str(check["workflow"]))
    if doc is None:
        return errors + [str(err)]
    errors += _cron_errors(doc, str(check["workflow"]))
    if route in _ROUTES:
        errors += _route_errors(root, doc, str(route))
    return errors


def _entry_errors(root: Path, table: str, entry: Any, kinds: dict[str, Any], tables: dict[str, Any]) -> list[str]:
    if not isinstance(entry, dict):
        return ["entry is not a mapping"]
    kind = entry.get("kind")
    spec = kinds.get(kind) if isinstance(kind, str) else None
    if spec is None or kind not in {"enforced", "residual", "derived", "waived"}:
        return [f"unknown kind {kind!r}"]
    required = list(spec["required"])
    allowed = set(required) | set(spec.get("optional") or []) | {"kind", "denominator"}
    errors = [f"missing required field {f}" for f in required if f not in entry]
    errors += [f"field {f} is not allowed for kind {kind}" for f in entry if f not in allowed]
    if errors:
        return errors
    if kind == "enforced":
        errors += _enforced_errors(root, entry)
    elif kind == "residual":
        if not _OWNER.match(str(entry["owner"])):
            errors.append("owner must match ^rec-[0-9]{4,}$")
        try:
            dt.date.fromisoformat(str(entry["review_by"]))
        except ValueError:
            errors.append("review_by must be an ISO date")
    elif kind == "waived":
        if not _AUTHORIZED.match(str(entry["authorized_by"])):
            errors.append("authorized_by must be 'Decision N' or rec-NNNN")
    elif kind == "derived":
        parent = tables.get(entry["parent"])
        if not isinstance(parent, dict) or parent.get("kind") not in {"enforced", "residual"}:
            errors.append("parent must be a registered table whose kind is enforced or residual")
        doc, err = _workflow_doc(root, str(entry["cardinality_check"]))
        errors += [str(err)] if doc is None else _cron_errors(doc, str(entry["cardinality_check"]))
    return errors


def _population(root: Path) -> set[str]:
    root_str = str(root)
    injected = root_str not in sys.path
    if injected:
        sys.path.insert(0, root_str)
    try:
        from src.common.ducklake_control_tables import control_table_names
        from src.common.ducklake_scd2_schema import ops_table_names

        return set(ops_table_names()) | set(control_table_names())
    finally:
        if injected and root_str in sys.path:
            sys.path.remove(root_str)


def judge(root: Path, today: dt.date | None = None, known_tables: set[str] | None = None) -> tuple[list[str], int]:
    """(failures, examined). today is accepted for the residual leg's pinning; this core never reads the calendar."""
    del today
    try:
        standard = _load(root / _STANDARD)
    except (OSError, yaml.YAMLError) as exc:
        return [f"{_STANDARD} is unreadable: {type(exc).__name__}"], 0
    kinds, tables, bad = _grammar(standard)
    if bad:
        return [bad], 0
    population = known_tables if known_tables is not None else _population(root)
    failures = [f"{t}: registry table has no arrival declaration" for t in sorted(population - set(tables))]
    failures += [f"{t}: declaration names no registry table" for t in sorted(set(tables) - population)]
    for table in sorted(population & set(tables)):
        failures += [f"{table}: {e}" for e in _entry_errors(root, table, tables[table], kinds, tables)]
    if (
        isinstance(tables.get("telemetry_sessions"), dict)
        and tables["telemetry_sessions"].get("kind") == "enforced"
        and not (root / _ORACLE).is_file()
    ):
        failures.append(f"telemetry_sessions: oracle {_ORACLE} does not exist")
    return failures, len(population)


def residual_failures(root: Path, today: dt.date) -> tuple[list[str], int]:
    try:
        tables = _grammar(_load(root / _STANDARD))[1]
    except (OSError, yaml.YAMLError):
        return [f"{_STANDARD} is unreadable"], 0
    failures, count = [], 0
    for table, entry in sorted(tables.items()):
        if not isinstance(entry, dict) or entry.get("kind") != "residual":
            continue
        count += 1
        try:
            due = dt.date.fromisoformat(str(entry.get("review_by")))
        except ValueError:
            continue
        if due < today:
            failures.append(
                f"{table}: residual arrival expectation review_by {due} has passed (owner {entry.get('owner')}); "
                "build its check, or renew review_by with a data-modeling-standard amendment"
            )
    return failures, count


@registry.register("validate_arrival_expectations", owner="platform")
def validate_arrival_expectations(
    failed: list[str], *, root: Path | None = None, known_tables: set[str] | None = None
) -> None:
    """Fail when a registry table lacks a well-formed arrival declaration (both tiers, ungated)."""
    print("\n=== Arrival expectations (Decision 250) ===")
    failures, examined = judge(root if root is not None else _common.ROOT, known_tables=known_tables)
    registry.examined(examined, unit="tables")
    for line in failures:
        print(f"  FAIL: {line}")
    if failures:
        failed.append("Arrival expectations")
    else:
        print(f"  PASS: {examined} registry table(s) each declare an arrival expectation.")


@registry.register("validate_arrival_residual_review", owner="platform")
def validate_arrival_residual_review(failed: list[str], *, root: Path | None = None, today: dt.date | None = None) -> None:
    """Fail when a residual expectation's review_by is past (UTC); full tier only, never --pre."""
    print("\n=== Arrival residual review (Decision 250) ===")
    today = today if today is not None else dt.datetime.now(dt.timezone.utc).date()
    failures, count = residual_failures(root if root is not None else _common.ROOT, today)
    registry.examined(count, unit="residuals")
    for line in failures:
        print(f"  FAIL: {line}")
    if failures:
        failed.append("Arrival residual review")
    else:
        print(f"  PASS: {count} residual expectation(s) within review_by.")
