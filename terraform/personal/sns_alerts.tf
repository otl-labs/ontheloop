# Shared personal-account SNS alerts topic (T2.18 FP-B / Decision 39).
#
# Decision 39: SNS is the canonical alarm/failure-notification primitive. This module
# introduces the single shared personal-account SNS topic; both the DuckLake maintenance
# circuit-breaker alarm and the catalog-DR freshness alarm target this topic.
#
# Email subscription endpoint supplied via gitignored terraform.personal.tfvars
# (alerts_email = "..."). NOT hardcoded -- no email address is committed to source.
# The subscription is created in PENDING state; the recipient must click the confirmation
# link in the initial subscription email before alarms can page (VP9 manual step).
#
# ---------------------------------------------------------------------------
# APPLY POSTURE (Decision 35 + 77): HUMAN-GATED via agent_platform_admin.
# ---------------------------------------------------------------------------
# aws_sns_topic and aws_sns_topic_subscription are NOT IAM-sensitive resources, but they
# are created together with the DR Lambda (IAM role + policy), so they ride the same
# human-gated admin apply for the T2.18 FP-B apply pass.

variable "alerts_email" {
  description = "Email address for the shared personal-account SNS alerts subscription. Supplied via gitignored terraform.personal.tfvars."
  type        = string
  # No default: this must be explicitly set in terraform.personal.tfvars.
}

resource "aws_sns_topic" "alerts" {
  name = "agent-platform-alerts"

  tags = {
    Name    = "Agent Platform Alerts"
    Purpose = "Shared personal-account alarm page-out topic - T2.18 FP-B / Decision 39"
  }
}

# CI cannot unsubscribe (github_ci_apply_policy.tf): forget the founder's subscription and let the operator
# delete it in the console once the company successor pages (PLAN-terraform-company-emails-forget).
removed {
  from = aws_sns_topic_subscription.alerts_email

  lifecycle {
    destroy = false
  }
}

# Transitional (PLAN-terraform-company-emails): the company successor beside the predecessor. CI may
# subscribe but never unsubscribe (github_ci_apply_policy.tf), so PLAN-terraform-company-emails-forget
# forgets the predecessor for the operator to delete, and PLAN-terraform-company-emails-closing moves
# this subscription back to aws_sns_topic_subscription.alerts_email.
variable "company_alerts_email" {
  description = "Company alerts role alias for the successor alerts subscription, from the tfvars parameter (never committed)."
  type        = string
}

resource "aws_sns_topic_subscription" "company_alerts_email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.company_alerts_email
}

output "alerts_topic_arn" {
  description = "Shared personal-account SNS alerts topic ARN (wired to DuckLake alarms)."
  value       = aws_sns_topic.alerts.arn
}
