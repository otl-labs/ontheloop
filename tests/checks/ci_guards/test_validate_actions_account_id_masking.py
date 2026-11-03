"""Tests for validate_actions_account_id_masking() (rec-4211): inline fixtures, ROOT patched to tmp_path.

Fixtures use short placeholder values only -- never a 12-digit account number.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

from scripts.checks import _common
from scripts.checks.ci_guards.validate_actions_account_id_masking import (
    scan_repository,
    validate_actions_account_id_masking,
)

_ROOT_PATCH_TARGET = "scripts.checks._common.ROOT"

_GOOD_STEP = """
      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v6
        env:
          MASK_AWS_ROLE_ID_ACCOUNT_PART: ${{ secrets.AWS_ROLE_ID_ACCOUNT_PART }}
        with:
          role-to-assume: arn:aws:iam::${{ secrets.AWS_ACCOUNT_ID }}:role/x
"""

_COMPLIANT = f"""
name: fixture
on: push
jobs:
  a:
    runs-on: ubuntu-latest
    steps:{_GOOD_STEP}
"""


def _write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _run(root: Path, files: dict[str, str]) -> tuple[list[str], list[str], int]:
    for rel, content in files.items():
        _write(root, rel, content)
    with patch(_ROOT_PATCH_TARGET, root):
        failed: list[str] = []
        validate_actions_account_id_masking(failed)
        violations, examined = scan_repository()
    return failed, violations, examined


def _wf(body: str, top: str = "") -> str:
    return f"name: fixture\non: push\n{top}jobs:\n{body}"


def test_compliant_workflow_passes_and_counts(tmp_path: Path) -> None:
    failed, violations, examined = _run(tmp_path, {".github/workflows/w.yml": _COMPLIANT})
    assert failed == [] and violations == [] and examined == 1


def test_check_appends_to_failed_on_violation(tmp_path: Path) -> None:
    _write(tmp_path, ".github/workflows/w.yml", _COMPLIANT.replace("secrets.AWS_ACCOUNT_ID", "vars.AWS_ACCOUNT_ID"))
    failed: list[str] = []
    with patch(_ROOT_PATCH_TARGET, tmp_path):
        validate_actions_account_id_masking(failed)
    assert failed
    assert any("actions-account-id-masking" in f and "R1" in f for f in failed)


def test_r1_flags_vars_reference_in_shell_file_under_actions(tmp_path: Path) -> None:
    _, violations, _ = _run(tmp_path, {".github/actions/x/run.sh": "echo ${{ vars.AWS_ACCOUNT_ID }}\n"})
    assert any("R1" in v and "run.sh:1" in v for v in violations)


def test_r1_dot_matches_any_character(tmp_path: Path) -> None:
    _, violations, _ = _run(tmp_path, {".github/workflows/notes.txt": "varsXAWS_ACCOUNT_ID\n"})
    assert any("R1" in v for v in violations)


def test_r2_flags_vars_built_arn_outside_role_to_assume(tmp_path: Path) -> None:
    wf = _wf(
        "  a:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo\n        env:\n"
        "          T: arn:aws:iam::${{ vars.OTHER }}:role/x\n"
    )
    _, violations, _ = _run(tmp_path, {".github/workflows/w.yml": wf})
    assert any("R2" in v for v in violations)


def test_r2_ignores_tfvars_substring_expression(tmp_path: Path) -> None:
    wf = _wf(
        "  a:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo\n        env:\n"
        "          T: arn:aws:iam::x ${{ steps.tfvars.outcome }}\n"
    )
    _, violations, _ = _run(tmp_path, {".github/workflows/w.yml": wf})
    assert violations == []


def test_r3_flags_role_to_assume_without_secret(tmp_path: Path) -> None:
    wf = _COMPLIANT.replace("secrets.AWS_ACCOUNT_ID", "env.ACCT")
    _, violations, _ = _run(tmp_path, {".github/workflows/w.yml": wf})
    assert any("R3" in v and "role-to-assume" in v for v in violations)


def test_r3_flags_missing_role_part_reference(tmp_path: Path) -> None:
    wf = _COMPLIANT.replace("ROLE_ID_ACCOUNT_PART", "OTHER")
    _, violations, _ = _run(tmp_path, {".github/workflows/w.yml": wf})
    assert any("R3" in v and "AWS_ROLE_ID_ACCOUNT_PART" in v for v in violations)


def test_r3_workflow_level_reference_rejected_when_a_job_does_not_sign_in(tmp_path: Path) -> None:
    body = (
        "  a:\n    runs-on: ubuntu-latest\n    steps:\n      - name: Configure AWS credentials\n"
        "        uses: aws-actions/configure-aws-credentials@v6\n        with:\n"
        "          role-to-assume: arn:aws:iam::${{ secrets.AWS_ACCOUNT_ID }}:role/x\n"
        "  b:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo\n"
    )
    top = "env:\n  M: ${{ secrets.AWS_ROLE_ID_ACCOUNT_PART }}\n"
    _, violations, _ = _run(tmp_path, {".github/workflows/w.yml": _wf(body, top)})
    assert any("R3" in v and "AWS_ROLE_ID_ACCOUNT_PART" in v for v in violations)


def test_workflow_level_reference_accepted_when_every_job_signs_in(tmp_path: Path) -> None:
    body = (
        "  a:\n    runs-on: ubuntu-latest\n    steps:\n      - name: Configure AWS credentials\n"
        "        uses: aws-actions/configure-aws-credentials@v6\n        with:\n"
        "          role-to-assume: arn:aws:iam::${{ secrets.AWS_ACCOUNT_ID }}:role/x\n"
    )
    top = "env:\n  M: ${{ secrets.AWS_ROLE_ID_ACCOUNT_PART }}\n"
    failed, violations, _ = _run(tmp_path, {".github/workflows/w.yml": _wf(body, top)})
    assert failed == [] and violations == []


def test_r4_flags_composite_action_signin(tmp_path: Path) -> None:
    action = (
        "name: c\nruns:\n  using: composite\n  steps:\n"
        "    - uses: aws-actions/configure-aws-credentials@v6\n"
        "      with:\n        role-to-assume: x\n"
    )
    _, violations, examined = _run(tmp_path, {".github/actions/c/action.yml": action})
    assert any("R4" in v for v in violations) and examined == 1


def test_r5_flags_redundant_workflow_level_reference(tmp_path: Path) -> None:
    secretless = "  b:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo\n"
    body = f"  a:\n    runs-on: ubuntu-latest\n    steps:{_GOOD_STEP}{secretless}"
    top = "env:\n  M: ${{ secrets.AWS_ROLE_ID_ACCOUNT_PART }}\n"
    _, violations, _ = _run(tmp_path, {".github/workflows/w.yml": _wf(body, top)})
    assert any("R5" in v for v in violations)


def test_folded_multiline_role_to_assume_passes(tmp_path: Path) -> None:
    wf = _COMPLIANT.replace(
        "role-to-assume: arn:aws:iam::${{ secrets.AWS_ACCOUNT_ID }}:role/x",
        "role-to-assume: >-\n            arn:aws:iam::${{ secrets.AWS_ACCOUNT_ID }}:role/${{ github.event_name }}",
    )
    _, violations, _ = _run(tmp_path, {".github/workflows/w.yml": wf})
    assert violations == []


class TestDefensiveBranchesOnMalformedInput:
    def test_unparseable_yaml_is_skipped(self, tmp_path: Path) -> None:
        _, violations, examined = _run(tmp_path, {".github/workflows/w.yml": "a: [unclosed\n"})
        assert violations == [] and examined == 0

    def test_non_dict_document(self, tmp_path: Path) -> None:
        _, violations, examined = _run(tmp_path, {".github/workflows/w.yml": "- a\n- b\n"})
        assert violations == [] and examined == 0

    def test_non_dict_jobs_job_and_steps(self, tmp_path: Path) -> None:
        files = {
            ".github/workflows/a.yml": "jobs: [x]\n",
            ".github/workflows/b.yml": "jobs:\n  a: 1\n",
            ".github/workflows/c.yml": "jobs:\n  a:\n    steps: nope\n",
            ".github/workflows/d.yml": "jobs:\n  a:\n    steps:\n      - plain string\n      - run: echo\n",
            ".github/actions/e/action.yml": "runs: nope\n",
        }
        _, violations, examined = _run(tmp_path, files)
        assert violations == [] and examined == 0

    def test_non_dict_with_block_is_a_violation(self, tmp_path: Path) -> None:
        wf = _COMPLIANT.replace(
            "        with:\n          role-to-assume: arn:aws:iam::${{ secrets.AWS_ACCOUNT_ID }}:role/x\n",
            "        with: nope\n",
        )
        _, violations, _ = _run(tmp_path, {".github/workflows/w.yml": wf})
        assert any("R3" in v for v in violations)

    def test_missing_trees_are_empty(self, tmp_path: Path) -> None:
        _, violations, examined = _run(tmp_path, {})
        assert violations == [] and examined == 0


def test_real_tree_is_compliant() -> None:
    violations, examined = scan_repository()
    pattern = re.compile(r"^\s*(-\s+)?uses:\s*[\"']?aws-actions/configure-aws-credentials", re.M)
    raw = sum(
        len(pattern.findall(f.read_text(encoding="utf-8")))
        for tree in (".github/workflows", ".github/actions")
        for f in (_common.ROOT / tree).rglob("*")
        if f.is_file() and f.suffix in (".yml", ".yaml")
    )
    assert violations == []
    assert raw > 0 and examined == raw


def test_yaml_outside_workflow_globs_gets_only_text_and_expression_rules(tmp_path: Path) -> None:
    nested = "t: arn:aws:iam::x:role/${{ vars.OTHER }}\nu: uses aws-actions/configure-aws-credentials\n"
    _, violations, examined = _run(tmp_path, {".github/workflows/sub/nested.yml": nested})
    assert any("R2" in v for v in violations)
    assert examined == 0
