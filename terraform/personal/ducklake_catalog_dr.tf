# DuckLake catalog disaster-recovery Lambda (T2.18 FP-B / CD.34, Decision 82).
#
# Daily pg_dump --format=custom --serializable-deferrable of the Neon catalog to a
# dedicated versioned + lifecycle-managed S3 bucket. Emits CatalogDumpSuccess to CloudWatch
# DuckLakeCatalogDR namespace. A >25h freshness alarm pages via the shared SNS topic when
# a daily dump is missed.
#
# pg_dump is provided by the ducklake-pgclient layer (/opt/bin/pg_dump + /opt/lib/libpq.so;
# PG16, AL2023/x86_64). No pip wheel exists for pg_dump; the binary is vendored to S3 and
# fetched at build time by build_pgclient_layer (scripts/build_lambda.py).
#
# ---------------------------------------------------------------------------
# APPLY POSTURE (Decision 77, 126, 144 cl.5): CI plans and applies; the guard decides the route.
# ---------------------------------------------------------------------------
# terraform/personal applies through CI (speculative plan on the PR, apply-sandbox on merge). The
# deterministic guard (scripts/terraform_apply_guard.py) auto-applies in-budget changes and routes role
# creation, trust changes, destroys and out-of-budget IAM to the tf-gated-apply Environment.
# IAM must precede the code that reads it (terraform CLAUDE.md IAM-precedence).
#
# CODE/INFRA COUPLING (Decision 125/126, environment-taxonomy.yaml conformance): RESOLVED. The
# aws_lambda_function resource below carries a lifecycle block ignoring source_code_hash changes, so
# code-only redeploys never surface as a Terraform diff. Routine code deploys go through the governed
# code-deploy channel; `build_lambda --ducklake-only --deploy` is break-glass only (Decision 125 pt 2-5).

locals {
  ducklake_catalog_dr_function = "agent-platform-ducklake-catalog-dr"
  ducklake_dr_bucket_name      = "agent-platform-ducklake-catalog-dr"
}

# ---------------------------------------------------------------------------
# DR S3 bucket: versioned + SSE + public-access-block + lifecycle.
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "ducklake_catalog_dr" {
  bucket = local.ducklake_dr_bucket_name

  tags = {
    Name    = "DuckLake Catalog DR"
    Purpose = "T2.18 FP-B catalog disaster-recovery dump storage - CD.34"
  }
}

resource "aws_s3_bucket_versioning" "ducklake_catalog_dr" {
  bucket = aws_s3_bucket.ducklake_catalog_dr.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "ducklake_catalog_dr" {
  bucket = aws_s3_bucket.ducklake_catalog_dr.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "ducklake_catalog_dr" {
  bucket = aws_s3_bucket.ducklake_catalog_dr.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "ducklake_catalog_dr" {
  bucket = aws_s3_bucket.ducklake_catalog_dr.id

  rule {
    id     = "dr-dump-expiry"
    status = "Enabled"

    expiration {
      # 30-day retention (tunable to 7). See CD.34 O-2 / CD.33 Decision 82.
      # On any DuckLake/DuckDB engine bump (OQ.12), re-baseline this window.
      days = 30
    }

    noncurrent_version_expiration {
      # Retain >=7 days of prior versions (matches FILE_CLEANUP_GRACE_DAYS). Daily dumps use
      # unique timestamped keys so overwrites are not expected, but versioning still protects
      # against an accidental overwrite/partial-failure: keep the immediately-preceding version
      # restorable for a week before permanent deletion (code-review H1).
      noncurrent_days = 7
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }

    filter {}
  }
}

# ---------------------------------------------------------------------------
# CloudWatch log group (pre-created so the exec-role grant can be scoped to its ARN).
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "ducklake_catalog_dr" {
  name              = "/aws/lambda/${local.ducklake_catalog_dr_function}"
  retention_in_days = 14

  tags = {
    Name    = "DuckLake Catalog DR Logs"
    Purpose = "T2.18 FP-B ducklake_catalog_dr Lambda"
  }
}

# ---------------------------------------------------------------------------
# Exec role + inline policy: DSN read, DR bucket Put/List, metrics, logs.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "ducklake_catalog_dr" {
  # Decision 144 (T2.48): mandatory broad-but-bounded exec-identity boundary (16/17 roles; PlatformAdmin excluded).
  name                 = "agent-platform-ducklake-catalog-dr"
  description          = "Catalog DR Lambda: Neon DSN read, DR bucket Put/List, DuckLakeCatalogDR metrics"
  permissions_boundary = "arn:aws:iam::${var.account_id}:policy/agent-platform-github-ci-apply-boundary"
  assume_role_policy   = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "ducklake_catalog_dr" {
  name = "DuckLakeCatalogDRRuntime"
  role = aws_iam_role.ducklake_catalog_dr.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "Logs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = ["${aws_cloudwatch_log_group.ducklake_catalog_dr.arn}:*"]
      },
      {
        Sid    = "NeonDsnRead"
        Effect = "Allow"
        Action = ["secretsmanager:GetSecretValue"]
        # pragma: allowlist secret
        Resource = [aws_secretsmanager_secret.ducklake_neon_catalog_dsn.arn]
      },
      {
        # Decision 237: SSM twin of the Secrets Manager read above; secrets-to-ssm-retire removes the old one.
        Sid      = "NeonDsnParameterRead"
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = ["arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/secret/ducklake-neon-catalog-dsn"]
      },
      {
        Sid      = "DRBucketWrite"
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = ["${aws_s3_bucket.ducklake_catalog_dr.arn}/*"]
      },
      {
        Sid      = "DRBucketList"
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = [aws_s3_bucket.ducklake_catalog_dr.arn]
      },
      {
        # PutMetricData does not support resource-level scoping; constrain to the DR namespace.
        Sid      = "CloudWatchMetrics"
        Effect   = "Allow"
        Action   = ["cloudwatch:PutMetricData"]
        Resource = "*"
        Condition = {
          StringEquals = {
            "cloudwatch:namespace" = "DuckLakeCatalogDR"
          }
        }
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# pgclient layer version (pg_dump 16 + libpq.so + transitive libs).
# Built by build_pgclient_layer in scripts/build_lambda.py (S3/CDN fetch at build time).
# ---------------------------------------------------------------------------

resource "aws_lambda_layer_version" "ducklake_pgclient" {
  layer_name               = "ducklake-pgclient"
  description              = "pg_dump 16 + libpq.so + transitive libs under /opt/bin + /opt/lib (T2.18 FP-B)"
  compatible_runtimes      = ["python3.12"]
  compatible_architectures = ["x86_64"]

  s3_bucket = aws_s3_bucket.data_lake.id
  s3_key    = "lambda-packages/ducklake-pgclient-layer.zip"

  # T2.42 c1 (rec-2646/rec-2654 pattern extended to layers, Decision 126 pt3/pt5, DEP-03): decouples
  # routine rebuild churn from this IAM-gated apply path. No source_code_hash is set (Terraform reads no
  # build artifact) and this block stays as the conformance marker and a backstop. Terraform never
  # republishes a layer on content change (it did not before either; rec-4253). s3_key stays the
  # fixed literal (unaffected by T2.42 c3's content-addressed upload path).
  #
  # SILENT-FREEZE GUARD: source_code_hash is not set, so Terraform only publishes a new layer
  # version when `description` changes. This description is fully static (no interpolation) -- ANY
  # content bump (e.g. a pg_dump/libpq version bump) leaves the description unchanged, so terraform
  # publishes NO new layer version and the function silently keeps the OLD pgclient binaries. Any
  # such bump MUST also bump the description/marker (or go via the deploy-verb publish path) to
  # force republish.
  lifecycle {
    ignore_changes = [source_code_hash]
  }
}

# ---------------------------------------------------------------------------
# DR Lambda function.
# ---------------------------------------------------------------------------

resource "aws_lambda_function" "ducklake_catalog_dr" {
  function_name = local.ducklake_catalog_dr_function
  description   = "T2.18 FP-B DuckLake catalog DR Lambda (CD.34/Decision 82). Daily pg_dump -> S3."
  role          = aws_iam_role.ducklake_catalog_dr.arn
  runtime       = "python3.12"
  handler       = "src.lambdas.ducklake_catalog_dr.handler.handler"
  architectures = ["x86_64"]
  timeout       = 300
  memory_size   = 512

  s3_bucket = aws_s3_bucket.data_lake.id
  s3_key    = "lambda-packages/ducklake-catalog-dr.zip"

  layers = [
    aws_lambda_layer_version.ducklake_pgclient.arn,
    # ducklake-deps supplies PyYAML (+ boto3): catalog_dr.py imports src.common.ducklake_runtime
    # (for emit_metric / PINNED_DUCKDB_VERSION / fetch_dsn), whose module-level `import yaml` fails
    # without it. duckdb is imported lazily and never exercised by the DR path (no connection opened).
    aws_lambda_layer_version.ducklake_deps.arn,
  ]

  environment {
    variables = {
      DUCKLAKE_DR_BUCKET = local.ducklake_dr_bucket_name
    }
  }

  depends_on = [
    aws_iam_role_policy.ducklake_catalog_dr,
    aws_cloudwatch_log_group.ducklake_catalog_dr,
  ]

  tags = {
    Name    = "DuckLake Catalog DR"
    Purpose = "T2.18 FP-B catalog disaster-recovery Lambda"
  }

  # Decision 125 physical decoupling: code deploys go through the governed code-deploy channel
  # (build_lambda --deploy is break-glass), not terraform. No source_code_hash is set (Terraform reads no
  # build artifact); this block is the conformance marker and a backstop (rec-2646/rec-2654).
  lifecycle {
    ignore_changes = [source_code_hash]
  }
}

# ---------------------------------------------------------------------------
# Function URL (AWS_IAM) -- smoke-test invoke ingress.
# ---------------------------------------------------------------------------

resource "aws_lambda_function_url" "ducklake_catalog_dr" {
  function_name      = aws_lambda_function.ducklake_catalog_dr.function_name
  authorization_type = "AWS_IAM"
}

# ---------------------------------------------------------------------------
# EventBridge schedule: weekly cron(0 3 ? * SUN *) -- 03:00 UTC Sunday.
#
# Lowered from daily to weekly by neon-egress-reduction (D1): the daily full-catalog pg_dump was a
# session-independent Neon egress line (CD.34 called it "a negligible add-on" -- true for storage,
# FALSE for egress). Paid-tier Neon provides 7-day PITR for finer-grained recovery BETWEEN the weekly
# full dumps, so the durability floor is preserved while the pg_dump egress line drops ~7x.
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "ducklake_catalog_dr" {
  name                = "agent-platform-ducklake-catalog-dr"
  description         = "Weekly DuckLake catalog DR pg_dump (T2.18 FP-B / CD.34; neon-egress D1). cron 03:00 UTC Sunday."
  schedule_expression = "cron(0 3 ? * SUN *)"
  state               = "ENABLED"

  tags = {
    Name    = "DuckLake Catalog DR Schedule"
    Purpose = "T2.18 FP-B weekly catalog DR - neon-egress D1"
  }
}

resource "aws_cloudwatch_event_target" "ducklake_catalog_dr" {
  rule      = aws_cloudwatch_event_rule.ducklake_catalog_dr.name
  target_id = "ducklake-catalog-dr"
  arn       = aws_lambda_function.ducklake_catalog_dr.arn
}

resource "aws_lambda_permission" "ducklake_catalog_dr" {
  statement_id  = "AllowEventBridgeCatalogDR"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ducklake_catalog_dr.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.ducklake_catalog_dr.arn
}

# ---------------------------------------------------------------------------
# Freshness alarm: 7-day lookback via DAILY periods (period=86400, evaluation_periods=7).
#
# AWS hard-caps EvaluationPeriods*Period at 604800s (1 week) for alarms with period>=3600, so the
# prior ~8-day hourly window (192*3600=691200) was rejected at apply with ValidationError. 7 days is
# the maximum achievable window; built from DAILY periods it is also correct for a weekly dump:
#   period=86400, evaluation_periods=7, datapoints_to_alarm=7   (7*86400 = 604800, exactly the ceiling)
# meaning "in all 7 of the last 7 daily periods, CatalogDumpSuccess < 1".
# Why 7 daily periods does not false-alarm under a weekly (Sunday) dump: the success lands in one
# daily bucket every 7 buckets, so any 7 consecutive daily buckets contain exactly one success --
# never 7-of-7 breaching in normal operation (max empty run = 6 days, Mon-Sat). The alarm fires only
# when a scheduled weekly dump is genuinely missed (>=7 consecutive empty daily periods).
# treat_missing_data=breaching: missing datapoints (no invocation) are counted as failing.
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "ducklake_catalog_dr_freshness" {
  alarm_name          = "ducklake-catalog-dr-freshness"
  alarm_description   = "DuckLake catalog DR missed: no CatalogDumpSuccess in 7 days. T2.18 FP-B CD.34 (neon-egress D1 weekly cadence)."
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 7
  datapoints_to_alarm = 7
  metric_name         = "CatalogDumpSuccess"
  namespace           = "DuckLakeCatalogDR"
  period              = 86400
  statistic           = "Sum"
  threshold           = 1
  treat_missing_data  = "breaching"

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]

  tags = {
    Name    = "DuckLake Catalog DR Freshness Alarm"
    Purpose = "T2.18 FP-B CD.34 over-25h freshness guard"
  }
}

# ---------------------------------------------------------------------------
# Outputs.
# ---------------------------------------------------------------------------

output "ducklake_catalog_dr_function_url" {
  description = "AWS_IAM Function URL for the ducklake_catalog_dr Lambda (T2.18 FP-B smoke gate)."
  value       = aws_lambda_function_url.ducklake_catalog_dr.function_url
}

output "ducklake_catalog_dr_function_name" {
  description = "ducklake_catalog_dr Lambda function name."
  value       = aws_lambda_function.ducklake_catalog_dr.function_name
}

output "ducklake_catalog_dr_bucket" {
  description = "DR S3 bucket name (versioned + lifecycle-managed)."
  value       = aws_s3_bucket.ducklake_catalog_dr.bucket
}
