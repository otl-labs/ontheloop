# ---------------------------------------------------------------------------
# Trusted speculative-plan role (PLAN-trusted-plan-identity, rec-4636; the Decision that PR lands).
#
# The speculative plan runs main's terraform-plan-trusted.yml on pull_request_target under this role, so
# no identity an agent can obtain without review reads Terraform state or saved plans. Dedicated rather
# than a third planner trust statement: the planner's trust sits near the 2048-character trust limit.
#
# Trust: ONE statement, StringEquals on aud, the tf-trusted-plan Environment sub (main-only deployment
# policy, no reviewers, no admin bypass) and job_workflow_ref naming a trusted workflow file at
# refs/heads/main. No StringLike, no PR or branch sub.
#
# The CREATE routes to tf-gated-apply (Decision 144 cl.4). Later trust edits are admin split-applies
# (Decision 223 cl.4), including any rename or transfer pre-stage of job_workflow_ref values.
# ---------------------------------------------------------------------------

locals {
  trusted_plan_environment = "tf-trusted-plan"
  # The second entry is pre-staged for the GitHub-root plan's move to this role (rec-4638).
  trusted_plan_workflow_refs = [
    "otl-labs/ontheloop/.github/workflows/terraform-plan-trusted.yml@refs/heads/main",
    "otl-labs/ontheloop/.github/workflows/terraform-github-plan.yml@refs/heads/main",
  ]
}

resource "aws_iam_role" "github_ci_trusted_plan" {
  name                 = "agent-platform-github-ci-trusted-plan"
  description          = "GitHub Actions trusted speculative plan: pull_request_target on main's workflow, tf-trusted-plan Environment"
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
            "token.actions.githubusercontent.com:job_workflow_ref" = local.trusted_plan_workflow_refs
          }
        }
      }
    ]
  })
}

data "aws_iam_policy_document" "github_ci_trusted_plan" {
  source_policy_documents = [data.aws_iam_policy_document.ci_full_refresh_read.json]

  statement {
    # Upload plan.bin keyed by the PR head SHA for the apply-the-saved-plan merge path (Decision 77).
    # Write only: the merge-time fetch runs under the apply role.
    sid       = "TfplanPersonalWrite"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.data_lake.arn}/tfplan/personal/*"]
  }

  statement {
    # Hermetic provider init from the S3 mirror.
    sid       = "ProviderMirrorRead"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.data_lake.arn}/tf-provider-mirror/*"]
  }

  statement {
    # The Neon provider key and catalog DSN for the plan's refresh. No condition: the role has one
    # trusted subject.
    sid     = "PlanSecretValueRead"
    effect  = "Allow"
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      "arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:neon-api-key-*",
      "arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:ducklake-neon-catalog-dsn-*",
    ]
  }

  statement {
    # Decision 237 (trailer 2026-10-29): the SSM twin of PlanSecretValueRead, for the same single trusted subject.
    # secrets-to-ssm-retire deletes PlanSecretValueRead. Needed once a full refresh decrypts the DSN parameter and
    # once scripts/ci/neon_api_key.sh reads SSM.
    sid     = "PlanSecretParameterRead"
    effect  = "Allow"
    actions = ["ssm:GetParameter"]
    resources = [
      "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/secret/neon-api-key",
      "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/secret/ducklake-neon-catalog-dsn",
    ]
  }

  statement {
    # The GitHub-root plan reads that root's state: the ONE state object, no write (plans run -lock=false).
    sid       = "TfstateGithubRead"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.data_lake.arn}/tfstate/github/terraform.tfstate"]
  }
}

resource "aws_iam_role_policy" "github_ci_trusted_plan" {
  name   = "agent-platform-github-ci-trusted-plan"
  role   = aws_iam_role.github_ci_trusted_plan.id
  policy = data.aws_iam_policy_document.github_ci_trusted_plan.json

  lifecycle {
    precondition {
      # AWS excludes whitespace from the 10,240 B inline-policy limit (rec-2793): measure the minified form.
      condition     = length(jsonencode(jsondecode(data.aws_iam_policy_document.github_ci_trusted_plan.json))) <= 10240
      error_message = "github_ci_trusted_plan inline policy exceeds the 10,240 B IAM inline-policy limit (whitespace-stripped measure, rec-2793)."
    }
  }
}
