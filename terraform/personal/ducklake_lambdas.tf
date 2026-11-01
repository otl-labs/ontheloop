# DuckLake operational-lakehouse runtime Lambdas (T2.17 / CD.33, Decision 81).
#
# First Lambda functions in the personal account: ducklake_writer + ducklake_reader. They ATTACH to
# the live Neon serverless-Postgres DuckLake catalog over TLS (NO VPC attach -- Neon is a public TLS
# endpoint) with the ducklake/httpfs/postgres DuckDB extensions baked into a layer (extension_directory
# + autoload/autoinstall off + custom_extension_repository fail-closed). Function URLs are AWS_IAM
# (SigV4-signed; unsigned -> 403). The two roles are write-scoped / read-scoped: the reader has S3
# GetObject only, so a write from the read role is denied (closed-boundary proof).
#
# ---------------------------------------------------------------------------
# APPLY POSTURE (Decision 77, 126, 144 cl.5): CI plans and applies; the guard decides the route.
# ---------------------------------------------------------------------------
# terraform/personal applies through CI (speculative plan on the PR, apply-sandbox on merge). The
# deterministic guard (scripts/terraform_apply_guard.py) routes by change class: in-budget inline-policy
# UPDATEs on these boundary-carrying roles (Decision 144 cl.5 / T2.25), Lambda environment changes and
# lifecycle changes auto-apply; role CREATION, trust changes, destroys and replaces, out-of-budget IAM
# and any aws_lambda_function_url change (T2.45) route to the tf-gated-apply Environment. IAM must
# precede the code that reads it (terraform CLAUDE.md IAM-precedence).
#
# CODE/INFRA COUPLING (Decision 125/126, environment-taxonomy.yaml conformance): RESOLVED. The two
# aws_lambda_function resources below carry a lifecycle block ignoring source_code_hash changes, so a
# code-only redeploy never surfaces as a Terraform diff. Routine code deploys go through the governed
# code-deploy channel (.github/workflows/deploy-ducklake-lambdas.yml); `build_lambda --ducklake-only
# --deploy` is break-glass only (Decision 125 pt 2-5).
#
# SINGLE-PORTAL NOTE (Decision 78/81): at T2.19 these Function URLs become the CLOSED ops boundary --
# the writer is the sole ops_* write authority, the reader the sole read authority. ops_data_portal
# transits them unconditionally (sole backend, Decision 84 I-1); the caller surface is unchanged. This
# apply widens both roles to the production ducklake/ data path + flips DUCKLAKE_DATA_PATH smoke->prod.

locals {
  ducklake_smoke_data_path   = "s3://${aws_s3_bucket.data_lake.bucket}/ducklake-neon-smoke/"
  ducklake_smoke_data_prefix = "ducklake-neon-smoke"
  # T2.19 production ops data path. The generalized writer/reader operate on the ops_* SCD2 tables
  # here at cutover; smoke access is RETAINED for the T2.17 gates + rollback (Decision 78 cl.7).
  ducklake_prod_data_path   = "s3://${aws_s3_bucket.data_lake.bucket}/ducklake/"
  ducklake_prod_data_prefix = "ducklake"
  ducklake_writer_function  = "agent-platform-ducklake-writer"
  ducklake_reader_function  = "agent-platform-ducklake-reader"
  ducklake_extension_dir    = "/opt/duckdb_extensions"
  # SSOT pin: config/lambda/ducklake/version.yaml -- must be try()-wrapped (validate_terraform_try gate)
  ducklake_version = try(yamldecode(file("${path.module}/../../config/lambda/ducklake/version.yaml")).duckdb_version, "")

  # Telemetry (T2.36): the writer's catalog pin, moved to the production telemetry catalog by Decision 230 (slice 2c's
  # production flip). The blob root is scoped by the catalog it serves, so smoke-era and production blobs never share a root.
  ducklake_telemetry_meta_schema = "ducklake_telemetry"
  telemetry_blob_data_prefix     = "telemetry-blobs"
  ducklake_telemetry_blob_root   = "s3://${aws_s3_bucket.data_lake.bucket}/${local.telemetry_blob_data_prefix}/${local.ducklake_telemetry_meta_schema}/"

  # Telemetry writer function name; declared here so the pin guard's locals parse sees it.
  ducklake_telemetry_writer_function = "agent-platform-ducklake-telemetry-writer"

  # Production telemetry catalog (Decision 230): a sibling of ducklake/, never inside it, so the ops catalog's
  # catalog-wide orphan cleanup never sees telemetry Parquet. The writer pin above holds the same value (Decision 230).
  ducklake_telemetry_data_prefix      = "ducklake-telemetry"
  ducklake_telemetry_data_path        = "s3://${aws_s3_bucket.data_lake.bucket}/ducklake-telemetry/"
  ducklake_telemetry_prod_meta_schema = "ducklake_telemetry"
  ducklake_telemetry_reader_function  = "agent-platform-ducklake-telemetry-reader"

  # Smoke twin of the telemetry writer (Decision 236 cl.1): pinned to the fixed smoke locals below, never to the
  # flipping ducklake_telemetry_meta_schema, so the flip cannot move it.
  ducklake_telemetry_writer_smoke_function = "agent-platform-ducklake-telemetry-writer-smoke"
  ducklake_smoke_meta_schema               = "ducklake_smoke"
  ducklake_telemetry_smoke_blob_root       = "s3://${aws_s3_bucket.data_lake.bucket}/${local.telemetry_blob_data_prefix}/${local.ducklake_smoke_meta_schema}/"
}

# ---------------------------------------------------------------------------
# Layers (from S3) -- duckdb (pinned via config/lambda/ducklake/version.yaml) + deps, and the 3
# baked extensions. The layer zips are uploaded to S3 by build_lambda BEFORE the apply; no
# source_code_hash is set, so Terraform reads no local zip.
# ---------------------------------------------------------------------------

resource "aws_lambda_layer_version" "ducklake_deps" {
  layer_name          = "ducklake-deps"
  description         = "DuckLake deps: duckdb==${local.ducklake_version}, psycopg2-binary, python-ulid, pyyaml (T2.17)"
  s3_bucket           = aws_s3_bucket.data_lake.id
  s3_key              = "lambda-packages/ducklake-deps-layer.zip"
  compatible_runtimes = ["python3.12"]

  # T2.42 c1 (rec-2646/rec-2654 pattern extended to layers, Decision 126 pt3/pt5, DEP-03): decouples
  # routine rebuild churn from this IAM-gated apply path. No source_code_hash is set (Terraform reads no
  # build artifact) and this block stays as the conformance marker and a backstop. Terraform never
  # republishes a layer on content change (it did not before either; rec-4253). s3_key stays the
  # fixed literal (unaffected by T2.42 c3's content-addressed upload path).
  #
  # SILENT-FREEZE GUARD: source_code_hash is not set, so Terraform only publishes a new layer
  # version when `description` changes. This description interpolates local.ducklake_version ONLY --
  # a genuine non-duckdb dependency bump (psycopg2-binary / python-ulid / pyyaml) with an unchanged
  # duckdb version leaves the description unchanged, so terraform publishes NO new layer version and
  # the function silently keeps the OLD deps. Any such bump MUST also bump the description/marker (or
  # go via the deploy-verb publish path, publish_canary_layers) to force republish.
  lifecycle {
    ignore_changes = [source_code_hash]
  }
}

resource "aws_lambda_layer_version" "ducklake_extensions" {
  layer_name          = "ducklake-extensions"
  description         = "Baked DuckDB v${local.ducklake_version} linux_amd64 extensions: ducklake, httpfs, postgres_scanner (T2.17)"
  s3_bucket           = aws_s3_bucket.data_lake.id
  s3_key              = "lambda-packages/ducklake-extensions-layer.zip"
  compatible_runtimes = ["python3.12"]

  # T2.42 c1 (rec-2646/rec-2654 pattern extended to layers, Decision 126 pt3/pt5, DEP-03): decouples
  # routine rebuild churn from this IAM-gated apply path. No source_code_hash is set (Terraform reads no
  # build artifact) and this block stays as the conformance marker and a backstop. Terraform never
  # republishes a layer on content change (it did not before either; rec-4253). s3_key stays the
  # fixed literal (unaffected by T2.42 c3's content-addressed upload path). This layer's description
  # is fully duckdb-version-driven (local.ducklake_version), so a genuine content bump (a DuckDB
  # version bump) always moves the description and republishes correctly -- no silent-freeze risk.
  lifecycle {
    ignore_changes = [source_code_hash]
  }
}

# ---------------------------------------------------------------------------
# CloudWatch log groups (pre-created so the execution-role grant can be scoped to their ARNs).
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "ducklake_writer" {
  name              = "/aws/lambda/${local.ducklake_writer_function}"
  retention_in_days = 14

  tags = {
    Name    = "DuckLake Writer Logs"
    Purpose = "T2.17 ducklake_writer runtime"
  }
}

resource "aws_cloudwatch_log_group" "ducklake_reader" {
  name              = "/aws/lambda/${local.ducklake_reader_function}"
  retention_in_days = 14

  tags = {
    Name    = "DuckLake Reader Logs"
    Purpose = "T2.17 ducklake_reader runtime"
  }
}

# ---------------------------------------------------------------------------
# Write-scoped execution role: S3 RW on the ducklake/ + smoke prefixes, DSN secret read, metrics, logs (ops only; telemetry has its own function and role).
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ducklake_writer" {
  # Decision 144 (T2.48): mandatory broad-but-bounded exec-identity boundary (16/17 roles; PlatformAdmin excluded).
  name                 = "agent-platform-ducklake-writer"
  description          = "Write-scoped DuckLake runtime: S3 RW on ducklake/ + smoke prefixes, Neon DSN read, metrics"
  permissions_boundary = "arn:aws:iam::${var.account_id}:policy/agent-platform-github-ci-apply-boundary"
  assume_role_policy   = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "ducklake_writer" {
  name = "DuckLakeWriterRuntime"
  role = aws_iam_role.ducklake_writer.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "Logs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = ["${aws_cloudwatch_log_group.ducklake_writer.arn}:*"]
      },
      {
        Sid      = "NeonDsnRead"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
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
        # Writer is the SOLE write authority for ops_* (CD.33 clause 4): RW on the production
        # ducklake/ data path AND the retained smoke prefix (T2.17 gates / rollback).
        Sid    = "S3DataReadWrite"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = [
          "${aws_s3_bucket.data_lake.arn}/${local.ducklake_prod_data_prefix}/*",
          "${aws_s3_bucket.data_lake.arn}/${local.ducklake_smoke_data_prefix}/*",
        ]
      },
      {
        Sid      = "S3ListDataPrefix"
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = [aws_s3_bucket.data_lake.arn]
        Condition = {
          StringLike = {
            "s3:prefix" = ["${local.ducklake_prod_data_prefix}/*", "${local.ducklake_smoke_data_prefix}/*"]
          }
        }
      },
      {
        # PutMetricData does not support resource-level scoping; constrain to the writer namespace.
        Sid      = "CloudWatchMetrics"
        Effect   = "Allow"
        Action   = ["cloudwatch:PutMetricData"]
        Resource = "*"
        Condition = {
          StringEquals = {
            "cloudwatch:namespace" = "DuckLakeWriter"
          }
        }
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# Read-scoped execution role: S3 GetObject ONLY on the ducklake/ + smoke prefixes (no Put/Delete -> writes denied), DSN
# secret read, logs. No metrics, and no telemetry grant: event reads are the telemetry reader's (Decision 236).
# ---------------------------------------------------------------------------

resource "aws_iam_role" "ducklake_reader" {
  # Decision 144 (T2.48): mandatory broad-but-bounded exec-identity boundary (16/17 roles; PlatformAdmin excluded).
  name                 = "agent-platform-ducklake-reader"
  description          = "Read-scoped DuckLake runtime: S3 GetObject on ducklake/ + smoke prefixes only, Neon DSN read"
  permissions_boundary = "arn:aws:iam::${var.account_id}:policy/agent-platform-github-ci-apply-boundary"
  assume_role_policy   = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "ducklake_reader" {
  name = "DuckLakeReaderRuntime"
  role = aws_iam_role.ducklake_reader.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "Logs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = ["${aws_cloudwatch_log_group.ducklake_reader.arn}:*"]
      },
      {
        Sid      = "NeonDsnRead"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
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
        # GetObject ONLY -- no PutObject/DeleteObject. A DuckLake write (Parquet PutObject) from this
        # role is denied at S3, which is the closed-boundary proof (write_probe -> write_denied=true).
        # Read-only on the production ducklake/ data path AND the retained smoke prefix.
        Sid    = "S3DataReadOnly"
        Effect = "Allow"
        Action = ["s3:GetObject"]
        Resource = [
          "${aws_s3_bucket.data_lake.arn}/${local.ducklake_prod_data_prefix}/*",
          "${aws_s3_bucket.data_lake.arn}/${local.ducklake_smoke_data_prefix}/*",
        ]
      },
      {
        Sid      = "S3ListDataPrefix"
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = [aws_s3_bucket.data_lake.arn]
        Condition = {
          StringLike = {
            "s3:prefix" = ["${local.ducklake_prod_data_prefix}/*", "${local.ducklake_smoke_data_prefix}/*"]
          }
        }
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# The two Lambda functions (from S3). No source_code_hash is set; code is updated post-apply by
# the governed code-deploy channel (Decision 125/126).
# ---------------------------------------------------------------------------

resource "aws_lambda_function" "ducklake_writer" {
  function_name = local.ducklake_writer_function
  description   = "T2.17 DuckLake write-scoped runtime (CD.33). Smoke-test ingress only (Decision 78/81)."
  role          = aws_iam_role.ducklake_writer.arn
  runtime       = "python3.12"
  handler       = "src.lambdas.ducklake_writer.handler.handler"
  architectures = ["x86_64"]
  timeout       = 120
  memory_size   = 3008 # Retained as baseline headroom per human decision (Decision 82 frame correction). Branch-P rationale superseded: EC8 now measures N concurrent invocations (each its own vCPU), not in-container thread contention.

  s3_bucket = aws_s3_bucket.data_lake.id
  s3_key    = "lambda-packages/ducklake-writer.zip"

  layers = [
    aws_lambda_layer_version.ducklake_deps.arn,
    aws_lambda_layer_version.ducklake_extensions.arn,
  ]

  environment {
    variables = {
      DUCKLAKE_DATA_PATH            = local.ducklake_prod_data_path
      DUCKLAKE_EXTENSION_DIRECTORY  = local.ducklake_extension_dir
      DUCKLAKE_FIELD_SEMANTICS_PATH = "/var/task/config/lambda/ducklake/field_semantics.yaml"
    }
  }

  depends_on = [
    aws_iam_role_policy.ducklake_writer,
    aws_cloudwatch_log_group.ducklake_writer,
  ]

  # Decision 125 physical decoupling: code deploys go through the governed code-deploy channel
  # (deploy-ducklake-lambdas.yml; build_lambda --deploy is break-glass), not terraform. No source_code_hash
  # is set (Terraform reads no build artifact); this block is the conformance marker and a backstop
  # (rec-2646/rec-2654).
  lifecycle {
    ignore_changes = [source_code_hash]
  }

  tags = {
    Name    = "DuckLake Writer"
    Purpose = "T2.17 ducklake_writer runtime"
  }
}

resource "aws_lambda_function" "ducklake_reader" {
  function_name = local.ducklake_reader_function
  description   = "T2.17 DuckLake read-scoped runtime (CD.33). Smoke-test ingress only (Decision 78/81)."
  role          = aws_iam_role.ducklake_reader.arn
  runtime       = "python3.12"
  handler       = "src.lambdas.ducklake_reader.handler.handler"
  architectures = ["x86_64"]
  timeout       = 120
  memory_size   = 1024

  s3_bucket = aws_s3_bucket.data_lake.id
  s3_key    = "lambda-packages/ducklake-reader.zip"

  layers = [
    aws_lambda_layer_version.ducklake_deps.arn,
    aws_lambda_layer_version.ducklake_extensions.arn,
  ]

  environment {
    variables = {
      DUCKLAKE_DATA_PATH            = local.ducklake_prod_data_path
      DUCKLAKE_EXTENSION_DIRECTORY  = local.ducklake_extension_dir
      DUCKLAKE_FIELD_SEMANTICS_PATH = "/var/task/config/lambda/ducklake/field_semantics.yaml"
    }
  }

  depends_on = [
    aws_iam_role_policy.ducklake_reader,
    aws_cloudwatch_log_group.ducklake_reader,
  ]

  # Decision 125 physical decoupling: code deploys go through the governed code-deploy channel
  # (deploy-ducklake-lambdas.yml; build_lambda --deploy is break-glass), not terraform. No source_code_hash
  # is set (Terraform reads no build artifact); this block is the conformance marker and a backstop
  # (rec-2646/rec-2654).
  lifecycle {
    ignore_changes = [source_code_hash]
  }

  tags = {
    Name    = "DuckLake Reader"
    Purpose = "T2.17 ducklake_reader runtime"
  }
}

# ---------------------------------------------------------------------------
# Function URLs (AWS_IAM). SigV4-signed requests from a principal with lambda:InvokeFunctionUrl pass;
# unsigned requests return 403 (EC4). CD.10 / NS.5 ingress surface, unaffected by the no-VPC config.
# ---------------------------------------------------------------------------

resource "aws_lambda_function_url" "ducklake_writer" {
  function_name      = aws_lambda_function.ducklake_writer.function_name
  authorization_type = "AWS_IAM"
}

resource "aws_lambda_function_url" "ducklake_reader" {
  function_name      = aws_lambda_function.ducklake_reader.function_name
  authorization_type = "AWS_IAM"
}

# ---------------------------------------------------------------------------
# Outputs -- consumed by the [post-deploy] smoke-test invoke gates (ducklake_neon_smoke_test).
# ---------------------------------------------------------------------------

output "ducklake_writer_function_url" {
  description = "AWS_IAM Function URL for the ducklake_writer Lambda (T2.17 smoke gates)."
  value       = aws_lambda_function_url.ducklake_writer.function_url
}

output "ducklake_reader_function_url" {
  description = "AWS_IAM Function URL for the ducklake_reader Lambda (T2.17 smoke gates)."
  value       = aws_lambda_function_url.ducklake_reader.function_url
}

output "ducklake_writer_function_name" {
  description = "ducklake_writer Lambda function name (code-deploy channel target)."
  value       = aws_lambda_function.ducklake_writer.function_name
}

output "ducklake_reader_function_name" {
  description = "ducklake_reader Lambda function name (code-deploy channel target)."
  value       = aws_lambda_function.ducklake_reader.function_name
}

# ---------------------------------------------------------------------------
# SSM endpoint-discovery parameters (Decision 79 SSOT / Decision 81 cl.7 closed boundary).
# Published by push-to-main auto-apply (non-IAM, non-destroy: guard passes).
# Runtime clients resolve the Function URL via ssm:GetParameter on the PlatformDev role
# (see platform_roles.tf DuckLakeEndpointDiscovery statement). No write-path data
# transits SSM -- these parameters hold only the public Function URL strings.
# ---------------------------------------------------------------------------

resource "aws_ssm_parameter" "ducklake_reader_url" {
  name        = "/agent-platform/ducklake/reader_url"
  type        = "String"
  value       = aws_lambda_function_url.ducklake_reader.function_url
  description = "DuckLake reader Function URL for endpoint discovery (Decision 79 / Decision 81 cl.7)"

  tags = {
    Name    = "ducklake-reader-url"
    Purpose = "T2.19 DuckLake endpoint discovery"
  }
}

resource "aws_ssm_parameter" "ducklake_writer_url" {
  name        = "/agent-platform/ducklake/writer_url"
  type        = "String"
  value       = aws_lambda_function_url.ducklake_writer.function_url
  description = "DuckLake writer Function URL for endpoint discovery (Decision 79 / Decision 81 cl.7)"

  tags = {
    Name    = "ducklake-writer-url"
    Purpose = "T2.19 DuckLake endpoint discovery"
  }
}

# Writer loud-failure visibility (Decision 84 I-4): with the offline outbox retired, a failed
# write surfaces only at the call site -- which may be a headless GitHub Actions run (ci-rca).
# This alarm makes writer errors reach a human within 15 minutes regardless of the caller.
# Url5xxCount is summed because the handler now answers a typed 5xx (a handled return) that never counts in Errors.
resource "aws_cloudwatch_metric_alarm" "ducklake_writer_errors" {
  alarm_name          = "ducklake-writer-errors"
  alarm_description   = "ducklake_writer: AWS/Lambda Errors plus Url5xxCount > 0 over 15m. Decision 84 I-4: no outbox buffers a failed ops write; a sustained error here means rec/decision filing is down. An operator-run gate that elicits a typed 5xx on purpose pages too (PLAN-ducklake-typed-unmapped-errors)."
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
        FunctionName = aws_lambda_function.ducklake_writer.function_name
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
        FunctionName = aws_lambda_function.ducklake_writer.function_name
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
    Name    = "DuckLake Writer Error Alarm"
    Purpose = "Decision 84 I-4 loud-failure visibility"
  }
}
