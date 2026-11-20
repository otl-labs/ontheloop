variable "aws_region" {
  description = "The one project region (config/aws-region)."
  type        = string
  default     = "eu-west-2"
}

variable "alerts_email" {
  description = "Company alerts address; read from the tfvars SecureString the CloudShell blocks fetch."
  type        = string
  sensitive   = true
}
