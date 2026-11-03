"""CI refresh-read + write coverage gate (facade -- Decision 128 decomposition, Decision 144 / T2.48).

This module is a thin facade/orchestrator over two cohesive submodules (each < 500 SLOC, Decision 128
decompose-by-default, not raise):
  - _read_coverage: the READ classification maps + shared HCL parse/scan/match primitives + the
    per-resource read-coverage assertion (extracted byte-equivalent from the pre-decomposition module).
  - _write_coverage: the WRITE-coverage map (managed resource type -> required write verbs + prefix)
    + the assertion that github_ci_apply's inline policy write-covers every apply-role-written type
    (c5, DEP-01; closes the read-covered-but-write-missing recurrence rec-2703/rec-2757).

The registered check name (validate_ci_refresh_read_coverage) and its registry entry are UNCHANGED.
The private parse/resolve helpers are re-exported here so their existing branch-level tests (which
import them from this module path) keep passing unmodified.
"""

from __future__ import annotations

import re

from scripts.checks import _common, registry
from scripts.checks.iam_tf._read_coverage import (
    _BOOTSTRAP_DIR_REL,
    _PERSONAL_DIR_REL,
    ROLE_APPLY,
    _action_matches,  # noqa: F401 -- re-exported for tests
    _check_resource,
    _extract_bracket_block,  # noqa: F401 -- re-exported for tests
    _extract_capitalized_field,  # noqa: F401 -- re-exported for tests
    _literal_or_prefix_match,  # noqa: F401 -- re-exported for tests
    _parse_bootstrap_statements,
    _parse_managed_policy_statements,
    _read_root_text,
    _resolve_resource_name,  # noqa: F401 -- re-exported for tests
    _resolve_role_statements,
    _resolve_value,  # noqa: F401 -- re-exported for tests
    _resource_covered,  # noqa: F401 -- re-exported for tests
    _scan_resources,
    _split_top_level_objects,  # noqa: F401 -- re-exported for tests
)
from scripts.checks.iam_tf._secure_string_coverage import check_secure_string_write_coverage
from scripts.checks.iam_tf._write_companions import (
    check_identity_iam_actions_subset_of_boundary,
    check_lifecycle_companions,
)
from scripts.checks.iam_tf._write_coverage import check_write_coverage
from scripts.checks.iam_tf._write_symmetry import check_read_write_scope_parity, check_tag_untag_symmetry

_READS_ATTACHMENT_RE = re.compile(
    r'resource\s+"aws_iam_role_policy_attachment"\s+"\w+"\s*\{(?P<body>[^}]*)\}',
    re.DOTALL,
)


def _reads_policy_attached(bootstrap_text: str) -> bool:
    """True iff some attachment binds aws_iam_policy.github_ci_apply_reads to the apply role.

    Both halves are asserted: a bare `aws_iam_role_policy_attachment` substring proves nothing (it
    could bind any policy to any role), and an unattached managed policy is INERT -- the relocated
    read grants would silently stop applying while every static grep still found them in the file.
    """
    for m in _READS_ATTACHMENT_RE.finditer(bootstrap_text):
        body = m.group("body")
        if "aws_iam_policy.github_ci_apply_reads" in body and "aws_iam_role.github_ci_apply" in body:
            return True
    return False


@registry.register("validate_ci_refresh_read_coverage", owner="platform")
def validate_ci_refresh_read_coverage(failed: list[str]) -> None:
    """Whole-module refresh-read + write coverage gate (rec-2702 anti-recurrence + Decision 144 c5).

    READ half (unchanged behaviour; T2.49 / DEP-12 collapsed the role identity 3->2): every
    grant-requiring resource across terraform/personal/*.tf must be refresh-read-covered in
    BOTH plan-capable role policies -- github_ci_apply (terraform/bootstrap/github_ci_apply.tf)
    + github_ci_planner, the merged plan+drift role (terraform/personal/oidc.tf). A resource of
    a type this module does not classify FAILS LOUD.

    WRITE half (Decision 144 / T2.48 c5, DEP-01): github_ci_apply's inline policy must WRITE-cover
    every apply-role-written managed type (aws_lambda_function / aws_cloudwatch_log_group /
    aws_cloudwatch_metric_alarm / aws_cloudwatch_event_rule / aws_iam_role). A write-managed type
    with no covering write grant FAILS LOUD -- the read-covered-but-write-missing recurrence
    (rec-2703/rec-2757) the enumerated model kept reproducing.

    Credential-free (pure text parsing, no boto3/terraform invocation) -- eligible for --pre and
    full tiers. Test isolation: patch `scripts.checks._common.ROOT` (both paths are computed from
    it at call time), mirroring validate_invoke_implies_resolve's convention.
    """
    print("\n=== CI refresh-read + write coverage gate (rec-2702 + Decision 144 c5) ===")
    key = "ci-refresh-read-coverage:"

    personal_dir = _common.ROOT / _PERSONAL_DIR_REL
    bootstrap_dir = _common.ROOT / _BOOTSTRAP_DIR_REL

    bootstrap_text = _read_root_text(bootstrap_dir)
    if not bootstrap_text:
        failed.append(f"{key} cannot read any *.tf file under {bootstrap_dir}")
        print(f"  FAIL: cannot read bootstrap HCL under {bootstrap_dir}")
        return
    personal_text = _read_root_text(personal_dir)
    if not personal_text:
        failed.append(f"{key} cannot read any *.tf file under {personal_dir}")
        print(f"  FAIL: cannot read terraform/personal HCL under {personal_dir}")
        return

    inline_statements = _parse_bootstrap_statements(bootstrap_text, "github_ci_apply")
    if not inline_statements:
        failed.append(f"{key} no statements parsed from the github_ci_apply policy under {bootstrap_dir}")
        print("  FAIL: could not parse the apply role's inline policy statements -- has the HCL shape changed?")
        return

    # Policy-architecture split: the apply role's EFFECTIVE grant surface is the UNION of its inline
    # identity policy and the attached customer-managed reads policy (the 11 read-only Sids relocated
    # to buy inline bytes). Every downstream assertion must see the union, or relocating a read grant
    # would read as deleting it. The parser returns [] when the reads policy is absent, so the
    # synthetic single-inline-policy fixtures stay green unmodified.
    reads_statements = _parse_managed_policy_statements(bootstrap_text, "github_ci_apply_reads")
    if reads_statements and not _reads_policy_attached(bootstrap_text):
        failed.append(
            f"{key} the github_ci_apply_reads policy is declared but no aws_iam_role_policy_attachment "
            f"binds it to aws_iam_role.github_ci_apply -- its grants would be inert"
        )
        print("  FAIL: reads policy declared with no attachment binding it to the role.")
    apply_statements = inline_statements + reads_statements
    if reads_statements:
        print(
            f"  reads-policy split: {len(inline_statements)} inline + {len(reads_statements)} managed "
            f"= {len(apply_statements)} effective statements"
        )

    planner_statements = _resolve_role_statements(personal_text)
    if planner_statements is None:
        failed.append(f"{key} could not resolve github_ci_planner role policy under {personal_dir}")
        print("  FAIL: could not resolve the planner role policy document -- has the HCL shape changed?")
        return

    role_statements: dict[str, list[dict]] = {ROLE_APPLY: apply_statements, **planner_statements}

    resources, locals_map, attr_index = _scan_resources(personal_dir)
    if not resources:
        failed.append(f"{key} no terraform resources discovered under {personal_dir}")
        print("  FAIL: no terraform resources discovered -- has the module moved?")
        return

    checked = 0
    for rtype, rname, fname in resources:
        findings, was_checked = _check_resource(rtype, rname, fname, locals_map, attr_index, role_statements, key)
        failed.extend(findings)
        if was_checked:
            checked += 1
    registry.examined(checked, unit="grant_requiring_resources")

    # Design (a) discovery + (b) mandatory-declaration companions + (c) the two mechanical scope
    # rules. Each is a sibling sub-check orchestrated HERE, not nested inside another checker: the
    # facade is the single production entry point every guard must be reachable from (the PR #752
    # REVISE lesson -- a checker that is defined and unit-tested but never called is dead in --pre).
    write_types = check_write_coverage(apply_statements, resources, failed, key)
    check_secure_string_write_coverage(apply_statements, resources, locals_map, attr_index, failed, key)
    check_lifecycle_companions(apply_statements, failed, key)
    check_identity_iam_actions_subset_of_boundary(apply_statements, failed, key)
    check_tag_untag_symmetry(apply_statements, failed, key)
    check_read_write_scope_parity(apply_statements, failed, key)

    if not any(f.startswith(key) for f in failed):
        print(
            f"  PASS: all {checked} grant-requiring resources are refresh-read-covered in apply/planner, "
            f"and github_ci_apply write-covers all {write_types} apply-role-written managed types."
        )


if __name__ == "__main__":  # pragma: no cover
    # Standalone entry point so `python -m scripts.checks.iam_tf.validate_ci_refresh_read_coverage`
    # actually exercises the check (exit 1 on any finding), mirroring validate_ghas_probe's runner.
    _failed: list[str] = []
    validate_ci_refresh_read_coverage(_failed)
    for _f in _failed:
        print(f"  - {_f}")
    raise SystemExit(1 if _failed else 0)
