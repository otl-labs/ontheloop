# One IAM-authorised Regional REST API in front of the four DuckLake verb Lambdas (Decision 255).
#
# The session egress proxy signs only amazonaws.com hosts, so agents cannot reach the on.aws Function URLs; an
# execute-api host is signable. The API is declared here, not in terraform/personal, because every change to it is an
# operator admin apply (Decision 255 cl.1): no bounded identity may write API Gateway (DenyApiGatewayWrite in
# github_ci_apply_boundary.tf). Nothing calls it until the agent session user holds execute-api:Invoke (plan B).
#
# Every regional ARN comes from var.aws_region (Decision 202 cl.4) and every account from var.account_id; no API id,
# host, account or region literal is committed (Decision 101). SIT's functions do not exist when its bootstrap applies,
# so it declares no API here (map index, never a conditional operator, as account_names.tf).
locals {
  ducklake_api_routes = {
    sandbox = {
      "ops/reader"       = local.ducklake_reader_function
      "ops/writer"       = local.ducklake_writer_function
      "telemetry/reader" = local.ducklake_telemetry_reader_function
      "telemetry/writer" = local.ducklake_telemetry_writer_function
    }
    sit = {}
  }[var.environment]

  ducklake_api_count      = { sandbox = 1, sit = 0 }[var.environment]
  ducklake_api_stage      = "live"
  ducklake_api_timeout_ms = 120000

  ducklake_api_session_user_arn = "arn:aws:iam::${var.account_id}:user/agent-platform-session"

  ducklake_api_log_role_policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DuckLakeApiAccessLogWrite"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:DescribeLogStreams"
        ]
        Resource = [
          "arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:/aws/apigateway/agent-platform-ducklake-api",
          "arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:/aws/apigateway/agent-platform-ducklake-api:*"
        ]
      },
      {
        Sid      = "DuckLakeApiAccessLogGroups"
        Effect   = "Allow"
        Action   = "logs:DescribeLogGroups"
        Resource = "*"
      },
    ]
  })
}

resource "aws_api_gateway_rest_api" "agent_ducklake" {
  count = local.ducklake_api_count

  name        = "agent-platform-ducklake-api"
  description = "IAM-authorised front door for the four DuckLake verb Lambdas (Decision 255)"

  endpoint_configuration {
    types = ["REGIONAL"]
  }
}

# No Allow: a same-account caller is allowed by its identity policy unless a Deny matches.
resource "aws_api_gateway_rest_api_policy" "agent_ducklake" {
  count = local.ducklake_api_count

  rest_api_id = aws_api_gateway_rest_api.agent_ducklake[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyAllButAgentSession"
        Effect    = "Deny"
        Principal = "*"
        Action    = "execute-api:Invoke"
        Resource  = "${aws_api_gateway_rest_api.agent_ducklake[0].execution_arn}/*"
        Condition = {
          StringNotEquals = {
            "aws:PrincipalArn" = local.ducklake_api_session_user_arn
          }
        }
      },
    ]
  })
}

# 400 days, the trail bucket's: the access log is the API path's caller record. skip_destroy keeps it on a revert.
resource "aws_cloudwatch_log_group" "agent_ducklake_api_access" {
  count = local.ducklake_api_count

  name              = "/aws/apigateway/agent-platform-ducklake-api"
  retention_in_days = 400
  skip_destroy      = true
}

# Outside agent-platform-* so no CI IAM grant reaches it; a service role holding four logs actions gains nothing from
# Decision 144 cl.3's boundary. Whether API Gateway supplies aws:SourceAccount when it assumes the account CloudWatch
# role is unverified, so no condition (owed rec 19).
resource "aws_iam_role" "agent_ducklake_api_logs" {
  count = local.ducklake_api_count

  name        = "ducklake-api-access-logs"
  description = "API Gateway account CloudWatch role: writes the DuckLake API access log only (Decision 255)"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "apigateway.amazonaws.com" }
        Action    = "sts:AssumeRole"
      },
    ]
  })
}

resource "aws_iam_role_policy" "agent_ducklake_api_logs" {
  count = local.ducklake_api_count

  name   = "ducklake-api-access-logs"
  role   = aws_iam_role.agent_ducklake_api_logs[0].id
  policy = local.ducklake_api_log_role_policy_json

  lifecycle {
    precondition {
      condition     = length(jsonencode(jsondecode(local.ducklake_api_log_role_policy_json))) <= 10240
      error_message = "ducklake-api-access-logs inline policy exceeds the 10,240 B inline-policy limit."
    }
  }
}

# API Gateway needs the account-level CloudWatch role to write any CloudWatch log.
resource "aws_api_gateway_account" "agent_ducklake" {
  count = local.ducklake_api_count

  cloudwatch_role_arn = aws_iam_role.agent_ducklake_api_logs[0].arn

  depends_on = [aws_iam_role_policy.agent_ducklake_api_logs]
}

resource "aws_servicequotas_service_quota" "apigateway_integration_timeout" {
  count = local.ducklake_api_count

  service_code = "apigateway"
  quota_code   = "L-E5AE38E3"
  value        = 120000
}

resource "aws_api_gateway_resource" "agent_ducklake_domain" {
  for_each = toset([for route in keys(local.ducklake_api_routes) : split("/", route)[0]])

  rest_api_id = aws_api_gateway_rest_api.agent_ducklake[0].id
  parent_id   = aws_api_gateway_rest_api.agent_ducklake[0].root_resource_id
  path_part   = each.key
}

resource "aws_api_gateway_resource" "agent_ducklake_route" {
  for_each = local.ducklake_api_routes

  rest_api_id = aws_api_gateway_rest_api.agent_ducklake[0].id
  parent_id   = aws_api_gateway_resource.agent_ducklake_domain[split("/", each.key)[0]].id
  path_part   = split("/", each.key)[1]
}

resource "aws_api_gateway_method" "agent_ducklake_route" {
  for_each = local.ducklake_api_routes

  rest_api_id   = aws_api_gateway_rest_api.agent_ducklake[0].id
  resource_id   = aws_api_gateway_resource.agent_ducklake_route[each.key].id
  http_method   = "POST"
  authorization = "AWS_IAM"
}

# No credentials: API Gateway invokes through the function policy alone (the exact-route permission below).
resource "aws_api_gateway_integration" "agent_ducklake_route" {
  for_each = local.ducklake_api_routes

  rest_api_id             = aws_api_gateway_rest_api.agent_ducklake[0].id
  resource_id             = aws_api_gateway_resource.agent_ducklake_route[each.key].id
  http_method             = aws_api_gateway_method.agent_ducklake_route[each.key].http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = "arn:aws:apigateway:${var.aws_region}:lambda:path/2015-03-31/functions/arn:aws:lambda:${var.aws_region}:${var.account_id}:function:${each.value}/invocations"
  timeout_milliseconds    = local.ducklake_api_timeout_ms
}

resource "aws_lambda_permission" "agent_ducklake_route" {
  for_each = local.ducklake_api_routes

  statement_id  = "AgentDuckLakeApi"
  action        = "lambda:InvokeFunction"
  principal     = "apigateway.amazonaws.com"
  function_name = each.value
  source_arn    = "${aws_api_gateway_rest_api.agent_ducklake[0].execution_arn}/${local.ducklake_api_stage}/POST/${each.key}"
}

resource "aws_api_gateway_deployment" "agent_ducklake" {
  count = local.ducklake_api_count

  rest_api_id = aws_api_gateway_rest_api.agent_ducklake[0].id

  triggers = {
    redeployment = sha1(jsonencode([
      local.ducklake_api_routes,
      aws_api_gateway_rest_api_policy.agent_ducklake[0].policy,
      aws_api_gateway_method.agent_ducklake_route,
      aws_api_gateway_integration.agent_ducklake_route,
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }

  depends_on = [
    aws_api_gateway_integration.agent_ducklake_route,
    aws_api_gateway_rest_api_policy.agent_ducklake,
  ]
}

resource "aws_api_gateway_stage" "agent_ducklake" {
  count = local.ducklake_api_count

  rest_api_id   = aws_api_gateway_rest_api.agent_ducklake[0].id
  deployment_id = aws_api_gateway_deployment.agent_ducklake[0].id
  stage_name    = local.ducklake_api_stage

  # No request or response body and no header: the caller record names who called what, nothing they sent.
  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.agent_ducklake_api_access[0].arn
    format = jsonencode({
      requestId          = "$context.requestId"
      requestTime        = "$context.requestTime"
      caller             = "$context.identity.userArn"
      resourcePath       = "$context.resourcePath"
      httpMethod         = "$context.httpMethod"
      status             = "$context.status"
      errorType          = "$context.error.responseType"
      integrationStatus  = "$context.integrationStatus"
      integrationLatency = "$context.integrationLatency"
      integrationError   = "$context.integrationErrorMessage"
      responseLatency    = "$context.responseLatency"
      responseLength     = "$context.responseLength"
    })
  }

  depends_on = [aws_api_gateway_account.agent_ducklake]
}

# A runaway guard only.
resource "aws_api_gateway_method_settings" "agent_ducklake" {
  count = local.ducklake_api_count

  rest_api_id = aws_api_gateway_rest_api.agent_ducklake[0].id
  stage_name  = aws_api_gateway_stage.agent_ducklake[0].stage_name
  method_path = "*/*"

  settings {
    metrics_enabled        = false
    logging_level          = "OFF"
    data_trace_enabled     = false
    throttling_rate_limit  = 20
    throttling_burst_limit = 40
  }
}

# Read by plan B's clients; never under /secret/ (Decision 237).
resource "aws_ssm_parameter" "agent_ducklake_route_url" {
  for_each = local.ducklake_api_routes

  name  = "/agent-platform/ducklake/api_url/${each.key}"
  type  = "String"
  value = "${aws_api_gateway_stage.agent_ducklake[0].invoke_url}/${each.key}"
}
