# ---------------------------------------------------------------------------
# Verdict App key role (PLAN-persona-verdict-app-grant PR 3, T4.32 egress E4; Decision 273).
#
# persona-verdict.yml's post job reads the verdict GitHub App's private key under this role. Dedicated because no
# model-running job may reach the key (the operator's 2026-11-17 ruling); the persona-verdict role is unchanged
# (Decision 263 cl.2).
#
# Trust: ONE statement, StringEquals on aud, the persona-verdict-app Environment sub and job_workflow_ref naming
# persona-verdict.yml at refs/heads/main. The CI apply boundary's DenyVerdictAppKeyRead names this role as the only
# carrier that may read the key (terraform/bootstrap/github_ci_apply_boundary.tf).
#
# The CREATE routes to tf-gated-apply (Decision 144 cl.4). Later trust edits are admin split-applies
# (Decision 223 cl.4, Decision 238 cl.6).
# ---------------------------------------------------------------------------

locals {
  persona_verdict_app_environment = "persona-verdict-app"
}

resource "aws_iam_role" "github_ci_persona_verdict_app" {
  name                 = "agent-platform-github-ci-persona-verdict-app"
  description          = "GitHub Actions verdict App key reader: main's persona-verdict.yml post job in the persona-verdict-app Environment"
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
                "repo:${repo}:environment:${local.persona_verdict_app_environment}"
              ]
            ])
            "token.actions.githubusercontent.com:job_workflow_ref" = "otl-labs/ontheloop/.github/workflows/persona-verdict.yml@refs/heads/main"
          }
        }
      }
    ]
  })
}

data "aws_iam_policy_document" "github_ci_persona_verdict_app" {
  statement {
    # The verdict App's private key, by literal ARN (Decision 237 cl.2).
    sid       = "VerdictAppKeyParameterRead"
    effect    = "Allow"
    actions   = ["ssm:GetParameter"]
    resources = ["arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/secret/agent-platform-persona-verdict-app-key"]
  }
}

resource "aws_iam_role_policy" "github_ci_persona_verdict_app" {
  name   = "agent-platform-github-ci-persona-verdict-app"
  role   = aws_iam_role.github_ci_persona_verdict_app.id
  policy = data.aws_iam_policy_document.github_ci_persona_verdict_app.json

  lifecycle {
    precondition {
      # AWS excludes whitespace from the 10,240 B inline-policy limit (rec-2793): measure the minified form.
      condition     = length(jsonencode(jsondecode(data.aws_iam_policy_document.github_ci_persona_verdict_app.json))) <= 10240
      error_message = "github_ci_persona_verdict_app inline policy exceeds the 10,240 B IAM inline-policy limit (whitespace-stripped measure, rec-2793)."
    }
  }
}
