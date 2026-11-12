"""Tests for validate_account_portable_names: one red case per predicate (a)-(g), the real tree, the registration."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.checks.iam_tf.validate_account_portable_names import (
    account_portable_findings,
    strip_comment,
    validate_account_portable_names,
)

REAL = Path(__file__).resolve().parents[3]
COPIED = (
    "terraform/bootstrap/account_names.tf",
    "terraform/bootstrap/variables.tf",
    "terraform/bootstrap/backend-sit.hcl",
    "terraform/personal/account_names.tf",
    "terraform/personal/variables.tf",
    "config/terraform/backend-personal-sit.hcl",
)


def _tree(tmp_path: Path) -> Path:
    for rel in COPIED:
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REAL / rel, dest)
    return tmp_path


def _edit(root: Path, rel: str, old: str, new: str) -> None:
    path = root / rel
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _tags(root: Path) -> set[str]:
    return {f[:3] for f in account_portable_findings(root)}


def test_real_tree_is_green() -> None:
    assert account_portable_findings(REAL) == []


def test_synthetic_baseline_is_green(tmp_path: Path) -> None:
    assert account_portable_findings(_tree(tmp_path)) == []


def test_a_arn_literal_is_red_and_names_the_local(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _write(root, "terraform/bootstrap/x.tf", 'r = ["arn:aws:s3:::agent-platform-data-lake/x/*"]\n')
    found = account_portable_findings(root)
    assert any(f.startswith("(a)") and "local.data_lake_bucket_arn" in f for f in found)


def test_a_s3_url_and_bucket_attribute_are_red(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _write(
        root,
        "terraform/personal/x.tf",
        'u = "s3://agent-platform-ducklake-catalog-dr/k"\nbucket = "agent-platform-data-lake"\n',
    )
    assert sum(f.startswith("(a)") for f in account_portable_findings(root)) == 2


def test_a_literal_on_a_line_with_an_arn_wildcard_is_red(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _write(root, "terraform/bootstrap/x.tf", 'r = "arn:aws:s3:::agent-platform-bootstrap-tfstate/*" # note\n')
    assert "(a)" in _tags(root)


def test_a_comment_backend_block_hcl_and_role_names_are_exempt(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _write(
        root,
        "terraform/bootstrap/x.tf",
        "# arn:aws:s3:::agent-platform-data-lake in a comment\n"
        'terraform {\n  backend "s3" {\n    bucket = "agent-platform-bootstrap-tfstate"\n  }\n}\n'
        'r = "arn:aws:iam::1:role/agent-platform-ducklake-catalog-dr"\n'
        'u = "https://x" # arn:aws:s3:::agent-platform-data-lake\n',
    )
    assert account_portable_findings(root) == []


def test_a_literal_state_key_in_personal_is_red(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _write(root, "terraform/personal/x.tf", 'k = "${a}/tfstate/personal/sandbox/terraform.tfstate"\n')
    assert any(f.startswith("(a)") and "state key" in f for f in account_portable_findings(root))


def test_b_the_roots_disagree_is_red(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _edit(root, "terraform/personal/account_names.tf", "agent-platform-data-lake${", "agent-platform-data-lakes${")
    assert "(b)" in _tags(root)


def test_b_a_missing_account_names_file_is_red(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    (root / "terraform/personal/account_names.tf").unlink()
    assert "(b)" in _tags(root)


def test_c_environment_variables_disagree_is_red(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _edit(root, "terraform/personal/variables.tf", 'default     = "sandbox"', 'default     = "sit"')
    assert "(c)" in _tags(root)


def test_c_a_default_other_than_sandbox_is_red(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    for rel in ("terraform/bootstrap/variables.tf", "terraform/personal/variables.tf"):
        _edit(root, rel, 'default     = "sandbox"', 'default     = "sit"')
    assert any(f.startswith("(c)") and "default" in f for f in account_portable_findings(root))


def test_d_the_sandbox_render_drifting_from_todays_names_is_red(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    for rel in ("terraform/bootstrap/account_names.tf", "terraform/personal/account_names.tf"):
        _edit(root, rel, '{ sandbox = "", sit = "-sit" }', '{ sandbox = "-x", sit = "-sit" }')
    found = account_portable_findings(root)
    assert any(f.startswith("(d)") and "sandbox render" in f for f in found)


def test_d_the_sit_render_without_the_suffix_is_red(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    for rel in ("terraform/bootstrap/account_names.tf", "terraform/personal/account_names.tf"):
        _edit(root, rel, '{ sandbox = "", sit = "-sit" }', '{ sandbox = "", sit = "" }')
    assert any(f.startswith("(d)") and "sit render" in f for f in account_portable_findings(root))


def test_e_a_suffix_map_wider_than_the_validation_is_red(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    for rel in ("terraform/bootstrap/account_names.tf", "terraform/personal/account_names.tf"):
        _edit(root, rel, 'sit = "-sit" }', 'sit = "-sit", qa = "-qa" }')
    assert "(e)" in _tags(root)


def test_f_a_wrong_bucket_is_red(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _edit(root, "terraform/bootstrap/backend-sit.hcl", "tfstate-sit", "tfstate")
    assert "(f)" in _tags(root)


def test_f_a_wrong_key_is_red(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _edit(root, "config/terraform/backend-personal-sit.hcl", "tfstate/personal/sit/", "tfstate/personal/sandbox/")
    assert any(f.startswith("(f)") and "key" in f for f in account_portable_findings(root))


def test_f_a_region_is_red_and_a_missing_file_is_red(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    with (root / "terraform/bootstrap/backend-sit.hcl").open("a", encoding="utf-8") as fh:
        fh.write('region = "eu-west-2"\n')
    (root / "config/terraform/backend-personal-sit.hcl").unlink()
    found = account_portable_findings(root)
    assert any("sets region" in f for f in found) and any("is missing" in f for f in found)


def test_g_an_environment_token_in_a_secret_name_is_red(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _write(root, "terraform/personal/s.tf", 'resource "aws_secretsmanager_secret" "k" {\n  name = "platform/key-sit"\n}\n')
    assert "(g)" in _tags(root)


def test_g_a_sandbox_parameter_path_in_a_local_is_red(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _write(root, "terraform/personal/s.tf", 'locals {\n  secret_parameter_names = ["/secret/sandbox/key"]\n}\n')
    assert "(g)" in _tags(root)


def test_g_a_secret_grant_fragment_with_a_token_is_red(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _write(root, "terraform/bootstrap/s.tf", 'Resource = ["arn:aws:ssm:r:1:parameter/secret/deepseek-sit"]\n')
    assert "(g)" in _tags(root)


def test_g_neutral_names_and_the_next_block_are_not_flagged(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _write(
        root,
        "terraform/personal/s.tf",
        'resource "aws_ssm_parameter" "a" {\n  name = "/secret/deepseek"\n}\n'
        'resource "aws_s3_bucket" "b" {\n  name = "x-sit"\n}\n'
        'Resource = ["arn:aws:secretsmanager:r:1:secret:telemetry-writer-prod-dsn"]\n',
    )
    assert account_portable_findings(root) == []


def test_strip_comment_is_string_aware() -> None:
    assert strip_comment('a = "s3://b" # c') == 'a = "s3://b" '
    assert strip_comment('a = "x#logs" // c') == 'a = "x#logs" '
    assert strip_comment('a = "q\\"#"') == 'a = "q\\"#"'


def test_registered_check_reports_failure_and_success(tmp_path: Path, capsys) -> None:
    root = _tree(tmp_path)
    with patch("scripts.checks._common.ROOT", root):
        failed: list[str] = []
        validate_account_portable_names(failed)
        assert failed == []
        _write(root, "terraform/bootstrap/x.tf", 'r = "arn:aws:s3:::agent-platform-data-lake"\n')
        validate_account_portable_names(failed)
    assert failed == ["Account-portable names"]
    assert "(a)" in capsys.readouterr().out


def test_fast_tier_registration_and_taxonomy_row() -> None:
    manifest = (REAL / "scripts/checks/iam_tf/_manifest.py").read_text(encoding="utf-8")
    entry = manifest[manifest.index('name="validate_account_portable_names"') :]
    entry = entry[: entry.index("\n    ),\n")]
    assert "pre=True" in entry and "full_segment=" in entry
    taxonomy = yaml.safe_load((REAL / "config/ci_rca_taxonomy.yaml").read_text(encoding="utf-8"))
    assert taxonomy["function_to_category"]["validate_account_portable_names"] == "environment"


def test_block_body_skips_escaped_quotes_and_tolerates_an_unclosed_block() -> None:
    from scripts.checks.iam_tf.validate_account_portable_names import _block_body

    assert _block_body('{ a = "q\\"}" }', 0) == '{ a = "q\\"}" }'
    assert _block_body("{ a = [1, 2]", 0) == "{ a = [1, 2]"
