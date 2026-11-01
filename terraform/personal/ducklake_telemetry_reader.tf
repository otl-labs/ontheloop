# DuckLake telemetry reader Lambda (slice 2c; Decision 236 cl.2-3, Decision 213 cl.2, Decision 230).
#
# Production telemetry is read only through this function: the event verbs minus transcript_content, no caller SQL,
# no ops verb, a SELECT-only catalog login attached READ_ONLY (aws_secretsmanager_secret.ducklake_telemetry_reader_dsn),
# S3 read on the production telemetry data prefix only and nothing on the blob root. It is pinned to production from
# birth and reads the empty production catalog until the flip.
#
# APPLY POSTURE (environment-taxonomy.yaml guard_classification): the role CREATE and the aws_lambda_function_url
# change route the saved plan to the tf-gated-apply Environment. Code ships through deploy-ducklake-lambdas.yml
# (Decision 125/126); the function sets no source_code_hash and keeps the ignore_changes conformance marker.

resource "aws_cloudwatch_log_group" "ducklake_telemetry_reader" {
  name              = "/aws/lambda/${local.ducklake_telemetry_reader_function}"
  retention_in_days = 14

  tags = {
    Name    = "DuckLake Telemetry Reader Logs"
    Purpose = "Decision 236 telemetry reader runtime"
  }
}

resource "aws_iam_role" "ducklake_telemetry_reader" {
  # Decision 144 (T2.48): mandatory broad-but-bounded exec-identity boundary.
  name                 = "agent-platform-ducklake-telemetry-reader"
  description          = "Telemetry reader: S3 Get and List on the production telemetry data prefix only, reader scoped catalog login read"
  permissions_boundary = "arn:aws:iam::${var.account_id}:policy/agent-platform-github-ci-apply-boundary"
  assume_role_policy   = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "ducklake_telemetry_reader" {
  name = "DuckLakeTelemetryReaderRuntime"
  role = aws_iam_role.ducklake_telemetry_reader.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "Logs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = ["${aws_cloudwatch_log_group.ducklake_telemetry_reader.arn}:*"]
      },
      {
        Sid      = "TelemetryReaderDsnRead"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [aws_secretsmanager_secret.ducklake_telemetry_reader_dsn.arn]
      },
      {
        # Decision 237: SSM twin of the Secrets Manager read above; secrets-to-ssm-retire removes the old one.
        Sid      = "TelemetryReaderDsnParameterRead"
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = ["arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/secret/agent-platform-ducklake-telemetry-reader-dsn"]
      },
      {
        Sid      = "TelemetryCatalogDataRead"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
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
    ]
  })
}

resource "aws_lambda_function" "ducklake_telemetry_reader" {
  function_name = local.ducklake_telemetry_reader_function
  description   = "Decision 236 production telemetry reader. Event verbs minus transcript_content; SELECT-only login, READ_ONLY attach, env-pinned to ducklake_telemetry."
  role          = aws_iam_role.ducklake_telemetry_reader.arn
  runtime       = "python3.12"
  handler       = "src.lambdas.ducklake_telemetry_reader.handler.handler"
  architectures = ["x86_64"]
  timeout       = 120
  memory_size   = 1024

  # A literal: one telemetry slot and no ops slot per container, against the reader login's CONNECTION LIMIT 10
  # (catalog_logins.LOGINS), keeping the 2 x reserved-concurrency headroom recorded at ducklake_telemetry_writer_role.sql.
  reserved_concurrent_executions = 5

  s3_bucket = aws_s3_bucket.data_lake.id
  s3_key    = "lambda-packages/ducklake-telemetry-reader.zip"

  layers = [
    aws_lambda_layer_version.ducklake_deps.arn,
    aws_lambda_layer_version.ducklake_extensions.arn,
  ]

  environment {
    variables = {
      TELEMETRY_META_SCHEMA         = local.ducklake_telemetry_prod_meta_schema
      TELEMETRY_DATA_PATH           = local.ducklake_telemetry_data_path
      DUCKLAKE_EXTENSION_DIRECTORY  = local.ducklake_extension_dir
      DUCKLAKE_FIELD_SEMANTICS_PATH = "/var/task/config/lambda/ducklake/field_semantics.yaml"
      TELEMETRY_DSN_SECRET_ID       = aws_secretsmanager_secret.ducklake_telemetry_reader_dsn.name
      TELEMETRY_DSN_PARAMETER       = local.secret_parameter_names.telemetry_reader_dsn
    }
  }

  depends_on = [
    aws_iam_role_policy.ducklake_telemetry_reader,
    aws_cloudwatch_log_group.ducklake_telemetry_reader,
  ]

  tags = {
    Name    = "DuckLake Telemetry Reader"
    Purpose = "Decision 236 telemetry reader runtime"
  }

  # Decision 125/126 physical decoupling: code deploys go via the governed CD channel, not terraform.
  # No source_code_hash is set (Terraform reads no build artifact); this block is the conformance marker
  # and a backstop if the argument is ever reintroduced.
  lifecycle {
    ignore_changes = [source_code_hash]
  }
}

resource "aws_lambda_function_url" "ducklake_telemetry_reader" {
  function_name      = aws_lambda_function.ducklake_telemetry_reader.function_name
  authorization_type = "AWS_IAM"
}

resource "aws_ssm_parameter" "ducklake_telemetry_reader_url" {
  name  = "/agent-platform/ducklake/telemetry_reader_url"
  type  = "String"
  value = aws_lambda_function_url.ducklake_telemetry_reader.function_url

  tags = {
    Name    = "DuckLake Telemetry Reader URL"
    Purpose = "Decision 236 endpoint discovery"
  }
}

resource "aws_cloudwatch_metric_alarm" "ducklake_telemetry_reader_errors" {
  alarm_name          = "ducklake-telemetry-reader-errors"
  alarm_description   = "DuckLake telemetry reader: AWS/Lambda Errors plus Url5xxCount > 0 over 15m (loud-fail posture, Decision 55). An operator-run gate that elicits a typed 5xx on purpose pages too."
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
        FunctionName = aws_lambda_function.ducklake_telemetry_reader.function_name
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
        FunctionName = aws_lambda_function.ducklake_telemetry_reader.function_name
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
    Name    = "DuckLake Telemetry Reader Errors Alarm"
    Purpose = "Decision 236 runtime error alert"
  }
}

output "ducklake_telemetry_reader_function_url" {
  description = "AWS_IAM Function URL for the ducklake telemetry reader."
  value       = aws_lambda_function_url.ducklake_telemetry_reader.function_url
}

output "ducklake_telemetry_reader_function_name" {
  description = "Telemetry reader function name (governed CD deploy target)."
  value       = aws_lambda_function.ducklake_telemetry_reader.function_name
}
