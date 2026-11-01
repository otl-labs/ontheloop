"""Entry literals for the misc domain's registered checks (Decision 169, amends Decision 104).

Bare string-literal module=/attr= pairs only -- see docs/contracts/check-manifest.yaml. Aggregated
by scripts/checks/registry.py; never imported by scripts/validate.py directly.
"""

from __future__ import annotations

from scripts.checks._schema import Entry

ENTRIES: tuple[Entry, ...] = (
    Entry(
        name="validate_invariants",
        module="scripts.checks.misc.validate_invariants",
        attr="validate_invariants",
        pre=True,
        pre_globs=("scripts/**", "tests/test_execute_recommendation.py"),
        full_segment="full_after_lint",
    ),
    Entry(
        name="validate_test_coverage",
        module="scripts.checks.misc.validate_test_coverage",
        attr="validate_test_coverage",
        full_segment="full_after_lint",
    ),
    Entry(
        name="validate_coverage_baseline_edits",
        module="scripts.checks.misc.coverage_baseline",
        attr="validate_coverage_baseline_edits",
        pre=True,
        full_segment="full_after_lint",
    ),
    Entry(
        name="validate_scheduled_agent_logs",
        module="scripts.checks.misc.validate_scheduled_agent_logs",
        attr="validate_scheduled_agent_logs",
        pre=True,
        pre_globs=(
            "logs/**",
            "scripts/checks/misc/**",
            "scripts/checks/_common.py",
            "scripts/checks/registry.py",
        ),
        full_segment="full_after_lint",
    ),
    Entry(
        name="validate_ghas_probe",
        module="scripts.checks.misc.validate_ghas_probe",
        attr="validate_ghas_probe",
        full_segment="full_after_lint",
    ),
    Entry(
        name="validate_ducklake_version_lockstep",
        module="scripts.checks.misc.validate_ducklake_version_lockstep",
        attr="validate_ducklake_version_lockstep",
        pre=True,
        full_segment="full_after_lint",
    ),
    # PLAN-premerge-diff-coverage-gate: pre-tier-only, deliberately NO full_segment. `misc` sits
    # in full_after_lint, which the full-tier skeleton runs BEFORE the unit_tests scaffold -- a
    # full-tier leg here would dispatch before any coverage artifact exists and be permanently
    # skipped (the silent-vacuity shape this plan exists to avoid). `misc` is also absent from
    # every later segment's _FULL_SEGMENT_DOMAIN_ORDER tuple, so registering one there would fail
    # OD-0. Precedent for this exact shape: validate_sloc_budget_raises, validate_prose_budget_
    # raises (both pre=True, no full_segment) -- NOT validate_terraform_try, which is unsequenced
    # (pre=False, no full_segment) and would never dispatch at all, and NOT validate_vp_replay,
    # which DOES carry full_segment="full_after_lint" despite living in this same tier shape.
    Entry(
        name="validate_diff_coverage",
        module="scripts.checks.misc.validate_diff_coverage",
        attr="validate_diff_coverage",
        pre=True,
    ),
    # PLAN-sensitive-value-detectors: both run every tier (a few hundred regex calls), no pre_globs.
    Entry(
        name="validate_detector_ratchet",
        module="scripts.checks.misc.sensitive_value_detectors",
        attr="validate_detector_ratchet",
        pre=True,
        full_segment="full_after_lint",
    ),
    Entry(
        name="validate_detector_registry_edits",
        module="scripts.checks.misc.sensitive_value_detectors",
        attr="validate_detector_registry_edits",
        pre=True,
        full_segment="full_after_lint",
    ),
)
