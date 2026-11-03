# Prod-class Lambda functions (T2.43 / Decision 125/126): the decoupled_build_pipeline class.
#
# Provisions the two functions that were ABSENT from the personal account (rec-2157/rec-2164):
# agent-platform-scheduled-agent-dispatcher and agent-platform-findings-processor. Ported from the
# retired work-account root (CD.21) and adapted to the personal-module idiom: var.account_id / data.aws_caller_identity
# interpolation (never a literal, Decision 101), the single agent-platform-data-lake bucket (this
# module has no separate agent-logs bucket -- agents/, findings/, recommendations/, and staging/ are
# all prefixes on aws_s3_bucket.data_lake), the default provider (agent_platform_admin), and a
# PER-FUNCTION execution role + inline policy each (least-privilege, mirroring the ducklake_lambdas.tf
# precedent) rather than the one shared role the retired file used.
#
# ---------------------------------------------------------------------------
# APPLY POSTURE (Decision 35 + 77 + 98): HUMAN-GATED via agent_platform_admin.
# ---------------------------------------------------------------------------
# These resources create NEW IAM roles + inline policies, which trip the Decision-77 deterministic
# guard (scripts/terraform_apply_guard.py, fail-closed on any IAM/trust change). The whole
# terraform/personal apply for this change therefore routes to the MANUAL agent_platform_admin path,
# NOT push-to-main auto-apply.
#
# CODE/INFRA DECOUPLING (Decision 125, environment-taxonomy.yaml conformance): every aws_lambda_function
# below carries a lifecycle block ignoring source_code_hash changes FROM DAY ONE -- unlike the
# DuckLake class (which coupled first and decoupled later at #544), this class is decoupled from its
# very first apply. Code deploys go via the governed .github/workflows/deploy-prod-lambdas.yml channel
# (T2.43), never terraform.
#
# SCHEDULE STAYS DISABLED (Decision 61/37/116): the dispatcher's EventBridge rule is provisioned with
# state = "DISABLED" and env SCHEDULED_AGENTS_ENABLED = "false". Provisioning these functions does NOT
# re-enable the scheduled agents -- that is a separate, later decision (see AGENTS.md's "Re-enable
# Lambda scheduled agents" runbook). Both scheduled_agent_handler.handler and
# findings_processor_handler.handler degrade gracefully with no live GitHub PAT value or S3 event
# payload (verified against the handler source at plan time), so a smoke invocation of either function
# produces clean, observable JSON output without depending on the schedule being enabled.

locals {
  scheduled_agent_dispatcher_function = "agent-platform-scheduled-agent-dispatcher"
  findings_processor_function         = "agent-platform-findings-processor"
}

# ---------------------------------------------------------------------------
# NOTE (T2.43 apply-time correction): no shared dependencies layer is attached to the
# dispatcher/findings-processor functions -- neither needs one (verified against handler source:
# the only third-party import is pyyaml). pyyaml is bundled directly into data-pipeline.zip via
# src/lambdas/data-pipeline/manifest.yaml's pip_packages instead.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# CloudWatch log groups (pre-created so each execution role can be scoped to its own ARN).
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "scheduled_agent_dispatcher" {
  name              = "/aws/lambda/${local.scheduled_agent_dispatcher_function}"
  retention_in_days = 14

  tags = {
    Name    = "Scheduled Agent Dispatcher Logs"
    Purpose = "T2.43 scheduled-agent-dispatcher runtime"
  }
}

resource "aws_cloudwatch_log_group" "findings_processor" {
  name              = "/aws/lambda/${local.findings_processor_function}"
  retention_in_days = 14

  tags = {
    Name    = "Findings Processor Logs"
    Purpose = "T2.43 findings-processor runtime"
  }
}

# ---------------------------------------------------------------------------
# Execution role: scheduled-agent-dispatcher. Writes raw findings under agents/. Reuses data.aws_iam_policy_document.lambda_assume
# (defined once in ducklake_lambdas.tf; a module-global data source, not file-scoped).
# ---------------------------------------------------------------------------

resource "aws_iam_role" "scheduled_agent_dispatcher" {
  # Decision 144 (T2.48): mandatory broad-but-bounded exec-identity boundary (16/17 roles; PlatformAdmin excluded).
  name                 = local.scheduled_agent_dispatcher_function
  description          = "Execution role for the scheduled-agent-dispatcher Lambda (T2.43)"
  permissions_boundary = "arn:aws:iam::${var.account_id}:policy/agent-platform-github-ci-apply-boundary"
  assume_role_policy   = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "scheduled_agent_dispatcher" {
  name = "ScheduledAgentDispatcherRuntime"
  role = aws_iam_role.scheduled_agent_dispatcher.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "Logs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = ["${aws_cloudwatch_log_group.scheduled_agent_dispatcher.arn}:*"]
      },
      {
        Sid      = "S3AgentFindingsWrite"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = ["${aws_s3_bucket.data_lake.arn}/agents/*"]
      },
      {
        Sid      = "S3ListAgentsPrefix"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [aws_s3_bucket.data_lake.arn]
        Condition = {
          StringLike = {
            "s3:prefix" = ["agents/*"]
          }
        }
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# Execution role: findings-processor. Reads agents/ raw findings, writes findings/unified.jsonl +
# recommendations/agent-recommendations.jsonl + priority-queue/.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "findings_processor" {
  # Decision 144 (T2.48): mandatory broad-but-bounded exec-identity boundary (16/17 roles; PlatformAdmin excluded).
  name                 = local.findings_processor_function
  description          = "Execution role for the findings-processor Lambda (T2.43)"
  permissions_boundary = "arn:aws:iam::${var.account_id}:policy/agent-platform-github-ci-apply-boundary"
  assume_role_policy   = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "findings_processor" {
  name = "FindingsProcessorRuntime"
  role = aws_iam_role.findings_processor.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "Logs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = ["${aws_cloudwatch_log_group.findings_processor.arn}:*"]
      },
      {
        Sid    = "S3FindingsReadWrite"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:PutObject"]
        Resource = [
          "${aws_s3_bucket.data_lake.arn}/agents/*",
          "${aws_s3_bucket.data_lake.arn}/findings/*",
          "${aws_s3_bucket.data_lake.arn}/recommendations/*",
          "${aws_s3_bucket.data_lake.arn}/priority-queue/*",
        ]
      },
      {
        Sid      = "S3ListFindingsPrefixes"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [aws_s3_bucket.data_lake.arn]
        Condition = {
          StringLike = {
            "s3:prefix" = ["agents/*", "findings/*", "recommendations/*", "priority-queue/*"]
          }
        }
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# The two Lambda functions (from S3). No source_code_hash is set (Terraform reads no build artifact); code is updated post-apply
# by the governed .github/workflows/deploy-prod-lambdas.yml channel (T2.43).
# ---------------------------------------------------------------------------

resource "aws_lambda_function" "scheduled_agent_dispatcher" {
  function_name = local.scheduled_agent_dispatcher_function
  description   = "T2.43 scheduled-agent dispatcher (Decision 125/126 decoupled_build_pipeline class). Schedule stays DISABLED (Decision 61/37/116)."
  role          = aws_iam_role.scheduled_agent_dispatcher.arn
  handler       = "src.data.handlers.scheduled_agent_handler.handler"
  runtime       = "python3.12"
  architectures = ["x86_64"]
  timeout       = 900 # 15 minutes -- enough for sequential agent execution when eventually enabled
  memory_size   = 512

  s3_bucket = aws_s3_bucket.data_lake.id
  s3_key    = "lambda-packages/data-pipeline.zip"

  environment {
    variables = {
      S3_LOG_BUCKET            = aws_s3_bucket.data_lake.id
      SCHEDULED_AGENTS_ENABLED = "false"
    }
  }

  depends_on = [
    aws_iam_role_policy.scheduled_agent_dispatcher,
    aws_cloudwatch_log_group.scheduled_agent_dispatcher,
  ]

  # Decision 125 decoupling from day one (see file header): code deploys go via the governed
  # deploy-prod-lambdas.yml channel (T2.43), never terraform.
  lifecycle {
    ignore_changes = [source_code_hash]
  }

  tags = {
    Name    = "Scheduled Agent Dispatcher"
    Purpose = "T2.43 scheduled-agent-dispatcher runtime - schedule disabled"
  }
}

resource "aws_lambda_function" "findings_processor" {
  function_name = local.findings_processor_function
  description   = "T2.43 findings processor (Decision 125/126 decoupled_build_pipeline class). Flagged for Phase-5 retirement (Decision 61)."
  role          = aws_iam_role.findings_processor.arn
  handler       = "src.data.handlers.findings_processor_handler.handler"
  runtime       = "python3.12"
  architectures = ["x86_64"]
  timeout       = 300 # 5 minutes -- comparison call + S3 writes
  memory_size   = 256

  s3_bucket = aws_s3_bucket.data_lake.id
  s3_key    = "lambda-packages/data-pipeline.zip"

  environment {
    variables = {
      S3_LOG_BUCKET = aws_s3_bucket.data_lake.id
    }
  }

  depends_on = [
    aws_iam_role_policy.findings_processor,
    aws_cloudwatch_log_group.findings_processor,
  ]

  lifecycle {
    ignore_changes = [source_code_hash]
  }

  tags = {
    Name    = "Findings Processor"
    Purpose = "T2.43 findings-processor runtime"
  }
}

# ---------------------------------------------------------------------------
# EventBridge -- hourly schedule -> dispatcher. Provisioned DISABLED (Decision 61/37/116);
# provisioning this trigger does NOT re-enable the scheduled agents.
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "hourly_scheduled_agents" {
  name                = "agent-platform-hourly-scheduled-agents"
  description         = "Invoke scheduled agent dispatcher every hour (T2.43; stays DISABLED -- Decision 61/37/116)"
  schedule_expression = "cron(0 * * * ? *)"
  state               = "DISABLED"

  tags = {
    Name = "Hourly Scheduled Agents"
  }
}

resource "aws_cloudwatch_event_target" "hourly_scheduled_agents_dispatcher" {
  rule      = aws_cloudwatch_event_rule.hourly_scheduled_agents.name
  target_id = "scheduled-agent-dispatcher"
  arn       = aws_lambda_function.scheduled_agent_dispatcher.arn
}

resource "aws_lambda_permission" "eventbridge_invoke_dispatcher" {
  statement_id  = "AllowEventBridgeInvokeDispatcher"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.scheduled_agent_dispatcher.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.hourly_scheduled_agents.arn
}

# ---------------------------------------------------------------------------
# S3 event notifications -- agent findings -> findings-processor.
#
# GOTCHA: aws_s3_bucket_notification is a SINGLETON per bucket -- only ONE such resource may target
# aws_s3_bucket.data_lake in this module, or one apply will silently clobber the other's
# configuration. All data_lake triggers are declared inside this single resource; a future addition
# of another data_lake trigger must extend THIS resource, not add a sibling aws_s3_bucket_notification.
# ---------------------------------------------------------------------------

resource "aws_lambda_permission" "s3_invoke_findings_processor" {
  statement_id  = "AllowS3InvokeFindingsProcessor"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.findings_processor.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.data_lake.arn
}

resource "aws_s3_bucket_notification" "data_lake_prod_triggers" {
  bucket = aws_s3_bucket.data_lake.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.findings_processor.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "agents/"
    filter_suffix       = ".jsonl"
  }

  depends_on = [
    aws_lambda_permission.s3_invoke_findings_processor,
  ]
}

# ---------------------------------------------------------------------------
# Outputs -- consumed by the governed deploy workflow / operator verification (VP steps 8-11).
# ---------------------------------------------------------------------------

output "scheduled_agent_dispatcher_function_name" {
  description = "scheduled-agent-dispatcher Lambda function name (deploy-prod-lambdas.yml target)."
  value       = aws_lambda_function.scheduled_agent_dispatcher.function_name
}

output "findings_processor_function_name" {
  description = "findings-processor Lambda function name (deploy-prod-lambdas.yml target)."
  value       = aws_lambda_function.findings_processor.function_name
}
