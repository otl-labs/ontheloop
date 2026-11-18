# The management account's own trail, detector and alerts (rec-5026, Decision 202 as read per account).
#
# Not an organisation trail: one multi-region trail homed in var.aws_region with global service events,
# delivering to its own bucket and one CloudWatch Logs group. Five metric filters and alarms, all to
# one SNS topic with an e-mail subscription. Alerts are never filtered by actor except where the filter
# is about one identity. Every pattern is a plain string literal with no interpolation (an unknown
# value would make step 5's live read unknown, and an account id would never match the fixture), at
# most 1,024 characters. Alarm-action stripping stays an uncompensated residual until rec-5059.
locals {
  org_security_account_id = data.aws_caller_identity.current.account_id
  org_security_trail_arn  = "arn:aws:cloudtrail:${var.aws_region}:${local.org_security_account_id}:trail/agent-platform-org-security-trail"
  org_security_log_group  = "agent-platform-org-security-cloudtrail"
  org_security_topic_arn  = "arn:aws:sns:${var.aws_region}:${local.org_security_account_id}:agent-platform-org-security-alerts"

  org_security_filter_patterns = {
    "agent-platform-org-security-admin-role-assumed" = "{ (($.eventSource = \"sts.amazonaws.com\") && ($.eventName = \"AssumeRole\") && ($.requestParameters.roleArn = \"*:role/OrganizationAdmin\")) || (($.eventSource = \"signin.amazonaws.com\") && ($.eventName = \"SwitchRole\") && ($.additionalEventData.SwitchTo = \"*:role/OrganizationAdmin\")) }"

    "agent-platform-org-security-signin-anomaly" = "{ (($.eventSource = \"signin.amazonaws.com\") && ($.eventName = \"ConsoleLogin\") && (($.responseElements.ConsoleLogin = \"Failure\") || ($.additionalEventData.MFAUsed != \"Yes\"))) || (($.userIdentity.type = \"IAMUser\") && ($.userIdentity.userName = \"org-admin-operator\") && ($.readOnly IS FALSE) && ($.eventSource != \"signin.amazonaws.com\") && ($.eventName != \"AssumeRole\")) }"

    "agent-platform-org-security-policy-change" = "{ (($.eventSource = \"iam.amazonaws.com\") && (($.eventName = \"PutRolePolicy\") || ($.eventName = \"PutUserPolicy\") || ($.eventName = \"PutGroupPolicy\") || ($.eventName = \"AttachRolePolicy\") || ($.eventName = \"AttachUserPolicy\") || ($.eventName = \"AttachGroupPolicy\") || ($.eventName = \"AddUserToGroup\") || ($.eventName = \"CreateAccessKey\") || ($.eventName = \"CreateRole\") || ($.eventName = \"UpdateAssumeRolePolicy\"))) || (($.eventSource = \"organizations.amazonaws.com\") && (($.eventName = \"AttachPolicy\") || ($.eventName = \"DetachPolicy\") || ($.eventName = \"UpdatePolicy\") || ($.eventName = \"DisablePolicyType\"))) }"

    "agent-platform-org-security-root-activity" = "{ ($.userIdentity.type = \"Root\") && ($.userIdentity.invokedBy NOT EXISTS) && ($.eventType != \"AwsServiceEvent\") }"

    "agent-platform-org-security-detector-tamper" = "{ (($.eventSource = \"cloudtrail.amazonaws.com\") && ($.readOnly IS FALSE)) || (($.eventSource = \"s3.amazonaws.com\") && ($.readOnly IS FALSE) && ($.requestParameters.bucketName = \"agent-platform-org-security-trail-*\")) || (($.eventSource = \"logs.amazonaws.com\") && ($.readOnly IS FALSE) && ($.eventName != \"CreateLogStream\") && ($.requestParameters.logGroupName = \"agent-platform-org-security-cloudtrail\")) || (($.eventSource = \"sns.amazonaws.com\") && ($.readOnly IS FALSE) && (($.requestParameters.topicArn = \"*:agent-platform-org-security-alerts\") || ($.requestParameters.subscriptionArn = \"*:agent-platform-org-security-alerts:*\"))) || (($.eventSource = \"monitoring.amazonaws.com\") && (($.eventName = \"PutMetricAlarm\") || ($.eventName = \"DeleteAlarms\") || ($.eventName = \"DisableAlarmActions\"))) }"
  }

  org_security_metric_names = {
    "agent-platform-org-security-admin-role-assumed" = "AdminRoleAssumed"
    "agent-platform-org-security-signin-anomaly"     = "SigninAnomaly"
    "agent-platform-org-security-policy-change"      = "PolicyChange"
    "agent-platform-org-security-root-activity"      = "RootActivity"
    "agent-platform-org-security-detector-tamper"    = "DetectorTamper"
  }

  org_security_alarm_triage = "Read who changed what: CloudWatch Logs Insights on log group agent-platform-org-security-cloudtrail -- fields eventTime, userIdentity.arn, eventName, requestParameters.roleName, errorCode, errorMessage, eventID | sort eventTime desc | limit 50."

  org_security_logs_policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "OrgSecurityTrailLogDelivery"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:${local.org_security_account_id}:log-group:agent-platform-org-security-cloudtrail:log-stream:*"
      },
    ]
  })
}

resource "aws_s3_bucket" "org_security_trail" {
  bucket        = "agent-platform-org-security-trail-${local.org_security_account_id}-${var.aws_region}"
  force_destroy = false

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "org_security_trail" {
  bucket = aws_s3_bucket.org_security_trail.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "org_security_trail" {
  bucket                  = aws_s3_bucket.org_security_trail.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "org_security_trail" {
  bucket = aws_s3_bucket.org_security_trail.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_ownership_controls" "org_security_trail" {
  bucket = aws_s3_bucket.org_security_trail.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# S3 is the 400-day authoritative copy; the log group keeps 30 days for the alarms and queries.
resource "aws_s3_bucket_lifecycle_configuration" "org_security_trail" {
  bucket = aws_s3_bucket.org_security_trail.id

  rule {
    id     = "agent-platform-org-security-trail-retention"
    status = "Enabled"

    filter {}

    expiration {
      days = 400
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

resource "aws_s3_bucket_policy" "org_security_trail" {
  bucket = aws_s3_bucket.org_security_trail.id

  # The trail ARN is a template, not a reference: the trail depends on this policy existing.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "OrgSecurityTrailAclCheck"
        Effect    = "Allow"
        Principal = { Service = "cloudtrail.amazonaws.com" }
        Action    = "s3:GetBucketAcl"
        Resource  = aws_s3_bucket.org_security_trail.arn
        Condition = {
          StringEquals = { "aws:SourceArn" = local.org_security_trail_arn }
        }
      },
      {
        Sid       = "OrgSecurityTrailWrite"
        Effect    = "Allow"
        Principal = { Service = "cloudtrail.amazonaws.com" }
        Action    = "s3:PutObject"
        Resource  = "${aws_s3_bucket.org_security_trail.arn}/AWSLogs/${local.org_security_account_id}/*"
        Condition = {
          StringEquals = {
            "s3:x-amz-acl"  = "bucket-owner-full-control"
            "aws:SourceArn" = local.org_security_trail_arn
          }
        }
      },
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.org_security_trail.arn,
          "${aws_s3_bucket.org_security_trail.arn}/*",
        ]
        Condition = {
          Bool = { "aws:SecureTransport" = "false" }
        }
      },
    ]
  })

  depends_on = [aws_s3_bucket_public_access_block.org_security_trail]
}

resource "aws_cloudwatch_log_group" "org_security_trail" {
  name              = local.org_security_log_group
  retention_in_days = 30

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_iam_role" "org_security_trail_logs" {
  name        = "agent-platform-org-security-cloudtrail-logs"
  description = "CloudTrail delivery into the agent-platform-org-security-cloudtrail log group"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "cloudtrail.amazonaws.com" }
        Action    = "sts:AssumeRole"
        Condition = {
          StringEquals = { "aws:SourceArn" = local.org_security_trail_arn }
        }
      },
    ]
  })
}

resource "aws_iam_role_policy" "org_security_trail_logs" {
  name   = "agent-platform-org-security-cloudtrail-logs"
  role   = aws_iam_role.org_security_trail_logs.id
  policy = local.org_security_logs_policy_json
}

resource "aws_cloudtrail" "org_security" {
  name                          = "agent-platform-org-security-trail"
  s3_bucket_name                = aws_s3_bucket.org_security_trail.id
  is_multi_region_trail         = true
  include_global_service_events = true
  enable_log_file_validation    = true
  enable_logging                = true
  cloud_watch_logs_group_arn    = "${aws_cloudwatch_log_group.org_security_trail.arn}:*"
  cloud_watch_logs_role_arn     = aws_iam_role.org_security_trail_logs.arn

  event_selector {
    read_write_type           = "All"
    include_management_events = true
  }

  depends_on = [
    aws_s3_bucket_policy.org_security_trail,
    aws_iam_role_policy.org_security_trail_logs,
  ]

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_sns_topic" "org_security_alerts" {
  name = "agent-platform-org-security-alerts"
}

resource "aws_sns_topic_subscription" "org_security_alerts_email" {
  topic_arn = aws_sns_topic.org_security_alerts.arn
  protocol  = "email"
  endpoint  = var.alerts_email
}

resource "aws_cloudwatch_log_metric_filter" "org_security" {
  for_each = local.org_security_filter_patterns

  name           = each.key
  log_group_name = aws_cloudwatch_log_group.org_security_trail.name
  pattern        = each.value

  metric_transformation {
    name      = local.org_security_metric_names[each.key]
    namespace = "OrgSecurity"
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "org_security_admin_role_assumed" {
  alarm_name          = "agent-platform-org-security-admin-role-assumed"
  alarm_description   = "OrganizationAdmin was assumed or switched to (every operator sitting alarms by design, and a denied attempt too). ${local.org_security_alarm_triage}"
  namespace           = "OrgSecurity"
  metric_name         = aws_cloudwatch_log_metric_filter.org_security["agent-platform-org-security-admin-role-assumed"].metric_transformation[0].name
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.org_security_alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "org_security_signin_anomaly" {
  alarm_name          = "agent-platform-org-security-signin-anomaly"
  alarm_description   = "A failed or MFA-less console sign-in, or a non-read call by org-admin-operator outside sign-in other than AssumeRole. ${local.org_security_alarm_triage}"
  namespace           = "OrgSecurity"
  metric_name         = aws_cloudwatch_log_metric_filter.org_security["agent-platform-org-security-signin-anomaly"].metric_transformation[0].name
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.org_security_alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "org_security_policy_change" {
  alarm_name          = "agent-platform-org-security-policy-change"
  alarm_description   = "An IAM or Organizations policy change by any principal, denied attempts included. ${local.org_security_alarm_triage}"
  namespace           = "OrgSecurity"
  metric_name         = aws_cloudwatch_log_metric_filter.org_security["agent-platform-org-security-policy-change"].metric_transformation[0].name
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.org_security_alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "org_security_root_activity" {
  alarm_name          = "agent-platform-org-security-root-activity"
  alarm_description   = "Root user activity in the management account outside AWS service events. ${local.org_security_alarm_triage}"
  namespace           = "OrgSecurity"
  metric_name         = aws_cloudwatch_log_metric_filter.org_security["agent-platform-org-security-root-activity"].metric_transformation[0].name
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.org_security_alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "org_security_detector_tamper" {
  alarm_name          = "agent-platform-org-security-detector-tamper"
  alarm_description   = "A write touched this detector (trail, bucket, log group, topic or an alarm), or an alarm was edited, deleted or disabled. ${local.org_security_alarm_triage}"
  namespace           = "OrgSecurity"
  metric_name         = aws_cloudwatch_log_metric_filter.org_security["agent-platform-org-security-detector-tamper"].metric_transformation[0].name
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.org_security_alerts.arn]
}
