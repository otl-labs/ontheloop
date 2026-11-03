"""Entry literals for the deps domain's registered checks (Decision 169, amends Decision 104).

Bare string-literal module=/attr= pairs only -- see docs/contracts/check-manifest.yaml. Aggregated
by scripts/checks/registry.py; never imported by scripts/validate.py directly.
"""

from __future__ import annotations

from scripts.checks._schema import Entry

ENTRIES: tuple[Entry, ...] = (
    Entry(
        name="validate_requirements",
        module="scripts.checks.deps.validate_requirements",
        attr="validate_requirements",
        full_segment="full_after_dependency_health",
    ),
    Entry(
        name="validate_import_contracts",
        module="scripts.checks.deps.validate_import_contracts",
        attr="validate_import_contracts",
        pre=True,
        full_segment="full_after_lint",
    ),
    Entry(
        name="validate_lockfile_sync",
        module="scripts.checks.deps.validate_lockfile_sync",
        attr="validate_lockfile_sync",
        pre=True,
        full_segment="full_after_lint",
    ),
    Entry(
        name="validate_dependency_graph_freshness",
        module="scripts.checks.deps.validate_dependency_graph_freshness",
        attr="validate_dependency_graph_freshness",
        full_segment="full_after_lint",
    ),
    Entry(
        name="validate_check_manifests",
        module="scripts.checks.deps.validate_check_manifests",
        attr="validate_check_manifests",
        pre=True,
        pre_globs=(
            "scripts/checks/**",
            "docs/contracts/check-manifest.yaml",
            "scripts/dependency_graph.py",
            "scripts/extract_imports.py",
            "scripts/lambda_manifest.py",
        ),
        full_segment="full_after_lint",
    ),
    Entry(
        name="validate_pre_glob_closure",
        module="scripts.checks.deps.validate_pre_glob_closure",
        attr="validate_pre_glob_closure",
        pre=True,
        # This check's own Entry glob MUST cover its own import closure over the FULL union of
        # tracked .py files -- registry.py's fixed-point raise (Decision 187 point 6 form) refuses
        # to assemble pre_sequence() otherwise. Never narrow this below ("**/*.py",
        # "scripts/checks/**") -- see registry._assert_pre_glob_closure_gate_intact.
        pre_globs=("**/*.py", "scripts/checks/**"),
        full_segment="full_after_lint",
    ),
    Entry(
        name="validate_presubmit_tool_pin_escalation",
        module="scripts.checks.deps.validate_presubmit_tool_pin_escalation",
        attr="validate_presubmit_tool_pin_escalation",
        pre=True,
        # This check imports scripts.checks._scaffolding in full, so it inherits _scaffolding's
        # OWN transitive closure (_pytest_diff/_terraform pull in scripts.executor/scripts.llm/
        # scripts.verifiers/etc.) -- identical to validate_hermeticity_flags's Entry
        # (scripts/checks/verification/_manifest.py), the only other check importing the same
        # module. docs/contracts/presubmit-tool-pin-escalation.yaml is the runtime YAML read, not
        # an import, but is still a gate-relevant input (validate_check_manifests precedent: its
        # own Entry globs docs/contracts/check-manifest.yaml the same way).
        pre_globs=(
            "scripts/telemetry_capture/status_line.py",
            "src/row_rules/**",
            "scripts/contract_rules.py",
            "src/turn_capture/markers.py",
            "scripts/checks/deps/**",
            "scripts/checks/_pytest_diff.py",
            "scripts/checks/_scaffolding.py",
            "scripts/checks/_common.py",
            "scripts/checks/registry.py",
            "scripts/checks/_budget_recs.py",
            "scripts/checks/_terraform.py",
            "scripts/checks/validation_result.py",
            "scripts/checks/iam_tf/validate_terraform_try.py",
            "scripts/decisions_md.py",
            "scripts/aws_profile.py",
            "scripts/executor/**",
            "scripts/llm/**",
            "scripts/ops_data_portal.py",
            "scripts/ops_portal/**",
            "scripts/checks/contracts/_glossary_words.py",
            "scripts/rec_episode.py",
            "scripts/s3_log_store.py",
            "scripts/sync/ops.py",
            "scripts/verifiers/**",
            "src/common/**",
            "docs/contracts/presubmit-tool-pin-escalation.yaml",
        ),
        full_segment="full_after_lint",
    ),
    Entry(
        name="validate_hermetic_fast_tier_collectability",
        module="scripts.checks.deps.validate_hermetic_fast_tier_collectability",
        attr="validate_hermetic_fast_tier_collectability",
        pre=True,
        pre_globs=(
            "docs/plans/**",
            "tests/**/conftest.py",
            "requirements.in",
            "requirements-fast.txt",
            "requirements-dev.txt",
            "scripts/checks/deps/validate_hermetic_fast_tier_collectability.py",
            "scripts/checks/_pytest_diff.py",
            "scripts/checks/_common.py",
            "scripts/checks/registry.py",
        ),
        full_segment="full_after_lint",
    ),
)
