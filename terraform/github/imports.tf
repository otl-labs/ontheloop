# The state of every resource in this root is keyed by repository name. These blocks are inert
# while an address is in state; after `terraform state rm` they re-key it under the current
# var.repository_name with GitHub reads only (rec-3136). Kept so a rename or transfer re-keys by
# changing the variable defaults.
import {
  to = github_repository.this
  id = var.repository_name
}

import {
  to = github_repository_ruleset.main_protection
  id = "${var.repository_name}:${var.main_protection_ruleset_id}"
}

import {
  to = github_actions_repository_permissions.this
  id = var.repository_name
}

import {
  to = github_workflow_repository_permissions.this
  id = var.repository_name
}

import {
  to = github_repository_environment.tf_gated_apply
  id = "${var.repository_name}:tf-gated-apply"
}

import {
  to = github_repository_environment.tf_trusted_plan
  id = "${var.repository_name}:tf-trusted-plan"
}

import {
  to = github_repository_environment_deployment_policy.tf_trusted_plan_main
  id = "${var.repository_name}:tf-trusted-plan:${var.tf_trusted_plan_main_policy_id}"
}

import {
  to = github_repository_environment.deployment_approval_probe
  id = "${var.repository_name}:deployment-approval-probe"
}
