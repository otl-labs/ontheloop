# Isolated GitHub-settings root module (T2.12 / CD.20).
#
# WHY isolated: terraform/personal/ auto-applies from GitHub Actions via OIDC; the
# terraform_apply_guard.py is AWS-IAM-only and cannot inspect github_* resource diffs.
# A branch-protection change on that auto-apply path would apply ungated and could lock
# out the push-to-main flow the workflow depends on. This module is CI-applied only by
# .github/workflows/terraform-github-apply.yml behind one operator approval (Decision 248; see
# CLAUDE.md). NEVER add terraform/github/** to any other apply-workflow path.
#
# Apply: see terraform/github/CLAUDE.md; init with the region from config/aws-region (rec-4067):
# terraform -chdir=terraform/github init -backend-config="region=$(tr -d '[:space:]' < config/aws-region)".
# One credential lane, TF_VAR_github_token (ephemeral): an installation token minted from the apply
# App's key outside Terraform (rec-4355), the PR-time plan App's token, or the classic admin PAT as
# rollback until it is revoked. No app_auth block: provider 6.12.1 needs its three arguments in the
# config unless GITHUB_APP_* are set at validate time, so the key would have to enter Terraform.

terraform {
  required_version = ">= 1.10"
  required_providers {
    github = {
      source  = "integrations/github"
      version = "~> 6.0"
    }
  }
  backend "s3" {
    bucket       = "agent-platform-data-lake"
    key          = "tfstate/github/terraform.tfstate"
    encrypt      = true
    use_lockfile = true
  }
}

provider "github" {
  token = var.github_token
  owner = var.github_owner
}
