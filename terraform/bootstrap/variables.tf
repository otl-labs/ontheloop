variable "account_id" {
  description = "AWS account ID (12 digits). Never committed; pass via -var or a gitignored tfvars file."
  type        = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "account_id must be exactly 12 digits."
  }
}

variable "aws_region" {
  description = "AWS region."
  type        = string
  default     = "eu-west-2"
}

variable "aws_profile" {
  description = "AWS CLI profile for the bootstrap apply. Must have iam:* (PlatformAdmin)."
  type        = string
  default     = "agent_platform_admin"
}

variable "owner_email" {
  description = "Owner email for resource tags."
  type        = string
}

variable "agent_service_account_user_name" {
  description = "IAM user (the agent_static static-key source profile) permitted to assume PlatformDev and PlatformAdmin. Verify against the live roles' trust policies via `terraform plan` before apply."
  type        = string
  default     = "agent-service-account"
}

variable "platform_dev_external_id" {
  description = "ExternalId for the PlatformDev AssumeRole trust. Supplied via gitignored terraform.bootstrap.tfvars -- never a committed literal."
  type        = string
  sensitive   = true
}

variable "platform_admin_external_id" {
  description = "ExternalId for the PlatformAdmin AssumeRole trust. Supplied via gitignored terraform.bootstrap.tfvars -- never a committed literal."
  type        = string
  sensitive   = true
}
