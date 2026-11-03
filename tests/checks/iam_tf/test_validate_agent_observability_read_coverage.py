"""Tests for the Lever B agent-identity observability read-coverage gate (T2.48)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from scripts.checks.iam_tf.validate_agent_observability_read_coverage import (
    _resolve_log_group_name,
    validate_agent_observability_read_coverage,
)

_PERSONAL_LOCALS = """
locals {
  writer_function = "agent-platform-ducklake-writer"
}
"""

_BASE_ROLES = """
resource "aws_iam_role" "platform_dev" {
  name = "PlatformDev"
}

resource "aws_iam_role_policy" "platform_dev_runtime" {
  name = "DailyOps"
  role = aws_iam_role.platform_dev.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ObservabilityLogStreamRead"
        Effect = "Allow"
        Action = ["logs:DescribeLogStreams", "logs:GetLogEvents", "logs:FilterLogEvents"]
        Resource = [
          "arn:aws:logs:eu-west-2:ACCOUNTID:log-group:/aws/lambda/agent-platform-*",
          "arn:aws:logs:eu-west-2:ACCOUNTID:log-group:/aws/lambda/agent-platform-*:log-stream:*",
        ]
      },
    ]
  })
}

resource "aws_iam_role" "platform_admin" {
  name = "PlatformAdmin"
}

resource "aws_iam_role_policy" "platform_admin_ops" {
  name = "AdminOps"
  role = aws_iam_role.platform_admin.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "S3BucketInventoryRead"
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:ListBucketVersions"]
        Resource = "arn:aws:s3:::agent-platform-*"
      },
    ]
  })
}
"""


def _run(tmp_path: Path, extra_tf: str) -> list[str]:
    personal = tmp_path / "terraform" / "personal"
    bootstrap = tmp_path / "terraform" / "bootstrap"
    personal.mkdir(parents=True, exist_ok=True)
    bootstrap.mkdir(parents=True, exist_ok=True)
    (bootstrap / "roles.tf").write_text(_BASE_ROLES, encoding="utf-8")
    (personal / "locals.tf").write_text(_PERSONAL_LOCALS, encoding="utf-8")
    (personal / "resources.tf").write_text(extra_tf, encoding="utf-8")
    failed: list[str] = []
    with patch("scripts.checks._common.ROOT", tmp_path):
        validate_agent_observability_read_coverage(failed)
    return failed


def test_real_tree_all_covered() -> None:
    """Every observability-managed resource in the real terraform/personal is read-covered."""
    failed: list[str] = []
    validate_agent_observability_read_coverage(failed)
    assert failed == [], failed


def test_covered_bucket_and_log_group_pass(tmp_path: Path) -> None:
    tf = """
resource "aws_s3_bucket" "data_lake" {
  bucket = "agent-platform-data-lake"
}

resource "aws_cloudwatch_log_group" "writer" {
  name = "/aws/lambda/${local.writer_function}"
}
"""
    assert _run(tmp_path, tf) == []


def test_uncovered_log_group_fails_loud(tmp_path: Path) -> None:
    """A log group whose function name is not agent-platform-* is not covered by the prefix grant."""
    tf = """
locals {
  other_function = "some-other-service"
}

resource "aws_cloudwatch_log_group" "other" {
  name = "/aws/lambda/${local.other_function}"
}
"""
    failed = _run(tmp_path, tf)
    assert len(failed) == 1
    assert "aws_cloudwatch_log_group" in failed[0]
    assert "'other'" in failed[0]
    assert "not observability-read-covered" in failed[0]


def test_uncovered_s3_bucket_fails_loud(tmp_path: Path) -> None:
    tf = """
resource "aws_s3_bucket" "unrelated" {
  bucket = "some-unrelated-bucket"
}
"""
    failed = _run(tmp_path, tf)
    assert len(failed) == 1
    assert "aws_s3_bucket" in failed[0]
    assert "'unrelated'" in failed[0]


def test_unmapped_type_fails_loud(tmp_path: Path) -> None:
    tf = """
resource "aws_totally_novel_resource_type" "mystery" {
  name = "agent-platform-mystery"
}
"""
    failed = _run(tmp_path, tf)
    assert len(failed) == 1
    assert "unmapped resource type" in failed[0]
    assert "aws_totally_novel_resource_type" in failed[0]


def test_already_classified_type_needs_no_observability_row(tmp_path: Path) -> None:
    """A type _read_coverage.py already classifies (here: TRANSITIVE_TYPES) is not unmapped."""
    tf = """
resource "aws_lambda_permission" "invoke" {
  statement_id  = "AllowInvoke"
  function_name = "agent-platform-something"
}
"""
    assert _run(tmp_path, tf) == []


def test_unresolvable_name_fails_loud(tmp_path: Path) -> None:
    tf = """
resource "aws_s3_bucket" "dynamic" {
  bucket = var.some_bucket_name
}
"""
    failed = _run(tmp_path, tf)
    assert len(failed) == 1
    assert "could not resolve a name" in failed[0]


def test_no_resources_discovered_fails_loud(tmp_path: Path) -> None:
    personal = tmp_path / "terraform" / "personal"
    bootstrap = tmp_path / "terraform" / "bootstrap"
    personal.mkdir(parents=True, exist_ok=True)
    bootstrap.mkdir(parents=True, exist_ok=True)
    (bootstrap / "roles.tf").write_text(_BASE_ROLES, encoding="utf-8")
    failed: list[str] = []
    with (
        patch("scripts.checks._common.ROOT", tmp_path),
        patch(
            "scripts.checks.iam_tf.validate_agent_observability_read_coverage._scan_resources",
            return_value=([], {}, {}),
        ),
    ):
        validate_agent_observability_read_coverage(failed)
    assert len(failed) == 1
    assert "no terraform resources discovered" in failed[0]


def test_no_tf_files_fails_loud(tmp_path: Path) -> None:
    (tmp_path / "terraform" / "personal").mkdir(parents=True, exist_ok=True)
    (tmp_path / "terraform" / "bootstrap").mkdir(parents=True, exist_ok=True)
    failed: list[str] = []
    with patch("scripts.checks._common.ROOT", tmp_path):
        validate_agent_observability_read_coverage(failed)
    assert any("cannot read any" in f for f in failed)


def test_missing_role_policies_fails_loud(tmp_path: Path) -> None:
    personal = tmp_path / "terraform" / "personal"
    bootstrap = tmp_path / "terraform" / "bootstrap"
    personal.mkdir(parents=True, exist_ok=True)
    bootstrap.mkdir(parents=True, exist_ok=True)
    (bootstrap / "roles.tf").write_text('resource "aws_s3_bucket" "x" { bucket = "agent-platform-x" }', encoding="utf-8")
    failed: list[str] = []
    with patch("scripts.checks._common.ROOT", tmp_path):
        validate_agent_observability_read_coverage(failed)
    assert any("could not parse" in f for f in failed)


def test_policies_only_under_personal_fails_loud(tmp_path: Path) -> None:
    """The DailyOps/AdminOps policies must live in terraform/bootstrap -- a personal-only copy
    (the pre-adoption shape) is not read as coverage."""
    personal = tmp_path / "terraform" / "personal"
    bootstrap = tmp_path / "terraform" / "bootstrap"
    personal.mkdir(parents=True, exist_ok=True)
    bootstrap.mkdir(parents=True, exist_ok=True)
    (personal / "roles.tf").write_text(_BASE_ROLES, encoding="utf-8")
    (personal / "resources.tf").write_text(
        """
resource "aws_s3_bucket" "data_lake" {
  bucket = "agent-platform-data-lake"
}
""",
        encoding="utf-8",
    )
    # bootstrap exists but carries none of the role policies -- the pre-adoption shape.
    (bootstrap / "unrelated.tf").write_text('resource "aws_iam_policy" "unrelated" { name = "x" }', encoding="utf-8")
    failed: list[str] = []
    with patch("scripts.checks._common.ROOT", tmp_path):
        validate_agent_observability_read_coverage(failed)
    assert any("could not parse" in f for f in failed)


def test_resolve_log_group_name_variants() -> None:
    locals_map = {"writer_function": "agent-platform-ducklake-writer"}
    assert _resolve_log_group_name('"/aws/lambda/${local.writer_function}"', locals_map) == "agent-platform-ducklake-writer"
    assert _resolve_log_group_name('"/aws/lambda/agent-platform-literal"', locals_map) == "/aws/lambda/agent-platform-literal"
    assert _resolve_log_group_name(None, locals_map) is None
    assert _resolve_log_group_name('"/aws/lambda/${local.missing}"', locals_map) == "/aws/lambda/${local.missing}"
    # A bare (unquoted) local.* reference to an undefined local -- _resolve_value itself returns
    # None (no embedded-interpolation substring to find), distinct from the "found but not in
    # locals_map" case above where the surrounding literal text is still resolvable.
    assert _resolve_log_group_name("local.undefined_thing", locals_map) is None
