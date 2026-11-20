# Organization root: the company AWS Organization's foundation (rec-5026, Decision 252 cl.7).
#
# Admin-only and operator-applied from AWS CloudShell in the management account, signed in as the
# console-only IAM user org-admin-operator and switched to role OrganizationAdmin (Decision 256 cl.3).
# No agent environment, CI role or key holds a management-account identity. See CLAUDE.md here.

terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  backend "s3" {
    # Region comes from an init-time -backend-config region (Decision 202 cl.4).
    bucket       = "agent-platform-organization-tfstate"
    key          = "terraform.tfstate"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = "agent-platform"
      ManagedBy = "terraform-organization"
      Owner     = var.alerts_email
    }
  }
}

data "aws_caller_identity" "current" {}
