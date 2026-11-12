# ---------------------------------------------------------------------------
# Verdict persona role (PLAN-persona-verdict-credential PR 3, T4.32 c1; Decision 263).
#
# The verdict persona jobs (main's persona-verdict.yml) run under this role: they read the two inference keys
# (DeepSeek, Anthropic) from their SSM parameters and write telemetry rows through the production telemetry
# writer's URL. Dedicated because no PR- or branch-trusted role may read a key.
#
# Trust: ONE statement, StringEquals on aud, the persona-verdict Environment sub and job_workflow_ref naming
# persona-verdict.yml at refs/heads/main. No StringLike, no PR or branch sub.
#
# The CREATE routes to tf-gated-apply (Decision 144 cl.4). Later trust edits are admin split-applies
# (Decision 223 cl.4, Decision 238 cl.6).
# ---------------------------------------------------------------------------

locals {
  persona_verdict_environment = "persona-verdict"
}

resource "aws_iam_role" "github_ci_persona_verdict" {
  name                 = "agent-platform-github-ci-persona-verdict"
  description          = "GitHub Actions verdict persona jobs: main's persona-verdict.yml in the persona-verdict Environment"
  permissions_boundary = "arn:aws:iam::${var.account_id}:policy/agent-platform-github-ci-apply-boundary"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Federated = aws_iam_openid_connect_provider.github_actions.arn
        }
        Action = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
            "token.actions.githubusercontent.com:sub" = flatten([
              for repo in local.github_repos : [
                "repo:${repo}:environment:${local.persona_verdict_environment}"
              ]
            ])
            "token.actions.githubusercontent.com:job_workflow_ref" = "otl-labs/ontheloop/.github/workflows/persona-verdict.yml@refs/heads/main"
          }
        }
      }
    ]
  })
}

data "aws_iam_policy_document" "github_ci_persona_verdict" {
  statement {
    # The two inference keys, by literal ARN (Decision 237 cl.2).
    sid     = "InferenceKeyParameterRead"
    effect  = "Allow"
    actions = ["ssm:GetParameter"]
    resources = [
      "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/secret/agent-platform-deepseek-api-key",
      "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/secret/agent-platform-anthropic-api-key",
    ]
  }

  statement {
    # The telemetry writer's URL comes from its parameter, so no GetFunctionUrlConfig.
    sid       = "TelemetryWriterUrlParameterRead"
    effect    = "Allow"
    actions   = ["ssm:GetParameter"]
    resources = ["arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/agent-platform/ducklake/telemetry_writer_url"]
  }

  statement {
    # URL-only invoke of the production telemetry writer (Decision 213); never the smoke twin.
    sid     = "TelemetryWriterInvoke"
    effect  = "Allow"
    actions = ["lambda:InvokeFunctionUrl"]
    resources = [
      "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:${local.ducklake_telemetry_writer_function}",
      "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:${local.ducklake_telemetry_writer_function}:*",
    ]
  }

  statement {
    sid     = "TelemetryWriterInvokeViaUrlOnly"
    effect  = "Allow"
    actions = ["lambda:InvokeFunction"]
    resources = [
      "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:${local.ducklake_telemetry_writer_function}",
      "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:${local.ducklake_telemetry_writer_function}:*",
    ]
    condition {
      test     = "Bool"
      variable = "lambda:InvokedViaFunctionUrl"
      values   = ["true"]
    }
  }
}

resource "aws_iam_role_policy" "github_ci_persona_verdict" {
  name   = "agent-platform-github-ci-persona-verdict"
  role   = aws_iam_role.github_ci_persona_verdict.id
  policy = data.aws_iam_policy_document.github_ci_persona_verdict.json

  lifecycle {
    precondition {
      # AWS excludes whitespace from the 10,240 B inline-policy limit (rec-2793): measure the minified form.
      condition     = length(jsonencode(jsondecode(data.aws_iam_policy_document.github_ci_persona_verdict.json))) <= 10240
      error_message = "github_ci_persona_verdict inline policy exceeds the 10,240 B IAM inline-policy limit (whitespace-stripped measure, rec-2793)."
    }
  }
}
