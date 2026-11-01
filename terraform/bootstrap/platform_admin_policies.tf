# PlatformAdmin AdminOps inline policy, adopted from its terraform/personal source file (PLAN-
# platform-iam-bootstrap-adopt, rec-3838 plan 1 of 2). Zero-diff import -- no statement
# added, removed or reordered relative to the personal-root source; this file references no
# terraform/personal object (every action in AdminOps is account/region-scoped or "*").
locals {
  platform_admin_ops_policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "IAMFull"
        Effect   = "Allow"
        Action   = "iam:*"
        Resource = "*"
      },
      {
        Sid    = "LambdaAdminManagement"
        Effect = "Allow"
        Action = [
          "lambda:CreateFunction",
          "lambda:DeleteFunction",
          "lambda:UpdateFunctionCode",
          "lambda:UpdateFunctionConfiguration",
          "lambda:GetFunction",
          "lambda:GetFunctionConfiguration",
          "lambda:GetFunctionCodeSigningConfig",
          "lambda:GetRuntimeManagementConfig",
          "lambda:GetPolicy",
          "lambda:AddPermission",
          "lambda:RemovePermission",
          "lambda:ListFunctions",
          "lambda:ListVersionsByFunction",
          "lambda:InvokeFunction",
          "lambda:InvokeFunctionUrl",
          "lambda:CreateFunctionUrlConfig",
          "lambda:GetFunctionUrlConfig",
          "lambda:UpdateFunctionUrlConfig",
          "lambda:DeleteFunctionUrlConfig",
          "lambda:PublishLayerVersion",
          "lambda:GetLayerVersion",
          "lambda:DeleteLayerVersion",
          "lambda:ListLayerVersions",
          "lambda:TagResource",
          "lambda:UntagResource",
          "lambda:ListTags",
          "lambda:PutFunctionConcurrency",
          "lambda:DeleteFunctionConcurrency",
          "lambda:GetFunctionConcurrency",
        ]
        Resource = "*"
      },
      {
        Sid    = "EventBridgeScheduleManagement"
        Effect = "Allow"
        Action = [
          "events:PutRule",
          "events:DeleteRule",
          "events:DescribeRule",
          "events:EnableRule",
          "events:DisableRule",
          "events:PutTargets",
          "events:RemoveTargets",
          "events:ListTargetsByRule",
          "events:TagResource",
          "events:UntagResource",
          "events:ListTagsForResource",
        ]
        Resource = "arn:aws:events:${var.aws_region}:${var.account_id}:rule/agent-platform-*"
      },
      {
        Sid    = "CloudWatchAlarmManagement"
        Effect = "Allow"
        Action = [
          "cloudwatch:PutMetricAlarm",
          "cloudwatch:DeleteAlarms",
          "cloudwatch:TagResource",
          "cloudwatch:UntagResource",
          "cloudwatch:ListTagsForResource",
        ]
        Resource = "arn:aws:cloudwatch:${var.aws_region}:${var.account_id}:alarm:*"
      },
      {
        Sid      = "CloudWatchAlarmDescribe"
        Effect   = "Allow"
        Action   = ["cloudwatch:DescribeAlarms"]
        Resource = "*"
      },
      {
        Sid    = "LambdaLogGroupManagement"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:DeleteLogGroup",
          "logs:PutRetentionPolicy",
          "logs:TagResource",
          "logs:UntagResource",
          "logs:ListTagsForResource",
          "logs:TagLogGroup",
          "logs:UntagLogGroup",
          "logs:ListTagsLogGroup",
        ]
        Resource = "arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:/aws/lambda/agent-platform-*"
      },
      {
        Sid      = "LambdaLogGroupDescribe"
        Effect   = "Allow"
        Action   = "logs:DescribeLogGroups"
        Resource = "*"
      },
      {
        Sid    = "LambdaLogGroupRead"
        Effect = "Allow"
        Action = [
          "logs:DescribeLogStreams",
          "logs:GetLogEvents",
          "logs:FilterLogEvents",
        ]
        Resource = [
          "arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:/aws/lambda/agent-platform-*",
          "arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:/aws/lambda/agent-platform-*:log-stream:*",
        ]
      },
      {
        Sid    = "SecretsManagerAdmin"
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue",
          "secretsmanager:PutSecretValue",
          "secretsmanager:CreateSecret",
          "secretsmanager:UpdateSecret",
          "secretsmanager:DescribeSecret",
          "secretsmanager:GetResourcePolicy",
          "secretsmanager:TagResource",
          "secretsmanager:UntagResource",
        ]
        Resource = "*"
      },
      {
        Sid    = "ServiceQuotasManagement"
        Effect = "Allow"
        Action = [
          "servicequotas:GetServiceQuota",
          "servicequotas:GetAWSDefaultServiceQuota",
          "servicequotas:ListServiceQuotas",
          "servicequotas:RequestServiceQuotaIncrease",
          "servicequotas:GetRequestedServiceQuotaChange",
          "servicequotas:ListRequestedServiceQuotaChangeHistory",
          "servicequotas:ListRequestedServiceQuotaChangeHistoryByQuota",
        ]
        Resource = "*"
      },
      {
        Sid    = "SSMParameterProvisioning"
        Effect = "Allow"
        Action = [
          "ssm:PutParameter",
          "ssm:GetParameter",
          "ssm:GetParameters",
          "ssm:DeleteParameter",
          "ssm:AddTagsToResource",
          "ssm:RemoveTagsFromResource",
          "ssm:ListTagsForResource",
        ]
        Resource = "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/agent-platform/*"
      },
      {
        # Decision 237 cl.2: the operator's counterpart of its Secrets Manager admin grant, over the /secret/ family
        # (the copy tool, rotation, re-minting). Same verb set as SSMParameterProvisioning above.
        Sid    = "SecretParameterAdmin"
        Effect = "Allow"
        Action = [
          "ssm:PutParameter",
          "ssm:GetParameter",
          "ssm:GetParameters",
          "ssm:DeleteParameter",
          "ssm:AddTagsToResource",
          "ssm:RemoveTagsFromResource",
          "ssm:ListTagsForResource",
        ]
        Resource = "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/secret/*"
      },
      {
        Sid      = "SSMDescribeParameters"
        Effect   = "Allow"
        Action   = "ssm:DescribeParameters"
        Resource = "*"
      },
      {
        Sid    = "ObservabilityMetricAndTrailRead"
        Effect = "Allow"
        Action = [
          "cloudwatch:GetMetricStatistics",
          "cloudwatch:GetMetricData",
          "cloudwatch:ListMetrics",
          "cloudwatch:DescribeAlarmsForMetric",
          "cloudtrail:LookupEvents",
          "s3:ListAllMyBuckets",
        ]
        Resource = "*"
      },
      {
        Sid      = "CloudWatchAlarmHistoryRead"
        Effect   = "Allow"
        Action   = ["cloudwatch:DescribeAlarmHistory"]
        Resource = "arn:aws:cloudwatch:${var.aws_region}:${var.account_id}:alarm:*"
      },
      {
        Sid      = "S3BucketInventoryRead"
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:ListBucketVersions"]
        Resource = "arn:aws:s3:::agent-platform-*"
      },
    ]
  })
}

import {
  id = "PlatformAdmin:AdminOps"
  to = aws_iam_role_policy.platform_admin_ops
}

resource "aws_iam_role_policy" "platform_admin_ops" {
  name   = "AdminOps"
  role   = aws_iam_role.platform_admin.id
  policy = local.platform_admin_ops_policy_json

  lifecycle {
    prevent_destroy = true

    precondition {
      condition     = length(jsonencode(jsondecode(local.platform_admin_ops_policy_json))) <= 10240
      error_message = "PlatformAdmin AdminOps inline policy exceeds the 10,240 B IAM inline-policy limit (whitespace-stripped measure, Decision 156). Move a statement to a customer-managed policy or trim grants."
    }

    precondition {
      # Self-access guard: AdminOps must still carry an unconditioned Allow of the IAMFull statement
      # (the grant that lets PlatformAdmin manage IAM at all, import included). Checked by parsing
      # the policy local itself, never the byte-size measure above, which this file's other
      # precondition already owns -- personal-policies-carry-byte-preconditions asserts exactly one
      # such measure per file.
      condition = anytrue([
        for s in jsondecode(local.platform_admin_ops_policy_json).Statement :
        try(s.Sid, "") == "IAMFull" && s.Effect == "Allow" && !can(s.Condition)
        && contains(flatten([try(s.Action, [])]), "iam:*")
        && contains(flatten([try(s.Resource, [])]), "*")
      ])
      error_message = "PlatformAdmin AdminOps no longer has an unconditioned Allow of iam:* on * in the IAMFull statement -- this is the self-access grant that lets PlatformAdmin manage IAM at all."
    }
  }
}
