#!/usr/bin/env python3
"""Per-root policy rows for the trusted-plan HCL screen.

Decision 238 cl.3; PLAN-trusted-plan-github-root (rec-4638).

Stdlib-only and code-owned: the one place each screened Terraform root's constants live. terraform_hcl_screen.py and
trusted_plan_overlay.py read a row by its root path; an unknown root has no row and is refused. Adding a resource type,
function or import variable to a row is a code-owned edit that lands before the HCL needing it.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_ROOT = "terraform/personal"


@dataclass(frozen=True)
class Policy:
    root: str
    main_only_files: frozenset[str]
    data_types: frozenset[str]
    resource_types: frozenset[str]
    functions: frozenset[str]
    validation_only_functions: frozenset[str]
    file_paths: frozenset[str]
    top_level_blocks: frozenset[str]
    backstop_providers: frozenset[str]
    imports_allowed: bool
    import_id_vars: frozenset[str]
    config_files: tuple[str, ...]


_PERSONAL = Policy(
    root="terraform/personal",
    main_only_files=frozenset({"providers.tf", ".terraform.lock.hcl", "backend-sandbox.hcl"}),
    data_types=frozenset({"aws_iam_policy_document"}),
    resource_types=frozenset(
        {
            "aws_cloudwatch_event_rule",
            "aws_cloudwatch_event_target",
            "aws_cloudwatch_log_group",
            "aws_cloudwatch_metric_alarm",
            "aws_dynamodb_table",
            "aws_iam_openid_connect_provider",
            "aws_iam_role",
            "aws_iam_role_policy",
            "aws_lambda_function",
            "aws_lambda_function_url",
            "aws_lambda_layer_version",
            "aws_lambda_permission",
            "aws_s3_bucket",
            "aws_s3_bucket_lifecycle_configuration",
            "aws_s3_bucket_notification",
            "aws_s3_bucket_policy",
            "aws_s3_bucket_public_access_block",
            "aws_s3_bucket_server_side_encryption_configuration",
            "aws_s3_bucket_versioning",
            "aws_secretsmanager_secret",
            "aws_secretsmanager_secret_version",
            "aws_sns_topic",
            "aws_sns_topic_subscription",
            "aws_ssm_parameter",
            "neon_database",
            "neon_project",
            "neon_role",
        }
    ),
    functions=frozenset(
        {"jsonencode", "jsondecode", "yamldecode", "flatten", "try", "can", "length", "regex", "format", "ceil", "file"}
    ),
    validation_only_functions=frozenset({"regex", "can"}),
    file_paths=frozenset(
        {
            "${path.module}/../../config/feature_flags.yaml",
            "${path.module}/../../config/lambda/ducklake/version.yaml",
        }
    ),
    top_level_blocks=frozenset({"resource", "data", "locals", "variable", "output", "moved", "removed"}),
    backstop_providers=frozenset({"aws", "neon"}),
    imports_allowed=False,
    import_id_vars=frozenset(),
    config_files=("config/feature_flags.yaml", "config/lambda/ducklake/version.yaml"),
)

_GITHUB = Policy(
    root="terraform/github",
    main_only_files=frozenset({"main.tf", ".terraform.lock.hcl"}),
    data_types=frozenset(),
    resource_types=frozenset(
        {
            "github_actions_repository_permissions",
            "github_repository",
            "github_repository_environment",
            "github_repository_environment_deployment_policy",
            "github_repository_ruleset",
            "github_workflow_repository_permissions",
        }
    ),
    functions=frozenset({"list", "length"}),
    validation_only_functions=frozenset({"length"}),
    file_paths=frozenset(),
    top_level_blocks=frozenset({"resource", "data", "locals", "variable", "output", "moved", "removed", "import"}),
    backstop_providers=frozenset({"github"}),
    imports_allowed=True,
    import_id_vars=frozenset({"repository_name", "main_protection_ruleset_id", "tf_trusted_plan_main_policy_id"}),
    config_files=(),
)

POLICIES: dict[str, Policy] = {_PERSONAL.root: _PERSONAL, _GITHUB.root: _GITHUB}


def get(root: str) -> Policy | None:
    return POLICIES.get(root)


def default() -> Policy:
    return _PERSONAL
