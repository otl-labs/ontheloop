# Baseline SCP, attached to the sit OU so an account created later starts under it (rec-5026).
# local.prod_baseline_stages is empty here; the PROD baseline plan (rec-5066) creates baseline_prod
# from it one key at a time. protect_cloudtrail carries no exemption: the sit OU holds no account and
# SIT has no PlatformAdmin. A child plan that needs one adds a single ArnNotLike on that account's own
# role ARN, never an AND of two condition keys.
locals {
  # AWS's published region-deny NotAction list: services with no regional endpoint.
  global_actions = [
    "a4b:*",
    "access-analyzer:*",
    "account:*",
    "acm:*",
    "aws-marketplace-management:*",
    "aws-marketplace:*",
    "aws-portal:*",
    "billing:*",
    "budgets:*",
    "ce:*",
    "chime:*",
    "cloudfront:*",
    "config:*",
    "cur:*",
    "directconnect:*",
    "ec2:DescribeRegions",
    "ec2:DescribeTransitGateways",
    "ec2:DescribeVpnGateways",
    "fms:*",
    "globalaccelerator:*",
    "health:*",
    "iam:*",
    "importexport:*",
    "mobileanalytics:*",
    "networkmanager:*",
    "organizations:*",
    "payments:*",
    "pricing:*",
    "route53-recovery-cluster:*",
    "route53-recovery-control-config:*",
    "route53-recovery-readiness:*",
    "route53:*",
    "route53domains:*",
    "shield:*",
    "sts:*",
    "support:*",
    "supportapp:*",
    "supportplans:*",
    "sustainability:*",
    "tax:*",
    "trustedadvisor:*",
    "waf-regional:*",
    "waf:*",
    "wafv2:*",
    "wellarchitected:*",
  ]

  baseline_statements = {
    deny_leave_organization = {
      Sid      = "DenyLeaveOrganization"
      Effect   = "Deny"
      Action   = ["organizations:LeaveOrganization"]
      Resource = ["*"]
    }
    deny_root_user = {
      Sid      = "DenyRootUser"
      Effect   = "Deny"
      Action   = ["*"]
      Resource = ["*"]
      Condition = {
        ArnLike = { "aws:PrincipalArn" = ["arn:aws:iam::*:root"] }
        Null    = { "aws:AssumedRoot" = "true" }
      }
    }
    protect_cloudtrail = {
      Sid    = "ProtectCloudTrail"
      Effect = "Deny"
      Action = [
        "cloudtrail:StopLogging",
        "cloudtrail:DeleteTrail",
        "cloudtrail:UpdateTrail",
        "cloudtrail:PutEventSelectors",
        "cloudtrail:PutInsightSelectors",
        "cloudtrail:CreateTrail",
        "logs:DeleteLogGroup",
        "logs:DeleteMetricFilter",
        "logs:PutRetentionPolicy",
      ]
      Resource = [
        "arn:aws:cloudtrail:*:*:trail/*",
        "arn:aws:logs:*:*:log-group:platform-security-*",
      ]
    }
    deny_other_regions = {
      Sid       = "DenyOtherRegions"
      Effect    = "Deny"
      NotAction = local.global_actions
      Resource  = ["*"]
      Condition = {
        StringNotEquals = { "aws:RequestedRegion" = [var.aws_region] }
      }
    }
  }

  prod_baseline_stages = []

  baseline_policy_json = jsonencode({
    Version   = "2012-10-17"
    Statement = values(local.baseline_statements)
  })
}

resource "aws_organizations_policy" "baseline" {
  name    = "baseline"
  type    = "SERVICE_CONTROL_POLICY"
  content = local.baseline_policy_json

  lifecycle {
    precondition {
      condition     = length(local.baseline_policy_json) <= 5120
      error_message = "The baseline SCP exceeds the 5,120 character SCP limit."
    }
  }
}

resource "aws_organizations_policy_attachment" "baseline_sit" {
  policy_id = aws_organizations_policy.baseline.id
  target_id = aws_organizations_organizational_unit.sit.id
}
