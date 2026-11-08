# ---------------------------------------------------------------------------
# Trusted GitHub-root plan role (PLAN-trusted-plan-github-root PR 3, rec-4638; Decision 238).
#
# The terraform/github PR-time plan runs main's terraform-github-plan.yml on pull_request_target under this
# role. Separate from agent-platform-github-ci-trusted-plan so the GitHub-root plan holds no personal-root
# state, plan-upload or secret grants: this role reads one state object and the provider mirror, nothing else.
#
# Trust: ONE statement, StringEquals on aud, the tf-trusted-plan Environment sub and job_workflow_ref naming
# the one workflow file at refs/heads/main. No StringLike, no PR or branch sub.
#
# The CREATE routes to tf-gated-apply (Decision 144 cl.4). Later trust edits are admin split-applies
# (Decision 223 cl.4).
# ---------------------------------------------------------------------------

resource "aws_iam_role" "github_ci_trusted_github_plan" {
  name                 = "agent-platform-github-ci-trusted-github-plan"
  description          = "GitHub Actions trusted terraform/github plan: pull_request_target on main's workflow, tf-trusted-plan Environment"
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
                "repo:${repo}:environment:${local.trusted_plan_environment}"
              ]
            ])
            "token.actions.githubusercontent.com:job_workflow_ref" = "otl-labs/ontheloop/.github/workflows/terraform-github-plan.yml@refs/heads/main"
          }
        }
      }
    ]
  })
}

data "aws_iam_policy_document" "github_ci_trusted_github_plan" {
  statement {
    # The GitHub-root plan reads that root's state: the ONE state object, no write (plans run -lock=false).
    sid       = "TfstateGithubRead"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.data_lake.arn}/tfstate/github/terraform.tfstate"]
  }

  statement {
    # Hermetic provider init from the S3 mirror.
    sid       = "ProviderMirrorRead"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.data_lake.arn}/tf-provider-mirror/*"]
  }

  statement {
    # The mirror listing the hermetic init performs, scoped to the mirror prefix.
    sid       = "BucketListScoped"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.data_lake.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["tf-provider-mirror/*"]
    }
  }
}

resource "aws_iam_role_policy" "github_ci_trusted_github_plan" {
  name   = "agent-platform-github-ci-trusted-github-plan"
  role   = aws_iam_role.github_ci_trusted_github_plan.id
  policy = data.aws_iam_policy_document.github_ci_trusted_github_plan.json

  lifecycle {
    precondition {
      # AWS excludes whitespace from the 10,240 B inline-policy limit (rec-2793): measure the minified form.
      condition     = length(jsonencode(jsondecode(data.aws_iam_policy_document.github_ci_trusted_github_plan.json))) <= 10240
      error_message = "github_ci_trusted_github_plan inline policy exceeds the 10,240 B IAM inline-policy limit (whitespace-stripped measure, rec-2793)."
    }
  }
}
