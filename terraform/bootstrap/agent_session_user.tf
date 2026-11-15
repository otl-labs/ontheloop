# The agent session identity: IAM user agent-platform-session, whose long-term key the session egress proxy signs with
# (Decision 256 cl.1). The proxy refuses sts:AssumeRole, so agents hold a user, not a role. The user reaches the
# DuckLake verbs only through plan A's REST API (execute-api:Invoke on its four POST routes), reads endpoint
# parameters, observability metrics and logs, and reads four role definitions by name. Every policy below is a
# customer-managed policy built from a hoisted local, and the permissions boundary is exactly the grant set, so the
# shared deployer boundary never applies to it. No access key is declared: its secret would land in state, so the
# operator mints the key from CloudShell (deploy-paths.yaml admin_out_of_band.agent_session_identity).
locals {
  agent_session_ducklake_statements = concat(
    [for api in aws_api_gateway_rest_api.agent_ducklake : {
      Sid      = "DuckLakeApiInvoke"
      Effect   = "Allow"
      Action   = "execute-api:Invoke"
      Resource = [for route in sort(keys(local.ducklake_api_routes)) : "${api.execution_arn}/${local.ducklake_api_stage}/POST/${route}"]
    }],
    [
      {
        Sid      = "DuckLakeEndpointDiscovery"
        Effect   = "Allow"
        Action   = "ssm:GetParameter"
        Resource = "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/agent-platform/ducklake/*"
      },
    ],
  )

  agent_session_observe_statements = [
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

  agent_session_ducklake_policy_json = jsonencode({
    Version   = "2012-10-17"
    Statement = local.agent_session_ducklake_statements
  })

  agent_session_observe_policy_json = jsonencode({
    Version   = "2012-10-17"
    Statement = local.agent_session_observe_statements
  })

  agent_session_boundary_policy_json = jsonencode({
    Version   = "2012-10-17"
    Statement = concat(local.agent_session_ducklake_statements, local.agent_session_observe_statements)
  })

  agent_session_guard_policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # DEP-13 (T2.44 / Decision 144 pt.3, pt.7): fences the ambient PlatformDev runtime identity
        # off the convergence-record anti-masking anchor and the tfplan/tfstate prefixes.
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
        # DEP-13 (T2.44 / Decision 113): deny tfstate READ too -- both ExternalIds live in
        # tfstate's assume_role_policy trust documents.
        Sid      = "DenyStateRead"
        Effect   = "Deny"
        Action   = ["s3:GetObject"]
        Resource = ["${local.data_lake_bucket_arn}/tfstate/personal/*"]
      },
      {
        # Saved plans embed prior state (catalog passwords, ExternalIds; rec-2768) and tfstate/* covers every
        # root including tfstate/github and its lock file (rec-4353). Agents have no reason to read or write them.
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
        # The plan reviewer reads plan JSON with values and writes these transcripts, and only CI writes this prefix.
        # convergence/ and the rest of ci-rca-evidence/ stay open on purpose: agents read convergence/personal/sandbox.json
        # and file ci-rca-evidence/<sha>.json. The bootstrap root state is in another bucket where PlatformDev has no Allow.
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
        # The trust documents of these two roles carry the ExternalIds; removed when owed rec 3 retires those trusts.
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
        # STS takes no policy on GetSessionToken, so the call cannot be denied, but every request signed with a token
        # minted from the key is; the proxy's long-term-key signatures carry no token and pass.
        Sid       = "DenyTemporaryCredentialUse"
        Effect    = "Deny"
        Action    = "*"
        Resource  = "*"
        Condition = { Null = { "aws:TokenIssueTime" = "false" } }
      },
    ]
  })
}

resource "aws_iam_user" "agent_session" {
  name                 = "agent-platform-session"
  permissions_boundary = aws_iam_policy.agent_session_boundary.arn

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_iam_policy" "agent_session_ducklake" {
  name   = "agent-platform-session-ducklake"
  policy = local.agent_session_ducklake_policy_json

  lifecycle {
    precondition {
      condition     = length(jsonencode(jsondecode(local.agent_session_ducklake_policy_json))) <= 6144
      error_message = "agent-platform-session-ducklake exceeds the 6,144 B managed-policy limit (a LimitExceeded surfaces only at apply)."
    }
  }
}

resource "aws_iam_policy" "agent_session_observe" {
  name   = "agent-platform-session-observe"
  policy = local.agent_session_observe_policy_json

  lifecycle {
    precondition {
      condition     = length(jsonencode(jsondecode(local.agent_session_observe_policy_json))) <= 6144
      error_message = "agent-platform-session-observe exceeds the 6,144 B managed-policy limit (a LimitExceeded surfaces only at apply)."
    }
  }
}

resource "aws_iam_policy" "agent_session_guard" {
  name   = "agent-platform-session-guard"
  policy = local.agent_session_guard_policy_json

  lifecycle {
    precondition {
      condition     = length(jsonencode(jsondecode(local.agent_session_guard_policy_json))) <= 6144
      error_message = "agent-platform-session-guard exceeds the 6,144 B managed-policy limit (a LimitExceeded surfaces only at apply)."
    }
  }
}

resource "aws_iam_policy" "agent_session_boundary" {
  name   = "agent-platform-session-boundary"
  policy = local.agent_session_boundary_policy_json

  lifecycle {
    precondition {
      condition     = length(jsonencode(jsondecode(local.agent_session_boundary_policy_json))) <= 6144
      error_message = "agent-platform-session-boundary exceeds the 6,144 B managed-policy limit (a LimitExceeded surfaces only at apply)."
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
