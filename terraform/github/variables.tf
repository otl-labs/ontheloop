variable "github_token" {
  description = "GitHub admin PAT. Supplied at apply time from Secrets Manager -- never committed. Required scopes: repo, admin:repo_hook, read:org."
  type        = string
  sensitive   = true
}

variable "github_owner" {
  description = "GitHub organisation or user that owns the repository."
  type        = string
  default     = "benjamin-blake"
}

variable "repository_name" {
  description = "GitHub repository name managed by this module."
  type        = string
  default     = "theseus"
}

variable "main_protection_ruleset_id" {
  description = "Numeric id of the main-protection ruleset, used only by its import block. Re-read it from the API after a transfer: rulesets are not documented to carry over."
  type        = number
  default     = 17117072
}

variable "tf_trusted_plan_main_policy_id" {
  description = "Numeric id of the tf-trusted-plan main deployment branch policy, used only by its import block. Re-read it from the API after a transfer."
  type        = number
  default     = 64562499
}

variable "admin_bypass_actor_id" {
  description = "GitHub actor ID granted ruleset bypass. Use 5 for the built-in Repository Admin role (anyone with admin permission). Supplied at apply time."
  type        = number
  default     = 5
}

variable "gated_apply_reviewer_user_ids" {
  description = "List of GitHub numeric user IDs required to approve the tf-gated-apply Environment gate before the gated-apply job may execute. Supplied at apply time -- never committed with a literal default. Resolve before applying: GET /user with the reviewer's PAT (terraform/github/CLAUDE.md runbook step 2) or the GitHub MCP get_me tool."
  type        = list(number)

  validation {
    condition     = length(var.gated_apply_reviewer_user_ids) > 0
    error_message = "gated_apply_reviewer_user_ids must contain at least one GitHub numeric user id; an empty list silently disables the tf-gated-apply required-reviewer gate."
  }
}
