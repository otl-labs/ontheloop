"""Entry literals for the ops_governance domain's registered checks (Decision 169, amends Decision 104).

Bare string-literal module=/attr= pairs only -- see docs/contracts/check-manifest.yaml. Aggregated
by scripts/checks/registry.py; never imported by scripts/validate.py directly.

Every gated Entry's pre_globs must cover the check's whole transitive first-party import closure --
including the hops taken by DEFERRED (function-scope) imports that the check body always executes,
which is how validate_reconcile_pending_gate reaches scripts/contracts_schema.py
(_ops_table_ids -> schema_to_field_semantics.generate -> scripts.contracts -> the Pydantic models
load_contract validates against). validate_acceptance_literals' "scripts/**" already covers its
closure: acceptance_lint's src/common tail hangs off _check_acceptance_on_main (the executor
runtime path), not off lint_acceptance_command. Gated entries also carry
scripts/checks/_common.py and scripts/checks/registry.py, imported at module scope by every check
and called at run time; the rest of the scripts/checks/ spine (_schema.py, sibling domains'
_manifest.py, the package __init__) is deliberately NOT globbed, because a break there fails
registry -> scripts/validate.py at IMPORT time and crashes the whole --pre run before any gate is
consulted.
"""

from __future__ import annotations

from scripts.checks._schema import Entry

ENTRIES: tuple[Entry, ...] = (
    Entry(
        name="validate_recommendations_schema",
        module="scripts.checks.ops_governance.validate_recommendations_schema",
        attr="validate_recommendations_schema",
        pre=True,
        pre_globs=(
            "scripts/telemetry_capture/status_line.py",
            "src/turn_capture/markers.py",
            "src/row_rules/**",
            "scripts/contract_rules.py",
            "logs/.recommendations-log.jsonl",
            "scripts/executor/**",
            "scripts/s3_log_store.py",
            "scripts/checks/ops_governance/**",
            "scripts/checks/_common.py",
            "scripts/checks/registry.py",
            "scripts/decisions_md.py",
            "scripts/aws_profile.py",
            "scripts/llm/**",
            "scripts/ops_data_portal.py",
            "scripts/ops_portal/**",
            "scripts/checks/contracts/_glossary_words.py",
            "scripts/sync/ops.py",
            "src/common/**",
        ),
        full_segment="full_after_lint",
    ),
    Entry(
        name="validate_rec_write_paths",
        module="scripts.checks.ops_governance.validate_rec_write_paths",
        attr="validate_rec_write_paths",
        pre=True,
        pre_globs=("scripts/**",),
        full_segment="full_after_lint",
    ),
    Entry(
        name="validate_decisions_local_writes",
        module="scripts.checks.ops_governance.validate_decisions_local_writes",
        attr="validate_decisions_local_writes",
        pre=True,
        pre_globs=("scripts/**",),
        full_segment="full_after_lint",
    ),
    Entry(
        name="validate_warehouse_write_sources",
        module="scripts.checks.ops_governance.validate_warehouse_write_sources",
        attr="validate_warehouse_write_sources",
        pre=True,
        pre_globs=("scripts/**", "src/**"),
        full_segment="full_after_lint",
    ),
    Entry(
        name="validate_recs_read_topology",
        module="scripts.checks.ops_governance.validate_recs_read_topology",
        attr="validate_recs_read_topology",
        pre=True,
        pre_globs=None,
        full_segment="full_after_lint",
    ),
    Entry(
        name="check_source_registry",
        module="scripts.checks.ops_governance.check_source_registry",
        attr="check_source_registry",
        pre=True,
        full_segment="full_after_lint",
    ),
    Entry(
        name="validate_pydantic_yaml_drift",
        module="scripts.checks.ops_governance.validate_pydantic_yaml_drift",
        attr="validate_pydantic_yaml_drift",
        # Promoted into --pre; the module docstring's "full presubmit only (not --pre)" line is
        # stale and THIS ENTRY is the authority. Left alone because the module is in the
        # check-accounting grandfather baseline, whose touch-it-fix-it rule turns any edit to it
        # into an examined()/skipped() adoption -- a separate change.
        pre=True,
        pre_globs=(
            "config/agent/data_quality/ops.yaml",
            "src/schemas/**",
            "scripts/checks/ops_governance/**",
            "scripts/checks/_common.py",
            "scripts/checks/registry.py",
        ),
        full_segment="full_after_lint",
    ),
    Entry(
        name="validate_dq_manifest_gate",
        module="scripts.checks.ops_governance.validate_dq_manifest_gate",
        attr="validate_dq_manifest_gate",
        pre=True,
        pre_globs=(
            "config/agent/data_quality/**",
            "scripts/checks/ops_governance/**",
            "scripts/checks/_common.py",
            "scripts/checks/registry.py",
        ),
        full_segment="full_after_lint",
    ),
    Entry(
        name="validate_rec_relevance_contract",
        module="scripts.checks.ops_governance.validate_rec_relevance_contract",
        attr="validate_rec_relevance_contract",
        pre=True,
        pre_globs=(
            "docs/contracts/recommendation-relevance.yaml",
            "scripts/rec_relevance.py",
            "scripts/checks/ops_governance/**",
            "scripts/checks/_common.py",
            "scripts/checks/registry.py",
        ),
        full_segment="full_after_lint",
    ),
    Entry(
        name="validate_field_semantics_drift",
        module="scripts.checks.ops_governance.validate_field_semantics_drift",
        attr="validate_field_semantics_drift",
        pre=True,
        full_segment="full_after_lint",
    ),
    Entry(
        name="validate_reconcile_pending_gate",
        module="scripts.checks.ops_governance.validate_reconcile_pending_gate",
        attr="validate_reconcile_pending_gate",
        pre=True,
        pre_globs=(
            "src/row_rules/**",
            "scripts/field_semantics_rule_projection.py",
            "docs/contracts/**",
            "config/lambda/ducklake/field_semantics.static.yaml",
            "src/schemas/**",
            "scripts/schema_to_field_semantics.py",
            "scripts/field_semantics_event_projection.py",
            "scripts/contract_rules.py",
            "src/telemetry/**",
            "scripts/contracts.py",
            "scripts/contracts_schema.py",
            "src/common/ducklake_partition_spec.py",
            "scripts/checks/ops_governance/**",
            "scripts/checks/_common.py",
            "scripts/checks/registry.py",
        ),
        full_segment="full_after_lint",
    ),
    Entry(
        name="validate_deploy_channel_conformance",
        module="scripts.checks.ops_governance.validate_deploy_channel_conformance",
        attr="validate_deploy_channel_conformance",
        pre=True,
        full_segment="full_after_lint",
    ),
    Entry(
        name="validate_reversal_stanzas",
        module="scripts.checks.ops_governance.validate_reversal_stanzas",
        attr="validate_reversal_stanzas",
        pre=True,
        full_segment="full_after_lint",
    ),
    Entry(
        name="validate_acceptance_literals",
        module="scripts.checks.ops_governance.validate_acceptance_literals",
        attr="validate_acceptance_literals",
        pre=True,
        pre_globs=("scripts/**", "src/common/**", "src/row_rules/**", "src/turn_capture/markers.py"),
        full_segment="full_after_lint",
    ),
)
