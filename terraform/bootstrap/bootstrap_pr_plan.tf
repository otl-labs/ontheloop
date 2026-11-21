# bootstrap-pr-plan: the read-only identity of terraform-bootstrap-plan.yml (Decision 279 cl.2, PLAN-bootstrap-trusted-pr-plan).
#
# One trust statement, StringEquals only: the tf-trusted-plan Environment subjects and that workflow's job_workflow_ref at
# main. No permissions boundary, on purpose (Decision 270 pattern; see bootstrap_ci_apply.tf): its ceiling is the explicit
# Deny policy below. It shares bootstrap-plan-reads with the two CI roles so the refresh reads cannot drift, and adds only
# the Access Analyzer checks and the hashicorp/aws provider mirror prefix. It never holds a write.
# Changes here apply only by the operator's CloudShell apply (deploy-paths.yaml admin_out_of_band).

locals {
  bootstrap_pr_plan_workflow_refs = ["otl-labs/ontheloop/.github/workflows/terraform-bootstrap-plan.yml@refs/heads/main"]
  bootstrap_pr_plan_mirror_prefix = "tf-provider-mirror/registry.terraform.io/hashicorp/aws/"

  bootstrap_pr_plan_reads_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AccessAnalyzerPolicyChecks"
        Effect = "Allow"
        Action = [
          "access-analyzer:ValidatePolicy",
          "access-analyzer:CheckAccessNotGranted",
          "access-analyzer:CheckNoNewAccess",
          "access-analyzer:CheckNoPublicAccess",
        ]
        Resource = ["*"]
      },
      {
        Sid      = "ProviderMirrorRead"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = ["${local.data_lake_bucket_arn}/${local.bootstrap_pr_plan_mirror_prefix}*"]
      },
      {
        Sid      = "ProviderMirrorList"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [local.data_lake_bucket_arn]
        Condition = {
          StringLike = {
            "s3:prefix" = ["${local.bootstrap_pr_plan_mirror_prefix}*"]
          }
        }
      },
    ]
  })

  bootstrap_pr_plan_guard_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyAllButReads"
        Effect    = "Deny"
        NotAction = concat(local.bootstrap_plan_read_actions, local.bootstrap_preview_analyzer_actions, ["sts:GetCallerIdentity"])
        Resource  = ["*"]
        Condition = {
          BoolIfExists = {
            "aws:ViaAWSService" = "false"
          }
        }
      },
      {
        Sid    = "DenyObjectReadOutsideStateAndMirror"
        Effect = "Deny"
        Action = ["s3:GetObject*"]
        NotResource = [
          "${local.bootstrap_state_bucket_arn}/terraform.tfstate",
          "${local.data_lake_bucket_arn}/${local.bootstrap_pr_plan_mirror_prefix}*",
        ]
      },
      {
        Sid    = "DenyParameterReadOutsideTfvars"
        Effect = "Deny"
        Action = ["ssm:GetParameter*"]
        NotResource = [
          "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/secret/agent-platform-terraform-personal-tfvars",
          "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/agent-platform/ducklake/api_url/*",
        ]
      },
      {
        Sid      = "DenyAccountIAMDump"
        Effect   = "Deny"
        Action   = ["iam:GetAccountAuthorizationDetails", "iam:GetCredentialReport", "iam:GenerateCredentialReport"]
        Resource = ["*"]
      },
    ]
  })
}

resource "aws_iam_role" "bootstrap_pr_plan" {
  name        = "bootstrap-pr-plan"
  description = "terraform-bootstrap-plan.yml PR-time plan job: tf-trusted-plan Environment at main (PLAN-bootstrap-trusted-pr-plan)"
  # No permissions_boundary: see the header.

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
            "token.actions.githubusercontent.com:sub" = flatten([
              for repo in local.github_repos : ["repo:${repo}:environment:${local.bootstrap_ci_preview_env}"]
            ])
            "token.actions.githubusercontent.com:job_workflow_ref" = local.bootstrap_pr_plan_workflow_refs
          }
        }
      }
    ]
  })

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_iam_policy" "bootstrap_pr_plan_reads" {
  name        = "bootstrap-pr-plan-reads"
  description = "Access Analyzer checks and provider mirror reads for bootstrap-pr-plan (PLAN-bootstrap-trusted-pr-plan)"
  policy      = local.bootstrap_pr_plan_reads_json

  lifecycle {
    precondition {
      condition     = length(jsonencode(jsondecode(local.bootstrap_pr_plan_reads_json))) <= 6144
      error_message = "bootstrap-pr-plan-reads exceeds the 6,144 B managed-policy limit (whitespace-stripped measure)."
    }
  }
}

resource "aws_iam_policy" "bootstrap_pr_plan_guard" {
  name        = "bootstrap-pr-plan-guard"
  description = "Deny everything but plan-time reads for bootstrap-pr-plan (PLAN-bootstrap-trusted-pr-plan)"
  policy      = local.bootstrap_pr_plan_guard_json

  lifecycle {
    precondition {
      condition     = length(jsonencode(jsondecode(local.bootstrap_pr_plan_guard_json))) <= 6144
      error_message = "bootstrap-pr-plan-guard exceeds the 6,144 B managed-policy limit (whitespace-stripped measure)."
    }
  }
}

resource "aws_iam_role_policy_attachment" "bootstrap_pr_plan_shared_reads" {
  role       = aws_iam_role.bootstrap_pr_plan.name
  policy_arn = aws_iam_policy.bootstrap_plan_reads.arn
}

resource "aws_iam_role_policy_attachment" "bootstrap_pr_plan_reads" {
  role       = aws_iam_role.bootstrap_pr_plan.name
  policy_arn = aws_iam_policy.bootstrap_pr_plan_reads.arn
}

resource "aws_iam_role_policy_attachment" "bootstrap_pr_plan_guard" {
  role       = aws_iam_role.bootstrap_pr_plan.name
  policy_arn = aws_iam_policy.bootstrap_pr_plan_guard.arn
}
