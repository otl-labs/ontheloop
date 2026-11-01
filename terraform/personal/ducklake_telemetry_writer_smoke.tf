# DuckLake telemetry writer smoke twin (slice 2c; Decision 236 cl.1, Decision 143 cl.3, Decision 213).
#
# The twin runs the telemetry writer's code (the same zip) on the disposable smoke catalog with its own role and
# the smoke scoped login (aws_secretsmanager_secret.ducklake_telemetry_writer_dsn). It is deployed first and carries
# the disposable deploy proofs (fault, near-cap and row-cap vectors, first-sight auto-registration), so the
# production telemetry deploy waits on its gates. It is pinned to the fixed smoke locals, never to the flipping
# ducklake_telemetry_meta_schema, and holds no production prefix, no production secret and no Delete.
#
# APPLY POSTURE (environment-taxonomy.yaml guard_classification): the role CREATE and the aws_lambda_function_url
# change route the saved plan to the tf-gated-apply Environment. Code ships through deploy-ducklake-lambdas.yml
# (Decision 125/126); the function sets no source_code_hash and keeps the ignore_changes conformance marker.

resource "aws_cloudwatch_log_group" "ducklake_telemetry_writer_smoke" {
  name              = "/aws/lambda/${local.ducklake_telemetry_writer_smoke_function}"
  retention_in_days = 14

  tags = {
    Name    = "DuckLake Telemetry Writer Smoke Twin Logs"
    Purpose = "Decision 236 smoke twin runtime"
  }
}

resource "aws_iam_role" "ducklake_telemetry_writer_smoke" {
  # Decision 144 (T2.48): mandatory broad-but-bounded exec-identity boundary.
  name                 = "agent-platform-ducklake-telemetry-writer-smoke"
  description          = "Telemetry writer smoke twin: S3 Get/Put on the smoke catalog data prefix and smoke blob root only, smoke scoped catalog login read"
  permissions_boundary = "arn:aws:iam::${var.account_id}:policy/agent-platform-github-ci-apply-boundary"
  assume_role_policy   = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "ducklake_telemetry_writer_smoke" {
  name = "DuckLakeTelemetryWriterSmokeRuntime"
  role = aws_iam_role.ducklake_telemetry_writer_smoke.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "Logs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = ["${aws_cloudwatch_log_group.ducklake_telemetry_writer_smoke.arn}:*"]
      },
      {
        # The smoke scoped catalog login only; never the production writer or reader login.
        Sid      = "TelemetryDsnRead"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [aws_secretsmanager_secret.ducklake_telemetry_writer_dsn.arn]
      },
      {
        # Decision 237: SSM twin of the Secrets Manager read above; secrets-to-ssm-retire removes the old one.
        Sid      = "TelemetryDsnParameterRead"
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = ["arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/secret/agent-platform-ducklake-telemetry-writer-dsn"]
      },
      {
        # Smoke catalog data prefix ONLY. No Delete, and never the production ducklake/ or ducklake-telemetry/ prefix.
        Sid      = "TelemetryCatalogDataReadWrite"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = ["${aws_s3_bucket.data_lake.arn}/${local.ducklake_smoke_data_prefix}/*"]
      },
      {
        Sid      = "TelemetryCatalogDataList"
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
        Sid      = "TelemetryBlobReadWrite"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = ["${aws_s3_bucket.data_lake.arn}/${local.telemetry_blob_data_prefix}/${local.ducklake_smoke_meta_schema}/*"]
      },
      {
        Sid      = "TelemetryBlobList"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [aws_s3_bucket.data_lake.arn]
        Condition = {
          StringLike = {
            "s3:prefix" = ["${local.telemetry_blob_data_prefix}/${local.ducklake_smoke_meta_schema}/*"]
          }
        }
      },
      {
        # PutMetricData does not support resource-level scoping; constrain to the writer's namespace.
        Sid      = "CloudWatchMetrics"
        Effect   = "Allow"
        Action   = ["cloudwatch:PutMetricData"]
        Resource = "*"
        Condition = {
          StringEquals = {
            "cloudwatch:namespace" = "DuckLakeTelemetryWriter"
          }
        }
      },
    ]
  })
}

resource "aws_lambda_function" "ducklake_telemetry_writer_smoke" {
  function_name = local.ducklake_telemetry_writer_smoke_function
  description   = "Decision 236 smoke twin of the telemetry writer. Same code, smoke catalog pin and smoke scoped login; carries the disposable deploy proofs."
  role          = aws_iam_role.ducklake_telemetry_writer_smoke.arn
  runtime       = "python3.12"
  handler       = "src.lambdas.ducklake_telemetry_writer.handler.handler"
  architectures = ["x86_64"]
  timeout       = 120
  memory_size   = 3008 # The writer's baseline (Decision 82).

  # A literal: the gate's fan-out needs four concurrent containers. The smoke writer login is shared with the writer
  # until the flip, sized at CONNECTION LIMIT 2 x (5 + 5) = 20 (catalog_logins.LOGINS, test-pinned).
  reserved_concurrent_executions = 5

  s3_bucket = aws_s3_bucket.data_lake.id
  s3_key    = "lambda-packages/ducklake-telemetry-writer.zip"

  layers = [
    aws_lambda_layer_version.ducklake_deps.arn,
    aws_lambda_layer_version.ducklake_extensions.arn,
  ]

  environment {
    variables = {
      TELEMETRY_META_SCHEMA         = local.ducklake_smoke_meta_schema
      TELEMETRY_DATA_PATH           = local.ducklake_smoke_data_path
      TELEMETRY_BLOB_ROOT           = local.ducklake_telemetry_smoke_blob_root
      DUCKLAKE_EXTENSION_DIRECTORY  = local.ducklake_extension_dir
      DUCKLAKE_FIELD_SEMANTICS_PATH = "/var/task/config/lambda/ducklake/field_semantics.yaml"
      TELEMETRY_DSN_SECRET_ID       = aws_secretsmanager_secret.ducklake_telemetry_writer_dsn.name
      TELEMETRY_DSN_PARAMETER       = local.secret_parameter_names.telemetry_writer_dsn
    }
  }

  depends_on = [
    aws_iam_role_policy.ducklake_telemetry_writer_smoke,
    aws_cloudwatch_log_group.ducklake_telemetry_writer_smoke,
  ]

  tags = {
    Name    = "DuckLake Telemetry Writer Smoke Twin"
    Purpose = "Decision 236 smoke twin runtime"
  }

  # Decision 125/126 physical decoupling: code deploys go via the governed CD channel, not terraform.
  # No source_code_hash is set (Terraform reads no build artifact); this block is the conformance marker
  # and a backstop if the argument is ever reintroduced.
  lifecycle {
    ignore_changes = [source_code_hash]
  }
}

resource "aws_lambda_function_url" "ducklake_telemetry_writer_smoke" {
  function_name      = aws_lambda_function.ducklake_telemetry_writer_smoke.function_name
  authorization_type = "AWS_IAM"
}

resource "aws_ssm_parameter" "ducklake_telemetry_writer_smoke_url" {
  name  = "/agent-platform/ducklake/telemetry_writer_smoke_url"
  type  = "String"
  value = aws_lambda_function_url.ducklake_telemetry_writer_smoke.function_url

  tags = {
    Name    = "DuckLake Telemetry Writer Smoke Twin URL"
    Purpose = "Decision 236 endpoint discovery"
  }
}

resource "aws_cloudwatch_metric_alarm" "ducklake_telemetry_writer_smoke_errors" {
  alarm_name          = "ducklake-telemetry-writer-smoke-errors"
  alarm_description   = "DuckLake telemetry writer smoke twin: AWS/Lambda Errors plus Url5xxCount > 0 over 15m (loud-fail posture, Decision 55). An operator-run gate that elicits a typed 5xx on purpose pages too."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 0
  treat_missing_data  = "notBreaching"

  metric_query {
    id = "m1"

    metric {
      metric_name = "Errors"
      namespace   = "AWS/Lambda"
      period      = 900
      stat        = "Sum"

      dimensions = {
        FunctionName = aws_lambda_function.ducklake_telemetry_writer_smoke.function_name
      }
    }
  }

  metric_query {
    id = "m2"

    metric {
      metric_name = "Url5xxCount"
      namespace   = "AWS/Lambda"
      period      = 900
      stat        = "Sum"

      dimensions = {
        FunctionName = aws_lambda_function.ducklake_telemetry_writer_smoke.function_name
      }
    }
  }

  metric_query {
    id          = "e1"
    expression  = "FILL(m1, 0) + FILL(m2, 0)"
    label       = "errors plus url 5xx"
    return_data = true
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]

  tags = {
    Name    = "DuckLake Telemetry Writer Smoke Twin Errors Alarm"
    Purpose = "Decision 236 runtime error alert"
  }
}

output "ducklake_telemetry_writer_smoke_function_url" {
  description = "AWS_IAM Function URL for the ducklake telemetry writer smoke twin."
  value       = aws_lambda_function_url.ducklake_telemetry_writer_smoke.function_url
}

output "ducklake_telemetry_writer_smoke_function_name" {
  description = "Telemetry writer smoke twin function name (governed CD deploy target)."
  value       = aws_lambda_function.ducklake_telemetry_writer_smoke.function_name
}
