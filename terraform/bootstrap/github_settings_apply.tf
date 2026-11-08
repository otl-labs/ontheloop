# terraform/github CI apply identities and the apply App key's lock (Decision 248, PLAN-terraform-github-gated-apply).
#
# Two roles for .github/workflows/terraform-github-apply.yml, named outside agent-platform-* so no CI role's IAM
# grant (IAMRoleWriteBounded and kin, keyed on that prefix) can write them (Decision 223 cl.1). Each trusts ONE
# statement, StringEquals only: one Environment subject and this workflow's job_workflow_ref at main.
#   - github-settings-apply (the apply job, environment local.github_settings_gate_env): reads the apply App entry and
#     reads and writes terraform/github's state and lock, and reads the integrations/github provider packages in the S3
#     provider mirror. No iam: action, no s3:ListBucket (the S3 backend needs none).
#   - github-settings-preview (the preview job, environment tf-trusted-plan): reads terraform/github's state and the
#     integrations/github provider packages in the S3 provider mirror only.
# The apply App entry is created and rotated out of band (Decision 237 cl.3); this file reads its metadata only and
# attaches a resource policy denying every non-metadata action to all but four principals (Decision 248 cl.5).
# Changes here apply only by a human-directed admin apply (deploy-paths.yaml github_settings_apply_provisioning).

locals {
  github_settings_gate_env = "tf-gated-apply" # rec-4965 moves this to tf-github-apply.
  github_settings_workflow_refs = [
    "otl-labs/ontheloop/.github/workflows/terraform-github-apply.yml@refs/heads/main",
  ]
  github_settings_state_arn  = "arn:aws:s3:::agent-platform-data-lake/tfstate/github/terraform.tfstate"
  github_settings_mirror_arn = "arn:aws:s3:::agent-platform-data-lake/tf-provider-mirror/registry.terraform.io/integrations/github/*"

  github_settings_apply_policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "AppKeyRead"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [data.aws_secretsmanager_secret.github_apply_app.arn]
      },
      {
        Sid      = "TfstateGithubObject"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = [local.github_settings_state_arn]
      },
      {
        Sid      = "TfstateGithubLock"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = ["${local.github_settings_state_arn}.tflock"]
      },
      {
        Sid      = "ProviderMirrorGithubRead"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = [local.github_settings_mirror_arn]
      },
    ]
  })

  github_settings_preview_policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "TfstateGithubRead"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = [local.github_settings_state_arn]
      },
      {
        Sid      = "ProviderMirrorGithubRead"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = [local.github_settings_mirror_arn]
      },
    ]
  })

  github_settings_apply_key_policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyAllButMetadataOutsideFour"
        Effect    = "Deny"
        Principal = "*"
        NotAction = [
          "secretsmanager:DescribeSecret",
          "secretsmanager:GetResourcePolicy",
          "secretsmanager:ListSecretVersionIds",
        ]
        Resource = "*"
        Condition = {
          ArnNotLike = {
            "aws:PrincipalArn" = [
              "arn:aws:iam::${var.account_id}:role/github-settings-apply",
              "arn:aws:iam::${var.account_id}:role/PlatformAdmin",
              "arn:aws:iam::${var.account_id}:user/platform-breakglass",
              "arn:aws:iam::${var.account_id}:root",
            ]
          }
        }
      },
    ]
  })
}

data "aws_secretsmanager_secret" "github_apply_app" {
  name = "agent-platform-terraform-github-apply-app"
}

resource "aws_iam_role" "github_settings_apply" {
  name                 = "github-settings-apply"
  description          = "terraform-github-apply.yml apply job: operator-approved Environment at main (Decision 248)"
  permissions_boundary = aws_iam_policy.github_ci_apply_boundary.arn

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Federated = "arn:aws:iam::${var.account_id}:oidc-provider/token.actions.githubusercontent.com"
        }
        Action = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
            "token.actions.githubusercontent.com:sub" = flatten([
              for repo in local.github_repos : ["repo:${repo}:environment:${local.github_settings_gate_env}"]
            ])
            "token.actions.githubusercontent.com:job_workflow_ref" = local.github_settings_workflow_refs
          }
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "github_settings_apply" {
  name   = "github-settings-apply"
  role   = aws_iam_role.github_settings_apply.id
  policy = local.github_settings_apply_policy_json

  lifecycle {
    precondition {
      condition     = length(jsonencode(jsondecode(local.github_settings_apply_policy_json))) <= 10240
      error_message = "github-settings-apply inline policy exceeds the 10,240 B IAM inline-policy limit (whitespace-stripped measure)."
    }
  }
}

resource "aws_iam_role" "github_settings_preview" {
  name                 = "github-settings-preview"
  description          = "terraform-github-apply.yml preview job: tf-trusted-plan Environment at main (Decision 248)"
  permissions_boundary = aws_iam_policy.github_ci_apply_boundary.arn

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Federated = "arn:aws:iam::${var.account_id}:oidc-provider/token.actions.githubusercontent.com"
        }
        Action = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
            "token.actions.githubusercontent.com:sub" = flatten([
              for repo in local.github_repos : ["repo:${repo}:environment:tf-trusted-plan"]
            ])
            "token.actions.githubusercontent.com:job_workflow_ref" = local.github_settings_workflow_refs
          }
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "github_settings_preview" {
  name   = "github-settings-preview"
  role   = aws_iam_role.github_settings_preview.id
  policy = local.github_settings_preview_policy_json

  lifecycle {
    precondition {
      condition     = length(jsonencode(jsondecode(local.github_settings_preview_policy_json))) <= 10240
      error_message = "github-settings-preview inline policy exceeds the 10,240 B IAM inline-policy limit (whitespace-stripped measure)."
    }
  }
}

resource "aws_secretsmanager_secret_policy" "github_settings_apply_key" {
  secret_arn          = data.aws_secretsmanager_secret.github_apply_app.arn
  policy              = local.github_settings_apply_key_policy_json
  block_public_policy = true

  # PlatformAdmin may put and delete this policy only through AdminOps' GithubApplyKeyResourcePolicy statement.
  depends_on = [aws_iam_role_policy.platform_admin_ops]
}
