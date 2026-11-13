# agent-platform-session: the routine agent identity (Decision 255, PLAN-agent-proxy-signed-access).
#
# The session proxy refuses sts:AssumeRole, so agent sessions no longer chain through PlatformDev. They reach AWS as
# this one IAM user, whose access key lives only in each environment's egress proxy; the proxy signs every call, so no
# session holds or mints a credential. The key is created out of band (Decision 237 cl.3; deploy-paths.yaml
# admin_out_of_band.agent_session_identity), never as an aws_iam_access_key (its secret would land in state).
#
# Three customer-managed policies, not inline: an IAM user's inline policies share one 2,048 character quota.
# Grants are exactly what sessions use (the session consumers are named in the plan); a missing grant is a plan
# defect, never a local widening. tests/checks/iam_tf/test_agent_session_user.py pins every clause below.
locals {
  agent_session_ducklake_policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DuckLakeInvokeUrl"
        Effect = "Allow"
        Action = ["lambda:InvokeFunctionUrl"]
        Resource = [
          "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:${local.ducklake_writer_function}",
          "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:${local.ducklake_writer_function}:*",
          "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:${local.ducklake_reader_function}",
          "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:${local.ducklake_reader_function}:*",
          "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:${local.ducklake_telemetry_writer_function}",
          "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:${local.ducklake_telemetry_writer_function}:*",
          "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:${local.ducklake_telemetry_reader_function}",
          "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:${local.ducklake_telemetry_reader_function}:*",
        ]
      },
      {
        Sid    = "DuckLakeInvokeViaUrlOnly"
        Effect = "Allow"
        Action = ["lambda:InvokeFunction"]
        Resource = [
          "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:${local.ducklake_writer_function}",
          "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:${local.ducklake_writer_function}:*",
          "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:${local.ducklake_reader_function}",
          "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:${local.ducklake_reader_function}:*",
          "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:${local.ducklake_telemetry_writer_function}",
          "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:${local.ducklake_telemetry_writer_function}:*",
          "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:${local.ducklake_telemetry_reader_function}",
          "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:${local.ducklake_telemetry_reader_function}:*",
        ]
        Condition = {
          Bool = {
            "lambda:InvokedViaFunctionUrl" = "true"
          }
        }
      },
      {
        Sid    = "DuckLakeEndpointDiscovery"
        Effect = "Allow"
        Action = ["ssm:GetParameter"]
        Resource = [
          "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/agent-platform/ducklake/*",
        ]
      },
    ]
  })

  agent_session_observe_policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ObservabilityMetricRead"
        Effect = "Allow"
        Action = [
          "cloudwatch:GetMetricStatistics",
          "cloudwatch:GetMetricData",
          "cloudwatch:ListMetrics",
          "cloudwatch:DescribeAlarms",
          "logs:DescribeLogGroups",
        ]
        Resource = "*"
      },
      {
        Sid    = "ObservabilityLogStreamRead"
        Effect = "Allow"
        Action = [
          "logs:DescribeLogStreams",
          "logs:GetLogEvents",
          "logs:FilterLogEvents",
        ]
        Resource = [
          "arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:/aws/lambda/agent-platform-*",
          "arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:/aws/lambda/agent-platform-*:log-stream:*",
        ]
      },
      {
        Sid    = "IAMRoleRead"
        Effect = "Allow"
        Action = [
          "iam:GetRole",
          "iam:GetRolePolicy",
          "iam:ListRolePolicies",
          "iam:ListAttachedRolePolicies",
        ]
        Resource = "arn:aws:iam::${var.account_id}:role/*"
      },
    ]
  })

  agent_session_guard_policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DenyStateAndConvergenceWrite"
        Effect = "Deny"
        Action = ["s3:PutObject", "s3:DeleteObject"]
        Resource = [
          "${local.data_lake_bucket_arn}/convergence/personal/*",
          "${local.data_lake_bucket_arn}/tfplan/personal/*",
          "${local.data_lake_bucket_arn}/tfstate/personal/*",
        ]
      },
      {
        Sid      = "DenyStateRead"
        Effect   = "Deny"
        Action   = ["s3:GetObject"]
        Resource = ["${local.data_lake_bucket_arn}/tfstate/personal/*"]
      },
      {
        Sid    = "DenyTerraformArtifactRead"
        Effect = "Deny"
        Action = [
          "s3:GetObject",
          "s3:GetObjectVersion",
          "s3:GetObjectVersionAcl",
          "s3:GetObjectVersionTagging",
          "s3:GetObjectVersionAttributes",
          "s3:GetObjectAttributes",
        ]
        Resource = [
          "${local.data_lake_bucket_arn}/tfstate/*",
          "${local.data_lake_bucket_arn}/tfplan/*",
        ]
      },
      {
        Sid    = "DenyTerraformArtifactWrite"
        Effect = "Deny"
        Action = ["s3:PutObject", "s3:DeleteObject", "s3:DeleteObjectVersion"]
        Resource = [
          "${local.data_lake_bucket_arn}/tfstate/*",
          "${local.data_lake_bucket_arn}/tfplan/*",
        ]
      },
      {
        Sid    = "DenyReviewTranscriptRead"
        Effect = "Deny"
        Action = [
          "s3:GetObject",
          "s3:GetObjectVersion",
          "s3:GetObjectVersionAcl",
          "s3:GetObjectVersionTagging",
          "s3:GetObjectVersionAttributes",
          "s3:GetObjectAttributes",
        ]
        Resource = ["${local.data_lake_bucket_arn}/ci-rca-evidence/review-transcripts/*"]
      },
      {
        Sid      = "DenyReviewTranscriptWrite"
        Effect   = "Deny"
        Action   = ["s3:PutObject", "s3:DeleteObject", "s3:DeleteObjectVersion"]
        Resource = ["${local.data_lake_bucket_arn}/ci-rca-evidence/review-transcripts/*"]
      },
      {
        Sid    = "DenyStateAndConvergenceVersionRead"
        Effect = "Deny"
        Action = [
          "s3:GetObjectVersion",
          "s3:GetObjectVersionAcl",
          "s3:GetObjectVersionTagging",
          "s3:GetObjectVersionAttributes",
        ]
        Resource = [
          "${local.data_lake_bucket_arn}/convergence/personal/*",
          "${local.data_lake_bucket_arn}/tfplan/personal/*",
          "${local.data_lake_bucket_arn}/tfstate/personal/*",
        ]
      },
      {
        Sid      = "DenyStateAndConvergenceVersionList"
        Effect   = "Deny"
        Action   = ["s3:ListBucketVersions"]
        Resource = [local.data_lake_bucket_arn]
        Condition = {
          StringLike = {
            "s3:prefix" = [
              "convergence/personal/*",
              "tfplan/personal/*",
              "tfstate/personal/*",
            ]
          }
        }
      },
      {
        # The trust documents of these two roles carry the ExternalIds. Removed with the trusts (owed rec 3).
        Sid      = "DenyExternalIdTrustRead"
        Effect   = "Deny"
        Action   = ["iam:GetRole"]
        Resource = [aws_iam_role.platform_dev.arn, aws_iam_role.platform_admin.arn]
      },
      {
        Sid    = "DenySessionEscalation"
        Effect = "Deny"
        Action = [
          "sts:AssumeRole*",
          "sts:AssumeRoot",
          "sts:GetFederationToken",
          "iam:Create*",
          "iam:Update*",
          "iam:Put*",
          "iam:Attach*",
          "iam:Detach*",
          "iam:Delete*",
          "iam:Add*",
          "iam:Remove*",
          "iam:Tag*",
          "iam:Untag*",
          "iam:Set*",
          "iam:Upload*",
          "iam:Enable*",
          "iam:Deactivate*",
          "iam:Change*",
          "iam:Reset*",
          "iam:PassRole",
        ]
        Resource = "*"
      },
      {
        # STS takes no policy on GetSessionToken, so the call cannot be denied; every request signed with a token
        # minted from the key is, while the proxy's long-term-key signatures carry no token and pass.
        Sid      = "DenyTemporaryCredentialUse"
        Effect   = "Deny"
        Action   = "*"
        Resource = "*"
        Condition = {
          Null = {
            "aws:TokenIssueTime" = "false"
          }
        }
      },
    ]
  })
}

resource "aws_iam_user" "agent_session" {
  name                 = "agent-platform-session"
  permissions_boundary = aws_iam_policy.github_ci_apply_boundary.arn

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_iam_policy" "agent_session_ducklake" {
  name        = "agent-platform-session-ducklake"
  description = "agent-platform-session: URL-only invoke of the four DuckLake Function URLs and endpoint lookup"
  policy      = local.agent_session_ducklake_policy_json

  lifecycle {
    precondition {
      condition     = length(jsonencode(jsondecode(local.agent_session_ducklake_policy_json))) <= 6144
      error_message = "agent-platform-session-ducklake exceeds the 6,144 B managed-policy limit (whitespace-stripped measure, Decision 156)."
    }
  }
}

resource "aws_iam_policy" "agent_session_observe" {
  name        = "agent-platform-session-observe"
  description = "agent-platform-session: metric, agent-platform log and role-policy reads"
  policy      = local.agent_session_observe_policy_json

  lifecycle {
    precondition {
      condition     = length(jsonencode(jsondecode(local.agent_session_observe_policy_json))) <= 6144
      error_message = "agent-platform-session-observe exceeds the 6,144 B managed-policy limit (whitespace-stripped measure, Decision 156)."
    }
  }
}

resource "aws_iam_policy" "agent_session_guard" {
  name        = "agent-platform-session-guard"
  description = "agent-platform-session: Deny statements only (PlatformDev's Denies, minting and IAM writes)"
  policy      = local.agent_session_guard_policy_json

  lifecycle {
    precondition {
      condition     = length(jsonencode(jsondecode(local.agent_session_guard_policy_json))) <= 6144
      error_message = "agent-platform-session-guard exceeds the 6,144 B managed-policy limit (whitespace-stripped measure, Decision 156)."
    }
  }
}

resource "aws_iam_user_policy_attachment" "agent_session_ducklake" {
  user       = aws_iam_user.agent_session.name
  policy_arn = aws_iam_policy.agent_session_ducklake.arn
}

resource "aws_iam_user_policy_attachment" "agent_session_observe" {
  user       = aws_iam_user.agent_session.name
  policy_arn = aws_iam_policy.agent_session_observe.arn
}

resource "aws_iam_user_policy_attachment" "agent_session_guard" {
  user       = aws_iam_user.agent_session.name
  policy_arn = aws_iam_policy.agent_session_guard.arn
}
