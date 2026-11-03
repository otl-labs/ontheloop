# Bootstrap root: CI/CD apply role own IAM + authority budget (CD.35 Wave 4 / T2.23).
#
# WHY isolated: terraform/personal auto-applies from GitHub Actions via github_ci_apply. If that
# role's own IAM lived in personal, the pipeline could grant itself new privileges through the
# pipeline it gates (self-grant cycle). Moving the apply role + its permissions boundary to this
# admin-applied root breaks the cycle: github_ci_apply can no longer write its own policy.
#
# NEVER auto-apply: this root must only be applied manually under agent_platform_admin (PlatformAdmin,
# iam:*). Never add terraform/bootstrap/** to any apply-workflow path.
# See terraform/bootstrap/CLAUDE.md for the provisioning and ongoing apply runbook.

terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  backend "s3" {
    # Region comes from AWS_REGION (CI sign-in) or an init-time -backend-config region (rec-4067).
    bucket       = "agent-platform-bootstrap-tfstate"
    key          = "terraform.tfstate"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile

  default_tags {
    tags = {
      Project   = "agent-platform"
      ManagedBy = "terraform-bootstrap"
      Owner     = var.owner_email
    }
  }
}
