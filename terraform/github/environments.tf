# GitHub Environment: tf-gated-apply (CD.35 Wave 3 / T2.22 / Decision 92).
#
# This Environment gates the post-merge apply job for the guard fail-closed set
# (IAM/trust/destroy diffs that exit 2 from scripts/terraform_apply_guard.py). A job
# declaring `environment: tf-gated-apply` does NOT start until the required reviewer
# (benjamin-blake) approves in GitHub Actions -- this is the authorization boundary for
# the fail-closed set within github_ci_apply's existing IAM scope.
#
# Key constraints:
#   - prevent_self_review = false: sole-developer repo; the only reviewer must be able to
#     approve (prevent_self_review=true would wedge the pipeline permanently).
#   - deployment_branch_policy: protected_branches = true; custom_branch_policies = false. With a
#     ruleset-only main (no classic protection rule) this may let any branch deploy (see the
#     tf_trusted_plan note below and rec-4651).
#   - Reviewer user IDs are supplied at apply time via var.gated_apply_reviewer_user_ids
#     (mirrors the admin_bypass_actor_id convention); they are NOT committed with a literal
#     default. Resolve before applying: `gh api /user --jq .id` or GitHub MCP get_me.
#
# Authorization boundary: the Environment gates the post-merge apply JOB -- it is NOT a PR
# required status check (that would wedge autonomous fix-merges, Decision 83). The gated
# job assumes the existing agent-platform-github-ci-apply role. Decision 94 made that trust list
# both ref:refs/heads/main and environment:tf-gated-apply, so the Environment gates the job but
# is not the role boundary: any ungated main job can also assume the role.
#
# Broader IAM changes beyond github_ci_apply's current scope remain admin-gated until T2.23.
#
# NEVER add this resource to any auto-apply workflow path (README.md / Decision 77):
# terraform/github/ is human-gated LOCAL apply only; the guard cannot inspect github_* diffs.

resource "github_repository_environment" "tf_gated_apply" {
  repository          = github_repository.this.name
  environment         = "tf-gated-apply"
  prevent_self_review = false

  reviewers {
    users = var.gated_apply_reviewer_user_ids
  }

  deployment_branch_policy {
    protected_branches     = true
    custom_branch_policies = false
  }

  # A destroy of this Environment FAILS OPEN, which is why it is blocked outright: GitHub
  # silently re-creates a protection-rule-free environment the first time a job references
  # `environment: tf-gated-apply`, while github_ci_apply's OIDC trust still matches the
  # environment: sub form -- so every gated apply would keep authenticating and start running
  # with NO required reviewer. The gate would appear intact and enforce nothing.
  # prevent_destroy is a lifecycle meta-argument, so it produces no plan diff of its own.
  lifecycle {
    prevent_destroy = true
  }
}

# GitHub Environment: tf-trusted-plan (Decision for PLAN-trusted-plan-identity, rec-4636).
#
# No reviewer gates this Environment. Its safety is the main-only deployment policy below, the
# trusted role's job_workflow_ref binding in terraform/personal, and the repo check
# validate_trusted_plan_environment. Never "protected branches only": GitHub defines that by classic
# branch protection rules, this repo protects main by a ruleset, and with no classic rule every branch
# can deploy. The explicit branch_pattern policy is matched against GITHUB_REF (refs/heads/main under
# pull_request_target). Destroying this Environment fails open for the same reason as tf_gated_apply.
resource "github_repository_environment" "tf_trusted_plan" {
  repository        = github_repository.this.name
  environment       = "tf-trusted-plan"
  can_admins_bypass = false

  deployment_branch_policy {
    protected_branches     = false
    custom_branch_policies = true
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "github_repository_environment_deployment_policy" "tf_trusted_plan_main" {
  repository     = github_repository.this.name
  environment    = github_repository_environment.tf_trusted_plan.environment
  branch_pattern = "main"
}

# GitHub Environment: deployment-approval-probe (rec-4695).
#
# Inert probe target, created in the GitHub UI and imported (later terraform/github PR plans show it
# as an import until the operator next applies the root). Mirrors tf_gated_apply so the probe result
# transfers to the control in use; the one difference is can_admins_bypass = false (the operator's
# UI setting). It is never referenced by any OIDC trust and never by a job holding id-token or
# secrets. No prevent_destroy: it is not a control.
resource "github_repository_environment" "deployment_approval_probe" {
  repository          = github_repository.this.name
  environment         = "deployment-approval-probe"
  prevent_self_review = false
  can_admins_bypass   = false

  reviewers {
    users = var.gated_apply_reviewer_user_ids
  }

  deployment_branch_policy {
    protected_branches     = true
    custom_branch_policies = false
  }
}
