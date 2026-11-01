# ---------------------------------------------------------------------------
# Production telemetry catalog maintenance (PLAN-telemetry-production-maintenance, T2.36 c5; Decision 236
# cl.5). Points the every-6h merge and the weekly guarded GC at the production telemetry catalog on the
# admin maintenance function, and adds the breaker, liveness, merge-failure, merge-duration and growth
# alarms that replace the breaker Decision 188 retired. Every alarm reads the MetaSchema=ducklake_telemetry
# series; the ops series stays undimensioned (T2.18 c2, rec-3870). No IAM change: PLAN-telemetry-production-catalog
# granted the ducklake-telemetry/ prefix.
#
# Thresholds are literals from the 7-day pre-create measurement of the smoke telemetry catalog (window 2026-10-13 to
# 2026-10-20, recorded by PLAN-telemetry-production-catalog), never derived at apply time. The merge-duration threshold alone derives from the admin
# function's own timeout (rec-4218 trigger 2 is defined as half of it).
#
# Cadence on the reserved_concurrent_executions=1 singleton: control_health :15, ops merge :30, ops gc Sun 03:45,
# DR dump Sun 03:00, telemetry merge :50 (every 6h), telemetry gc Sun 04:05. Each minute is distinct.
# merge-liveness is in ALARM from creation until the first :50 pass, and gc-liveness until the first Sunday pass.
# ---------------------------------------------------------------------------

locals {
  # Measured D_files_per_day (1369.1, rounded to an integer).
  telemetry_data_files_per_day = 1369
  # Measured R_stats_rows_per_day (48262).
  telemetry_stats_rows_per_day = 48262
  # Measured commit_latency_p95_ms_7d (5912).
  telemetry_commit_latency_p95_ms_7d = 5912
  # SNAPSHOT_RETAIN_DAYS 30 + 7: a superseded file's stats rows persist until the weekly GC expires snapshots past retention.
  telemetry_stats_retention_days = 37

  telemetry_files_after_merge_threshold = 7 * local.telemetry_data_files_per_day
  telemetry_stats_rows_threshold        = 2 * local.telemetry_stats_retention_days * local.telemetry_stats_rows_per_day
  telemetry_merge_duration_threshold_ms = aws_lambda_function.ducklake_maintenance.timeout * 1000 / 2
}

resource "aws_cloudwatch_event_rule" "ducklake_maintenance_telemetry_merge" {
  name                = "agent-platform-ducklake-maintenance-telemetry-merge"
  description         = "Every-6h production telemetry catalog non-destructive merge (T2.36 c5, Decision 236 cl.5). cron every 6h at :50 UTC."
  schedule_expression = "cron(50 */6 * * ? *)"
  state               = "ENABLED"

  tags = {
    Name    = "DuckLake Maintenance Telemetry Merge Schedule"
    Purpose = "T2.36 c5 production telemetry merge"
  }
}

resource "aws_cloudwatch_event_target" "ducklake_maintenance_telemetry_merge" {
  rule      = aws_cloudwatch_event_rule.ducklake_maintenance_telemetry_merge.name
  target_id = "ducklake-maintenance-telemetry-merge"
  arn       = aws_lambda_function.ducklake_maintenance.arn
  input = jsonencode({
    action      = "merge_ops"
    data_path   = local.ducklake_telemetry_data_path
    meta_schema = local.ducklake_telemetry_prod_meta_schema
  })
}

resource "aws_lambda_permission" "ducklake_maintenance_telemetry_merge" {
  statement_id  = "AllowEventBridgeTelemetryMergeOps"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ducklake_maintenance.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.ducklake_maintenance_telemetry_merge.arn
}

# ENABLED under the committed counts-only dry-run reading tests/fixtures/gc_ops_dryrun_baseline_telemetry.json
# (referenced_missing 0, unsized_candidates 0, live bytes above zero), taken on the seeded catalog after the
# dimensioned-metric code deployed. Starts after the 03:00 DR dump and the 03:45 ops GC window.
resource "aws_cloudwatch_event_rule" "ducklake_maintenance_telemetry_gc" {
  name                = "agent-platform-ducklake-maintenance-telemetry-gc"
  description         = "Weekly production telemetry catalog guarded GC (T2.36 c5, Decision 236 cl.5). cron weekly Sun 04:05 UTC."
  schedule_expression = "cron(5 4 ? * SUN *)"
  state               = "ENABLED"

  tags = {
    Name    = "DuckLake Maintenance Telemetry GC Schedule"
    Purpose = "T2.36 c5 production telemetry guarded GC"
  }
}

resource "aws_cloudwatch_event_target" "ducklake_maintenance_telemetry_gc" {
  rule      = aws_cloudwatch_event_rule.ducklake_maintenance_telemetry_gc.name
  target_id = "ducklake-maintenance-telemetry-gc"
  arn       = aws_lambda_function.ducklake_maintenance.arn
  input = jsonencode({
    action      = "gc_ops"
    data_path   = local.ducklake_telemetry_data_path
    meta_schema = local.ducklake_telemetry_prod_meta_schema
  })
}

resource "aws_lambda_permission" "ducklake_maintenance_telemetry_gc" {
  statement_id  = "AllowEventBridgeTelemetryGcOps"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ducklake_maintenance.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.ducklake_maintenance_telemetry_gc.arn
}

resource "aws_cloudwatch_metric_alarm" "ducklake_telemetry_breaker" {
  alarm_name          = "ducklake-telemetry-maintenance-breaker"
  alarm_description   = "A guard trip (retention floor, reachability or catalog sanity) on the production telemetry catalog's maintenance. T2.36 c5."
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "MaintenanceBreakerTrip"
  namespace           = "DuckLakeMaintenance"
  period              = 300
  statistic           = "Sum"
  threshold           = 1

  dimensions = {
    MetaSchema = local.ducklake_telemetry_prod_meta_schema
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]

  treat_missing_data = "notBreaching"

  tags = {
    Name    = "DuckLake Telemetry Maintenance Breaker Alarm"
    Purpose = "T2.36 c5 production telemetry maintenance"
  }
}

resource "aws_cloudwatch_metric_alarm" "ducklake_telemetry_gc_referenced_missing" {
  alarm_name          = "ducklake-telemetry-gc-referenced-missing"
  alarm_description   = "Production telemetry gc_ops over-reclaim signal: a catalog-live path is missing from storage. T2.36 c5."
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "GcReferencedMissing"
  namespace           = "DuckLakeMaintenance"
  period              = 300
  statistic           = "Maximum"
  threshold           = 1

  dimensions = {
    MetaSchema = local.ducklake_telemetry_prod_meta_schema
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]

  treat_missing_data = "notBreaching"

  tags = {
    Name    = "DuckLake Telemetry Gc Referenced Missing Alarm"
    Purpose = "T2.36 c5 production telemetry maintenance"
  }
}

resource "aws_cloudwatch_metric_alarm" "ducklake_telemetry_gc_liveness" {
  alarm_name          = "ducklake-telemetry-gc-liveness"
  alarm_description   = "Production telemetry gc_ops pass never ran: no GcDeletedSnapshots datapoint across the weekly lookback. In ALARM from creation until the first Sunday pass. T2.36 c5."
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 7
  datapoints_to_alarm = 7
  metric_name         = "GcDeletedSnapshots"
  namespace           = "DuckLakeMaintenance"
  period              = 86400
  statistic           = "SampleCount"
  threshold           = 1

  dimensions = {
    MetaSchema = local.ducklake_telemetry_prod_meta_schema
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]

  treat_missing_data = "breaching"

  tags = {
    Name    = "DuckLake Telemetry Gc Liveness Alarm"
    Purpose = "T2.36 c5 production telemetry maintenance"
  }
}

resource "aws_cloudwatch_metric_alarm" "ducklake_telemetry_merge_failed" {
  alarm_name          = "ducklake-telemetry-merge-failed"
  alarm_description   = "A production telemetry merge_ops pass ended with a failed or unclassified table. T2.36 c5."
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "MergeOpsPassFailed"
  namespace           = "DuckLakeMaintenance"
  period              = 3600
  statistic           = "Maximum"
  threshold           = 1

  dimensions = {
    MetaSchema = local.ducklake_telemetry_prod_meta_schema
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]

  treat_missing_data = "notBreaching"

  tags = {
    Name    = "DuckLake Telemetry Merge Failed Alarm"
    Purpose = "T2.36 c5 production telemetry maintenance"
  }
}

resource "aws_cloudwatch_metric_alarm" "ducklake_telemetry_merge_liveness" {
  alarm_name          = "ducklake-telemetry-merge-liveness"
  alarm_description   = "Production telemetry merge_ops pass never ran: no MergeOpsTablesCount datapoint across the recent 6-hourly lookback. In ALARM from creation until the first scheduled pass. T2.36 c5."
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  metric_name         = "MergeOpsTablesCount"
  namespace           = "DuckLakeMaintenance"
  period              = 21600
  statistic           = "SampleCount"
  threshold           = 1

  dimensions = {
    MetaSchema = local.ducklake_telemetry_prod_meta_schema
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]

  treat_missing_data = "breaching"

  tags = {
    Name    = "DuckLake Telemetry Merge Liveness Alarm"
    Purpose = "T2.36 c5 production telemetry maintenance"
  }
}

resource "aws_cloudwatch_metric_alarm" "ducklake_telemetry_merge_duration" {
  alarm_name          = "ducklake-telemetry-merge-duration"
  alarm_description   = "A production telemetry merge_ops pass ran past half the function timeout (rec-4218 ingestion trigger 2). T2.36 c5."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "MergeOpsDurationMs"
  namespace           = "DuckLakeMaintenance"
  period              = 21600
  statistic           = "Maximum"
  threshold           = local.telemetry_merge_duration_threshold_ms

  dimensions = {
    MetaSchema = local.ducklake_telemetry_prod_meta_schema
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]

  treat_missing_data = "notBreaching"

  tags = {
    Name    = "DuckLake Telemetry Merge Duration Alarm"
    Purpose = "T2.36 c5 production telemetry maintenance"
  }
}

resource "aws_cloudwatch_metric_alarm" "ducklake_telemetry_files_after_merge" {
  alarm_name          = "ducklake-telemetry-files-after-merge"
  alarm_description   = "Production telemetry data files remaining after merge exceed the sized ceiling (S3 small-file growth; rec-4218 ingestion trigger 1). T2.36 c5."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  metric_name         = "MergeOpsFilesAfterTotal"
  namespace           = "DuckLakeMaintenance"
  period              = 21600
  statistic           = "Maximum"
  threshold           = local.telemetry_files_after_merge_threshold

  dimensions = {
    MetaSchema = local.ducklake_telemetry_prod_meta_schema
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]

  treat_missing_data = "notBreaching"

  tags = {
    Name    = "DuckLake Telemetry Files After Merge Alarm"
    Purpose = "T2.36 c5 production telemetry maintenance"
  }
}

resource "aws_cloudwatch_metric_alarm" "ducklake_telemetry_catalog_stats_rows" {
  alarm_name          = "ducklake-telemetry-catalog-stats-rows"
  alarm_description   = "Production telemetry catalog file-column-stats rows exceed the sized ceiling (Neon storage growth; rec-4218 ingestion trigger 3). T2.36 c5."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 4
  datapoints_to_alarm = 4
  metric_name         = "CatalogFileColumnStatsRows"
  namespace           = "DuckLakeMaintenance"
  period              = 21600
  statistic           = "Maximum"
  threshold           = local.telemetry_stats_rows_threshold

  dimensions = {
    MetaSchema = local.ducklake_telemetry_prod_meta_schema
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]

  treat_missing_data = "notBreaching"

  tags = {
    Name    = "DuckLake Telemetry Catalog Stats Rows Alarm"
    Purpose = "T2.36 c5 production telemetry maintenance"
  }
}
