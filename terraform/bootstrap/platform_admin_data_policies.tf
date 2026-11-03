# PlatformAdmin data-lake, bootstrap-state and DuckLake break-glass inline policies, adopted from
# their terraform/personal source file (PLAN-platform-iam-bootstrap-adopt, rec-3838 plan 1 of 2).
# Each hoisted into a local so a <= 10240 B size precondition can measure it (a precondition
# cannot reference self).

locals {
  platform_admin_datalake_policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DataLakeBucketManage"
        Effect = "Allow"
        Action = [
          "s3:CreateBucket",
          "s3:ListBucket",
          "s3:GetBucketLocation",
          "s3:GetBucketAcl",
          "s3:GetBucketVersioning",
          "s3:PutBucketVersioning",
          "s3:GetEncryptionConfiguration",
          "s3:PutEncryptionConfiguration",
          "s3:GetBucketPublicAccessBlock",
          "s3:PutBucketPublicAccessBlock",
          "s3:GetBucketPolicy",
          "s3:PutBucketPolicy",
          "s3:GetBucketTagging",
          "s3:PutBucketTagging",
          "s3:GetBucketOwnershipControls",
          "s3:GetAccelerateConfiguration",
          "s3:GetBucketRequestPayment",
          "s3:GetBucketLogging",
          "s3:GetLifecycleConfiguration",
          "s3:GetReplicationConfiguration",
          "s3:GetBucketObjectLockConfiguration",
          "s3:GetBucketCORS",
          "s3:GetBucketWebsite",
          "s3:GetBucketNotification",
          "s3:PutBucketNotification",
        ]
        Resource = [local.data_lake_bucket_arn]
      },
      {
        Sid    = "DataLakeObjectIO"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:AbortMultipartUpload",
          "s3:ListMultipartUploadParts",
        ]
        Resource = ["${local.data_lake_bucket_arn}/*"]
      },
      {
        Sid    = "DynamoDBCountersTable"
        Effect = "Allow"
        Action = [
          "dynamodb:CreateTable",
          "dynamodb:DescribeTable",
          "dynamodb:DescribeContinuousBackups",
          "dynamodb:DescribeTimeToLive",
          "dynamodb:UpdateTable",
          "dynamodb:TagResource",
          "dynamodb:UntagResource",
          "dynamodb:ListTagsOfResource",
        ]
        Resource = [local.counters_table_arn]
      },
      {
        Sid    = "CatalogDrBucketManage"
        Effect = "Allow"
        Action = [
          "s3:CreateBucket",
          "s3:ListBucket",
          "s3:GetBucketLocation",
          "s3:GetBucketAcl",
          "s3:GetBucketVersioning",
          "s3:PutBucketVersioning",
          "s3:GetEncryptionConfiguration",
          "s3:PutEncryptionConfiguration",
          "s3:GetBucketPublicAccessBlock",
          "s3:PutBucketPublicAccessBlock",
          "s3:GetBucketPolicy",
          "s3:PutBucketPolicy",
          "s3:GetBucketTagging",
          "s3:PutBucketTagging",
          "s3:GetLifecycleConfiguration",
          "s3:PutLifecycleConfiguration",
          "s3:GetBucketOwnershipControls",
          "s3:GetAccelerateConfiguration",
          "s3:GetBucketRequestPayment",
          "s3:GetBucketLogging",
          "s3:GetReplicationConfiguration",
          "s3:GetBucketObjectLockConfiguration",
          "s3:GetBucketCORS",
          "s3:GetBucketWebsite",
        ]
        Resource = ["arn:aws:s3:::agent-platform-ducklake-catalog-dr"]
      },
      {
        Sid    = "CatalogDrObjectRead"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:GetObjectTagging",
          "s3:GetObjectVersion",
          "s3:ListBucket",
        ]
        Resource = [
          "arn:aws:s3:::agent-platform-ducklake-catalog-dr",
          "arn:aws:s3:::agent-platform-ducklake-catalog-dr/*",
        ]
      },
      {
        Sid    = "AlertsTopicManage"
        Effect = "Allow"
        Action = [
          "sns:CreateTopic",
          "sns:DeleteTopic",
          "sns:GetTopicAttributes",
          "sns:SetTopicAttributes",
          "sns:ListTagsForResource",
          "sns:TagResource",
          "sns:UntagResource",
          "sns:Subscribe",
          "sns:Unsubscribe",
          "sns:GetSubscriptionAttributes",
          "sns:SetSubscriptionAttributes",
          "sns:ListSubscriptionsByTopic",
        ]
        Resource = [
          "arn:aws:sns:${var.aws_region}:${var.account_id}:agent-platform-alerts",
          "arn:aws:sns:${var.aws_region}:${var.account_id}:agent-platform-alerts:*",
        ]
      },
    ]
  })

  platform_admin_bootstrap_state_policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "BootstrapStateBucketManage"
        Effect = "Allow"
        Action = [
          "s3:CreateBucket",
          "s3:ListBucket",
          "s3:GetBucketLocation",
          "s3:PutBucketVersioning",
          "s3:GetBucketVersioning",
          "s3:PutEncryptionConfiguration",
          "s3:GetEncryptionConfiguration",
          "s3:PutBucketPublicAccessBlock",
          "s3:GetBucketPublicAccessBlock",
        ]
        Resource = ["arn:aws:s3:::agent-platform-bootstrap-tfstate"]
      },
      {
        Sid    = "BootstrapStateObjectIO"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
        ]
        Resource = ["arn:aws:s3:::agent-platform-bootstrap-tfstate/*"]
      },
    ]
  })

  platform_admin_ducklake_breakglass_policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "NeonCatalogDsnRead"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [data.aws_secretsmanager_secret.ducklake_neon_catalog_dsn.arn]
      },
      {
        Sid      = "DuckLakeDataRead"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = ["${local.data_lake_bucket_arn}/ducklake-*"]
      },
      {
        Sid      = "DuckLakeDataList"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [local.data_lake_bucket_arn]
        Condition = {
          StringLike = {
            "s3:prefix" = ["ducklake-*"]
          }
        }
      },
    ]
  })
}

data "aws_secretsmanager_secret" "ducklake_neon_catalog_dsn" {
  name = "ducklake-neon-catalog-dsn"
}

import {
  id = "PlatformAdmin:PlatformDataLakeProvisioning"
  to = aws_iam_role_policy.platform_admin_datalake
}

resource "aws_iam_role_policy" "platform_admin_datalake" {
  name   = "PlatformDataLakeProvisioning"
  role   = aws_iam_role.platform_admin.id
  policy = local.platform_admin_datalake_policy_json

  lifecycle {
    precondition {
      condition     = length(jsonencode(jsondecode(local.platform_admin_datalake_policy_json))) <= 10240
      error_message = "PlatformAdmin PlatformDataLakeProvisioning inline policy exceeds the 10,240 B IAM inline-policy limit (whitespace-stripped measure, Decision 156). Move a statement to a customer-managed policy or trim grants."
    }
  }
}

# Bootstrap state backend (T2.23): PlatformAdmin provisions and uses terraform/bootstrap's own S3
# state backend. The bucket (agent-platform-bootstrap-tfstate) is created out-of-band and is NOT a
# Terraform resource in any root -- codifying it would be circular.
import {
  id = "PlatformAdmin:BootstrapStateProvisioning"
  to = aws_iam_role_policy.platform_admin_bootstrap_state
}

resource "aws_iam_role_policy" "platform_admin_bootstrap_state" {
  name   = "BootstrapStateProvisioning"
  role   = aws_iam_role.platform_admin.id
  policy = local.platform_admin_bootstrap_state_policy_json

  lifecycle {
    prevent_destroy = true

    precondition {
      condition     = length(jsonencode(jsondecode(local.platform_admin_bootstrap_state_policy_json))) <= 10240
      error_message = "PlatformAdmin BootstrapStateProvisioning inline policy exceeds the 10,240 B IAM inline-policy limit (whitespace-stripped measure, Decision 156). Move a statement to a customer-managed policy or trim grants."
    }

    precondition {
      # Self-access guard: PlatformAdmin must still be able to read/write its own backend bucket and
      # the one state object this root's apply depends on, checked by parsing the policy local
      # itself, never the byte-size measure above (this file carries three such measures, one per
      # policy -- personal-policies-carry-byte-preconditions does not scan this file).
      condition = alltrue([
        for arn, actions in {
          "arn:aws:s3:::agent-platform-bootstrap-tfstate"   = ["s3:ListBucket"]
          "arn:aws:s3:::agent-platform-bootstrap-tfstate/*" = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
          } : anytrue([
            for s in jsondecode(local.platform_admin_bootstrap_state_policy_json).Statement :
            s.Effect == "Allow" && !can(s.Condition)
            && contains(flatten([try(s.Resource, [])]), arn)
            && alltrue([for a in actions : contains(flatten([try(s.Action, [])]), a)])
        ])
      ])
      error_message = "PlatformAdmin BootstrapStateProvisioning no longer has an unconditioned Allow of the bootstrap-tfstate bucket and object ARNs with their needed actions -- this is the self-access grant this root's own apply depends on."
    }
  }
}

# DuckLake break-glass (CD.33 O-1 / Decision 81): an EXPLICIT, auditable PlatformAdmin grant to
# attach the DuckLake catalog read-only for inspect/repair.
import {
  id = "PlatformAdmin:DuckLakeBreakGlass"
  to = aws_iam_role_policy.platform_admin_ducklake_breakglass
}

resource "aws_iam_role_policy" "platform_admin_ducklake_breakglass" {
  name   = "DuckLakeBreakGlass"
  role   = aws_iam_role.platform_admin.id
  policy = local.platform_admin_ducklake_breakglass_policy_json

  lifecycle {
    precondition {
      condition     = length(jsonencode(jsondecode(local.platform_admin_ducklake_breakglass_policy_json))) <= 10240
      error_message = "PlatformAdmin DuckLakeBreakGlass inline policy exceeds the 10,240 B IAM inline-policy limit (whitespace-stripped measure, Decision 156). Move a statement to a customer-managed policy or trim grants."
    }
  }
}
