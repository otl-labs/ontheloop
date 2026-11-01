# DuckLake maintenance ADMIN singleton Lambda (T2.18 / CD.33, Decision 81; T2.18 c9 follow-on
# split, bundled Decision amending Decision 81 clause 1, runtime artifacts 3 -> 4).
#
# Admin-gated: retains the production-destructive and operational verbs (catalog_reinit,
# restore_drill, reconcile_columns, catalog_stats, clone_catalog) plus the scheduled merge_ops
# cadence (production ops_* catalog, every 6h, non-destructive). The non-destructive smoke cadences
# (merge/gc/hot_merge/breaker_probe) moved to ducklake_maintenance_smoke.tf's CI-invokable sibling
# function -- see that file for the corresponding EventBridge rules (moved there via `moved {}`
# blocks preserving their AWS name/ARN).
#
# reserved_concurrent_executions = 1: maintenance is a singleton (Decision 81 clause 6).
# NOTE: the writer Lambda (ducklake_lambdas.tf) intentionally does NOT set reserved_concurrency so
# its OCC concurrency model is not artificially constrained (Decision 81 clause 3 + Decision 82).
# The maintenance pipeline IS intentionally a singleton -- it must not run concurrently with itself.
# The reserved_concurrency=1 here is correct and is NOT a keyword-collision with the writer's OCC
# model. This distinction is documented to pre-empt a reviewer flag.
#
# ---------------------------------------------------------------------------
# APPLY POSTURE (Decision 35 + 77): HUMAN-GATED via agent_platform_admin.
# ---------------------------------------------------------------------------
# This file creates a NEW IAM role + inline policy, which trips the Decision-77 deterministic guard
# (scripts/terraform_apply_guard.py, fail-closed on IAM/trust change). Apply routes to the MANUAL
# agent_platform_admin path, NOT push-to-main auto-apply. IAM must precede the code deploy:
#   1. build_lambda --ducklake-only       (upload all zips + layers to S3)
#   2. terraform plan -> human review -> terraform apply via agent_platform_admin
#   3. build_lambda --ducklake-only --deploy  (update the function code pointers from S3)
#
# CODE/INFRA COUPLING (Decision 125, environment-taxonomy.yaml conformance): RESOLVED. The
# aws_lambda_function resource below now carries a lifecycle block ignoring source_code_hash
# changes, so code-only redeploys no longer surface as a Terraform diff on this apply path. Code
# deploys now go via the governed CD channel (.github/workflows/deploy-ducklake-lambdas.yml, T2.38).
#
# FP-B (2026-06-07): shared SNS topic (sns_alerts.tf) created and wired as the alarm_actions
# target for BOTH this circuit-breaker alarm AND the CD.34 catalog-DR freshness alarm.

locals {
  ducklake_maintenance_function = "agent-platform-ducklake-maintenance"
}

# Singleton concurrency cap (Decision 81 clause 6) = 1: AWS physically refuses a second concurrent
# invocation, so the singleton is enforced by the platform, not just by schedule geometry. This was
# briefly -1 (unreserved) at initial deploy because the account Lambda "Concurrent executions" quota
# (L-B99A9384) sat at the unverified-account floor of 10 and PutFunctionConcurrency cannot drop the
# unreserved pool below 10. AWS support case 178085808000233 raised L-B99A9384 to 1000 (2026-06-08),
# so the reservation is now applied. Kept as a variable so the cap can be lifted again for a quota or
# load test without editing the resource body.
variable "ducklake_maintenance_reserved_concurrency" {
  description = "Reserved concurrency for the maintenance singleton (Decision 81 cl.6). 1 = singleton; -1 = unreserved (only for a quota/load test)."
  type        = number
  default     = 1
}

# ---------------------------------------------------------------------------
# CloudWatch log group (pre-created so the execution-role grant can be scoped to its ARN).
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "ducklake_maintenance" {
  name              = "/aws/lambda/${local.ducklake_maintenance_function}"
  retention_in_days = 14

  tags = {
    Name    = "DuckLake Maintenance Logs"
    Purpose = "T2.18 ducklake_maintenance singleton"
  }
}

# ---------------------------------------------------------------------------
# Write-scoped execution role: S3 Get/Put/Delete/List on the PRODUCTION prefix ONLY (the smoke
# prefix grant moved to ducklake_maintenance_smoke.tf's own exec role -- this narrowing is the
# other half of the T2.18 c9 blast-radius split), DSN read, DuckLakeMaintenance CloudWatch metrics,
# logs.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "ducklake_maintenance" {
  # Decision 144 (T2.48): mandatory broad-but-bounded exec-identity boundary (16/17 roles; PlatformAdmin excluded).
  name                 = "agent-platform-ducklake-maintenance"
  description          = "Maintenance admin singleton: S3 RW+Delete on the production prefix ONLY, Neon DSN read, maintenance metrics"
  permissions_boundary = "arn:aws:iam::${var.account_id}:policy/agent-platform-github-ci-apply-boundary"
  assume_role_policy   = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "ducklake_maintenance" {
  name = "DuckLakeMaintenanceRuntime"
  role = aws_iam_role.ducklake_maintenance.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "Logs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = ["${aws_cloudwatch_log_group.ducklake_maintenance.arn}:*"]
      },
      {
        # Decision 213: the scoped telemetry login's secret is read here too, so provision_telemetry_login
        # can set the role's password from it. GetSecretValue only; the function holds no write on it.
        Sid    = "NeonDsnRead"
        Effect = "Allow"
        Action = ["secretsmanager:GetSecretValue"]
        Resource = [
          aws_secretsmanager_secret.ducklake_neon_catalog_dsn.arn,
          aws_secretsmanager_secret.ducklake_telemetry_writer_dsn.arn,
          aws_secretsmanager_secret.ducklake_telemetry_writer_prod_dsn.arn,
          aws_secretsmanager_secret.ducklake_telemetry_reader_dsn.arn,
          aws_secretsmanager_secret.ducklake_smoke_maintainer_dsn.arn,
        ]
      },
      {
        # Decision 237: SSM twin of the Secrets Manager read above; secrets-to-ssm-retire removes the old one.
        Sid    = "NeonDsnParameterRead"
        Effect = "Allow"
        Action = ["ssm:GetParameter"]
        Resource = [
          "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/secret/ducklake-neon-catalog-dsn",
          "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/secret/agent-platform-ducklake-telemetry-writer-dsn",
          "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/secret/agent-platform-ducklake-telemetry-writer-prod-dsn",
          "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/secret/agent-platform-ducklake-telemetry-reader-dsn",
          "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/secret/agent-platform-ducklake-smoke-maintainer-dsn",
        ]
      },
      {
        # T2.19: the operational actions write to the PRODUCTION prefix -- catalog_reinit at
        # ducklake/, restore_drill at ducklake/_restore_drill/ (a sub-prefix), merge_ops/
        # reconcile_columns at the caller-supplied production data_path. delete_orphaned_files is
        # catalog-wide but only ever invoked against the production catalog on this function (the
        # smoke cadences that touched the smoke prefix moved to ducklake_maintenance_smoke.tf).
        Sid      = "S3DataReadWriteDelete"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = ["${aws_s3_bucket.data_lake.arn}/${local.ducklake_prod_data_prefix}/*"]
      },
      {
        Sid      = "S3ListDataPrefix"
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = [aws_s3_bucket.data_lake.arn]
        Condition = {
          StringLike = {
            "s3:prefix" = ["${local.ducklake_prod_data_prefix}/*"]
          }
        }
      },
      {
        # Decision 230: provision_telemetry_catalog seeds the production telemetry catalog's data prefix, and the
        # telemetry merge and GC read, write and delete there. Object versions are never deleted (no DeleteObjectVersion);
        # the bucket's noncurrent lifecycle reclaims them. No telemetry-blobs grant here.
        Sid      = "TelemetryCatalogDataReadWriteDelete"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = ["${aws_s3_bucket.data_lake.arn}/${local.ducklake_telemetry_data_prefix}/*"]
      },
      {
        Sid      = "TelemetryCatalogDataList"
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = [aws_s3_bucket.data_lake.arn]
        Condition = {
          StringLike = {
            "s3:prefix" = ["${local.ducklake_telemetry_data_prefix}/*"]
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
# Lambda function (from S3). Layers reused from T2.17 (no new layer build).
# ---------------------------------------------------------------------------

resource "aws_lambda_function" "ducklake_maintenance" {
  function_name = local.ducklake_maintenance_function
  description   = "T2.18 DuckLake maintenance ADMIN singleton (CD.33/Decision 81). Production-destructive + operational verbs; merge_ops every 6h."
  role          = aws_iam_role.ducklake_maintenance.arn
  runtime       = "python3.12"
  handler       = "src.lambdas.ducklake_maintenance.handler.handler"
  architectures = ["x86_64"]
  # F51 (production-gc-and-storage-stability): raised from 300s/1024MB. gc_ops is the heaviest verb
  # this function now runs -- a universal catalog live-set enumeration, the destructive sequence, a
  # post-pass re-enumeration for the G1 re-check and G3, a VALUE-SCANNING read-path re-read of every
  # table at a pinned snapshot (forces real Parquet reads from S3 by design), and a ListObjectsV2
  # walk of the production prefix. A timeout between the deletion and the G1 re-check/G3 leaves
  # production data deleted with NO safety verdict and NO metric -- raised to the Lambda ceiling
  # (900s) so correctness is never traded for the (weekly, low-invocation-count) cost delta.
  # 1536MB gives headroom for the added value-scan workload (a single-pass streaming aggregate over
  # Parquet columns, not a full-table materialization) without the cost multiplier a full doubling
  # would apply to every 6h merge_ops/control_health invocation on this shared function.
  timeout     = 900
  memory_size = 1536

  # Singleton cap (Decision 81 clause 6) = 1 via the variable default. Differs from the writer's OCC
  # model (no reserved concurrency, clause 3). See the variable definition above for the quota history.
  reserved_concurrent_executions = var.ducklake_maintenance_reserved_concurrency

  s3_bucket = aws_s3_bucket.data_lake.id
  s3_key    = "lambda-packages/ducklake-maintenance.zip"

  layers = [
    aws_lambda_layer_version.ducklake_deps.arn,
    aws_lambda_layer_version.ducklake_extensions.arn,
    # T2.19: restore_drill runs pg_dump/pg_restore (/opt/bin) from the pgclient layer.
    aws_lambda_layer_version.ducklake_pgclient.arn,
  ]

  environment {
    variables = {
      # The production-destructive/operational actions target production via explicit event
      # params (catalog_reinit, reconcile_columns, merge_ops, clone_catalog) -- DUCKLAKE_DATA_PATH
      # is NOT read by any of those (no-arg invokes refused, Decision 84/81). It IS read by the
      # read-mostly control_health action (T2.26) as a convenience default, since that action only
      # asserts invariants and never mutates -- see src/lambdas/ducklake_maintenance/handler.py's
      # DATA_PATH comment. DUCKLAKE_META_SCHEMA/GC_BREAKER_* remain consumed only by the scheduled
      # smoke cadences (merge/gc/hot_merge/breaker_probe) on ducklake_maintenance_smoke.tf's sibling.
      DUCKLAKE_DATA_PATH           = local.ducklake_prod_data_path
      DUCKLAKE_EXTENSION_DIRECTORY = local.ducklake_extension_dir
      # catalog_reinit's create_scd2_tables + reconcile_columns/restore_drill's field-spec
      # resolution load the field-semantics contract bundled into the zip (manifest assets[]).
      DUCKLAKE_FIELD_SEMANTICS_PATH = "/var/task/config/lambda/ducklake/field_semantics.yaml"
    }
  }

  depends_on = [
    aws_iam_role_policy.ducklake_maintenance,
    aws_cloudwatch_log_group.ducklake_maintenance,
  ]

  tags = {
    Name    = "DuckLake Maintenance Admin"
    Purpose = "T2.18 ducklake_maintenance admin singleton - production-destructive + operational verbs"
  }

  # Decision 125 physical decoupling: code deploys go via build_lambda --ducklake-only --deploy
  # (update-function-code), not terraform. No source_code_hash is set (Terraform reads no build artifact);
  # this block is the conformance marker and a backstop (rec-2646/rec-2654).
  lifecycle {
    ignore_changes = [source_code_hash]
  }
}

# ---------------------------------------------------------------------------
# Function URL (AWS_IAM) -- admin invoke ingress for the operational actions.
# ---------------------------------------------------------------------------

resource "aws_lambda_function_url" "ducklake_maintenance" {
  function_name      = aws_lambda_function.ducklake_maintenance.function_name
  authorization_type = "AWS_IAM"
}

# ---------------------------------------------------------------------------
# The daily merge / weekly GC / 6h hot_merge EventBridge rules+targets+permissions MOVED to
# ducklake_maintenance_smoke.tf (T2.18 c9 split) -- the rule resources use `moved {}` blocks there
# to preserve their AWS name/ARN; only merge_ops stays scheduled on this admin function below.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# EventBridge prod-merge rule: every-6h non-destructive merge of ALL live ops_* SCD2 table pairs
# in the production catalog (ducklake_ops @ s3://.../ducklake/). Staggered to :30 (off the 6-hourly
# hot_merge at :00) to avoid throttle under reserved_concurrent_executions=1. Non-destructive:
# merge_ops dispatches merge_adjacent_files only -- no expire/cleanup/orphan (gated by rec-2113/T2.26).
# Cadence raised from daily (cron(30 4 * * ? *)) to every 6h by neon-egress-reduction (D3a): paying
# down ops_* small-file growth faster shrinks the per-query ducklake_file_column_stats footprint that
# drives every read's Neon metadata egress (ducklake #859).
# No new IAM: reuses the existing maintenance role (already grants S3 RW on the prod prefix).
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "ducklake_maintenance_merge_ops" {
  name                = "agent-platform-ducklake-maintenance-merge-ops"
  description         = "Every-6h production DuckLake ops_* non-destructive merge (T2.18 Phase-4 / Decision 84; neon-egress D3a). cron every 6h at :30 UTC."
  schedule_expression = "cron(30 */6 * * ? *)"
  state               = "ENABLED"

  tags = {
    Name    = "DuckLake Maintenance Prod Merge Ops Schedule"
    Purpose = "T2.18 Phase-4 every-6h production ops non-destructive merge - neon-egress D3a"
  }
}

resource "aws_cloudwatch_event_target" "ducklake_maintenance_merge_ops" {
  rule      = aws_cloudwatch_event_rule.ducklake_maintenance_merge_ops.name
  target_id = "ducklake-maintenance-merge-ops"
  arn       = aws_lambda_function.ducklake_maintenance.arn
  input     = jsonencode({ action = "merge_ops", data_path = local.ducklake_prod_data_path, meta_schema = "ducklake_ops" })
}

resource "aws_lambda_permission" "ducklake_maintenance_merge_ops" {
  statement_id  = "AllowEventBridgeMergeOps"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ducklake_maintenance.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.ducklake_maintenance_merge_ops.arn
}

# ---------------------------------------------------------------------------
# EventBridge control-health rule (T2.26 control-table-class-and-counter-conformance): periodic
# assertion of the control-class table invariants (row count, counter floor, live-file ceiling) --
# the health binding docs/contracts/ops_entity_counters.yaml's dq_scope exemption names as the
# substitute for DQ-runner coverage (that table has no reachable read path for the DQ runner to
# use). Staggered to :15 (off both the hot_merge :00 and merge_ops :30, on maintenance_smoke.tf /
# this file respectively) to avoid throttle under reserved_concurrent_executions=1. No new IAM:
# reuses the existing maintenance role (control_health only reads).
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "ducklake_maintenance_control_health" {
  name                = "agent-platform-ducklake-maintenance-control-health"
  description         = "Every-6h control-class table invariant assertion (T2.26). cron every 6h at :15 UTC."
  schedule_expression = "cron(15 */6 * * ? *)"
  state               = "ENABLED"

  tags = {
    Name    = "DuckLake Maintenance Control Health Schedule"
    Purpose = "T2.26 control-table-class-and-counter-conformance periodic invariant assertion"
  }
}

resource "aws_cloudwatch_event_target" "ducklake_maintenance_control_health" {
  rule      = aws_cloudwatch_event_rule.ducklake_maintenance_control_health.name
  target_id = "ducklake-maintenance-control-health"
  arn       = aws_lambda_function.ducklake_maintenance.arn
  input     = jsonencode({ action = "control_health", meta_schema = "ducklake_ops" })
}

resource "aws_lambda_permission" "ducklake_maintenance_control_health" {
  statement_id  = "AllowEventBridgeControlHealth"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ducklake_maintenance.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.ducklake_maintenance_control_health.arn
}

# ---------------------------------------------------------------------------
# EventBridge prod gc_ops rule (production-gc-and-storage-stability, T2.18 c2): weekly guarded
# destructive GC against the production catalog (ducklake_ops @ s3://.../ducklake/). Resource name
# is SUFFIXED ("...-gc-ops") because the un-suffixed "agent-platform-ducklake-maintenance-gc" name
# is already the SMOKE host's rule (terraform/personal/ducklake_maintenance_smoke.tf) -- naming it
# "gc" here would collide at apply time.
#
# ENABLED (gc-ops-baseline-gate-and-schedule-enable): the CD deploy of the handler carrying
# action=gc_ops (deploy-ducklake-lambdas.yml run 17, head 1402004) is green, and the pre-enablement
# baseline gate cleared against the committed dry_run reading in
# tests/fixtures/gc_ops_dryrun_baseline.json (referenced_missing 0, unsized_candidates 0, at least
# one drain-ladder rung admitting a positive count under both G4 caps). A re-enable after any future
# rollback to DISABLED is gated the same way -- see docs/contracts/ducklake_maintenance.yaml's
# gc_ops verb contract for the standing re-gate clause.
#
# Schedule cron(45 3 ? * SUN *): weekly, deliberately OFFSET from this singleton's other two
# 6-hourly cadences (merge_ops :30, control_health :15; reserved_concurrent_executions=1, so a
# collision throttles with a 429 that -- per the absence-detection problem below -- would be
# SILENT). Asserted by TestProductionGcRuleInvariants::test_gc_ops_rule_cron_does_not_collide_with_the_singleton_cadences.
# No new IAM: reuses the existing maintenance role (already grants S3 RW+Delete + ListBucket on the
# prod prefix, and PutMetricData conditioned on the DuckLakeMaintenance namespace).
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "ducklake_maintenance_gc_ops" {
  name                = "agent-platform-ducklake-maintenance-gc-ops"
  description         = "Weekly production DuckLake destructive GC (T2.18 / production-gc-and-storage-stability). cron weekly Sun 03:45 UTC. Enabled after the pre-enablement baseline gate and CD deploy both cleared (gc-ops-baseline-gate-and-schedule-enable)."
  schedule_expression = "cron(45 3 ? * SUN *)"
  state               = "ENABLED"

  tags = {
    Name    = "DuckLake Maintenance Prod GC Ops Schedule"
    Purpose = "T2.18 production-gc-and-storage-stability weekly guarded destructive GC"
  }
}

resource "aws_cloudwatch_event_target" "ducklake_maintenance_gc_ops" {
  rule      = aws_cloudwatch_event_rule.ducklake_maintenance_gc_ops.name
  target_id = "ducklake-maintenance-gc-ops"
  arn       = aws_lambda_function.ducklake_maintenance.arn
  input     = jsonencode({ action = "gc_ops", data_path = local.ducklake_prod_data_path, meta_schema = "ducklake_ops" })
}

resource "aws_lambda_permission" "ducklake_maintenance_gc_ops" {
  statement_id  = "AllowEventBridgeGcOps"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ducklake_maintenance.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.ducklake_maintenance_gc_ops.arn
}

# ---------------------------------------------------------------------------
# Circuit-breaker CloudWatch metric alarm.
# Fires when MaintenanceBreakerTrip >= 1 in a 5-minute window.
# alarm_actions wired to shared SNS topic (FP-B / Decision 39).
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "ducklake_maintenance_breaker" {
  alarm_name          = "ducklake-maintenance-circuit-breaker"
  alarm_description   = "DuckLake maintenance GC fail-closed guard-set trip (reachability, retention floor, or catalog sanity). T2.18 / CD.33 H1."
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "MaintenanceBreakerTrip"
  namespace           = "DuckLakeMaintenance"
  period              = 300
  statistic           = "Sum"
  threshold           = 1

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]

  treat_missing_data = "notBreaching"

  tags = {
    Name    = "DuckLake Maintenance Breaker Alarm"
    Purpose = "T2.18 CD.33 H1 circuit breaker alert"
  }
}

# ---------------------------------------------------------------------------
# gc_ops BREACH alarm: fires when GcReferencedMissing >= 1 -- the over-reclaim safety invariant.
# treat_missing_data = "notBreaching" DELIBERATELY: a weekly metric is legitimately absent six days
# in seven, and this alarm is the BREACH detector, not the liveness detector (see the liveness
# alarm below for that half of the absence-detection problem).
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "ducklake_maintenance_gc_ops_referenced_missing" {
  alarm_name          = "ducklake-maintenance-gc-ops-referenced-missing"
  alarm_description   = "DuckLake production gc_ops over-reclaim signal: a catalog-live path is missing from storage. T2.18 / production-gc-and-storage-stability."
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "GcReferencedMissing"
  namespace           = "DuckLakeMaintenance"
  period              = 300
  statistic           = "Maximum"
  threshold           = 1

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]

  treat_missing_data = "notBreaching"

  tags = {
    Name    = "DuckLake Maintenance GC Ops Referenced-Missing Alarm"
    Purpose = "T2.18 production-gc-and-storage-stability over-reclaim breach detector"
  }
}

# ---------------------------------------------------------------------------
# gc_ops LIVENESS alarm: fires when GcDeletedSnapshots reports NO datapoint at all across the
# lookback -- the OTHER half of the absence-detection problem the breach alarm's notBreaching
# posture deliberately leaves open. Without this, a rule that silently never fires (a schedule
# misconfiguration, a permission gap, an exception before any metric emits) is invisible for the
# whole nine-week observation window this plan exists to open.
#
# CloudWatch enforces TWO separate EvaluationPeriods*Period product caps on PutMetricAlarm
# (confirmed empirically by rec-3881 -- the prior period=1800 design was rejected at apply):
# <=86400s (1 day) when period<3600, and <=604800s (1 week) only once period>=3600. The prior
# version conflated the two and sized 480*1800=864000s against the wrong (looser) ceiling. This
# version follows ducklake_catalog_dr.tf's freshness-alarm precedent instead: DAILY periods
# (period=86400, evaluation_periods=7, datapoints_to_alarm=7 -- exactly 604800s, the >=3600
# ceiling) give the same one-day slack that makes a weekly cadence never false-trip (the run lands
# in one of 7 daily buckets; max empty run under normal operation is 6 days, never 7-of-7
# breaching -- see that file's comment for the full argument).
# statistic=SampleCount + datapoints_to_alarm equal to evaluation_periods ("ALL periods breaching")
# is what makes this a DATAPOINT-COUNT check rather than a value-threshold check: a real pass that
# legitimately deletes nothing still emits a 0-valued datapoint, which SampleCount still counts as
# present -- Sum would misread that same 0-valued datapoint as absence.
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "ducklake_maintenance_gc_ops_liveness" {
  alarm_name          = "ducklake-maintenance-gc-ops-liveness"
  alarm_description   = "DuckLake production gc_ops pass never ran: no GcDeletedSnapshots datapoint in 7 days (weekly cadence). T2.18 / production-gc-and-storage-stability."
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 7
  datapoints_to_alarm = 7
  metric_name         = "GcDeletedSnapshots"
  namespace           = "DuckLakeMaintenance"
  period              = 86400
  statistic           = "SampleCount"
  threshold           = 1

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]

  treat_missing_data = "breaching"

  tags = {
    Name    = "DuckLake Maintenance GC Ops Liveness Alarm"
    Purpose = "T2.18 production-gc-and-storage-stability liveness guard"
  }
}

# ---------------------------------------------------------------------------
# Outputs -- admin-invoked operational actions only (the c9 smoke gates resolve
# ducklake_maintenance_smoke_function_url instead; see ducklake_maintenance_smoke.tf).
# ---------------------------------------------------------------------------

output "ducklake_maintenance_function_url" {
  description = "AWS_IAM Function URL for the ducklake_maintenance admin Lambda (operational actions)."
  value       = aws_lambda_function_url.ducklake_maintenance.function_url
}

output "ducklake_maintenance_function_name" {
  description = "ducklake_maintenance Lambda function name (build_lambda --ducklake-only --deploy target)."
  value       = aws_lambda_function.ducklake_maintenance.function_name
}

output "ducklake_prod_data_path" {
  description = "Production DuckLake data_path (s3://.../ducklake/) for manual-invoke recipes -- resolve via `terraform output -raw`, never hardcode the bucket name in runbook commands."
  value       = local.ducklake_prod_data_path
}
