"""Source-registry CI guard: schedule.yaml agent names + the complete producer-module scan set's
hardcoded source string literals (Decision 104, extended by Decision 124, widened by
PLAN-convergence-health-prod-drift-red / Decision 170)."""

from __future__ import annotations

from pathlib import Path

from scripts.checks import _common, registry

# The COMPLETE scan set (constraints: exactly this list, never scripts/** wholesale -- src/ and
# tests/ carry unrelated "source": "..." keys (layer, mock, custom-source) that would
# red collaterally). Repo-relative STRINGS, joined against _common.ROOT at CALL TIME inside
# _resolve_scanned_paths -- never resolved at import time: tests/checks/_common/test_primitives.py
# patches scripts.checks._common.ROOT and dispatches this check, and an import-time absolute
# constant would freeze the real ROOT and silently defeat that seam. A "*.py" entry is a glob
# pattern, expanded at call time (the ops_portal implementation package, Decision 124, holds more
# than one file).
SCANNED_PATHS: tuple[str, ...] = (
    "scripts/ops_data_portal.py",
    "scripts/ops_portal/*.py",
    "scripts/convergence_health/code_drift.py",
    "scripts/convergence_health/escalate.py",
    "scripts/convergence_health/sensor_liveness.py",
    "scripts/checks/_budget_recs.py",
    "scripts/ci_rca/probe_health.py",
    "scripts/preflight/ci_rca_gauges.py",
    "scripts/executor/jsonl_store.py",
    "scripts/executor/branch_lifecycle.py",
    "scripts/ducklake_smoke/lambda_ops_gates.py",
    "scripts/backlog_health/escalate.py",
    "scripts/telemetry_verify/alarms.py",
)


def _resolve_scanned_paths(root: Path) -> list[Path]:
    """Expand SCANNED_PATHS against `root`, globbing any entry that carries a `*`."""
    resolved: list[Path] = []
    for entry in SCANNED_PATHS:
        if "*" in entry:
            resolved.extend(sorted(root.glob(entry)))
        else:
            resolved.append(root / entry)
    return resolved


@registry.register("check_source_registry", owner="platform")
def check_source_registry(failed: list[str]) -> None:
    """Verify that all agent names in schedule.yaml are registered canonical_ids.

    Also scans every module in SCANNED_PATHS (the portal surface plus the enumerated producer
    modules that file/close recs -- see that constant's docstring) for hardcoded source string
    literals and verifies each is registered. Wired into run_python_checks() -- runs on presubmit
    (pre=True, pre_globs=None -- ungated, so no scanned path can skip the fast tier regardless of
    which file a PR touches).
    """
    import re as _re

    import yaml as _yaml

    print("\n=== Source registry CI guard ===")

    registry_path = _common.ROOT / "config" / "agent" / "data_quality" / "source_registry.yaml"
    if not registry_path.exists():
        print(f"  FAIL: {registry_path} not found -- create source_registry.yaml first")
        failed.append("Source registry CI guard")
        registry.skipped("source_registry.yaml not found")
        return

    registry_data = _yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    valid_ids: set[str] = {e["canonical_id"] for e in registry_data.get("entries", [])}

    violations: list[str] = []

    schedule_path = _common.ROOT / ".github" / "agents" / "schedule.yaml"
    if schedule_path.exists():
        schedule_data = _yaml.safe_load(schedule_path.read_text(encoding="utf-8"))
        for agent in schedule_data.get("agents", []):
            name = agent.get("name", "")
            if name and name not in valid_ids:
                violations.append(f"schedule.yaml agent name '{name}' not in source_registry.yaml")
    else:
        print(f"  WARNING: {schedule_path} not found -- skipping agent name check")

    scanned_files = [p for p in _resolve_scanned_paths(_common.ROOT) if p.exists()]

    literal_count = 0
    for scanned_path in scanned_files:
        scanned_source = scanned_path.read_text(encoding="utf-8")
        rel_label = scanned_path.relative_to(_common.ROOT).as_posix()

        for match in _re.finditer(r'source\s*==\s*[\'"]([^\'"]+)[\'"]|"source"\s*:\s*"([^"]+)"', scanned_source):
            literal = match.group(1) or match.group(2)
            if not literal or literal.startswith("{"):
                continue
            literal_count += 1
            if literal not in valid_ids:
                violations.append(f"{rel_label} hardcoded source '{literal}' not in source_registry.yaml")

    registry.examined(literal_count, unit="source_literals")

    if violations:
        for v in violations:
            print(f"  FAIL: {v}")
        failed.append("Source registry CI guard")
    else:
        print(
            f"  PASS: all agent names and {literal_count} hardcoded source value(s) registered "
            f"({len(valid_ids)} entries, {len(scanned_files)} files scanned)"
        )
