# Scheduled workflow dispatcher stack (Decision 267, rec-4233; PLAN-scheduled-workflow-dispatch PR 3).
#
# Three EventBridge rules start terraform-drift.yml, convergence-health.yml and main-canary.yml from AWS through
# one Lambda, agent-platform-workflow-dispatcher, which mints a short-lived GitHub App token and starts the workflow on
# main with workflow_dispatch. Each workflow keeps its GitHub cron as the fallback. The SIT copy is switched off three
# ways: rules DISABLED, the function returns before any secret read (WORKFLOW_DISPATCH_ENABLED=false) and the alarms'
# actions are off.
#
# APPLY POSTURE: the role CREATE is trust-bearing, so the plan routes to tf-gated-apply (Decision 144 cl.4). The App
# private key value is put out of band (Decision 237 cl.3) and never by Terraform: no SSM parameter resource and no
# data source on /secret/ here; the role reads it by literal ARN. Code deploys through deploy-prod-lambdas.yml
# (Decision 125/126) from PR 4 on, so the function carries ignore_changes on source_code_hash from day one.
#
# TRUSTED-PLAN HCL SCREEN (Decision 238 cl.4, scripts/ci/terraform_hcl_screen.py): no count, for_each, ? operator or
# file read, so every rule, target, permission and alarm is written out literally. Each row's cron, name and alarm
# period repeat config/lambda/scheduled_workflow_dispatcher/schedules.yaml; the stack test holds them equal.

locals {
  workflow_dispatcher_function = "agent-platform-workflow-dispatcher"
  workflow_dispatch_production = { sandbox = true, sit = false }[var.environment]
}

resource "aws_cloudwatch_log_group" "workflow_dispatcher" {
  name              = "/aws/lambda/${local.workflow_dispatcher_function}"
  retention_in_days = 14

  tags = {
    Name    = "Workflow Dispatcher Logs"
    Purpose = "Scheduled workflow dispatcher runtime"
  }
}

resource "aws_iam_role" "workflow_dispatcher" {
  name                 = local.workflow_dispatcher_function
  description          = "Execution role for the scheduled workflow dispatcher Lambda (Decision 267)"
  permissions_boundary = "arn:aws:iam::${var.account_id}:policy/agent-platform-github-ci-apply-boundary"
  assume_role_policy   = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "workflow_dispatcher" {
  statement {
    sid       = "WorkflowDispatcherLogs"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:/aws/lambda/agent-platform-workflow-dispatcher:*"]
  }

  statement {
    sid       = "WorkflowDispatcherAppKeyRead"
    effect    = "Allow"
    actions   = ["ssm:GetParameter"]
    resources = ["arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/secret/agent-platform-workflow-dispatcher-app-key"]
  }
}

resource "aws_iam_role_policy" "workflow_dispatcher" {
  name   = "WorkflowDispatcherRuntime"
  role   = aws_iam_role.workflow_dispatcher.id
  policy = data.aws_iam_policy_document.workflow_dispatcher.json
}

resource "aws_lambda_function" "workflow_dispatcher" {
  function_name = local.workflow_dispatcher_function
  description   = "Starts the register's workflows on main through a GitHub App (Decision 267; Decision 125/126 decoupled_build_pipeline class)."
  role          = aws_iam_role.workflow_dispatcher.arn
  handler       = "src.data.handlers.workflow_dispatcher_handler.handler"
  runtime       = "python3.12"
  architectures = ["x86_64"]
  timeout       = 60
  memory_size   = 256

  reserved_concurrent_executions = 1

  s3_bucket = aws_s3_bucket.data_lake.id
  s3_key    = "lambda-packages/data-pipeline.zip"

  environment {
    variables = {
      WORKFLOW_DISPATCH_ENABLED = { sandbox = "true", sit = "false" }[var.environment]
    }
  }

  depends_on = [
    aws_iam_role_policy.workflow_dispatcher,
    aws_cloudwatch_log_group.workflow_dispatcher,
  ]

  lifecycle {
    ignore_changes = [source_code_hash]
  }

  tags = {
    Name    = "Workflow Dispatcher"
    Purpose = "Scheduled workflow dispatch via GitHub App"
  }
}

resource "aws_cloudwatch_event_rule" "workflow_dispatch_terraform_drift" {
  name                = "agent-platform-workflow-dispatch-terraform-drift"
  description         = "Start terraform-drift.yml on main through the workflow dispatcher (Decision 267)"
  schedule_expression = "cron(17 * * * ? *)"
  state               = { sandbox = "ENABLED", sit = "DISABLED" }[var.environment]

  tags = {
    Name = "Workflow Dispatch terraform-drift"
  }
}

resource "aws_cloudwatch_event_target" "workflow_dispatch_terraform_drift" {
  rule      = aws_cloudwatch_event_rule.workflow_dispatch_terraform_drift.name
  target_id = "workflow-dispatcher"
  arn       = aws_lambda_function.workflow_dispatcher.arn
  input     = jsonencode({ schedule = "terraform-drift" })
}

resource "aws_lambda_permission" "workflow_dispatch_terraform_drift" {
  statement_id  = "AllowEventBridgeInvokeDispatchTerraformDrift"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.workflow_dispatcher.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.workflow_dispatch_terraform_drift.arn
}

resource "aws_cloudwatch_event_rule" "workflow_dispatch_convergence_health" {
  name                = "agent-platform-workflow-dispatch-convergence-health"
  description         = "Start convergence-health.yml on main through the workflow dispatcher (Decision 267)"
  schedule_expression = "cron(37 * * * ? *)"
  state               = { sandbox = "ENABLED", sit = "DISABLED" }[var.environment]

  tags = {
    Name = "Workflow Dispatch convergence-health"
  }
}

resource "aws_cloudwatch_event_target" "workflow_dispatch_convergence_health" {
  rule      = aws_cloudwatch_event_rule.workflow_dispatch_convergence_health.name
  target_id = "workflow-dispatcher"
  arn       = aws_lambda_function.workflow_dispatcher.arn
  input     = jsonencode({ schedule = "convergence-health" })
}

resource "aws_lambda_permission" "workflow_dispatch_convergence_health" {
  statement_id  = "AllowEventBridgeInvokeDispatchConvergenceHealth"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.workflow_dispatcher.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.workflow_dispatch_convergence_health.arn
}

resource "aws_cloudwatch_event_rule" "workflow_dispatch_main_canary" {
  name                = "agent-platform-workflow-dispatch-main-canary"
  description         = "Start main-canary.yml on main through the workflow dispatcher (Decision 267)"
  schedule_expression = "cron(0 0/3 * * ? *)"
  state               = { sandbox = "ENABLED", sit = "DISABLED" }[var.environment]

  tags = {
    Name = "Workflow Dispatch main-canary"
  }
}

resource "aws_cloudwatch_event_target" "workflow_dispatch_main_canary" {
  rule      = aws_cloudwatch_event_rule.workflow_dispatch_main_canary.name
  target_id = "workflow-dispatcher"
  arn       = aws_lambda_function.workflow_dispatcher.arn
  input     = jsonencode({ schedule = "main-canary" })
}

resource "aws_lambda_permission" "workflow_dispatch_main_canary" {
  statement_id  = "AllowEventBridgeInvokeDispatchMainCanary"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.workflow_dispatcher.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.workflow_dispatch_main_canary.arn
}

resource "aws_cloudwatch_metric_alarm" "workflow_dispatcher_errors" {
  alarm_name          = "agent-platform-workflow-dispatcher-errors"
  alarm_description   = "The workflow dispatcher Lambda errored: a failed mint, dispatch or revoke (Decision 267)."
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 1
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.workflow_dispatcher.function_name
  }

  actions_enabled = local.workflow_dispatch_production
  alarm_actions   = [aws_sns_topic.alerts.arn]

  tags = {
    Name = "Workflow Dispatcher Errors Alarm"
  }
}

resource "aws_cloudwatch_metric_alarm" "workflow_dispatch_not_fired_terraform_drift" {
  alarm_name          = "agent-platform-workflow-dispatch-not-fired-terraform-drift"
  alarm_description   = "The terraform-drift rule did not fire within its window; the GitHub cron fallback still runs (Decision 267)."
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 2
  metric_name         = "TriggeredRules"
  namespace           = "AWS/Events"
  period              = 3600
  statistic           = "Sum"
  threshold           = 1
  treat_missing_data  = "breaching"

  dimensions = {
    RuleName = aws_cloudwatch_event_rule.workflow_dispatch_terraform_drift.name
  }

  actions_enabled = local.workflow_dispatch_production
  alarm_actions   = [aws_sns_topic.alerts.arn]

  tags = {
    Name = "Workflow Dispatch Not Fired terraform-drift"
  }
}

resource "aws_cloudwatch_metric_alarm" "workflow_dispatch_not_fired_convergence_health" {
  alarm_name          = "agent-platform-workflow-dispatch-not-fired-convergence-health"
  alarm_description   = "The convergence-health rule did not fire within its window; the GitHub cron fallback still runs (Decision 267)."
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 2
  metric_name         = "TriggeredRules"
  namespace           = "AWS/Events"
  period              = 3600
  statistic           = "Sum"
  threshold           = 1
  treat_missing_data  = "breaching"

  dimensions = {
    RuleName = aws_cloudwatch_event_rule.workflow_dispatch_convergence_health.name
  }

  actions_enabled = local.workflow_dispatch_production
  alarm_actions   = [aws_sns_topic.alerts.arn]

  tags = {
    Name = "Workflow Dispatch Not Fired convergence-health"
  }
}

resource "aws_cloudwatch_metric_alarm" "workflow_dispatch_not_fired_main_canary" {
  alarm_name          = "agent-platform-workflow-dispatch-not-fired-main-canary"
  alarm_description   = "The main-canary rule did not fire within its window; the GitHub cron fallback still runs (Decision 267)."
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 1
  metric_name         = "TriggeredRules"
  namespace           = "AWS/Events"
  period              = 10800
  statistic           = "Sum"
  threshold           = 1
  treat_missing_data  = "breaching"

  dimensions = {
    RuleName = aws_cloudwatch_event_rule.workflow_dispatch_main_canary.name
  }

  actions_enabled = local.workflow_dispatch_production
  alarm_actions   = [aws_sns_topic.alerts.arn]

  tags = {
    Name = "Workflow Dispatch Not Fired main-canary"
  }
}
