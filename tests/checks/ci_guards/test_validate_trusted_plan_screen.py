"""validate_trusted_plan_screen keeps main's terraform/personal inside the trusted plan's own screen."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from scripts.checks.ci_guards.validate_trusted_plan_screen import validate_trusted_plan_screen

REAL_ROOT = Path(__file__).resolve().parents[3]

_GOOD = 'resource "aws_s3_bucket" "b" {\n  bucket = "x"\n}\n'


def _run(root: Path) -> list[str]:
    failed: list[str] = []
    with patch("scripts.checks._common.ROOT", root):
        validate_trusted_plan_screen(failed)
    return failed


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    personal = tmp_path / "terraform" / "personal"
    personal.mkdir(parents=True)
    for name, text in files.items():
        (personal / name).write_text(text, encoding="utf-8")
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
