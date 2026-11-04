# Main-only provider, backend and terraform {} blocks plus the four variables they read (Decision 238 cl.3).
# The trusted plan (terraform-plan-trusted.yml) never takes this file from a PR, and no other file in this root may
# declare a terraform, provider or backend block (enforced by scripts/ci/terraform_hcl_screen.py).

terraform {
  # use_lockfile (native S3 state locking, no DynamoDB lock table) requires Terraform 1.10+.
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    # Third-party Neon provider for the DuckLake catalog (T2.16b / CD.34). Pinned to an exact
    # published version; checksums committed in .terraform.lock.hcl (supply-chain control). Terraform
    # allows only ONE required_providers block per module, so the Neon pin lives here rather than in
    # neon_ducklake_catalog.tf. Verify the version on the Terraform Registry before bumping.
    neon = {
      source  = "kislerdm/neon"
      version = "0.13.0"
    }
  }
  # S3 backend with native state locking (use_lockfile). The data-lake bucket was bootstrapped
  # under the prior local backend, so the chicken-and-egg that motivated "local" no longer holds.
  # Partial config: bucket/key/encrypt come from -backend-config=backend-sandbox.hcl, and the
  # region from AWS_REGION in CI or a -backend-config region from config/aws-region at hand-run
  # init (rec-4067); this block stays account-agnostic and a future backend-production.hcl is a
  # pure config addition.
  # One-time migration: terraform init -migrate-state -backend-config=backend-sandbox.hcl -backend-config="region=$(tr -d '[:space:]' < config/aws-region)".
  backend "s3" {
    use_lockfile = true
  }
}

provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile

  default_tags {
    tags = {
      Project   = "agent-platform"
      Account   = "personal"
      ManagedBy = "Terraform"
      Owner     = var.owner_email
    }
  }
}

provider "neon" {
  api_key = var.neon_api_key
}

variable "aws_region" {
  description = "AWS region for personal-account platform resources"
  type        = string
  default     = "eu-west-2"
}

variable "aws_profile" {
  description = "Provisioning SSO profile (PlatformAdmin role -- creates IAM + OIDC). Runtime code uses agent_platform (PlatformDev)."
  type        = string
  default     = "agent_platform_admin"
}

variable "owner_email" {
  description = "Owner email for resource tagging (GitHub no-reply identity)"
  type        = string
}

variable "neon_api_key" {
  description = "Neon control-plane API key for the neon provider. Supplied per run via TF_VAR_neon_api_key by trusted CI sessions and the admin break-glass loop. Never in tfvars, state, or a saved plan."
  type        = string
  ephemeral   = true
  sensitive   = true
  nullable    = false
}
