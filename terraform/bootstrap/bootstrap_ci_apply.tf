# terraform/bootstrap CI apply identities (PLAN-bootstrap-ci-gated-apply, first of two PRs).
#
# Two roles for .github/workflows/terraform-bootstrap-apply.yml (added by the second PR), named outside agent-platform-*
# so no CI role's prefix-keyed IAM grant can write them. Each trusts ONE statement, StringEquals only: one Environment
# subject and that workflow's job_workflow_ref at main.
#   - bootstrap-preview (environment tf-trusted-plan): refreshes and plans this root, read-only.
#   - bootstrap-apply (environment tf-bootstrap-apply, operator-approved): applies only changes to boundary-carrying
#     agent-platform-* roles and their policies; the protected set and the root of trust stay the operator's.
#
# Neither role carries a permissions boundary, on purpose (Decision 144 cl.3 style): the boundary denies
# iam:GetPolicy, iam:GetPolicyVersion and iam:GetUser, the cloudtrail, scheduler and states reads and
# CreatePolicyVersion on agent-platform-github-ci-apply-reads, all of which these roles need. Their ceiling is the
# explicit Deny policies below, which cover the protected set (Decision 202 cl.1 for the detector).
# Changes here apply only by the operator's CloudShell apply (deploy-paths.yaml admin_out_of_band).

locals {
  bootstrap_ci_apply_env     = "tf-bootstrap-apply"
  bootstrap_ci_preview_env   = "tf-trusted-plan"
  bootstrap_ci_workflow_refs = ["otl-labs/ontheloop/.github/workflows/terraform-bootstrap-apply.yml@refs/heads/main"]

  # Every action the plan-time refresh needs, flat, so the two NotAction Denies below equal the reads policy.
  bootstrap_plan_read_actions = [
    "iam:Get*",
    "iam:List*",
    "cloudtrail:DescribeTrails",
    "cloudtrail:GetTrail",
    "cloudtrail:GetTrailStatus",
    "cloudtrail:GetEventSelectors",
    "cloudtrail:GetInsightSelectors",
    "cloudtrail:ListTags",
    "logs:Describe*",
    "logs:ListTagsLogGroup",
    "logs:ListTagsForResource",
    "cloudwatch:DescribeAlarms",
    "cloudwatch:ListTagsForResource",
    "scheduler:GetSchedule",
    "scheduler:GetScheduleGroup",
    "scheduler:ListTagsForResource",
    "states:Describe*",
    "states:List*",
    "states:ValidateStateMachineDefinition",
    "secretsmanager:DescribeSecret",
    "secretsmanager:GetResourcePolicy",
    "secretsmanager:ListSecrets",
    "s3:GetBucket*",
    "s3:GetAccelerateConfiguration",
    "s3:GetLifecycleConfiguration",
    "s3:GetReplicationConfiguration",
    "s3:GetEncryptionConfiguration",
    "s3:ListBucket",
    "s3:GetObject",
    "ssm:GetParameter",
    "ssm:GetParameters",
    "ssm:ListTagsForResource",
    "ssm:DescribeParameters",
    "apigateway:GET",
    "lambda:GetPolicy",
    "servicequotas:Get*",
    "servicequotas:List*",
  ]

  bootstrap_plan_reads_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "IAMRead"
        Effect   = "Allow"
        Action   = ["iam:Get*", "iam:List*"]
        Resource = ["*"]
      },
      {
        Sid      = "CloudTrailRead"
        Effect   = "Allow"
        Action   = ["cloudtrail:DescribeTrails", "cloudtrail:GetTrail", "cloudtrail:GetTrailStatus", "cloudtrail:GetEventSelectors", "cloudtrail:GetInsightSelectors", "cloudtrail:ListTags"]
        Resource = ["*"]
      },
      {
        Sid      = "LogsConfigRead"
        Effect   = "Allow"
        Action   = ["logs:Describe*", "logs:ListTagsLogGroup", "logs:ListTagsForResource"]
        Resource = ["*"]
      },
      {
        Sid      = "AlarmRead"
        Effect   = "Allow"
        Action   = ["cloudwatch:DescribeAlarms", "cloudwatch:ListTagsForResource"]
        Resource = ["*"]
      },
      {
        Sid      = "SchedulerRead"
        Effect   = "Allow"
        Action   = ["scheduler:GetSchedule", "scheduler:GetScheduleGroup", "scheduler:ListTagsForResource"]
        Resource = ["*"]
      },
      {
        # states:ValidateStateMachineDefinition is non-mutating; the provider calls it at plan time whenever the
        # heartbeat state machine's definition changes, so such a plan reaches CLOUDSHELL_REQUIRED, not AccessDenied.
        Sid      = "StatesRead"
        Effect   = "Allow"
        Action   = ["states:Describe*", "states:List*", "states:ValidateStateMachineDefinition"]
        Resource = ["*"]
      },
      {
        Sid      = "SecretMetadataRead"
        Effect   = "Allow"
        Action   = ["secretsmanager:DescribeSecret", "secretsmanager:GetResourcePolicy", "secretsmanager:ListSecrets"]
        Resource = ["*"]
      },
      {
        Sid    = "TrailBucketConfigRead"
        Effect = "Allow"
        Action = [
          "s3:GetBucket*",
          "s3:GetAccelerateConfiguration",
          "s3:GetLifecycleConfiguration",
          "s3:GetReplicationConfiguration",
          "s3:GetEncryptionConfiguration",
          "s3:ListBucket",
        ]
        Resource = ["arn:aws:s3:::platform-security-trail-${var.account_id}-${var.aws_region}"]
      },
      {
        Sid      = "BootstrapStateRead"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = ["${local.bootstrap_state_bucket_arn}/terraform.tfstate"]
      },
      {
        # Decision 237 cl.2: these two roles are the declared consumers of the personal tfvars parameter.
        Sid      = "TfvarsRead"
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = ["arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/secret/agent-platform-terraform-personal-tfvars"]
      },
      {
        # PLAN-agent-ducklake-rest-api's agent_ducklake_api.tf, covered now so either build order keeps the preview
        # green. Never ::/apikeys*, whose GET can return key values.
        Sid    = "ApiGatewayRead"
        Effect = "Allow"
        Action = ["apigateway:GET"]
        Resource = [
          "arn:aws:apigateway:${var.aws_region}::/restapis",
          "arn:aws:apigateway:${var.aws_region}::/restapis/*",
          "arn:aws:apigateway:${var.aws_region}::/account",
          "arn:aws:apigateway:${var.aws_region}::/tags/*",
        ]
      },
      {
        Sid      = "LambdaPolicyRead"
        Effect   = "Allow"
        Action   = ["lambda:GetPolicy"]
        Resource = ["arn:aws:lambda:${var.aws_region}:${var.account_id}:function:agent-platform-ducklake-*"]
      },
      {
        Sid      = "QuotaRead"
        Effect   = "Allow"
        Action   = ["servicequotas:Get*", "servicequotas:List*"]
        Resource = ["*"]
      },
      {
        Sid      = "ApiUrlParamRead"
        Effect   = "Allow"
        Action   = ["ssm:GetParameter", "ssm:GetParameters", "ssm:ListTagsForResource"]
        Resource = ["arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/agent-platform/ducklake/api_url/*"]
      },
      {
        Sid      = "SsmDescribeParameters"
        Effect   = "Allow"
        Action   = ["ssm:DescribeParameters"]
        Resource = ["*"]
      },
    ]
  })

  bootstrap_apply_write_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "BootstrapStateWrite"
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = ["${local.bootstrap_state_bucket_arn}/terraform.tfstate"]
      },
      {
        Sid      = "BootstrapStateLock"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = ["${local.bootstrap_state_bucket_arn}/terraform.tfstate.tflock"]
      },
      {
        Sid      = "IAMRoleCreateBounded"
        Effect   = "Allow"
        Action   = ["iam:CreateRole"]
        Resource = ["arn:aws:iam::${var.account_id}:role/agent-platform-*"]
        Condition = {
          StringEquals = {
            "iam:PermissionsBoundary" = "arn:aws:iam::${var.account_id}:policy/agent-platform-github-ci-apply-boundary"
          }
        }
      },
      {
        Sid      = "IAMRoleWriteBounded"
        Effect   = "Allow"
        Action   = ["iam:PutRolePolicy", "iam:PutRolePermissionsBoundary"]
        Resource = ["arn:aws:iam::${var.account_id}:role/agent-platform-*"]
        Condition = {
          StringEquals = {
            "iam:PermissionsBoundary" = "arn:aws:iam::${var.account_id}:policy/agent-platform-github-ci-apply-boundary"
          }
        }
      },
      {
        # Split from the precedent's IAMRoleWriteBounded: only agent-platform-* policies can be attached, so no
        # AWS-managed or other-prefix policy reaches a bounded role.
        Sid      = "IAMRoleAttachBounded"
        Effect   = "Allow"
        Action   = ["iam:AttachRolePolicy"]
        Resource = ["arn:aws:iam::${var.account_id}:role/agent-platform-*"]
        Condition = {
          StringEquals = {
            "iam:PermissionsBoundary" = "arn:aws:iam::${var.account_id}:policy/agent-platform-github-ci-apply-boundary"
          }
          ArnLike = {
            "iam:PolicyARN" = "arn:aws:iam::*:policy/agent-platform-*"
          }
        }
      },
      {
        Sid      = "IAMRoleMetadataWrite"
        Effect   = "Allow"
        Action   = ["iam:TagRole", "iam:UntagRole", "iam:UpdateRole", "iam:UpdateRoleDescription"]
        Resource = ["arn:aws:iam::${var.account_id}:role/agent-platform-*"]
      },
      {
        # UpdateAssumeRolePolicy takes no iam:PermissionsBoundary condition, so a prefix would reach terraform/personal's
        # roles (Decision 143 cl.1): the resource is the enumerated agent-platform-* roles this root declares.
        Sid      = "IAMRoleTrustWrite"
        Effect   = "Allow"
        Action   = ["iam:UpdateAssumeRolePolicy"]
        Resource = ["arn:aws:iam::${var.account_id}:role/agent-platform-github-ci-apply"]
      },
      {
        Sid      = "IAMManagedPolicyWrite"
        Effect   = "Allow"
        Action   = ["iam:CreatePolicy", "iam:CreatePolicyVersion", "iam:DeletePolicyVersion", "iam:TagPolicy", "iam:UntagPolicy"]
        Resource = ["arn:aws:iam::${var.account_id}:policy/agent-platform-*"]
      },
    ]
  })

  bootstrap_apply_guard_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DenyProtectedRoleWrite"
        Effect = "Deny"
        Action = ["iam:Create*", "iam:Put*", "iam:Attach*", "iam:Detach*", "iam:Delete*", "iam:Update*", "iam:Tag*", "iam:Untag*", "iam:PassRole"]
        Resource = [
          "arn:aws:iam::${var.account_id}:role/PlatformAdmin",
          "arn:aws:iam::${var.account_id}:role/PlatformDev",
          "arn:aws:iam::${var.account_id}:role/github-settings-*",
          "arn:aws:iam::${var.account_id}:role/bootstrap-*",
          "arn:aws:iam::${var.account_id}:role/platform-security-*",
          "arn:aws:iam::${var.account_id}:role/ducklake-api-*",
        ]
      },
      {
        Sid    = "DenyUserAndGroupWrite"
        Effect = "Deny"
        Action = [
          "iam:Create*", "iam:Put*", "iam:Attach*", "iam:Detach*", "iam:Delete*", "iam:Update*", "iam:Tag*", "iam:Untag*",
          "iam:Add*", "iam:Remove*", "iam:Enable*", "iam:Deactivate*", "iam:Resync*", "iam:Upload*", "iam:Change*", "iam:Reset*", "iam:Set*",
        ]
        Resource = [
          "arn:aws:iam::${var.account_id}:user/*",
          "arn:aws:iam::${var.account_id}:group/*",
          "arn:aws:iam::${var.account_id}:mfa/*",
        ]
      },
      {
        Sid    = "DenyProtectedPolicyWrite"
        Effect = "Deny"
        Action = ["iam:CreatePolicy", "iam:CreatePolicyVersion", "iam:DeletePolicy", "iam:DeletePolicyVersion", "iam:SetDefaultPolicyVersion", "iam:TagPolicy", "iam:UntagPolicy"]
        Resource = [
          "arn:aws:iam::${var.account_id}:policy/agent-platform-github-ci-apply-boundary",
          "arn:aws:iam::${var.account_id}:policy/agent-platform-session-*",
          "arn:aws:iam::${var.account_id}:policy/platform-security-*",
          "arn:aws:iam::${var.account_id}:policy/bootstrap-*",
        ]
      },
      {
        Sid      = "DenyBoundaryRemoval"
        Effect   = "Deny"
        Action   = ["iam:DeleteRolePermissionsBoundary", "iam:DeleteUserPermissionsBoundary", "iam:PutUserPermissionsBoundary"]
        Resource = ["*"]
      },
      {
        Sid    = "DenyProviderAndAccountWrite"
        Effect = "Deny"
        Action = [
          "iam:CreateOpenIDConnectProvider", "iam:DeleteOpenIDConnectProvider", "iam:UpdateOpenIDConnectProviderThumbprint",
          "iam:AddClientIDToOpenIDConnectProvider", "iam:RemoveClientIDFromOpenIDConnectProvider",
          "iam:TagOpenIDConnectProvider", "iam:UntagOpenIDConnectProvider",
          "iam:CreateSAMLProvider", "iam:UpdateSAMLProvider", "iam:DeleteSAMLProvider", "iam:TagSAMLProvider", "iam:UntagSAMLProvider",
          "iam:CreateServiceLinkedRole", "iam:DeleteServiceLinkedRole", "iam:CreateAccountAlias", "iam:DeleteAccountAlias",
          "iam:UpdateAccountPasswordPolicy", "iam:DeleteAccountPasswordPolicy", "sts:SetSecurityTokenServicePreferences",
        ]
        Resource = ["*"]
      },
      {
        # Decision 202 cl.1: the detector statements are left out of the Allows, not allowed and denied; every
        # non-read action on a platform-security-* resource of any service is denied here.
        Sid       = "DenyDetectorWrite"
        Effect    = "Deny"
        NotAction = local.bootstrap_plan_read_actions
        Resource = [
          "arn:aws:iam::${var.account_id}:role/platform-security-*",
          "arn:aws:iam::${var.account_id}:policy/platform-security-*",
          "arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:platform-security-*",
          "arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:platform-security-*:*",
          "arn:aws:cloudwatch:${var.aws_region}:${var.account_id}:alarm:platform-security-*",
          "arn:aws:cloudtrail:${var.aws_region}:${var.account_id}:trail/platform-security-*",
          "arn:aws:s3:::platform-security-trail-*",
          "arn:aws:s3:::platform-security-trail-*/*",
          "arn:aws:states:${var.aws_region}:${var.account_id}:stateMachine:platform-security-*",
          "arn:aws:scheduler:${var.aws_region}:${var.account_id}:schedule-group/platform-security-*",
          "arn:aws:scheduler:${var.aws_region}:${var.account_id}:schedule/platform-security-*/*",
        ]
      },
      {
        # Decision 248 cl.5: the apply App key is the operator's.
        Sid    = "DenyGithubSettingsKey"
        Effect = "Deny"
        Action = [
          "secretsmanager:GetSecretValue", "secretsmanager:PutSecretValue", "secretsmanager:UpdateSecret",
          "secretsmanager:DeleteSecret", "secretsmanager:RestoreSecret", "secretsmanager:RotateSecret",
          "secretsmanager:PutResourcePolicy", "secretsmanager:DeleteResourcePolicy", "secretsmanager:TagResource",
          "secretsmanager:UntagResource", "secretsmanager:ReplicateSecretToRegions", "secretsmanager:RemoveRegionsFromReplication",
          "secretsmanager:CancelRotateSecret", "secretsmanager:UpdateSecretVersionStage", "secretsmanager:StopReplicationToReplica",
        ]
        Resource = ["arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:agent-platform-terraform-github-apply-app*"]
      },
      {
        Sid    = "DenySecretValues"
        Effect = "Deny"
        Action = ["secretsmanager:GetSecretValue", "ssm:GetParameter*"]
        NotResource = [
          "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/secret/agent-platform-terraform-personal-tfvars",
          "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/agent-platform/ducklake/api_url/*",
        ]
      },
      {
        Sid    = "DenyObjectIOOutsideState"
        Effect = "Deny"
        Action = ["s3:GetObject*", "s3:PutObject*", "s3:DeleteObject*"]
        NotResource = [
          "${local.bootstrap_state_bucket_arn}/terraform.tfstate",
          "${local.bootstrap_state_bucket_arn}/terraform.tfstate.tflock",
        ]
      },
      {
        Sid      = "DenyCredentialMinting"
        Effect   = "Deny"
        Action   = ["sts:AssumeRole", "sts:AssumeRoleWithWebIdentity", "sts:AssumeRoleWithSAML", "sts:AssumeRoot", "sts:GetFederationToken"]
        Resource = ["*"]
      },
      {
        # Account-wide dumps no refresh needs, which IAMRead's iam:Get* would otherwise allow.
        Sid      = "DenyAccountIAMDump"
        Effect   = "Deny"
        Action   = ["iam:GetAccountAuthorizationDetails", "iam:GetCredentialReport", "iam:GenerateCredentialReport"]
        Resource = ["*"]
      },
    ]
  })

  bootstrap_preview_guard_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # A call a service makes on the role's behalf (the aws/ssm key decrypt behind materialise-tfvars) is not denied
        # by this statement; a direct call is. The key policy's kms:ViaService still bounds the forwarded one.
        Sid       = "DenyAllButReads"
        Effect    = "Deny"
        NotAction = concat(local.bootstrap_plan_read_actions, ["sts:GetCallerIdentity"])
        Resource  = ["*"]
        Condition = {
          BoolIfExists = {
            "aws:ViaAWSService" = "false"
          }
        }
      },
      {
        Sid         = "DenyObjectReadOutsideState"
        Effect      = "Deny"
        Action      = ["s3:GetObject*"]
        NotResource = ["${local.bootstrap_state_bucket_arn}/terraform.tfstate"]
      },
      {
        Sid    = "DenyParameterReadOutsideTfvars"
        Effect = "Deny"
        Action = ["ssm:GetParameter*"]
        NotResource = [
          "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/secret/agent-platform-terraform-personal-tfvars",
          "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/agent-platform/ducklake/api_url/*",
        ]
      },
      {
        Sid      = "DenyAccountIAMDump"
        Effect   = "Deny"
        Action   = ["iam:GetAccountAuthorizationDetails", "iam:GetCredentialReport", "iam:GenerateCredentialReport"]
        Resource = ["*"]
      },
    ]
  })
}

resource "aws_iam_role" "bootstrap_apply" {
  name        = "bootstrap-apply"
  description = "terraform-bootstrap-apply.yml apply job: operator-approved Environment at main (PLAN-bootstrap-ci-gated-apply)"
  # No permissions_boundary: see the header. The explicit Denies in bootstrap_apply_guard are the ceiling.

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Federated = "arn:aws:iam::${var.account_id}:oidc-provider/token.actions.githubusercontent.com"
        }
        Action = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
            "token.actions.githubusercontent.com:sub" = flatten([
              for repo in local.github_repos : ["repo:${repo}:environment:${local.bootstrap_ci_apply_env}"]
            ])
            "token.actions.githubusercontent.com:job_workflow_ref" = local.bootstrap_ci_workflow_refs
          }
        }
      }
    ]
  })

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_iam_role" "bootstrap_preview" {
  name        = "bootstrap-preview"
  description = "terraform-bootstrap-apply.yml preview job: tf-trusted-plan Environment at main (PLAN-bootstrap-ci-gated-apply)"
  # No permissions_boundary: see the header.

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Federated = "arn:aws:iam::${var.account_id}:oidc-provider/token.actions.githubusercontent.com"
        }
        Action = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
            "token.actions.githubusercontent.com:sub" = flatten([
              for repo in local.github_repos : ["repo:${repo}:environment:${local.bootstrap_ci_preview_env}"]
            ])
            "token.actions.githubusercontent.com:job_workflow_ref" = local.bootstrap_ci_workflow_refs
          }
        }
      }
    ]
  })

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_iam_policy" "bootstrap_apply_reads" {
  name        = "bootstrap-plan-reads"
  description = "Plan-time refresh reads for the bootstrap CI roles (PLAN-bootstrap-ci-gated-apply)"
  policy      = local.bootstrap_plan_reads_json

  lifecycle {
    precondition {
      condition     = length(jsonencode(jsondecode(local.bootstrap_plan_reads_json))) <= 6144
      error_message = "bootstrap-plan-reads exceeds the 6,144 B managed-policy limit (whitespace-stripped measure)."
    }
  }
}

resource "aws_iam_policy" "bootstrap_apply_write" {
  name        = "bootstrap-apply-write"
  description = "Bounded IAM writes and state writes for bootstrap-apply (PLAN-bootstrap-ci-gated-apply)"
  policy      = local.bootstrap_apply_write_json

  lifecycle {
    precondition {
      condition     = length(jsonencode(jsondecode(local.bootstrap_apply_write_json))) <= 6144
      error_message = "bootstrap-apply-write exceeds the 6,144 B managed-policy limit (whitespace-stripped measure)."
    }
  }
}

resource "aws_iam_policy" "bootstrap_apply_guard" {
  name        = "bootstrap-apply-guard"
  description = "Explicit Denies over the protected set for bootstrap-apply (PLAN-bootstrap-ci-gated-apply)"
  policy      = local.bootstrap_apply_guard_json

  lifecycle {
    precondition {
      condition     = length(jsonencode(jsondecode(local.bootstrap_apply_guard_json))) <= 6144
      error_message = "bootstrap-apply-guard exceeds the 6,144 B managed-policy limit (whitespace-stripped measure)."
    }
  }
}

resource "aws_iam_policy" "bootstrap_preview_guard" {
  name        = "bootstrap-preview-guard"
  description = "Deny everything but plan-time reads for bootstrap-preview (PLAN-bootstrap-ci-gated-apply)"
  policy      = local.bootstrap_preview_guard_json

  lifecycle {
    precondition {
      condition     = length(jsonencode(jsondecode(local.bootstrap_preview_guard_json))) <= 6144
      error_message = "bootstrap-preview-guard exceeds the 6,144 B managed-policy limit (whitespace-stripped measure)."
    }
  }
}

resource "aws_iam_role_policy_attachment" "bootstrap_apply_reads" {
  role       = aws_iam_role.bootstrap_apply.name
  policy_arn = aws_iam_policy.bootstrap_apply_reads.arn
}

resource "aws_iam_role_policy_attachment" "bootstrap_apply_write" {
  role       = aws_iam_role.bootstrap_apply.name
  policy_arn = aws_iam_policy.bootstrap_apply_write.arn
}

resource "aws_iam_role_policy_attachment" "bootstrap_apply_guard" {
  role       = aws_iam_role.bootstrap_apply.name
  policy_arn = aws_iam_policy.bootstrap_apply_guard.arn
}

resource "aws_iam_role_policy_attachment" "bootstrap_preview_reads" {
  role       = aws_iam_role.bootstrap_preview.name
  policy_arn = aws_iam_policy.bootstrap_apply_reads.arn
}

resource "aws_iam_role_policy_attachment" "bootstrap_preview_guard" {
  role       = aws_iam_role.bootstrap_preview.name
  policy_arn = aws_iam_policy.bootstrap_preview_guard.arn
}
