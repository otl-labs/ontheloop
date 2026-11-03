"""Tests for validate_admin_root_premerge_gate() -- rec-4140's acceptance file."""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts.checks import registry
from scripts.checks.verification.validate_admin_root_premerge_gate import (
    ENV_MARKERS,
    GATE_MARKERS,
    V3_PREFIX,
    validate_admin_root_premerge_gate,
)

_CONTRACT_SPANS = (
    "docs/contracts/deploy-paths.yaml",
    "admin_out_of_band",
    "pre_merge_live_plan",
)

_GATE_COMMAND = (
    'test -n "$PR_HEAD_SHA" && terraform -chdir="$W/terraform/bootstrap" plan -input=false -lock=false -var-file=x.tfvars'
)
_ENV_COMMAND = (
    "bin/venv-python -m scripts.aws_identity caller-arn --profile agent_platform_admin "
    "2>/dev/null | grep -q 'assumed-role/PlatformAdmin/' && echo ADMIN_CHAIN_OK || echo ADMIN_CHAIN_ABSENT"
)
_OLD_ENV_COMMAND = (
    "aws sts get-caller-identity --profile agent_platform_admin --query Arn --output text "
    "2>/dev/null | grep -q 'assumed-role/PlatformAdmin/' && echo ADMIN_CHAIN_OK || echo ADMIN_CHAIN_ABSENT"
)
_V3_LINE = "V3 resolution: the posted pre-merge gate evidence substitutes for post-deploy evidence."

_TAXONOMY = {
    "axis_a": {
        "bootstrap": {
            "code_vs_infra": "terraform/bootstrap/ only",
            "apply_gating": "admin-only (agent_platform_admin), NEVER auto-apply",
        },
        "sandbox": {
            "code_vs_infra": "same code path, mocked externals",
            "apply_gating": "auto-apply on push to main behind the deterministic guard",
        },
        "mock_vs_real_note": "a string note, not a mapping",
    }
}


def _write_yaml(path: Path, data: dict) -> Path:
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def _taxonomy(tmp_path: Path, data: dict | None = None) -> Path:
    return _write_yaml(tmp_path / "environment-taxonomy.yaml", data if data is not None else _TAXONOMY)


def _base_plan(**overrides) -> dict:
    data = {
        "schema_version": 5,
        "plan_type": "IMPLEMENTATION",
        "slug": "fixture-probe",
        "plan_path": "docs/plans/PLAN-fixture-probe.yaml",
        "scope": [
            {"file": "terraform/bootstrap/foo.tf", "action": "Modify", "purpose": "p"},
        ],
        "verification_plan": [
            {"step": 1, "phase": "pre-deploy", "command": _GATE_COMMAND},
            {"step": 2, "phase": "pre-deploy", "command": _ENV_COMMAND},
        ],
        "context": [_V3_LINE],
    }
    data.update(overrides)
    return data


def _write_plan(tmp_path: Path, data: dict, name: str = "PLAN-fixture-probe.yaml") -> Path:
    return _write_yaml(tmp_path / name, data)


class TestCompliantAndNonAdminPlans:
    def test_compliant_plan_passes(self, tmp_path: Path) -> None:
        taxonomy = _taxonomy(tmp_path)
        plan = _write_plan(tmp_path, _base_plan())
        failed: list[str] = []
        validate_admin_root_premerge_gate(failed, plan_paths=[plan], taxonomy_path=taxonomy)
        assert failed == []

    def test_plan_touching_only_terraform_personal_passes(self, tmp_path: Path) -> None:
        taxonomy = _taxonomy(tmp_path)
        data = _base_plan(scope=[{"file": "terraform/personal/foo.tf", "action": "Modify", "purpose": "p"}])
        plan = _write_plan(tmp_path, data)
        failed: list[str] = []
        validate_admin_root_premerge_gate(failed, plan_paths=[plan], taxonomy_path=taxonomy)
        assert failed == []

    def test_plan_touching_only_markdown_under_admin_root_passes(self, tmp_path: Path) -> None:
        taxonomy = _taxonomy(tmp_path)
        data = _base_plan(scope=[{"file": "terraform/bootstrap/CLAUDE.md", "action": "Modify", "purpose": "p"}])
        plan = _write_plan(tmp_path, data)
        failed: list[str] = []
        validate_admin_root_premerge_gate(failed, plan_paths=[plan], taxonomy_path=taxonomy)
        assert failed == []

    def test_non_markdown_non_tf_file_under_admin_root_is_in_scope(self, tmp_path: Path) -> None:
        """Fail-closed: any non-.md file under the root triggers, since Terraform can load any
        file via file()/templatefile()."""
        taxonomy = _taxonomy(tmp_path)
        data = _base_plan(
            scope=[{"file": "terraform/bootstrap/authority_budget.json", "action": "Modify", "purpose": "p"}],
            verification_plan=[],
            context=[],
        )
        plan = _write_plan(tmp_path, data)
        failed: list[str] = []
        validate_admin_root_premerge_gate(failed, plan_paths=[plan], taxonomy_path=taxonomy)
        assert len(failed) == 3

    def test_lock_file_under_admin_root_is_in_scope(self, tmp_path: Path) -> None:
        taxonomy = _taxonomy(tmp_path)
        data = _base_plan(
            scope=[{"file": "terraform/bootstrap/.terraform.lock.hcl", "action": "Modify", "purpose": "p"}],
            verification_plan=[],
            context=[],
        )
        plan = _write_plan(tmp_path, data)
        failed: list[str] = []
        validate_admin_root_premerge_gate(failed, plan_paths=[plan], taxonomy_path=taxonomy)
        assert len(failed) == 3


class TestRedCasesByClause:
    def test_no_pre_deploy_gate_step_flags_gate_clause(self, tmp_path: Path) -> None:
        taxonomy = _taxonomy(tmp_path)
        data = _base_plan(verification_plan=[{"step": 1, "phase": "pre-deploy", "command": _ENV_COMMAND}])
        plan = _write_plan(tmp_path, data)
        failed: list[str] = []
        validate_admin_root_premerge_gate(failed, plan_paths=[plan], taxonomy_path=taxonomy)
        assert len(failed) == 1 and "PRE-MERGE GATE (ADMIN)" in failed[0]

    def test_gate_step_tagged_post_deploy_flags_gate_clause(self, tmp_path: Path) -> None:
        taxonomy = _taxonomy(tmp_path)
        data = _base_plan(
            verification_plan=[
                {"step": 1, "phase": "post-deploy", "command": _GATE_COMMAND},
                {"step": 2, "phase": "pre-deploy", "command": _ENV_COMMAND},
            ]
        )
        plan = _write_plan(tmp_path, data)
        failed: list[str] = []
        validate_admin_root_premerge_gate(failed, plan_paths=[plan], taxonomy_path=taxonomy)
        assert len(failed) == 1 and "PRE-MERGE GATE (ADMIN)" in failed[0]

    def test_gate_command_missing_lock_false_flags_gate_clause(self, tmp_path: Path) -> None:
        taxonomy = _taxonomy(tmp_path)
        broken = _GATE_COMMAND.replace(" -lock=false", "")
        data = _base_plan(
            verification_plan=[
                {"step": 1, "phase": "pre-deploy", "command": broken},
                {"step": 2, "phase": "pre-deploy", "command": _ENV_COMMAND},
            ]
        )
        plan = _write_plan(tmp_path, data)
        failed: list[str] = []
        validate_admin_root_premerge_gate(failed, plan_paths=[plan], taxonomy_path=taxonomy)
        assert len(failed) == 1 and "PRE-MERGE GATE (ADMIN)" in failed[0]

    def test_gate_command_missing_pr_head_sha_flags_gate_clause(self, tmp_path: Path) -> None:
        taxonomy = _taxonomy(tmp_path)
        broken = _GATE_COMMAND.replace("PR_HEAD_SHA", "HEAD_SHA")
        data = _base_plan(
            verification_plan=[
                {"step": 1, "phase": "pre-deploy", "command": broken},
                {"step": 2, "phase": "pre-deploy", "command": _ENV_COMMAND},
            ]
        )
        plan = _write_plan(tmp_path, data)
        failed: list[str] = []
        validate_admin_root_premerge_gate(failed, plan_paths=[plan], taxonomy_path=taxonomy)
        assert len(failed) == 1 and "PRE-MERGE GATE (ADMIN)" in failed[0]

    def test_gate_command_naming_different_root_flags_gate_clause(self, tmp_path: Path) -> None:
        taxonomy = _taxonomy(tmp_path)
        broken = _GATE_COMMAND.replace("terraform/bootstrap", "terraform/personal")
        data = _base_plan(
            verification_plan=[
                {"step": 1, "phase": "pre-deploy", "command": broken},
                {"step": 2, "phase": "pre-deploy", "command": _ENV_COMMAND},
            ]
        )
        plan = _write_plan(tmp_path, data)
        failed: list[str] = []
        validate_admin_root_premerge_gate(failed, plan_paths=[plan], taxonomy_path=taxonomy)
        assert len(failed) == 1 and "PRE-MERGE GATE (ADMIN)" in failed[0]

    def test_no_environment_check_step_flags_env_clause(self, tmp_path: Path) -> None:
        taxonomy = _taxonomy(tmp_path)
        data = _base_plan(verification_plan=[{"step": 1, "phase": "pre-deploy", "command": _GATE_COMMAND}])
        plan = _write_plan(tmp_path, data)
        failed: list[str] = []
        validate_admin_root_premerge_gate(failed, plan_paths=[plan], taxonomy_path=taxonomy)
        assert len(failed) == 1 and "ENVIRONMENT CHECK" in failed[0]

    def test_caller_arn_without_admin_chain_ok_flags_env_clause(self, tmp_path: Path) -> None:
        """The caller-arn form appears, but not ADMIN_CHAIN_OK."""
        taxonomy = _taxonomy(tmp_path)
        partial_env = "bin/venv-python -m scripts.aws_identity caller-arn --profile agent_platform_admin"
        data = _base_plan(
            verification_plan=[
                {"step": 1, "phase": "pre-deploy", "command": _GATE_COMMAND},
                {"step": 2, "phase": "pre-deploy", "command": partial_env},
            ]
        )
        plan = _write_plan(tmp_path, data)
        failed: list[str] = []
        validate_admin_root_premerge_gate(failed, plan_paths=[plan], taxonomy_path=taxonomy)
        assert len(failed) == 1 and "ENVIRONMENT CHECK" in failed[0]

    def test_old_get_caller_identity_form_alone_is_flagged(self, tmp_path: Path) -> None:
        """The pre-proxy form (always ADMIN_CHAIN_ABSENT under the agent proxy) no longer satisfies the clause."""
        taxonomy = _taxonomy(tmp_path)
        data = _base_plan(
            verification_plan=[
                {"step": 1, "phase": "pre-deploy", "command": _GATE_COMMAND},
                {"step": 2, "phase": "pre-deploy", "command": _OLD_ENV_COMMAND},
            ]
        )
        plan = _write_plan(tmp_path, data)
        failed: list[str] = []
        validate_admin_root_premerge_gate(failed, plan_paths=[plan], taxonomy_path=taxonomy)
        assert len(failed) == 1 and "ENVIRONMENT CHECK" in failed[0]

    def test_no_v3_resolution_context_line_flags_v3_clause(self, tmp_path: Path) -> None:
        taxonomy = _taxonomy(tmp_path)
        data = _base_plan(context=["some other context line"])
        plan = _write_plan(tmp_path, data)
        failed: list[str] = []
        validate_admin_root_premerge_gate(failed, plan_paths=[plan], taxonomy_path=taxonomy)
        assert len(failed) == 1 and "V3 RESOLUTION" in failed[0]

    def test_all_three_clauses_missing_yields_three_findings(self, tmp_path: Path) -> None:
        taxonomy = _taxonomy(tmp_path)
        data = _base_plan(verification_plan=[], context=[])
        plan = _write_plan(tmp_path, data)
        failed: list[str] = []
        validate_admin_root_premerge_gate(failed, plan_paths=[plan], taxonomy_path=taxonomy)
        assert len(failed) == 3
        joined = " ".join(failed)
        assert "PRE-MERGE GATE (ADMIN)" in joined and "ENVIRONMENT CHECK" in joined and "V3 RESOLUTION" in joined


class TestFailClosed:
    def test_taxonomy_with_no_admin_only_row_fails(self, tmp_path: Path) -> None:
        taxonomy = _taxonomy(tmp_path, {"axis_a": {"sandbox": {"code_vs_infra": "x", "apply_gating": "auto-apply"}}})
        failed: list[str] = []
        validate_admin_root_premerge_gate(failed, plan_paths=[], taxonomy_path=taxonomy)
        assert failed and "admin-only" in failed[0]

    def test_unreadable_taxonomy_fails_with_no_plan_in_diff(self, tmp_path: Path) -> None:
        failed: list[str] = []
        validate_admin_root_premerge_gate(failed, plan_paths=[], taxonomy_path=tmp_path / "absent.yaml")
        assert failed and "could not read" in failed[0]

    def test_malformed_taxonomy_fails(self, tmp_path: Path) -> None:
        taxonomy = tmp_path / "bad-taxonomy.yaml"
        taxonomy.write_text("key: [unterminated", encoding="utf-8")
        failed: list[str] = []
        validate_admin_root_premerge_gate(failed, plan_paths=[], taxonomy_path=taxonomy)
        assert failed and "could not parse" in failed[0]


class TestDriftPin:
    """Every literal in GATE_MARKERS, ENV_MARKERS and V3_PREFIX appears in deploy-paths.yaml's
    pre_merge_live_plan rule text, whitespace-folded -- so the contract text and the validator's
    structural check cannot silently drift apart."""

    def test_module_markers_appear_in_the_live_contract(self) -> None:
        root = Path(__file__).resolve().parents[3]
        text = (root / "docs/contracts/deploy-paths.yaml").read_text(encoding="utf-8")
        folded = " ".join(text.split())
        for literal in (*GATE_MARKERS, *ENV_MARKERS, V3_PREFIX):
            assert " ".join(literal.split()) in folded, f"{literal!r} missing from deploy-paths.yaml"


class TestRealTaxonomy:
    def test_real_taxonomy_derives_bootstrap_root(self) -> None:
        root = Path(__file__).resolve().parents[3]
        from scripts.checks.verification.validate_admin_root_premerge_gate import (
            _ROOT_RE,
            _derive_admin_only_roots,
        )

        roots, error = _derive_admin_only_roots(root / "docs/contracts/environment-taxonomy.yaml")
        assert error is None
        assert "terraform/bootstrap" in roots
        for derived_root in roots:
            assert _ROOT_RE.fullmatch(derived_root)


class TestMalformedShapes:
    """Defensive branches the structural checks never raise on: a malformed taxonomy, a
    malformed plan, and malformed scope/verification_plan rows -- each reported as a finding
    (or treated as absent) rather than propagating an exception."""

    def test_taxonomy_not_a_mapping_fails(self, tmp_path: Path) -> None:
        taxonomy = tmp_path / "environment-taxonomy.yaml"
        taxonomy.write_text(yaml.safe_dump(["not", "a", "mapping"]), encoding="utf-8")
        failed: list[str] = []
        validate_admin_root_premerge_gate(failed, plan_paths=[], taxonomy_path=taxonomy)
        assert failed and "is not a YAML mapping" in failed[0]

    def test_taxonomy_missing_axis_a_fails(self, tmp_path: Path) -> None:
        taxonomy = tmp_path / "environment-taxonomy.yaml"
        taxonomy.write_text(yaml.safe_dump({"other_key": 1}), encoding="utf-8")
        failed: list[str] = []
        validate_admin_root_premerge_gate(failed, plan_paths=[], taxonomy_path=taxonomy)
        assert failed and "missing an 'axis_a' mapping" in failed[0]

    def test_axis_a_row_with_non_string_code_vs_infra_is_ignored(self, tmp_path: Path) -> None:
        """An admin-only row whose code_vs_infra isn't a string is skipped, not crashed on; if
        it's the only row, the taxonomy still fails closed (no derivable root)."""
        taxonomy = _taxonomy(
            tmp_path,
            {
                "axis_a": {
                    "bootstrap": {"code_vs_infra": 12345, "apply_gating": "admin-only (x)"},
                }
            },
        )
        failed: list[str] = []
        validate_admin_root_premerge_gate(failed, plan_paths=[], taxonomy_path=taxonomy)
        assert failed and "no admin-only row with a derivable terraform root" in failed[0]

    def test_scope_not_a_list_yields_no_in_scope_files(self, tmp_path: Path) -> None:
        taxonomy = _taxonomy(tmp_path)
        data = _base_plan(scope="not-a-list")
        plan = _write_plan(tmp_path, data)
        failed: list[str] = []
        validate_admin_root_premerge_gate(failed, plan_paths=[plan], taxonomy_path=taxonomy)
        assert failed == []

    def test_scope_row_not_a_dict_and_missing_file_are_skipped(self, tmp_path: Path) -> None:
        taxonomy = _taxonomy(tmp_path)
        data = _base_plan(
            scope=[
                "not-a-dict",
                {"action": "Modify", "purpose": "p"},
                {"file": "terraform/bootstrap/foo.tf", "action": "Modify", "purpose": "p"},
            ]
        )
        plan = _write_plan(tmp_path, data)
        failed: list[str] = []
        validate_admin_root_premerge_gate(failed, plan_paths=[plan], taxonomy_path=taxonomy)
        assert failed == []

    def test_plan_with_malformed_yaml_reported_never_raises(self, tmp_path: Path) -> None:
        taxonomy = _taxonomy(tmp_path)
        plan = tmp_path / "PLAN-bad.yaml"
        plan.write_text("key: [unterminated", encoding="utf-8")
        failed: list[str] = []
        validate_admin_root_premerge_gate(failed, plan_paths=[plan], taxonomy_path=taxonomy)
        assert failed and "could not parse" in failed[0]

    def test_plan_not_a_mapping_reported_never_raises(self, tmp_path: Path) -> None:
        taxonomy = _taxonomy(tmp_path)
        plan = tmp_path / "PLAN-list.yaml"
        plan.write_text(yaml.safe_dump(["not", "a", "mapping"]), encoding="utf-8")
        failed: list[str] = []
        validate_admin_root_premerge_gate(failed, plan_paths=[plan], taxonomy_path=taxonomy)
        assert failed and "is not a YAML mapping" in failed[0]

    def test_gate_step_with_non_string_command_is_ignored(self, tmp_path: Path) -> None:
        taxonomy = _taxonomy(tmp_path)
        data = _base_plan(
            verification_plan=[
                {"step": 1, "phase": "pre-deploy", "command": 12345},
                {"step": 2, "phase": "pre-deploy", "command": _ENV_COMMAND},
            ]
        )
        plan = _write_plan(tmp_path, data)
        failed: list[str] = []
        validate_admin_root_premerge_gate(failed, plan_paths=[plan], taxonomy_path=taxonomy)
        assert len(failed) == 1 and "PRE-MERGE GATE (ADMIN)" in failed[0]

    def test_env_check_step_with_non_string_command_is_ignored(self, tmp_path: Path) -> None:
        taxonomy = _taxonomy(tmp_path)
        data = _base_plan(
            verification_plan=[
                {"step": 1, "phase": "pre-deploy", "command": _GATE_COMMAND},
                {"step": 2, "phase": "pre-deploy", "command": 12345},
            ]
        )
        plan = _write_plan(tmp_path, data)
        failed: list[str] = []
        validate_admin_root_premerge_gate(failed, plan_paths=[plan], taxonomy_path=taxonomy)
        assert len(failed) == 1 and "ENVIRONMENT CHECK" in failed[0]


class TestDispatchDefaults:
    def test_registered_and_sequenced_in_pre_tier(self) -> None:
        registry.resolve("validate_admin_root_premerge_gate")
        pre = {step.name for step in registry.pre_sequence()}
        assert "validate_admin_root_premerge_gate" in pre

    def test_absent_plan_file_reported_never_raises(self, tmp_path: Path) -> None:
        taxonomy = _taxonomy(tmp_path)
        failed: list[str] = []
        validate_admin_root_premerge_gate(failed, plan_paths=[tmp_path / "PLAN-absent.yaml"], taxonomy_path=taxonomy)
        assert failed and "could not read" in failed[0]

    def test_no_plans_in_diff_passes(self, tmp_path: Path) -> None:
        taxonomy = _taxonomy(tmp_path)
        failed: list[str] = []
        validate_admin_root_premerge_gate(failed, plan_paths=[], taxonomy_path=taxonomy)
        assert failed == []
