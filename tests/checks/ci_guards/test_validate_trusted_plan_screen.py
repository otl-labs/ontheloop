"""validate_trusted_plan_screen keeps main's terraform roots inside the trusted plan's own screen."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from scripts.checks.ci_guards.validate_trusted_plan_screen import validate_trusted_plan_screen

REAL_ROOT = Path(__file__).resolve().parents[3]

_GOOD = 'resource "aws_s3_bucket" "b" {\n  bucket = "x"\n}\n'
_GOOD_GITHUB = 'resource "github_repository" "this" {\n  name = "x"\n}\n'


def _run(root: Path) -> list[str]:
    failed: list[str] = []
    with patch("scripts.checks._common.ROOT", root):
        validate_trusted_plan_screen(failed)
    return failed


def _tree(tmp_path: Path, files: dict[str, str], github: dict[str, str] | None = None) -> Path:
    for root_name, root_files in (("personal", files), ("github", github or {"repo.tf": _GOOD_GITHUB})):
        root = tmp_path / "terraform" / root_name
        root.mkdir(parents=True)
        for name, text in root_files.items():
            (root / name).write_text(text, encoding="utf-8")
    return tmp_path


def test_real_tree_is_green() -> None:
    assert _run(REAL_ROOT) == []


def test_synthetic_good_tree_is_green(tmp_path: Path) -> None:
    assert _run(_tree(tmp_path, {"main.tf": _GOOD})) == []


def test_non_allowlisted_resource_type_is_red(tmp_path: Path) -> None:
    failed = _run(_tree(tmp_path, {"main.tf": 'resource "aws_kms_key" "k" {\n}\n'}))
    assert failed and "main.tf" in failed[0]


def test_provider_block_outside_providers_tf_is_red(tmp_path: Path) -> None:
    failed = _run(_tree(tmp_path, {"main.tf": 'provider "aws" {\n  region = "eu-west-2"\n}\n'}))
    assert failed


def test_heredoc_is_red(tmp_path: Path) -> None:
    text = "locals {\n  a = <<EOT\nx\nEOT\n}\n"
    assert _run(_tree(tmp_path, {"main.tf": text}))


def test_missing_root_is_red_and_examines_nothing(tmp_path: Path) -> None:
    failed = _run(tmp_path)
    assert failed and "missing" in failed[0]


def test_github_root_red_case_names_the_root(tmp_path: Path) -> None:
    bad = {"repo.tf": 'data "github_user" "u" {\n  username = "x"\n}\n'}
    failed = _run(_tree(tmp_path, {"main.tf": _GOOD}, github=bad))
    assert len(failed) == 1
    assert "terraform/github/repo.tf" in failed[0] and "data_type" in failed[0]


def test_manifest_pre_globs_cover_both_roots() -> None:
    from scripts.checks.ci_guards import _manifest

    entry = next(e for e in _manifest.ENTRIES if e.name == "validate_trusted_plan_screen")
    assert {"terraform/personal/**", "terraform/github/**", "scripts/ci/terraform_hcl_screen_policy.py"} <= set(
        entry.pre_globs
    )
