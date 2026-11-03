"""Deterministic V-tier floor check for schema_version-2 plans (VF-04, T3.17).

Colocated with validate_plan_documents.py in roadmap/ for domain cohesion (both
are PLAN-*.yaml governance checks). schema_version-1 plans are grandfathered
(skipped entirely) -- see docs/ROADMAP-PLATFORM.yaml T3.17 c1/c2.
"""

from __future__ import annotations

from pathlib import Path

from scripts import lambda_manifest
from scripts.checks import _common, registry
from scripts.contract_rules import parse_yaml

_TIER_RANK: dict[str, int] = {"V1": 1, "V2": 2, "V3": 3}


def _lambda_code_files() -> set[str]:
    """Repo-relative code-file paths across every active manifest's handlers + includes,
    minus excludes -- deliberately NOT compute_affected_artifacts, which also unions
    assets/config and would over-flag bundled non-code assets (e.g. docs/ROADMAP-PLATFORM.yaml).
    """
    try:
        manifests = lambda_manifest.load_all()
    except Exception:
        return set()

    code_files: set[str] = set()
    for manifest in manifests.values():
        if manifest.status == "stub":
            continue
        for p in manifest.handlers + manifest.includes:
            clean = p.rstrip("/")
            if lambda_manifest._is_excluded(clean, manifest.excludes):
                continue
            code_files.add(clean)
    return code_files


def _matches_lambda_code(scope_file: str, code_files: set[str]) -> bool:
    for cf in code_files:
        if scope_file == cf or scope_file.startswith(cf + "/"):
            return True
    return False


def _compute_floor(scope_files: list[str], code_files: set[str]) -> str:
    """Highest-tier-wins floor over a plan's scope files (Decision 48 semantics)."""
    floor = "V1"
    for sf in scope_files:
        if _matches_lambda_code(sf, code_files):
            return "V3"
        if sf.endswith(".tf"):
            return "V3"
        if sf.endswith(".py") and _TIER_RANK[floor] < _TIER_RANK["V2"]:
            floor = "V2"
    return floor


@registry.register("validate_tier_floor", owner="platform")
def validate_tier_floor(failed: list[str], plans_dir: Path | None = None) -> None:
    """For each schema_version-2 PLAN-*.yaml, fail if declared verification_tier is below
    the deterministic floor computed from its scope (VF-04), unless tier_waiver is set.

    schema_version-1 plans are skipped entirely (grandfathered, Option A). plans_dir
    overrides the scanned directory (test seam, mirrors validate_plan_documents).

    Declares examined over the schema_version-2 plans whose floor it computes (unit "v2
    plans"); plans of any other schema_version, and non-mapping documents, are neither judged
    nor counted. A directory with no PLAN-*.yaml, or none at schema_version 2, declares
    examined(0).
    """
    print("\n=== Deterministic V-tier floor validation ===")

    target_dir = plans_dir if plans_dir is not None else _common.ROOT / "docs" / "plans"
    plan_paths = sorted(target_dir.glob("PLAN-*.yaml"))
    if not plan_paths:
        registry.examined(0, unit="v2 plans")
        print("  PASS: no PLAN-*.yaml files to validate.")
        return

    code_files = _lambda_code_files()
    violations: list[str] = []
    judged = 0
    for path in plan_paths:
        data = parse_yaml(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema_version") != 2:
            continue
        judged += 1
        scope_files = [entry["file"] for entry in data.get("scope", []) if "file" in entry]
        floor = _compute_floor(scope_files, code_files)
        declared = data.get("verification_tier", "V1")
        if _TIER_RANK.get(declared, 0) < _TIER_RANK[floor] and not data.get("tier_waiver"):
            msg = f"{path.name}: declared {declared} below floor {floor} (no tier_waiver)"
            print(f"  FAIL: {msg}")
            violations.append(msg)

    registry.examined(judged, unit="v2 plans")
    if violations:
        failed.append("Deterministic V-tier floor validation")
    else:
        print(f"  PASS: {len(plan_paths)} plan document(s) meet their deterministic V-tier floor.")
