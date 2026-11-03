"""Admin-only-root pre-merge live-plan gate check (rec-4140, docs/contracts/deploy-paths.yaml
admin_out_of_band.pre_merge_live_plan).

Standing rule from the #1316 -> #1323 incident: an admin-only Terraform root's first live
provider contact must happen BEFORE merge, never after. For every net-new IMPLEMENTATION plan
whose scope touches a non-Markdown file under such a root, this check flags each of the rule's
three clauses separately when the plan does not carry it: a PRE-MERGE GATE (ADMIN) pre-deploy
step, an ENVIRONMENT CHECK pre-deploy step, and a "V3 resolution:" context line.

Root derivation runs UNCONDITIONALLY, before plan discovery (the validate_plan_scope_closure
grammar-leg pattern), so a taxonomy-only diff is examined too, and fails CLOSED: an unreadable
taxonomy or zero derived admin-only roots is itself a finding, never a silent pass.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from scripts.checks import _common, registry
from scripts.roadmap import plan_obligations

_TAXONOMY_REL_PATH = "docs/contracts/environment-taxonomy.yaml"
_CONTRACT_POINTER = "docs/contracts/deploy-paths.yaml admin_out_of_band.pre_merge_live_plan"
_ROOT_RE = re.compile(r"terraform/[A-Za-z0-9_-]+")

EXEMPT_SUFFIXES = (".md",)
GATE_MARKERS = ("terraform", " plan", "-lock=false", "PR_HEAD_SHA")
ENV_MARKERS = ("caller-arn", "ADMIN_CHAIN_OK")
V3_PREFIX = "V3 resolution:"


def _derive_admin_only_roots(taxonomy_path: Path) -> tuple[list[str], str | None]:
    """Read environment-taxonomy.yaml's axis_a and return the Terraform roots whose
    apply_gating is admin-only. Never raises -- an unreadable/malformed taxonomy, or an
    axis_a with zero admin-only rows, is reported as (error_or_empty, error)."""
    try:
        text = taxonomy_path.read_text(encoding="utf-8")
    except OSError as exc:
        return [], f"could not read {taxonomy_path}: {exc}"
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return [], f"could not parse {taxonomy_path}: {exc}"
    if not isinstance(data, dict):
        return [], f"{taxonomy_path} is not a YAML mapping"
    axis_a = data.get("axis_a")
    if not isinstance(axis_a, dict):
        return [], f"{taxonomy_path} is missing an 'axis_a' mapping"
    roots: list[str] = []
    for value in axis_a.values():
        if not isinstance(value, dict):
            continue
        apply_gating = value.get("apply_gating")
        code_vs_infra = value.get("code_vs_infra")
        if not isinstance(apply_gating, str) or not apply_gating.startswith("admin-only"):
            continue
        if not isinstance(code_vs_infra, str):
            continue
        match = _ROOT_RE.search(code_vs_infra)
        if match:
            roots.append(match.group(0))
    if not roots:
        return [], f"{taxonomy_path} axis_a has no admin-only row with a derivable terraform root"
    return roots, None


def _in_scope_files(data: dict[str, Any], roots: list[str]) -> list[str]:
    scope = data.get("scope")
    if not isinstance(scope, list):
        return []
    hits: list[str] = []
    for row in scope:
        if not isinstance(row, dict):
            continue
        file_path = row.get("file")
        if not isinstance(file_path, str):
            continue
        if file_path.endswith(EXEMPT_SUFFIXES):
            continue
        for root in roots:
            if file_path.startswith(root + "/"):
                hits.append(file_path)
                break
    return hits


def _read_plan(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"could not read {path}: {exc}"
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return None, f"could not parse {path}: {exc}"
    if not isinstance(data, dict):
        return None, f"{path} is not a YAML mapping"
    return data, None


def _has_gate_step(data: dict[str, Any], root: str) -> bool:
    for step in data.get("verification_plan") or []:
        if not isinstance(step, dict) or step.get("phase") != "pre-deploy":
            continue
        command = step.get("command")
        if not isinstance(command, str):
            continue
        if all(marker in command for marker in GATE_MARKERS) and root in command:
            return True
    return False


def _has_env_check_step(data: dict[str, Any]) -> bool:
    for step in data.get("verification_plan") or []:
        if not isinstance(step, dict) or step.get("phase") != "pre-deploy":
            continue
        command = step.get("command")
        if not isinstance(command, str):
            continue
        if all(marker in command for marker in ENV_MARKERS):
            return True
    return False


def _has_v3_resolution(data: dict[str, Any]) -> bool:
    for entry in data.get("context") or []:
        if isinstance(entry, str) and entry.strip().startswith(V3_PREFIX):
            return True
    return False


def _evaluate_plan(path: Path, roots: list[str]) -> list[str]:
    data, error = _read_plan(path)
    if error or data is None:
        return [f"{path.name}: {error} ({_CONTRACT_POINTER})"]
    in_scope = _in_scope_files(data, roots)
    if not in_scope:
        return []
    findings: list[str] = []
    matched_root = next((root for root in roots for f in in_scope if f.startswith(root + "/")), roots[0])
    if not _has_gate_step(data, matched_root):
        findings.append(f"{path.name}: missing PRE-MERGE GATE (ADMIN) pre-deploy step ({_CONTRACT_POINTER})")
    if not _has_env_check_step(data):
        findings.append(f"{path.name}: missing ENVIRONMENT CHECK pre-deploy step ({_CONTRACT_POINTER})")
    if not _has_v3_resolution(data):
        findings.append(f"{path.name}: missing V3 RESOLUTION context entry ({_CONTRACT_POINTER})")
    return findings


@registry.register("validate_admin_root_premerge_gate", owner="platform")
def validate_admin_root_premerge_gate(
    failed: list[str],
    plan_paths: list[Path] | None = None,
    taxonomy_path: Path | None = None,
) -> None:
    """Flag a net-new IMPLEMENTATION plan whose scope touches a non-Markdown file under an
    admin-only Terraform root, for each of pre_merge_live_plan's three clauses it does not meet.

    `plan_paths` overrides plan discovery and is accepted UNFILTERED, like
    validate_plan_scope_closure's fixture convention. `None` (real dispatch) derives the net-new
    IMPLEMENTATION set from the git diff via plan_obligations.net_new_v4_implementation_plan_paths
    -- this check never globs docs/plans/.
    """
    print("\n=== Admin-root pre-merge live-plan gate validation ===")

    resolved_taxonomy_path = taxonomy_path if taxonomy_path is not None else _common.ROOT / _TAXONOMY_REL_PATH
    roots, taxonomy_error = _derive_admin_only_roots(resolved_taxonomy_path)
    if taxonomy_error:
        print(f"  FAIL: {taxonomy_error} ({_CONTRACT_POINTER})")
        failed.append(f"{taxonomy_error} ({_CONTRACT_POINTER})")
        registry.examined(1, unit="artefacts")
        return

    paths = plan_obligations.net_new_v4_implementation_plan_paths() if plan_paths is None else plan_paths
    if not paths:
        print("  PASS: no net-new IMPLEMENTATION plan to check.")
        registry.examined(1, unit="artefacts")
        return

    errors: list[str] = []
    for path in paths:
        errors.extend(_evaluate_plan(path, roots))
    for error in errors:
        print(f"  FAIL: {error}")
    if errors:
        failed.extend(errors)
    else:
        print(f"  PASS: {len(paths)} plan(s) meet admin_out_of_band.pre_merge_live_plan.")
    registry.examined(1 + len(paths), unit="artefacts")
