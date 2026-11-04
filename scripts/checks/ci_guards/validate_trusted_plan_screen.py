"""Keeps terraform/personal on main inside the trusted plan's own HCL screen (PLAN-trusted-plan-cutover, rec-4637).

The trusted plan job refuses any PR whose Terraform fails scripts/ci/terraform_hcl_screen.py. If main itself
failed it, every later PR's plan would be refused. A PR that adds a resource type, data type, function or file()
path must therefore add it to the screen's allowlist in the same PR (that file is code-owned, so Benjamin reviews
it). Allowlist entries main does not yet use are allowed: a main-first allowlist PR lands before the HCL that needs
it. Only the pure tree and lexer rules apply here; the overlay's diff-based rules need a PR and a base.
"""

from __future__ import annotations

from scripts.checks import _common, registry
from scripts.ci import terraform_hcl_screen

ROOT_NAME = "terraform/personal"


@registry.register("validate_trusted_plan_screen", owner="platform")
def validate_trusted_plan_screen(failed: list[str]) -> None:
    """Assert terraform/personal passes the trusted plan's static screen. See module docstring."""
    print("\n=== trusted-plan screen over terraform/personal ===")
    root = _common.ROOT / ROOT_NAME
    if not root.is_dir():
        print(f"  FAIL: {ROOT_NAME} is missing")
        failed.append(f"trusted-plan-screen: {ROOT_NAME} is missing, so nothing was screened")
        registry.examined(0, unit="terraform_files")
        return
    files, read_findings = terraform_hcl_screen.read_root(root)
    findings = sorted(set(read_findings + terraform_hcl_screen.screen_tree(files)), key=lambda f: (f[1], f[2], f[0]))
    for rule, name, line in findings:
        print(f"  FAIL: {name}:{line} {rule}")
        failed.append(f"trusted-plan-screen: {name}:{line} {rule} -- extend the screen's allowlist in the same PR")
    registry.examined(len(files), unit="terraform_files")
    if not findings:
        print(f"  PASS: {len(files)} file(s) screened clean")
