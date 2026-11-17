# Telemetry content reader (Decision 239 as amended by Decision 270; rec-4696).
#
# The masked transcript content reader lives in this admin-only root so that no CI identity can change it: the CI
# apply boundary's DenyTelemetryContentChange refuses every boundary carrier any Lambda write on the function, and its
# roles are named telemetry-content-*, outside agent-platform-*, so no CI IAM grant reaches them and the boundary's
# protected-role Deny refuses every carrier the rest. Agents reach it only through the DuckLake API's
# telemetry/content route (agent_ducklake_api.tf); there is no Function URL.
#
# Terraform owns identity, runtime, memory, timeout, concurrency and logging. The gated job
# (.github/workflows/deploy-telemetry-content.yml, plan 2) owns code, layers, handler and environment, which
# ignore_changes hands over. Until the first approved deploy the function runs the committed
# terraform/lambda-placeholders/telemetry_content_placeholder.zip, which answers 503 to every event: Terraform reads no
# build artifact, and the zip sits outside this root because the bootstrap screen admits only .tf text.
#
# The access log group, its metric filter and both alarms are platform-security-* resources (Decision 202 cl.3 as
# amended by Decision 270 cl.3), so the detector-tamper alarm pages on changes to them. SIT declares none of this
# (Decision 252 cl.5 keeps SIT's transcripts behind the same lock when its twin is planned).
locals {
  telemetry_content_count             = { sandbox = 1, sit = 0 }[var.environment]
  ducklake_telemetry_content_function = "agent-platform-ducklake-telemetry-content"
  telemetry_content_data_prefix       = "ducklake-telemetry"
  telemetry_content_blob_prefix       = "telemetry-blobs/ducklake_telemetry"
  telemetry_content_alerts_topic_arn  = "arn:aws:sns:${var.aws_region}:${var.account_id}:agent-platform-alerts"
}

resource "aws_iam_role" "telemetry_content" {
  count = local.telemetry_content_count

  # Decision 144 cl.3: the mandatory exec-identity boundary; DenyTelemetryProdPrefixIO excepts this exact role.
  name                 = "telemetry-content-reader"
  description          = "Telemetry content reader: masked transcript reads over the reader login, data and blob prefixes read-only"
  permissions_boundary = "arn:aws:iam::${var.account_id}:policy/agent-platform-github-ci-apply-boundary"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "lambda.amazonaws.com" }
        Action    = "sts:AssumeRole"
      }
    ]
  })
}

locals {
  telemetry_content_policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "Logs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = ["arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:platform-security-telemetry-content-access:*"]
      },
      {
        Sid      = "TelemetryCatalogDataRead"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = ["${local.data_lake_bucket_arn}/${local.telemetry_content_data_prefix}/*"]
      },
      {
        Sid      = "TelemetryCatalogDataList"
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = [local.data_lake_bucket_arn]
        Condition = {
          StringLike = {
            "s3:prefix" = ["${local.telemetry_content_data_prefix}/*"]
          }
        }
      },
      {
        Sid      = "TelemetryBlobRead"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = ["${local.data_lake_bucket_arn}/${local.telemetry_content_blob_prefix}/*"]
      },
      {
        # Decision 237 cl.2: a declared consumer of the existing reader login, by literal ARN; no new parameter.
        Sid      = "TelemetryReaderDsnParameterRead"
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = ["arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/secret/agent-platform-ducklake-telemetry-reader-dsn"]
      },
    ]
  })
}

resource "aws_iam_role_policy" "telemetry_content" {
  count = local.telemetry_content_count

  name   = "TelemetryContentRuntime"
  role   = aws_iam_role.telemetry_content[0].id
  policy = local.telemetry_content_policy_json

  lifecycle {
    precondition {
      # AWS measures the whitespace-stripped document against the 10,240 B inline-policy limit (rec-2793).
      condition     = length(jsonencode(jsondecode(local.telemetry_content_policy_json))) <= 10240
      error_message = "Telemetry content reader inline policy exceeds the 10,240 B IAM inline-policy limit (whitespace-stripped measure, rec-2793)."
    }
  }
}

resource "aws_cloudwatch_log_group" "telemetry_content" {
  count = local.telemetry_content_count

  # Decision 239 cl.4: every read's access line is kept at least 90 days. skip_destroy keeps it on a revert.
  name              = "platform-security-telemetry-content-access"
  retention_in_days = 365
  skip_destroy      = true

  tags = {
    Name    = "DuckLake Telemetry Content Reader Logs"
    Purpose = "Decision 239 content reader access log"
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_lambda_function" "telemetry_content" {
  count = local.telemetry_content_count

  function_name = local.ducklake_telemetry_content_function
  description   = "Decision 239 masked transcript content reader. Deployed only by the operator-approved telemetry-content-deploy job."
  role          = aws_iam_role.telemetry_content[0].arn
  runtime       = "python3.12"
  handler       = "src.lambdas.ducklake_telemetry_content_reader.handler.handler"
  architectures = ["x86_64"]
  timeout       = 120
  memory_size   = 1024

  # With the telemetry reader's 4, inside the reader login's CONNECTION LIMIT 10 at two slots each (Decision 239 cl.1).
  reserved_concurrent_executions = 1

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.telemetry_content[0].name
  }

  # One member, src/lambdas/ducklake_telemetry_content_reader/handler.py, answering 503 content_reader_not_deployed to
  # every event (the IAM pins test pins its digest and decodes it).
  filename = "${path.module}/../lambda-placeholders/telemetry_content_placeholder.zip"

  depends_on = [
    aws_iam_role_policy.telemetry_content,
    aws_cloudwatch_log_group.telemetry_content,
  ]

  tags = {
    Name    = "DuckLake Telemetry Content Reader"
    Purpose = "Decision 239 masked content reads"
  }

  # Decision 239 cl.5: the gated job owns code, layers, handler and environment.
  lifecycle {
    ignore_changes = [filename, source_code_hash, layers, handler, environment]
  }
}

resource "aws_cloudwatch_log_metric_filter" "telemetry_content_unexpected_caller" {
  count = local.telemetry_content_count

  # Any reader but the agent session user (Decision 270 cl.1); role "unknown", the access log's fallback line,
  # matches on purpose.
  name           = "platform-security-telemetry-content-unexpected-caller"
  log_group_name = aws_cloudwatch_log_group.telemetry_content[0].name
  pattern        = "{ ($.event = \"telemetry_content_read\") && ($.role != \"agent-platform-session\") }"

  metric_transformation {
    name          = "TelemetryContentUnexpectedCaller"
    namespace     = "AgentPlatform/TelemetryContent"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "telemetry_content_unexpected_caller" {
  count = local.telemetry_content_count

  alarm_name          = "platform-security-telemetry-content-unexpected-caller"
  alarm_description   = "A content read by a caller other than agent-platform-session (Decision 239 cl.6). Who read what: Logs Insights on platform-security-telemetry-content-access, filter event = 'telemetry_content_read' | stats count() by role, verb, session_id."
  namespace           = "AgentPlatform/TelemetryContent"
  metric_name         = aws_cloudwatch_log_metric_filter.telemetry_content_unexpected_caller[0].metric_transformation[0].name
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [local.telemetry_content_alerts_topic_arn]
}

resource "aws_cloudwatch_metric_alarm" "telemetry_content_errors" {
  count = local.telemetry_content_count

  alarm_name          = "platform-security-telemetry-content-errors"
  alarm_description   = "DuckLake telemetry content reader: AWS/Lambda Errors > 0 over 15m (loud-fail posture, Decision 55)."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.telemetry_content[0].function_name }
  statistic           = "Sum"
  period              = 900
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [local.telemetry_content_alerts_topic_arn]
  ok_actions          = [local.telemetry_content_alerts_topic_arn]
}
