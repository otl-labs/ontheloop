"""Tests for scripts/checks/iam_tf/validate_planner_secret_value_reads.py (plan neon-key-out-of-pr-plans).

Each fixture copies the live files the check reads into a temp tree and breaks exactly one property.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.checks import registry
from scripts.checks.iam_tf.validate_planner_secret_value_reads import _logical_commands, validate_planner_secret_value_reads

REPO = Path(__file__).resolve().parents[3]
_FILES = [
    "terraform/personal/oidc.tf",
    "terraform/personal/oidc_pipeline_roles.tf",
    "terraform/personal/neon_ducklake_catalog.tf",
    ".github/workflows/terraform-apply-sandbox.yml",
    ".github/workflows/reconcile.yml",
    ".github/workflows/terraform-drift.yml",
    ".github/workflows/terraform-plan-trusted.yml",
    ".github/actions/terraform-init-retry/action.yml",
    ".github/actions/terraform-init-retry/neon_api_key_check.sh",
]
_TFVARS_ARN = "arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:agent-platform-terraform-personal-tfvars-*"
_PLANNER_ANCHOR = 'data "aws_iam_policy_document" "github_ci_planner" {\n'


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    for rel in _FILES:
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / rel, dest)
    return tmp_path


def _run(root: Path) -> list[str]:
    failed: list[str] = []
    with patch("scripts.checks._common.ROOT", root):
        validate_planner_secret_value_reads(failed)
    return failed


def _edit(root: Path, rel: str, old: str, new: str) -> None:
    path = root / rel
    text = path.read_text(encoding="utf-8")
    assert old in text, (rel, old)
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _append_planner_statement(root: Path, statement: str) -> None:
    _edit(root, "terraform/personal/oidc_pipeline_roles.tf", _PLANNER_ANCHOR, _PLANNER_ANCHOR + statement)


def test_live_tree_passes() -> None:
    failed: list[str] = []
    validate_planner_secret_value_reads(failed)
    assert failed == []


def test_synthetic_copy_passes(tree: Path) -> None:
    assert _run(tree) == []


def test_unconditioned_value_read_fails(tree: Path) -> None:
    _append_planner_statement(
        tree,
        '  statement {\n    sid = "X"\n    effect = "Allow"\n    actions = ["secretsmanager:GetSecretValue"]\n'
        '    resources = ["arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:other-*"]\n  }\n',
    )
    assert any("unconditioned Secrets Manager value read" in f for f in _run(tree))


def test_get_star_on_inference_keys_fails(tree: Path) -> None:
    _append_planner_statement(
        tree,
        '  statement {\n    sid = "Inference"\n    effect = "Allow"\n'
        '    actions = ["secretsmanager:Describe*", "secretsmanager:Get*"]\n'
        '    resources = ["arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:'
        'secret:agent-platform-anthropic-api-key-*"]\n  }\n',
    )
    assert any("Inference" in f for f in _run(tree))


def test_lowercase_action_fails(tree: Path) -> None:
    _append_planner_statement(
        tree,
        '  statement {\n    sid = "Lower"\n    effect = "Allow"\n    actions = ["secretsmanager:getsecretvalue"]\n'
        '    resources = ["arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:other-*"]\n  }\n',
    )
    assert any("Lower" in f for f in _run(tree))


def test_not_action_statement_fails(tree: Path) -> None:
    _append_planner_statement(
        tree,
        '  statement {\n    sid = "NotAct"\n    effect = "Allow"\n    not_actions = ["iam:*"]\n    resources = ["*"]\n  }\n',
    )
    assert any("NotAct" in f for f in _run(tree))


def test_condition_naming_a_different_session_fails(tree: Path) -> None:
    _append_planner_statement(
        tree,
        '  statement {\n    sid = "Other"\n    effect = "Allow"\n    actions = ["secretsmanager:GetSecretValue"]\n'
        '    resources = ["arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:other-*"]\n'
        '    condition {\n      test = "StringLike"\n      variable = "aws:userid"\n'
        '      values = ["*:terraform-plan-pr"]\n    }\n  }\n',
    )
    assert any("Other" in f for f in _run(tree))


def test_tfvars_secret_stays_allowed_unconditioned(tree: Path) -> None:
    assert _TFVARS_ARN in (tree / "terraform/personal/oidc.tf").read_text(encoding="utf-8")
    assert _run(tree) == []


def test_second_planner_role_policy_fails(tree: Path) -> None:
    path = tree / "terraform/personal/oidc_pipeline_roles.tf"
    path.write_text(
        path.read_text(encoding="utf-8")
        + '\nresource "aws_iam_role_policy" "extra" {\n  name = "x"\n  role = aws_iam_role.github_ci_planner.id\n'
        "  policy = data.aws_iam_policy_document.github_ci_planner.json\n}\n",
        encoding="utf-8",
    )
    assert any("exactly one aws_iam_role_policy" in f for f in _run(tree))


def test_policy_attachment_fails(tree: Path) -> None:
    path = tree / "terraform/personal/oidc_pipeline_roles.tf"
    path.write_text(
        path.read_text(encoding="utf-8")
        + '\nresource "aws_iam_role_policy_attachment" "extra" {\n  role = aws_iam_role.github_ci_planner.name\n'
        '  policy_arn = "arn:aws:iam::aws:policy/SecretsManagerReadWrite"\n}\n',
        encoding="utf-8",
    )
    assert any("exactly one aws_iam_role_policy" in f for f in _run(tree))


def test_reserved_name_in_second_workflow_fails(tree: Path) -> None:
    path = tree / ".github/workflows/reconcile.yml"
    path.write_text(
        path.read_text(encoding="utf-8") + "\n# role-session-name: tf-drift-convergence-writer\n", encoding="utf-8"
    )
    assert any("reserved session name" in f for f in _run(tree))


def test_refresh_false_on_drift_plan_fails(tree: Path) -> None:
    _edit(
        tree,
        ".github/workflows/terraform-drift.yml",
        "run -- terraform plan -detailed-exitcode",
        "run -- terraform plan -refresh=false -detailed-exitcode",
    )
    assert any("-refresh=false on a personal-root plan" in f for f in _run(tree))


def test_refresh_false_on_trusted_plan_fails(tree: Path) -> None:
    _edit(
        tree,
        ".github/workflows/terraform-plan-trusted.yml",
        "terraform plan -lock=false",
        "terraform plan -refresh=false -lock=false",
    )
    assert any("-refresh=false on a personal-root plan" in f for f in _run(tree))


def test_trusted_plan_without_the_neon_wrapper_fails(tree: Path) -> None:
    _edit(
        tree,
        ".github/workflows/terraform-plan-trusted.yml",
        'bash "$GITHUB_WORKSPACE/scripts/ci/neon_api_key.sh" run --\n',
        "",
    )
    assert any("not run through" in f for f in _run(tree))


def test_dispatch_plan_without_run_prefix_fails(tree: Path) -> None:
    _edit(
        tree,
        ".github/workflows/terraform-apply-sandbox.yml",
        'bash "$GITHUB_WORKSPACE/scripts/ci/neon_api_key.sh" run -- terraform plan',
        "terraform plan",
    )
    assert any("not run through" in f for f in _run(tree))


def test_apply_without_run_prefix_fails(tree: Path) -> None:
    _edit(
        tree,
        ".github/workflows/reconcile.yml",
        'run: bash "$GITHUB_WORKSPACE/.dispatch-tooling/scripts/ci/neon_api_key.sh" run -- terraform apply',
        "run: terraform apply",
    )
    assert any("not run through" in f for f in _run(tree))


def test_run_wrapped_job_without_init_retry_fails(tree: Path) -> None:
    path = tree / ".github/workflows/terraform-drift.yml"
    text = path.read_text(encoding="utf-8")
    old = "        uses: ./.github/actions/terraform-init-retry\n"
    assert old in text
    path.write_text(text.replace(old, "        run: echo no-init\n", 1), encoding="utf-8")
    assert any("does not use terraform-init-retry" in f for f in _run(tree))


def test_init_retry_without_check_step_fails(tree: Path) -> None:
    path = tree / ".github/actions/terraform-init-retry/action.yml"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("neon_api_key_check.sh", "other.sh"), encoding="utf-8")
    assert any("no neon_api_key_check.sh step" in f for f in _run(tree))


def test_delegate_not_exec_check_fails(tree: Path) -> None:
    (tree / ".github/actions/terraform-init-retry/neon_api_key_check.sh").write_text(
        "#!/usr/bin/env bash\nexit 0\n", encoding="utf-8"
    )
    assert any("does not exec" in f for f in _run(tree))


def test_secret_version_data_source_fails(tree: Path) -> None:
    path = tree / "terraform/personal/neon_ducklake_catalog.tf"
    path.write_text(
        path.read_text(encoding="utf-8")
        + '\ndata "aws_secretsmanager_secret_version" "neon_api_key" {\n  secret_id = "neon-api-key"\n}\n',
        encoding="utf-8",
    )
    assert any("aws_secretsmanager_secret_version" in f for f in _run(tree))


def test_drift_value_read_statement_missing_fails(tree: Path) -> None:
    path = tree / "terraform/personal/oidc_pipeline_roles.tf"
    text = path.read_text(encoding="utf-8")
    start = text.index('    sid     = "DriftSessionSecretValueRead"')
    start = text.rindex("  statement {", 0, start)
    end = text.index("\n  }\n", start) + len("\n  }\n")
    path.write_text(text[:start] + text[end:], encoding="utf-8")
    assert any("expected exactly one" in f for f in _run(tree))


def test_drift_value_read_covering_only_one_arn_fails(tree: Path) -> None:
    _edit(
        tree,
        "terraform/personal/oidc_pipeline_roles.tf",
        '      "arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:'
        'secret:ducklake-neon-catalog-dsn-*",\n    ]\n    condition',
        "    ]\n    condition",
    )
    assert any("expected exactly one" in f for f in _run(tree))


def test_registered_in_pre_and_full_sequence() -> None:
    pre = {step.name for step in registry.pre_sequence() if step.kind == "check"}
    full = {step.name for step in registry.full_sequence() if step.kind == "check"}
    assert "validate_planner_secret_value_reads" in pre
    assert "validate_planner_secret_value_reads" in full


def test_planner_policy_mapping_missing_fails(tree: Path) -> None:
    _edit(
        tree,
        "terraform/personal/oidc_pipeline_roles.tf",
        'resource "aws_iam_role_policy" "github_ci_planner"',
        'resource "aws_iam_role_policy" "renamed"',
    )
    assert any("could not resolve" in f for f in _run(tree))


def test_unresolvable_source_document_fails(tree: Path) -> None:
    _edit(
        tree,
        "terraform/personal/oidc_pipeline_roles.tf",
        "data.aws_iam_policy_document.ci_full_refresh_read.json",
        "data.aws_iam_policy_document.nope.json",
    )
    assert any("unresolvable policy document" in f for f in _run(tree))


def test_missing_workflow_fails(tree: Path) -> None:
    (tree / ".github/workflows/reconcile.yml").unlink()
    assert any("reconcile.yml is missing" in f for f in _run(tree))


def test_no_terraform_files_fails(tmp_path: Path) -> None:
    (tmp_path / "terraform/personal").mkdir(parents=True)
    assert any("cannot read any *.tf" in f for f in _run(tmp_path))


def test_missing_session_local_fails(tree: Path) -> None:
    _edit(tree, "terraform/personal/oidc.tf", "convergence_writer_session_name = ", "renamed_local = ")
    assert any("convergence_writer_session_name not found" in f for f in _run(tree))


def test_logical_commands_joins_continuations_and_keeps_trailing_buffer() -> None:
    got = [" ".join(c.split()) for c in _logical_commands("# c\nterraform plan \\\n  -x\nnext \\")]
    assert got == ["terraform plan -x", "next"]


_SSM_PREFIX = "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter"


def _ssm_statement(sid: str, actions: str, resources: str, condition: bool = False) -> str:
    cond = (
        '    condition {\n      test = "StringLike"\n      variable = "aws:userid"\n'
        "      values = [local.convergence_writer_session_name]\n    }\n"
        if condition
        else ""
    )
    head = f'  statement {{\n    sid = "{sid}"\n    effect = "Allow"\n'
    return head + f"    actions = {actions}\n    resources = {resources}\n{cond}  }}\n"


@pytest.mark.parametrize(
    ("sid", "actions", "resources"),
    [
        ("NeonKey", '["ssm:GetParameter"]', f'["{_SSM_PREFIX}/secret/neon-api-key"]'),
        ("GetStar", '["ssm:Get*"]', f'["{_SSM_PREFIX}/secret/*"]'),
        ("ByPath", '["ssm:GetParametersByPath"]', f'["{_SSM_PREFIX}/secret"]'),
        ("BareLocal", '["ssm:GetParameter"]', "local.arns"),
    ],
)
def test_unconditioned_ssm_secret_read_fails(tree: Path, sid: str, actions: str, resources: str) -> None:
    _append_planner_statement(tree, _ssm_statement(sid, actions, resources))
    assert any(sid in f and "SSM" in f for f in _run(tree)), sid


def test_ssm_read_conditioned_on_another_session_fails(tree: Path) -> None:
    stmt = _ssm_statement("Other", '["ssm:GetParameter"]', f'["{_SSM_PREFIX}/secret/neon-api-key"]')
    stmt = stmt.replace(
        "  }\n",
        '    condition {\n      test = "StringLike"\n      variable = "aws:userid"\n'
        '      values = ["*:terraform-plan-pr"]\n    }\n  }\n',
    )
    _append_planner_statement(tree, stmt)
    assert any("Other" in f for f in _run(tree))


def test_ssm_conditioned_drift_read_and_tfvars_read_pass(tree: Path) -> None:
    _append_planner_statement(
        tree, _ssm_statement("DriftRead", '["ssm:GetParameter"]', f'["{_SSM_PREFIX}/secret/neon-api-key"]', condition=True)
    )
    _append_planner_statement(
        tree,
        _ssm_statement(
            "TfvarsRead", '["ssm:GetParameter"]', f'["{_SSM_PREFIX}/secret/agent-platform-terraform-personal-tfvars"]'
        ),
    )
    assert _run(tree) == []
