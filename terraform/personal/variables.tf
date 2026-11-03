# Personal module variables. account_id has NO default by design: the personal account ID must
# never be a committed literal (PLAN-public-migration Step 11b). It is supplied at apply time from
# the gitignored terraform.personal.tfvars.

variable "account_id" {
  description = "Personal AWS account ID. Supplied via gitignored terraform.personal.tfvars -- never committed."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "account_id must be a 12-digit AWS account ID."
  }
}

variable "platform_dev_external_id" {
  description = "ExternalId for the PlatformDev AssumeRole trust. Supplied via gitignored terraform.personal.tfvars -- never a committed literal."
  type        = string
  sensitive   = true
}

variable "platform_admin_external_id" {
  description = "ExternalId for the PlatformAdmin AssumeRole trust. Supplied via gitignored terraform.personal.tfvars -- never a committed literal."
  type        = string
  sensitive   = true
}

variable "agent_service_account_user_name" {
  description = "IAM user (the agent_static static-key source profile) permitted to assume PlatformDev. Verify against the live role's trust policy via `terraform plan` before apply."
  type        = string
  default     = "agent-service-account"
}

# ---------------------------------------------------------------------------
# DuckLake catalog -- Neon serverless Postgres backend (T2.16b / CD.34, pending).
#
# The Neon provider API key is the ephemeral variable neon_api_key (providers.tf).
#
# There is no neon_org_id: this is a personal Neon account (no organization).
#
# There is no neon_catalog_allowed_ips: Neon IP-Allow is a Scale-plan feature (unavailable on the
# free tier this migration targets) and egress here is dynamic (CC-web + GitHub-hosted runners +
# Lambda), so no static allow-list is maintainable. The free-tier posture rests on compensating
# controls -- enforced TLS (sslmode=require) + a scoped non-owner neon_role + the DSN in Secrets
# Manager -- and the Neon-aware apply guard fail-closes on any neon_* update/replace/delete.
# ---------------------------------------------------------------------------

variable "neon_region_id" {
  description = "Neon region_id for the DuckLake catalog project. Default aws-eu-west-2 (Neon AWS Europe/London) co-locates with the data lake in the project region. Verified against Neon's supported-regions list at implementation time."
  type        = string
  default     = "aws-eu-west-2"
}

variable "neon_org_id" {
  description = "Neon organization ID owning the DuckLake catalog project. Neon's org-based model requires org_id on project creation (it is an identifier, not a secret -- it appears in console URLs). Default is the personal-account default organization."
  type        = string
  default     = "org-winter-night-78488109"
}
