# DuckLake maintenance SMOKE Lambda (T2.18 c9 follow-on; bundled Decision amending Decision 81
# clause 1, runtime artifacts 3 -> 4).
#
# CI-invokable smoke-safe sibling of ducklake_maintenance.tf's admin singleton (Fable
# frontier-architecture consult: scope every identity by the worst verb it can reach, and enforce
# the boundary in a primitive OUTSIDE the agent merge loop -- a separate Lambda + IAM execution
# role, not an in-handler guard). Implements the four non-destructive/disposable-smoke-catalog
# cadences (merge/gc/hot_merge/breaker_probe) that moved OFF the admin function in this split, so
# github_ci_branch (the always-on public-repo CI identity) can be granted invoke here without ever
# touching the production-destructive admin function.
#
# reserved_concurrent_executions = 1: singleton, same invariant as the admin function
# (ducklake_maintenance.tf). The merge/gc/hot_merge EventBridge rules move HERE from
# ducklake_maintenance.tf -- the rule resources use `moved {}` blocks below to preserve their AWS
# `name` (and therefore ARN), so the existing bootstrap EventBridgeWrite grant (enumerated by rule
# ARN) does not need an out-of-band admin edit for the rule side. The EventBridge TARGET and the
# Lambda PERMISSION are NOT moved -- they now point at THIS function, which is a materially
# different real-world object than the admin function's target/permission, so those are destroyed
# on the admin side and created fresh here (not a `moved` case).
#
# ---------------------------------------------------------------------------
# APPLY POSTURE (Decision 35 + 77 + 92/98): the exec role + inline policy below are a NEW IAM role,
# which trips the Decision-77 deterministic guard (IAM CREATE is out-of-budget per T2.25 / Decision
# 92 point 5 -- role CREATEs stay gated even for a boundary-carrying role). This file's IAM
# resources require agent_platform_admin `-target` apply (operator/ADMIN handoff, NOT
# tf-gated-apply). The non-IAM resources (function, Function URL, EventBridge rules/targets/
# permissions, alarm) auto-apply behind the guard once the exec role exists in state.
#
# CODE/INFRA COUPLING (Decision 125, environment-taxonomy.yaml conformance): the aws_lambda_function
# resource below sets no source_code_hash (Terraform reads no build artifact) and keeps a lifecycle
# block ignoring it as the conformance marker and a backstop -- code deploys go via
# the governed CD channel (.github/workflows/deploy-ducklake-lambdas.yml, T2.38), never via this
# apply path.
# ---------------------------------------------------------------------------

locals {
  ducklake_maintenance_smoke_function = "agent-platform-ducklake-maintenance-smoke"
}

variable "ducklake_maintenance_smoke_reserved_concurrency" {
  description = "Reserved concurrency for the smoke maintenance singleton (mirrors the admin function's Decision 81 cl.6 posture). 1 = singleton; -1 = unreserved (only for a quota/load test)."
  type        = number
  default     = 1
}

# ---------------------------------------------------------------------------
# CloudWatch log group (pre-created so the execution-role grant can be scoped to its ARN).
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "ducklake_maintenance_smoke" {
  name              = "/aws/lambda/${local.ducklake_maintenance_smoke_function}"
  retention_in_days = 14

  tags = {
    Name    = "DuckLake Maintenance Smoke Logs"
    Purpose = "T2.18 c9 ducklake_maintenance_smoke singleton"
  }
}

# ---------------------------------------------------------------------------
# Smoke-prefix-scoped execution role: S3 Get/Put/Delete/List on the SMOKE prefix ONLY (no prod
# prefix grant -- the blast-radius boundary this whole split exists to enforce), Neon DSN read,
# DuckLakeMaintenance CloudWatch metrics (shared namespace with the admin function; this function's
# data carry the Catalog=smoke dimension, so the two breaker alarms watch distinct metrics), logs.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "ducklake_maintenance_smoke" {
  # Decision 144 (T2.48): mandatory broad-but-bounded exec-identity boundary (16/17 roles; PlatformAdmin excluded).
  name                 = "agent-platform-ducklake-maintenance-smoke"
  description          = "Maintenance smoke singleton: S3 RW+Delete on the SMOKE prefix ONLY, Neon DSN read, maintenance metrics"
  permissions_boundary = "arn:aws:iam::${var.account_id}:policy/agent-platform-github-ci-apply-boundary"
  assume_role_policy   = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "ducklake_maintenance_smoke" {
  name = "DuckLakeMaintenanceSmokeRuntime"
  role = aws_iam_role.ducklake_maintenance_smoke.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "Logs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = ["${aws_cloudwatch_log_group.ducklake_maintenance_smoke.arn}:*"]
      },
      {
        Sid      = "NeonDsnRead"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [aws_secretsmanager_secret.ducklake_smoke_maintainer_dsn.arn]
      },
      {
        # Decision 237: SSM twin of the Secrets Manager read above; secrets-to-ssm-retire removes the old one.
        Sid      = "NeonDsnParameterRead"
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = ["arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/secret/agent-platform-ducklake-smoke-maintainer-dsn"]
      },
      {
        # Smoke prefix ONLY -- no prod prefix. This is the resource-shape half of the blast-radius
        # boundary (the IAM half is the CI invoke grant on THIS function ARN only, oidc.tf).
        Sid      = "S3SmokeDataReadWriteDelete"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = ["${aws_s3_bucket.data_lake.arn}/${local.ducklake_smoke_data_prefix}/*"]
      },
      {
        Sid      = "S3ListSmokeDataPrefix"
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = [aws_s3_bucket.data_lake.arn]
        Condition = {
          StringLike = {
            "s3:prefix" = ["${local.ducklake_smoke_data_prefix}/*"]
          }
        }
      },
      {
        # PutMetricData does not support resource-level scoping; constrain to the maintenance namespace.
        Sid      = "CloudWatchMetrics"
        Effect   = "Allow"
        Action   = ["cloudwatch:PutMetricData"]
        Resource = "*"
        Condition = {
          StringEquals = {
            "cloudwatch:namespace" = "DuckLakeMaintenance"
          }
        }
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# Lambda function (from S3). Layers: ducklake-deps + ducklake-extensions ONLY -- NO pgclient (the
# smoke function never runs restore_drill; that stays admin-side).
# ---------------------------------------------------------------------------

resource "aws_lambda_function" "ducklake_maintenance_smoke" {
  function_name = local.ducklake_maintenance_smoke_function
  description   = "T2.18 c9 DuckLake maintenance smoke singleton. CI-invokable merge/gc/hot_merge/breaker_probe only."
  role          = aws_iam_role.ducklake_maintenance_smoke.arn
  runtime       = "python3.12"
  handler       = "src.lambdas.ducklake_maintenance_smoke.handler.handler"
  architectures = ["x86_64"]
  timeout       = 300
  memory_size   = 1024

  reserved_concurrent_executions = var.ducklake_maintenance_smoke_reserved_concurrency

  s3_bucket = aws_s3_bucket.data_lake.id
  s3_key    = "lambda-packages/ducklake-maintenance-smoke.zip"

  layers = [
    aws_lambda_layer_version.ducklake_deps.arn,
    aws_lambda_layer_version.ducklake_extensions.arn,
  ]

  environment {
    variables = {
      DUCKLAKE_DATA_PATH            = local.ducklake_smoke_data_path
      DUCKLAKE_DSN_SECRET_ID        = aws_secretsmanager_secret.ducklake_smoke_maintainer_dsn.name
      DUCKLAKE_DSN_PARAMETER        = local.secret_parameter_names.smoke_maintainer_dsn
      DUCKLAKE_META_SCHEMA          = "ducklake_smoke"
      DUCKLAKE_EXTENSION_DIRECTORY  = local.ducklake_extension_dir
      DUCKLAKE_FIELD_SEMANTICS_PATH = "/var/task/config/lambda/ducklake/field_semantics.yaml"
    }
  }

  depends_on = [
    aws_iam_role_policy.ducklake_maintenance_smoke,
    aws_cloudwatch_log_group.ducklake_maintenance_smoke,
  ]

  tags = {
    Name    = "DuckLake Maintenance Smoke"
    Purpose = "T2.18 c9 ducklake_maintenance_smoke singleton"
  }

  # Decision 125/126 physical decoupling: code deploys go via the governed CD channel
  # (.github/workflows/deploy-ducklake-lambdas.yml), not terraform. No source_code_hash is set; this
  # block is the conformance marker and a backstop.
  lifecycle {
    ignore_changes = [source_code_hash]
  }
}

# ---------------------------------------------------------------------------
# Function URL (AWS_IAM) -- CI smoke-test invoke ingress (c9 autonomous gate) + admin invoke.
# ---------------------------------------------------------------------------

resource "aws_lambda_function_url" "ducklake_maintenance_smoke" {
  function_name      = aws_lambda_function.ducklake_maintenance_smoke.function_name
  authorization_type = "AWS_IAM"
}

# ---------------------------------------------------------------------------
# EventBridge schedule rules + targets + Lambda permissions -- moved from ducklake_maintenance.tf.
#
# `moved {}` blocks below preserve the RULE resources' AWS `name` (and therefore ARN) across the
# terraform-address move, so the bootstrap EventBridgeWrite grant (enumerated by rule ARN) keeps
# covering PutRule/PutTargets with no out-of-band admin edit. The TARGET and PERMISSION resources
# are NOT moved -- they now authorize/target THIS function (a different real-world object than the
# admin function's former target/permission), so they are destroyed on the admin side and created
# fresh here.
# ---------------------------------------------------------------------------

moved {
  from = aws_cloudwatch_event_rule.ducklake_maintenance_merge
  to   = aws_cloudwatch_event_rule.ducklake_maintenance_smoke_merge
}

moved {
  from = aws_cloudwatch_event_rule.ducklake_maintenance_gc
  to   = aws_cloudwatch_event_rule.ducklake_maintenance_smoke_gc
}

moved {
  from = aws_cloudwatch_event_rule.ducklake_maintenance_hot_merge
  to   = aws_cloudwatch_event_rule.ducklake_maintenance_smoke_hot_merge
}

resource "aws_cloudwatch_event_rule" "ducklake_maintenance_smoke_merge" {
  name                = "agent-platform-ducklake-maintenance-merge"
  description         = "Daily DuckLake non-destructive merge (T2.18 / CD.33), now on the smoke function. cron 04:00 UTC."
  schedule_expression = "cron(0 4 * * ? *)"
  state               = "ENABLED"

  tags = {
    Name    = "DuckLake Maintenance Merge Schedule"
    Purpose = "T2.18 daily non-destructive merge"
  }
}

resource "aws_cloudwatch_event_target" "ducklake_maintenance_smoke_merge" {
  rule      = aws_cloudwatch_event_rule.ducklake_maintenance_smoke_merge.name
  target_id = "ducklake-maintenance-smoke-merge"
  arn       = aws_lambda_function.ducklake_maintenance_smoke.arn
  input     = jsonencode({ action = "merge" })
}

resource "aws_lambda_permission" "ducklake_maintenance_smoke_merge" {
  statement_id  = "AllowEventBridgeMerge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ducklake_maintenance_smoke.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.ducklake_maintenance_smoke_merge.arn
}

resource "aws_cloudwatch_event_rule" "ducklake_maintenance_smoke_gc" {
  name                = "agent-platform-ducklake-maintenance-gc"
  description         = "Weekly DuckLake guarded GC (T2.18 / CD.33), now on the smoke function. cron 05:00 UTC Sunday."
  schedule_expression = "cron(0 5 ? * SUN *)"
  state               = "ENABLED"

  tags = {
    Name    = "DuckLake Maintenance GC Schedule"
    Purpose = "T2.18 weekly guarded GC"
  }
}

resource "aws_cloudwatch_event_target" "ducklake_maintenance_smoke_gc" {
  rule      = aws_cloudwatch_event_rule.ducklake_maintenance_smoke_gc.name
  target_id = "ducklake-maintenance-smoke-gc"
  arn       = aws_lambda_function.ducklake_maintenance_smoke.arn
  input     = jsonencode({ action = "gc" })
}

resource "aws_lambda_permission" "ducklake_maintenance_smoke_gc" {
  statement_id  = "AllowEventBridgeGc"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ducklake_maintenance_smoke.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.ducklake_maintenance_smoke_gc.arn
}

# ---------------------------------------------------------------------------
# EventBridge hot_merge rule: higher-frequency merge cadence (T2.18 FP-B / CD.34), now on the smoke
# function. Runs merge_adjacent_files ONLY over HOT_TABLE_SCOPE (no GC/deletion).
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "ducklake_maintenance_smoke_hot_merge" {
  name                = "agent-platform-ducklake-maintenance-hot-merge"
  description         = "Higher-frequency DuckLake merge-only cadence (T2.18 FP-B / CD.34), now on the smoke function. cron every 6h."
  schedule_expression = "cron(0 */6 * * ? *)"
  state               = "ENABLED"

  tags = {
    Name    = "DuckLake Maintenance Hot Merge Schedule"
    Purpose = "T2.18 FP-B higher-frequency merge-only cadence"
  }
}

resource "aws_cloudwatch_event_target" "ducklake_maintenance_smoke_hot_merge" {
  rule      = aws_cloudwatch_event_rule.ducklake_maintenance_smoke_hot_merge.name
  target_id = "ducklake-maintenance-smoke-hot-merge"
  arn       = aws_lambda_function.ducklake_maintenance_smoke.arn
  input     = jsonencode({ action = "hot_merge" })
}

resource "aws_lambda_permission" "ducklake_maintenance_smoke_hot_merge" {
  statement_id  = "AllowEventBridgeHotMerge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ducklake_maintenance_smoke.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.ducklake_maintenance_smoke_hot_merge.arn
}

# ---------------------------------------------------------------------------
# Circuit-breaker CloudWatch metric alarm (shared DuckLakeMaintenance namespace with the admin
# function's own alarm in ducklake_maintenance.tf). Both singletons emit MaintenanceBreakerTrip; this
# function emits it with the Catalog=smoke dimension (handler METRIC_DIMENSIONS) and this alarm
# selects that dimension, so the per-deploy breaker_probe gate's deliberate trip never reaches the
# admin alarm, which is declared with no dimensions.
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "ducklake_maintenance_smoke_breaker" {
  alarm_name          = "ducklake-maintenance-smoke-circuit-breaker"
  alarm_description   = "DuckLake maintenance SMOKE GC fail-closed guard-set trip (reachability, retention floor, or catalog sanity). T2.18 c9 / CD.33 H1."
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "MaintenanceBreakerTrip"
  namespace           = "DuckLakeMaintenance"
  period              = 300
  statistic           = "Sum"
  threshold           = 1

  dimensions = {
    Catalog = "smoke"
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]

  treat_missing_data = "notBreaching"

  tags = {
    Name    = "DuckLake Maintenance Smoke Breaker Alarm"
    Purpose = "T2.18 c9 CD.33 H1 circuit breaker alert"
  }
}

# ---------------------------------------------------------------------------
# Outputs -- consumed by the [post-deploy] smoke-test invoke gates (c9 autonomous gate).
# ---------------------------------------------------------------------------

output "ducklake_maintenance_smoke_function_url" {
  description = "AWS_IAM Function URL for the ducklake_maintenance_smoke Lambda (T2.18 c9 smoke gates)."
  value       = aws_lambda_function_url.ducklake_maintenance_smoke.function_url
}

output "ducklake_maintenance_smoke_function_name" {
  description = "ducklake_maintenance_smoke Lambda function name (governed CD deploy target)."
  value       = aws_lambda_function.ducklake_maintenance_smoke.function_name
}
