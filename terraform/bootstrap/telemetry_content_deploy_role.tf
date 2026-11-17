# Telemetry content deploy role (Decision 239 cl.5 as amended by Decision 270; rec-4696).
#
# The only identity that may change the content reader. Boundary-free by explicit exclusion (Decision 144 cl.3 as
# amended by Decision 270): the CI apply boundary's DenyTelemetryContentChange refuses every role it bounds, so this
# role must not carry it; its ceiling is this one policy, which only a CloudShell bootstrap apply changes (Decision 257
# cl.3's protected set). Trust is ONE statement: StringEquals on aud, the telemetry-content-deploy Environment sub
# (required reviewers, admin bypass off, main-only deployment policy; terraform/github/environments.tf) and
# job_workflow_ref naming the deploy workflow at refs/heads/main (Decision 238 cl.2, Decision 172). No StringLike, no
# ref or pull_request sub. Inert until plan 2 adds the workflow.
#
# Grants: code and configuration on the content function and reads of the layers it pins. No iam, PassRole,
# PublishVersion, concurrency, permission, invoke or S3 verb, and no statement names PlatformDev or PlatformAdmin
# (Decision 225 cl.1).
locals {
  telemetry_content_workflow_refs = [
    "otl-labs/ontheloop/.github/workflows/deploy-telemetry-content.yml@refs/heads/main",
  ]
}

resource "aws_iam_role" "telemetry_content_deploy" {
  count = local.telemetry_content_count

  name        = "telemetry-content-deploy"
  description = "Telemetry content reader deploy: the reviewer-gated telemetry-content-deploy Environment job on main only"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Federated = "arn:aws:iam::${var.account_id}:oidc-provider/token.actions.githubusercontent.com"
        }
        Action = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
            "token.actions.githubusercontent.com:sub" = [
              for repo in local.github_repos : "repo:${repo}:environment:telemetry-content-deploy"
            ]
            "token.actions.githubusercontent.com:job_workflow_ref" = local.telemetry_content_workflow_refs
          }
        }
      }
    ]
  })
}

locals {
  telemetry_content_deploy_policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DeployCode"
        Effect = "Allow"
        Action = [
          "lambda:GetFunction",
          "lambda:GetFunctionConfiguration",
          "lambda:UpdateFunctionCode",
          "lambda:UpdateFunctionConfiguration",
        ]
        Resource = ["arn:aws:lambda:${var.aws_region}:${var.account_id}:function:${local.ducklake_telemetry_content_function}"]
      },
      {
        Sid    = "LayerRead"
        Effect = "Allow"
        Action = ["lambda:GetLayerVersion"]
        Resource = [
          "arn:aws:lambda:${var.aws_region}:${var.account_id}:layer:ducklake-deps:*",
          "arn:aws:lambda:${var.aws_region}:${var.account_id}:layer:ducklake-extensions:*",
        ]
      },
    ]
  })
}

resource "aws_iam_role_policy" "telemetry_content_deploy" {
  count = local.telemetry_content_count

  name   = "TelemetryContentDeploy"
  role   = aws_iam_role.telemetry_content_deploy[0].id
  policy = local.telemetry_content_deploy_policy_json

  lifecycle {
    precondition {
      # AWS measures the whitespace-stripped document against the 10,240 B inline-policy limit (rec-2793).
      condition     = length(jsonencode(jsondecode(local.telemetry_content_deploy_policy_json))) <= 10240
      error_message = "Telemetry content deploy inline policy exceeds the 10,240 B IAM inline-policy limit (whitespace-stripped measure, rec-2793)."
    }
  }
}
