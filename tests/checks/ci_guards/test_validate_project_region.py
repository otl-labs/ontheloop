"""Tests for validate_project_region() (rec-4067): inline fixtures, ROOT patched to tmp_path.

One red case per rule (R1-R5), plus the Neon id staying green and test_real_tree_passes running
the check against the live repository.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from scripts.checks.ci_guards.validate_project_region import validate_project_region

_ROOT_PATCH_TARGET = "scripts.checks._common.ROOT"

_GOOD_SIGNIN_JOB = """
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: ./.github/actions/aws-region
      - uses: aws-actions/configure-aws-credentials@v6
        with:
          role-to-assume: arn:aws:iam::1:role/x
          aws-region: ${{ env.AWS_REGION }}
"""

_GOOD_WORKFLOW = f"name: fixture\non: push\n{_GOOD_SIGNIN_JOB}"

_GOOD_BOOTSTRAP_VARIABLES = """
variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "eu-west-2"
}
"""

_GOOD_PERSONAL_VARIABLES = _GOOD_BOOTSTRAP_VARIABLES


def _write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _base_tree(root: Path, region: str = "eu-west-2") -> None:
    _write(root, "config/aws-region", region + "\n")
    _write(root, "terraform/bootstrap/variables.tf", _GOOD_BOOTSTRAP_VARIABLES)
    _write(root, "terraform/personal/providers.tf", _GOOD_PERSONAL_VARIABLES)


def _run(root: Path) -> list[str]:
    with patch(_ROOT_PATCH_TARGET, root):
        failed: list[str] = []
        validate_project_region(failed)
    return failed


def _examined() -> int:
    from scripts.checks import registry

    declaration = registry._CURRENT_DECLARATION
    return declaration.count if declaration and declaration.kind == "examined" else 0


def test_compliant_tree_passes(tmp_path: Path) -> None:
    _base_tree(tmp_path)
    _write(tmp_path, ".github/workflows/w.yml", _GOOD_WORKFLOW)
    failed = _run(tmp_path)
    assert failed == []
    assert _examined() > 0


def test_r1_empty_region_file(tmp_path: Path) -> None:
    _base_tree(tmp_path)
    _write(tmp_path, "config/aws-region", "")
    failed = _run(tmp_path)
    assert any("R1" in f for f in failed)


def test_r1_invalid_region_file(tmp_path: Path) -> None:
    _base_tree(tmp_path)
    _write(tmp_path, "config/aws-region", "not-a-region\n")
    failed = _run(tmp_path)
    assert any("R1" in f for f in failed)


def test_r2_literal_in_signin_with(tmp_path: Path) -> None:
    _base_tree(tmp_path)
    wf = _GOOD_WORKFLOW.replace("${{ env.AWS_REGION }}", "eu-west-2")
    _write(tmp_path, ".github/workflows/w.yml", wf)
    failed = _run(tmp_path)
    assert any("R2" in f for f in failed)


def test_r2_literal_in_run_body(tmp_path: Path) -> None:
    _base_tree(tmp_path)
    wf = "name: fixture\non: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo eu-west-2\n"
    _write(tmp_path, ".github/workflows/w.yml", wf)
    failed = _run(tmp_path)
    assert any("R2" in f for f in failed)


def test_r2_az_id(tmp_path: Path) -> None:
    _base_tree(tmp_path)
    wf = "name: fixture\non: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo eu-west-2a\n"
    _write(tmp_path, ".github/workflows/w.yml", wf)
    failed = _run(tmp_path)
    assert any("R2" in f for f in failed)


def test_r2_comment_holding_region_id(tmp_path: Path) -> None:
    _base_tree(tmp_path)
    wf = "# region eu-west-2 default\nname: fixture\non: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps: []\n"
    _write(tmp_path, ".github/workflows/w.yml", wf)
    failed = _run(tmp_path)
    assert any("R2" in f for f in failed)


def test_r2_non_workflow_github_file(tmp_path: Path) -> None:
    _base_tree(tmp_path)
    _write(tmp_path, ".github/CODEOWNERS", "# eu-west-2 is our region\n")
    failed = _run(tmp_path)
    assert any("R2" in f for f in failed)


def test_r3_backend_s3_region_line(tmp_path: Path) -> None:
    _base_tree(tmp_path)
    _write(
        tmp_path,
        "terraform/bootstrap/main.tf",
        'terraform {\n  backend "s3" {\n    bucket = "x"\n    region = "eu-west-2"\n  }\n}\n',
    )
    failed = _run(tmp_path)
    assert any("R3" in f and "backend" in f for f in failed)


def test_r3_backend_hcl_region_line(tmp_path: Path) -> None:
    _base_tree(tmp_path)
    _write(tmp_path, "terraform/personal/backend-sandbox.hcl", 'bucket = "x"\nregion = "eu-west-2"\n')
    failed = _run(tmp_path)
    assert any("R3" in f and "backend" in f for f in failed)


def test_r3_default_differs_from_file(tmp_path: Path) -> None:
    _base_tree(tmp_path, region="eu-west-2")
    _write(
        tmp_path,
        "terraform/bootstrap/variables.tf",
        'variable "aws_region" {\n  default = "us-east-1"\n}\n',
    )
    failed = _run(tmp_path)
    assert any("R3" in f and "differs from" in f for f in failed)


def test_r3_hcl_comment_holding_region_id(tmp_path: Path) -> None:
    _base_tree(tmp_path)
    _write(tmp_path, "terraform/personal/notes.tf", "# historical region: eu-west-2\n")
    failed = _run(tmp_path)
    assert any("R3" in f for f in failed)


def test_r3_neon_id_stays_green(tmp_path: Path) -> None:
    _base_tree(tmp_path)
    _write(
        tmp_path,
        "terraform/personal/neon.tf",
        'variable "neon_region_id" {\n  default = "aws-eu-west-2"\n}\n',
    )
    failed = _run(tmp_path)
    assert failed == []


def test_r4_unwired_signin(tmp_path: Path) -> None:
    _base_tree(tmp_path)
    wf = (
        "name: fixture\non: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - uses: actions/checkout@v7\n"
        "      - uses: aws-actions/configure-aws-credentials@v6\n"
        "        with:\n"
        "          role-to-assume: arn:aws:iam::1:role/x\n"
        "          aws-region: ${{ env.AWS_REGION }}\n"
    )
    _write(tmp_path, ".github/workflows/w.yml", wf)
    failed = _run(tmp_path)
    assert any("R4" in f for f in failed)


def test_r4_region_step_before_checkout(tmp_path: Path) -> None:
    _base_tree(tmp_path)
    wf = (
        "name: fixture\non: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - uses: ./.github/actions/aws-region\n"
        "      - uses: actions/checkout@v7\n"
        "      - uses: aws-actions/configure-aws-credentials@v6\n"
        "        with:\n"
        "          role-to-assume: arn:aws:iam::1:role/x\n"
        "          aws-region: ${{ env.AWS_REGION }}\n"
    )
    _write(tmp_path, ".github/workflows/w.yml", wf)
    failed = _run(tmp_path)
    assert any("R4" in f for f in failed)


def test_r4_preceding_checkout_carries_ref(tmp_path: Path) -> None:
    _base_tree(tmp_path)
    wf = (
        "name: fixture\non: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - uses: actions/checkout@v7\n"
        "        with:\n"
        "          ref: some-other-sha\n"
        "      - uses: ./.github/actions/aws-region\n"
        "      - uses: aws-actions/configure-aws-credentials@v6\n"
        "        with:\n"
        "          role-to-assume: arn:aws:iam::1:role/x\n"
        "          aws-region: ${{ env.AWS_REGION }}\n"
    )
    _write(tmp_path, ".github/workflows/w.yml", wf)
    failed = _run(tmp_path)
    assert any("R4" in f for f in failed)


def test_r4_needs_output_job_does_not_run_action(tmp_path: Path) -> None:
    _base_tree(tmp_path)
    wf = (
        "name: fixture\non: push\njobs:\n"
        "  resolve-target:\n"
        "    runs-on: ubuntu-latest\n"
        "    outputs:\n"
        "      aws_region: ${{ steps.region.outputs.aws_region }}\n"
        "    steps:\n"
        "      - uses: actions/checkout@v7\n"
        "      - run: echo not-the-action\n"
        "        id: region\n"
        "  consumer:\n"
        "    needs: resolve-target\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: aws-actions/configure-aws-credentials@v6\n"
        "        with:\n"
        "          role-to-assume: arn:aws:iam::1:role/x\n"
        "          aws-region: ${{ needs.resolve-target.outputs.aws_region }}\n"
    )
    _write(tmp_path, ".github/workflows/w.yml", wf)
    failed = _run(tmp_path)
    assert any("R4" in f for f in failed)


def test_r5_env_aws_default_region(tmp_path: Path) -> None:
    _base_tree(tmp_path)
    wf = (
        "name: fixture\non: push\nenv:\n  AWS_DEFAULT_REGION: eu-west-2\n"
        "jobs:\n  a:\n    runs-on: ubuntu-latest\n    steps: []\n"
    )
    _write(tmp_path, ".github/workflows/w.yml", wf)
    failed = _run(tmp_path)
    assert any("R5" in f for f in failed)


def test_real_tree_passes() -> None:
    failed: list[str] = []
    validate_project_region(failed)
    assert failed == [], failed
    assert _examined() > 0


class TestBranchCoverage:
    """Exercises the defensive/early-return branches the main red-case tests don't reach."""

    def test_no_github_or_terraform_tree_at_all(self, tmp_path: Path) -> None:
        _write(tmp_path, "config/aws-region", "eu-west-2\n")
        failed = _run(tmp_path)
        assert failed == []
        assert _examined() == 0

    def test_r1_missing_region_file(self, tmp_path: Path) -> None:
        (tmp_path / "terraform" / "bootstrap").mkdir(parents=True)
        (tmp_path / "terraform" / "bootstrap" / "variables.tf").write_text(_GOOD_BOOTSTRAP_VARIABLES, encoding="utf-8")
        (tmp_path / "terraform" / "personal").mkdir(parents=True)
        (tmp_path / "terraform" / "personal" / "providers.tf").write_text(_GOOD_PERSONAL_VARIABLES, encoding="utf-8")
        failed = _run(tmp_path)
        assert any("R1" in f and "missing" in f for f in failed)

    def test_pinned_file_with_no_aws_region_variable_block(self, tmp_path: Path) -> None:
        _write(tmp_path, "config/aws-region", "eu-west-2\n")
        _write(tmp_path, "terraform/bootstrap/variables.tf", 'variable "other" {\n  default = "x"\n}\n')
        _write(tmp_path, "terraform/personal/providers.tf", _GOOD_PERSONAL_VARIABLES)
        failed = _run(tmp_path)
        assert failed == []

    def test_pinned_file_with_no_default_in_block(self, tmp_path: Path) -> None:
        _write(tmp_path, "config/aws-region", "eu-west-2\n")
        _write(tmp_path, "terraform/bootstrap/variables.tf", 'variable "aws_region" {\n  type = string\n}\n')
        _write(tmp_path, "terraform/personal/providers.tf", _GOOD_PERSONAL_VARIABLES)
        failed = _run(tmp_path)
        assert failed == []

    def test_needs_output_job_has_no_outputs_key(self, tmp_path: Path) -> None:
        _base_tree(tmp_path)
        wf = (
            "name: fixture\non: push\njobs:\n"
            "  resolve-target:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - run: echo hi\n"
            "  consumer:\n"
            "    needs: resolve-target\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - uses: aws-actions/configure-aws-credentials@v6\n"
            "        with:\n"
            "          role-to-assume: arn:aws:iam::1:role/x\n"
            "          aws-region: ${{ needs.resolve-target.outputs.aws_region }}\n"
        )
        _write(tmp_path, ".github/workflows/w.yml", wf)
        failed = _run(tmp_path)
        assert any("R4" in f for f in failed)

    def test_needs_output_value_does_not_match_steps_pattern(self, tmp_path: Path) -> None:
        _base_tree(tmp_path)
        wf = (
            "name: fixture\non: push\njobs:\n"
            "  resolve-target:\n"
            "    runs-on: ubuntu-latest\n"
            "    outputs:\n"
            "      aws_region: not-a-steps-expression\n"
            "    steps:\n"
            "      - run: echo hi\n"
            "  consumer:\n"
            "    needs: resolve-target\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - uses: aws-actions/configure-aws-credentials@v6\n"
            "        with:\n"
            "          role-to-assume: arn:aws:iam::1:role/x\n"
            "          aws-region: ${{ needs.resolve-target.outputs.aws_region }}\n"
        )
        _write(tmp_path, ".github/workflows/w.yml", wf)
        failed = _run(tmp_path)
        assert any("R4" in f for f in failed)

    def test_needs_list_absent_entirely(self, tmp_path: Path) -> None:
        _base_tree(tmp_path)
        wf = (
            "name: fixture\non: push\njobs:\n"
            "  consumer:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - uses: aws-actions/configure-aws-credentials@v6\n"
            "        with:\n"
            "          role-to-assume: arn:aws:iam::1:role/x\n"
            "          aws-region: ${{ needs.resolve-target.outputs.aws_region }}\n"
        )
        _write(tmp_path, ".github/workflows/w.yml", wf)
        failed = _run(tmp_path)
        assert any("R4" in f for f in failed)

    def test_needed_job_id_not_in_needs_list(self, tmp_path: Path) -> None:
        _base_tree(tmp_path)
        wf = (
            "name: fixture\non: push\njobs:\n"
            "  resolve-target:\n"
            "    runs-on: ubuntu-latest\n"
            "    outputs:\n"
            "      aws_region: ${{ steps.region.outputs.aws_region }}\n"
            "    steps:\n"
            "      - uses: ./.github/actions/aws-region\n"
            "        id: region\n"
            "  other-job:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - run: echo hi\n"
            "  consumer:\n"
            "    needs: other-job\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - uses: aws-actions/configure-aws-credentials@v6\n"
            "        with:\n"
            "          role-to-assume: arn:aws:iam::1:role/x\n"
            "          aws-region: ${{ needs.resolve-target.outputs.aws_region }}\n"
        )
        _write(tmp_path, ".github/workflows/w.yml", wf)
        failed = _run(tmp_path)
        assert any("R4" in f for f in failed)

    def test_signin_with_no_aws_region_key(self, tmp_path: Path) -> None:
        _base_tree(tmp_path)
        wf = (
            "name: fixture\non: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n"
            "      - uses: actions/checkout@v7\n"
            "      - uses: ./.github/actions/aws-region\n"
            "      - uses: aws-actions/configure-aws-credentials@v6\n"
            "        with:\n"
            "          role-to-assume: arn:aws:iam::1:role/x\n"
        )
        _write(tmp_path, ".github/workflows/w.yml", wf)
        failed = _run(tmp_path)
        assert any("R4" in f for f in failed)

    def test_unparseable_workflow_yaml(self, tmp_path: Path) -> None:
        _base_tree(tmp_path)
        _write(tmp_path, ".github/workflows/w.yml", "jobs:\n  a: [unclosed\n")
        failed = _run(tmp_path)
        assert any("unparseable" in f for f in failed)

    def test_workflow_yaml_is_not_a_mapping(self, tmp_path: Path) -> None:
        _base_tree(tmp_path)
        _write(tmp_path, ".github/workflows/w.yml", "- just\n- a\n- list\n")
        failed = _run(tmp_path)
        assert failed == []

    def test_job_value_is_not_a_mapping(self, tmp_path: Path) -> None:
        _base_tree(tmp_path)
        _write(tmp_path, ".github/workflows/w.yml", "name: fixture\non: push\njobs:\n  a: not-a-mapping\n")
        failed = _run(tmp_path)
        assert failed == []
