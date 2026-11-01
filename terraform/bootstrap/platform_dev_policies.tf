# PlatformDev DailyOps inline policy, adopted from its terraform/personal source file (PLAN-
# platform-iam-bootstrap-adopt, rec-3838 plan 1 of 2).
#
# Zero-diff import: the statements are unchanged from the personal-root source. Object references
# become bootstrap locals built from literal names and var.account_id / var.aws_region; the two
# inference-credential secret ARNs come from by-name data source lookups (exact ARN, Decision 157).
# Decision 213 cl.1 is carried verbatim: the DuckLake invoke statements name the writer, reader and
# telemetry writer as literal ARN templates (the only forms test_url_only_invoke_guard recognises
# without a name-only local -- never a full-ARN local referenced as bare local.x).
locals {
  data_lake_bucket_arn = "arn:aws:s3:::agent-platform-data-lake"
  counters_table_arn   = "arn:aws:dynamodb:${var.aws_region}:${var.account_id}:table/agent-platform-counters"

  ducklake_prod_data_prefix  = "ducklake"
  ducklake_smoke_data_prefix = "ducklake-neon-smoke"

  ducklake_writer_function           = "agent-platform-ducklake-writer"
  ducklake_reader_function           = "agent-platform-ducklake-reader"
  ducklake_telemetry_writer_function = "agent-platform-ducklake-telemetry-writer"
  ducklake_telemetry_reader_function = "agent-platform-ducklake-telemetry-reader"

  platform_dev_runtime_policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "S3ReadWrite"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = ["${local.data_lake_bucket_arn}/*"]
      },
      {
        Sid      = "S3List"
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = [local.data_lake_bucket_arn]
      },
      {
        Sid    = "DynamoDBCounters"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:Query",
          "dynamodb:Scan",
          "dynamodb:UpdateItem",
        ]
        Resource = [local.counters_table_arn]
      },
      {
        # T2.19 recs cutover: the ops portal runs as PlatformDev at RUNTIME and reaches the closed
        # DuckLake boundary by SigV4-invoking the writer/reader AWS_IAM Function URLs. Decision 213
        # adds the telemetry writer and reader. Two statements because the permissions boundary denies a direct
        # invoke (DenyDuckLakeDirectInvoke) and InvokeFunction here carries Bool
        # lambda:InvokedViaFunctionUrl = true, so it authorizes only the URL path.
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
      {
        # Decision 237 (secrets-to-ssm-grants): the agent runtime reads exactly the two inference-key parameters by
        # literal ARN (the parameters do not exist yet, so no data-source lookup; ARNs are built from variables).
        Sid    = "InferenceCredentialsParameterRead"
        Effect = "Allow"
        Action = ["ssm:GetParameter"]
        Resource = [
          "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/secret/agent-platform-deepseek-api-key",
          "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/secret/agent-platform-anthropic-api-key",
        ]
      },
      {
        # Scoped to exactly the two inference-credential secret ARNs (by-name lookup, Decision 157
        # DescribeSecret metadata only -- the rendered document is byte-identical to live).
        Sid    = "InferenceCredentialsRead"
        Effect = "Allow"
        Action = ["secretsmanager:GetSecretValue"]
        Resource = [
          data.aws_secretsmanager_secret.deepseek_api_key.arn,
          data.aws_secretsmanager_secret.anthropic_api_key.arn,
        ]
      },
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
        Sid      = "DataLakeBucketConfigRead"
        Effect   = "Allow"
        Action   = ["s3:GetLifecycleConfiguration", "s3:GetBucketVersioning"]
        Resource = [local.data_lake_bucket_arn]
      },
      {
        Sid      = "DataLakeVersionListRead"
        Effect   = "Allow"
        Action   = ["s3:ListBucketVersions"]
        Resource = [local.data_lake_bucket_arn]
        Condition = {
          StringLike = {
            "s3:prefix" = [
              "${local.ducklake_prod_data_prefix}/*",
              "${local.ducklake_smoke_data_prefix}/*",
            ]
          }
        }
      },
      {
        Sid    = "DataLakeObjectVersionRead"
        Effect = "Allow"
        Action = ["s3:GetObjectVersion"]
        Resource = [
          "${local.data_lake_bucket_arn}/${local.ducklake_prod_data_prefix}/*",
          "${local.data_lake_bucket_arn}/${local.ducklake_smoke_data_prefix}/*",
        ]
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
    ]
  })
}

data "aws_secretsmanager_secret" "deepseek_api_key" {
  name = "agent-platform-deepseek-api-key"
}

data "aws_secretsmanager_secret" "anthropic_api_key" {
  name = "agent-platform-anthropic-api-key"
}

import {
  id = "PlatformDev:DailyOps"
  to = aws_iam_role_policy.platform_dev_runtime
}

resource "aws_iam_role_policy" "platform_dev_runtime" {
  name   = "DailyOps"
  role   = aws_iam_role.platform_dev.id
  policy = local.platform_dev_runtime_policy_json

  lifecycle {
    precondition {
      # Decision 156: AWS excludes whitespace from the 10,240 B inline-policy limit, so measure
      # the WHITESPACE-STRIPPED/minified rendering.
      condition     = length(jsonencode(jsondecode(local.platform_dev_runtime_policy_json))) <= 10240
      error_message = "PlatformDev DailyOps inline policy exceeds the 10,240 B IAM inline-policy limit (whitespace-stripped measure, Decision 156). Move a statement to a customer-managed policy or trim grants."
    }
  }
}
