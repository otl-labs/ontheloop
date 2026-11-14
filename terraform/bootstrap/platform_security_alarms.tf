# Metric filters + alarms of the platform-security-* IAM-change detector (rec-4044, Decision 202).
#
# Seven filter/alarm pairs on the trail's log group, all targeting the EXISTING agent-platform-alerts
# topic (Decision 39; the topic stays in terraform/personal and is referenced by ARN template only).
#
# Pattern rules (TestPlatformRoleIamChangeDetector enforces each):
#   - plain string literals, no interpolation: ARN clauses match by suffix wildcard, so the
#     committed fixture (tests/fixtures/platform_security_filter_events.yaml) evaluates identically
#     against the live service via logs:TestMetricFilter;
#   - each <= 1024 characters (the CloudWatch Logs pattern limit);
#   - regex spans (%...%) use only the CloudWatch Logs regex subset -- no parentheses, so the
#     case-insensitive roleName match is character classes with top-level alternation. IAM role
#     names are case-insensitive in API calls, so an exact-case match is evadable.
# Alerts are never filtered by actor (Decision 202): no CI identity holds an IAM grant on either
# role (Decision 225).
locals {
  platform_security_alerts_topic_arn = "arn:aws:sns:${var.aws_region}:${var.account_id}:agent-platform-alerts"

  platform_security_filter_patterns = {
    # Any IAM write naming PlatformDev/PlatformAdmin in any case, or a content edit to a managed
    # policy attached to them (policy-version verbs carry no roleName). A denied call carries
    # requestParameters null, so it is matched on the role CloudTrail names in errorMessage
    # ("... on resource: role <name> because ...", or the older ":role/<name> ..." ARN form).
    "platform-security-platform-role-iam-change" = "{ ($.eventSource = \"iam.amazonaws.com\") && ($.readOnly IS FALSE) && (($.requestParameters.roleName = %^[Pp][Ll][Aa][Tt][Ff][Oo][Rr][Mm][Dd][Ee][Vv]$|^[Pp][Ll][Aa][Tt][Ff][Oo][Rr][Mm][Aa][Dd][Mm][Ii][Nn]$%) || ($.errorMessage = %role.[Pp][Ll][Aa][Tt][Ff][Oo][Rr][Mm][Dd][Ee][Vv]\\s|role.[Pp][Ll][Aa][Tt][Ff][Oo][Rr][Mm][Aa][Dd][Mm][Ii][Nn]\\s%) || ((($.eventName = \"CreatePolicyVersion\") || ($.eventName = \"SetDefaultPolicyVersion\") || ($.eventName = \"DeletePolicyVersion\") || ($.eventName = \"DeletePolicy\")) && (($.requestParameters.policyArn = \"*:policy/agent-platform-github-ci-apply-boundary\") || ($.requestParameters.policyArn = \"*:policy/platform-security-detector-admin\")))) }"

    # Any denied IAM write, any role.
    "platform-security-denied-iam-write" = "{ ($.eventSource = \"iam.amazonaws.com\") && ($.readOnly IS FALSE) && ($.errorCode = \"AccessDenied*\") }"

    # Tampering with the detector itself. CreateLogStream is excluded (the delivery role's own
    # routine stream creation would otherwise self-fire). DeleteAlarms/DisableAlarmActions match
    # regardless of name: their alarmNames is an array a filter pattern cannot quantify over. The
    # scheduler clause matches any EventBridge Scheduler write -- in practice the heartbeat canary's
    # own schedule only (CI's boundary excludes scheduler, PlatformDev holds no scheduler verb, and
    # PlatformAdmin's only scheduler verbs are on platform-security-* schedules): a disable, update
    # or delete of the canary's schedule emails within about 5 minutes instead of surfacing only as
    # an unattributed heartbeat ALARM roughly an hour later.
    "platform-security-detector-tamper" = "{ (($.eventSource = \"cloudtrail.amazonaws.com\") && ($.readOnly IS FALSE)) || (($.eventSource = \"iam.amazonaws.com\") && ($.readOnly IS FALSE) && ($.requestParameters.roleName = \"platform-security-*\")) || (($.eventSource = \"logs.amazonaws.com\") && ($.readOnly IS FALSE) && ($.eventName != \"CreateLogStream\") && ($.requestParameters.logGroupName = \"platform-security-*\")) || (($.eventSource = \"s3.amazonaws.com\") && ($.readOnly IS FALSE) && ($.requestParameters.bucketName = \"platform-security-trail-*\")) || (($.eventSource = \"monitoring.amazonaws.com\") && ($.eventName = \"PutMetricAlarm\") && ($.requestParameters.alarmName = \"platform-security-*\")) || (($.eventSource = \"monitoring.amazonaws.com\") && (($.eventName = \"DeleteAlarms\") || ($.eventName = \"DisableAlarmActions\"))) || (($.eventSource = \"scheduler.amazonaws.com\") && ($.readOnly IS FALSE)) }"

    # A mutating call on the alerts topic or one of its subscriptions.
    "platform-security-alerts-topic-change" = "{ ($.eventSource = \"sns.amazonaws.com\") && ($.readOnly IS FALSE) && (($.requestParameters.topicArn = \"*:agent-platform-alerts\") || ($.requestParameters.subscriptionArn = \"*:agent-platform-alerts:*\")) }"

    # Any API Gateway write in the account (bounded identities hold none, so each is an admin act or an attack), a Lambda
    # permission or resource-policy edit that names a DuckLake verb function or was denied (a denied call carries
    # requestParameters null but keeps its eventName), or an IAM write to the API's access-log role (Decision 255).
    # The role regex is case-insensitive, as the role-change filter's is.
    "platform-security-ducklake-api-change" = "{ (($.eventSource = \"apigateway.amazonaws.com\") && ($.readOnly IS FALSE)) || (($.eventSource = \"lambda.amazonaws.com\") && ($.readOnly IS FALSE) && (($.eventName = \"AddPermission*\") || ($.eventName = \"RemovePermission*\") || ($.eventName = \"*ResourcePolicy*\")) && (($.errorCode = \"AccessDenied*\") || ($.requestParameters.functionName = \"*agent-platform-ducklake-reader*\") || ($.requestParameters.functionName = \"*agent-platform-ducklake-writer*\") || ($.requestParameters.functionName = \"*agent-platform-ducklake-telemetry-*\"))) || (($.eventSource = \"iam.amazonaws.com\") && ($.readOnly IS FALSE) && ($.requestParameters.roleName = %^[Dd][Uu][Cc][Kk][Ll][Aa][Kk][Ee]-[Aa][Pp][Ii]-[Aa][Cc][Cc][Ee][Ss][Ss]-[Ll][Oo][Gg][Ss]$%)) }"

    # Any IAM write naming the agent session user agent-platform-session in any case, or one of its four
    # agent-platform-session-* policies (policy-version verbs carry no userName), so a key mint, a boundary
    # strip or a policy edit alerts before any key exists (Decision 256). A denied call carries requestParameters
    # null, so it is matched on the user or policy CloudTrail names in errorMessage: any IAM write the user itself is
    # denied, and any denied write on its policies. The policyArn clause and the policy alternative match lower case only.
    "platform-security-agent-session-iam-change" = "{ ($.eventSource = \"iam.amazonaws.com\") && ($.readOnly IS FALSE) && (($.requestParameters.userName = %^[Aa][Gg][Ee][Nn][Tt]-[Pp][Ll][Aa][Tt][Ff][Oo][Rr][Mm]-[Ss][Ee][Ss][Ss][Ii][Oo][Nn]$%) || ($.errorMessage = %user.[Aa][Gg][Ee][Nn][Tt]-[Pp][Ll][Aa][Tt][Ff][Oo][Rr][Mm]-[Ss][Ee][Ss][Ss][Ii][Oo][Nn]\\s|policy.agent-platform-session-%) || ($.requestParameters.policyArn = \"*:policy/agent-platform-session-*\")) }"

    # ANY IAM event, reads included: the platform-security-heartbeat-canary schedule (rec-4044,
    # Decision 202) makes an iam:GetRole call every 5 minutes, so this metric proves the
    # global-event path every other alarm depends on is alive without depending on unrelated CI or
    # organic IAM traffic.
    "platform-security-iam-event-heartbeat" = "{ $.eventSource = \"iam.amazonaws.com\" }"
  }

  platform_security_metric_names = {
    "platform-security-platform-role-iam-change" = "PlatformRoleIamChange"
    "platform-security-denied-iam-write"         = "DeniedIamWrite"
    "platform-security-detector-tamper"          = "DetectorTamper"
    "platform-security-alerts-topic-change"      = "AlertsTopicChange"
    "platform-security-iam-event-heartbeat"      = "IamEventHeartbeat"
    "platform-security-ducklake-api-change"      = "DuckLakeApiChange"
    "platform-security-agent-session-iam-change" = "AgentSessionIamChange"
  }

  platform_security_alarm_triage = "Read who changed what: CloudWatch Logs Insights on log group platform-security-cloudtrail -- fields eventTime, userIdentity.arn, eventName, requestParameters.roleName, errorCode, errorMessage, eventID | sort eventTime desc | limit 50. Console: https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#logsV2:logs-insights"
}

resource "aws_cloudwatch_log_metric_filter" "platform_security" {
  for_each = local.platform_security_filter_patterns

  name           = each.key
  log_group_name = aws_cloudwatch_log_group.platform_security_trail.name
  pattern        = each.value

  metric_transformation {
    name      = local.platform_security_metric_names[each.key]
    namespace = "PlatformSecurity"
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "platform_security_platform_role_iam_change" {
  alarm_name          = "platform-security-platform-role-iam-change"
  alarm_description   = "An IAM write (or denied attempt) named PlatformDev or PlatformAdmin, or edited a policy attached to them. ${local.platform_security_alarm_triage}"
  namespace           = "PlatformSecurity"
  metric_name         = aws_cloudwatch_log_metric_filter.platform_security["platform-security-platform-role-iam-change"].metric_transformation[0].name
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [local.platform_security_alerts_topic_arn]
}

resource "aws_cloudwatch_metric_alarm" "platform_security_denied_iam_write" {
  alarm_name          = "platform-security-denied-iam-write"
  alarm_description   = "An IAM write was denied (AccessDenied) for some principal. ${local.platform_security_alarm_triage}"
  namespace           = "PlatformSecurity"
  metric_name         = aws_cloudwatch_log_metric_filter.platform_security["platform-security-denied-iam-write"].metric_transformation[0].name
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [local.platform_security_alerts_topic_arn]
}

resource "aws_cloudwatch_metric_alarm" "platform_security_detector_tamper" {
  alarm_name          = "platform-security-detector-tamper"
  alarm_description   = "A write touched the platform-security detector (trail, delivery role, log group, bucket or alarms), or any alarm was deleted or disabled. ${local.platform_security_alarm_triage}"
  namespace           = "PlatformSecurity"
  metric_name         = aws_cloudwatch_log_metric_filter.platform_security["platform-security-detector-tamper"].metric_transformation[0].name
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [local.platform_security_alerts_topic_arn]
}

resource "aws_cloudwatch_metric_alarm" "platform_security_alerts_topic_change" {
  alarm_name          = "platform-security-alerts-topic-change"
  alarm_description   = "A write changed the agent-platform-alerts topic or one of its subscriptions. ${local.platform_security_alarm_triage}"
  namespace           = "PlatformSecurity"
  metric_name         = aws_cloudwatch_log_metric_filter.platform_security["platform-security-alerts-topic-change"].metric_transformation[0].name
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [local.platform_security_alerts_topic_arn]
}

resource "aws_cloudwatch_metric_alarm" "platform_security_ducklake_api_change" {
  alarm_name          = "platform-security-ducklake-api-change"
  alarm_description   = "A write touched the DuckLake REST API, a Lambda permission or resource policy of the four DuckLake verb functions, or the ducklake-api-access-logs role. ${local.platform_security_alarm_triage}"
  namespace           = "PlatformSecurity"
  metric_name         = aws_cloudwatch_log_metric_filter.platform_security["platform-security-ducklake-api-change"].metric_transformation[0].name
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [local.platform_security_alerts_topic_arn]
}

resource "aws_cloudwatch_metric_alarm" "platform_security_agent_session_iam_change" {
  alarm_name          = "platform-security-agent-session-iam-change"
  alarm_description   = "An IAM write named the agent session user agent-platform-session or one of its agent-platform-session-* policies, or the user was denied one. ${local.platform_security_alarm_triage}"
  namespace           = "PlatformSecurity"
  metric_name         = aws_cloudwatch_log_metric_filter.platform_security["platform-security-agent-session-iam-change"].metric_transformation[0].name
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [local.platform_security_alerts_topic_arn]
}

# Missing data is breaching by design: StopLogging, DeleteTrail, a multi-region or global-events
# flip, an event-selector change or a broken Logs delivery all starve this metric -- as does a dead
# platform-security-heartbeat-canary schedule. Stated blinding latency: ALARM fires 60-105 minutes
# after the last delivered IAM event (four 15-minute periods plus CloudWatch evaluation-range slack).
resource "aws_cloudwatch_metric_alarm" "platform_security_iam_event_heartbeat" {
  alarm_name          = "platform-security-iam-event-heartbeat"
  alarm_description   = "No IAM event reached the platform-security trail's log group in four consecutive 15-minute periods, so the detector may be blind. First check the platform-security-heartbeat-canary schedule and its Step Functions executions (a dead canary), then AWS/Logs IncomingLogEvents on platform-security-cloudtrail (zero means Logs delivery is broken, non-zero means the global IAM-event path is broken). ${local.platform_security_alarm_triage}"
  namespace           = "PlatformSecurity"
  metric_name         = aws_cloudwatch_log_metric_filter.platform_security["platform-security-iam-event-heartbeat"].metric_transformation[0].name
  statistic           = "Sum"
  period              = 900
  evaluation_periods  = 4
  datapoints_to_alarm = 4
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [local.platform_security_alerts_topic_arn]
  ok_actions          = [local.platform_security_alerts_topic_arn]
}
