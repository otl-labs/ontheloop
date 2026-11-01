# github_ci_apply refresh-read surface (relocated read-only Sids, rec-2793) -- split from
# github_ci_apply.tf (Decision 166 terraform-class grandfather drain,
# PLAN-terraform-decompose-oidc-rename).
#
# rec-2793 / policy-architecture split (Decision NNN_PLACEHOLDER): the 11 READ-ONLY Sids below were
# MOVED verbatim out of local.github_ci_apply_policy_json into this customer-managed policy. The
# inline identity policy was at 10,156 B of the 10,240 B AWS hard limit (84 B of headroom), and the
# write-surface remediation adds ~2,384 B -- so the relocation is a PREREQUISITE for the fix, not an
# optimisation. A LimitExceeded on an inline policy is INVISIBLE to `terraform plan`; it surfaces only
# at apply. Governing principle: READS MOVE, AUTHORITY STAYS -- every iam: write Sid and BOTH Deny
# statements remain inline. Hoisted into a local so the lifecycle precondition below can
# self-reference the rendered JSON (a precondition cannot reference `self`).
locals {
  github_ci_apply_reads_policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # s3:GetBucketAcl + s3:GetBucketOwnershipControls are refresh-time reads the AWS provider
        # issues on aws_s3_bucket every plan; without them `terraform plan` fails AccessDenied
        # before the guard runs. Do not prune as "unused".
        Sid    = "DataLakeBucketManage"
        Effect = "Allow"
        Action = [
          "s3:ListBucket",
          "s3:GetBucketLocation",
          "s3:GetBucketVersioning",
          "s3:GetBucketPolicy",
          "s3:GetEncryptionConfiguration",
          "s3:GetBucketPublicAccessBlock",
          "s3:GetBucketTagging",
          "s3:GetAccelerateConfiguration",
          "s3:GetBucketRequestPayment",
          "s3:GetBucketLogging",
          "s3:GetLifecycleConfiguration",
          "s3:GetReplicationConfiguration",
          "s3:GetBucketObjectLockConfiguration",
          "s3:GetBucketCORS",
          "s3:GetBucketWebsite",
          "s3:GetBucketAcl",
          "s3:GetBucketOwnershipControls",
          # T2.43 gap: aws_s3_bucket_notification.data_lake_prod_triggers refresh-reads this.
          "s3:GetBucketNotification"
        ]
        Resource = [
          "arn:aws:s3:::agent-platform-data-lake",
          "arn:aws:s3:::agent-platform-ducklake-catalog-dr",
        ]
      },
      {
        # Consolidated IAM read-quartet for all roles terraform/personal references during plan:
        # branch, pr, plan, drift, ducklake roles. Separated from write actions
        # (IAMRoleReconcile, IAMRoleCreateBounded, IAMRoleWriteBounded) to keep the write-scope
        # auditable. Literal ARNs per the refresh-read convention (no cross-root dependency edges).
        # rec-2079: IAMCIPlanRoleRead + IAMPlatformRolesRead merged here; no separate Sid for each.
        # Decision 98 (GAP 3 fix): drift added as READ-ONLY refresh grant; the IAM-WRITE budget
        # (IAMRoleWriteBounded / IAMRoleCreateBounded) is unchanged -- in-budget role-create remains
        # gated to T2.25. New peer CI roles are admin-provisioned in terraform/personal and added
        # here as read-only grants; the pipeline does not mint them.
        # RETIREMENT ORDERING RULE (by design -- do not "tidy" this list ahead of a destroy): when a
        # role is retired, prune its ARN from this list ONLY AFTER its destroy has actually applied.
        # The two obligations are asymmetric in time. validate_ci_refresh_read_coverage stops
        # REQUIRING the ARN the moment the resource leaves terraform/personal -- i.e. in the very PR
        # that deletes it -- but the destroy itself still issues a refresh iam:GetRole against the
        # live role before deleting it. Pruning in the same PR therefore removes the grant the
        # pending destroy needs, and the apply AccessDenies before the guard runs. Two PRs, in this
        # order: (1) delete the resource, keep the ARN here; (2) after the destroy has applied,
        # prune the ARN. The agent-platform-probe-liveproof-role entry below is a live instance of
        # exactly this ordering.
        # T2.49 / DEP-12 (Decision 144): the four retired CI roles (plan, drift, ducklake-deploy,
        # prod-deploy) are replaced by two merged roles -- planner (plan+drift) and deploy
        # (ducklake-deploy+prod-deploy) -- so this list shrinks by two entries (net -2, helps the
        # rec-2793 headroom). Same read-only refresh-grant class as the retired roles had; the
        # pipeline does not mint them (admin-provisioned in terraform/personal/oidc.tf).
        # rec-4049 (Decision 225): the PlatformDev and PlatformAdmin ARNs were pruned under this
        # rule after terraform/personal's forget converged (2026-10-17, #1540).
        Sid    = "IAMRolesRead"
        Effect = "Allow"
        Action = [
          "iam:GetRole",
          "iam:GetRolePolicy",
          "iam:ListRolePolicies",
          "iam:ListAttachedRolePolicies",
        ]
        Resource = [
          "arn:aws:iam::${var.account_id}:role/agent-platform-github-ci-branch",
          "arn:aws:iam::${var.account_id}:role/agent-platform-github-ci-pr",
          "arn:aws:iam::${var.account_id}:role/agent-platform-github-ci-planner",
          "arn:aws:iam::${var.account_id}:role/agent-platform-github-ci-deploy",
          # Pre-staged ahead of the role's own creation (same precedent as the telemetry-writer entries
          # below): without it the gated apply's iam:GetRole after the create, and every refresh, fail closed.
          "arn:aws:iam::${var.account_id}:role/agent-platform-github-ci-trusted-plan",
          "arn:aws:iam::${var.account_id}:role/agent-platform-ducklake-catalog-dr",
          "arn:aws:iam::${var.account_id}:role/agent-platform-ducklake-writer",
          "arn:aws:iam::${var.account_id}:role/agent-platform-ducklake-reader",
          "arn:aws:iam::${var.account_id}:role/agent-platform-ducklake-maintenance",
          # T2.18 c9 split (same class as ducklake-deploy/prod-deploy above): the smoke exec role
          # must be refresh-readable, or every subsequent apply plan fails closed with AccessDenied.
          "arn:aws:iam::${var.account_id}:role/agent-platform-ducklake-maintenance-smoke",
          "arn:aws:iam::${var.account_id}:role/agent-platform-scheduled-agent-dispatcher",
          "arn:aws:iam::${var.account_id}:role/agent-platform-findings-processor",
          "arn:aws:iam::${var.account_id}:role/agent-platform-ops-compaction",
          # rec-2831 / DEP-02 (T2.48 c2, PLAN-t248-passrole-liveproof): pre-staged ahead of the
          # role's own creation, the established planner/deploy pre-add pattern (rec-2688; mirrors
          # how github-ci-drift's own ARN was added here at T2.24) -- so github_ci_apply can
          # refresh-read the throwaway DEP-02 live-proof role once its create PR lands. Added here
          # in the PassRole-completion PR; the matching oidc.tf planner-read entry is added in the
          # DEP-02 create PR and both entries are removed together in the DEP-02 revert PR.
          "arn:aws:iam::${var.account_id}:role/agent-platform-probe-liveproof-role",
          # Decision 213: pre-staged ahead of the role's own creation, the same precedent as the
          # probe-liveproof entry above. PLAN-telemetry-writer-function creates
          # agent-platform-ducklake-telemetry-writer; without this entry the gated apply's own
          # iam:GetRole after that create, and every later plan's refresh, fail closed.
          "arn:aws:iam::${var.account_id}:role/agent-platform-ducklake-telemetry-writer",
          # Same pre-stage for PLAN-telemetry-reader-and-smoke-twin's creates.
          "arn:aws:iam::${var.account_id}:role/agent-platform-ducklake-telemetry-writer-smoke",
          "arn:aws:iam::${var.account_id}:role/agent-platform-ducklake-telemetry-reader",
        ]
      },
      {
        # METADATA half of the deliberate two-Sid READ split (write-symmetry rule 2 / Decision 129
        # pt 2 as amended). SecretsManagerMetadataWrite creates secrets at secret:agent-platform-*
        # while the value-read Sid below enumerates four ARNs, so before this Sid existed the
        # pipeline could CREATE a secret it could not then refresh-READ: the create succeeds and the
        # NEXT plan AccessDenies on DescribeSecret -- a stranded pipeline, not a failed apply.
        #
        # WHY PREFIXING THESE IS NOT A WIDENING OF THE RATIFIED CONTROL: Decision 129 pt 2 keeps
        # Secrets Manager enumerated on the ground that "secrets return VALUES". That is a
        # GetSecretValue-class argument. Within Secrets Manager, GetSecretValue is the ONLY API that
        # returns secret material -- note that AWS deliberately named the batch form
        # BatchGetSecretValue rather than folding it under the Get* metadata pattern, so it too is
        # caught by name, not missed by it. Describe*, List* and GetResourcePolicy are provably
        # value-free, so prefixing them restores autonomy for a new agent-platform-* secret without
        # widening anything value-capable by one byte.
        #
        # INVARIANT -- RE-CHECK THIS BEFORE ADDING ANY VERB HERE: every secretsmanager action
        # matching Describe*, List* or GetResourcePolicy returns METADATA ONLY and never secret
        # material. If AWS ever ships a value-returning verb under one of those prefixes, this Sid
        # stops being value-free and must be re-enumerated (or that verb explicitly Denied) in the
        # SAME change -- a prefixed grant here is fully effective over every current AND future
        # agent-platform-* secret, with no review.
        #
        # Provider grounding (hashicorp/aws v5.100.0): aws_secretsmanager_secret's refresh calls
        # exactly DescribeSecret + GetResourcePolicy and NEVER GetSecretValue; value reads come only
        # from aws_secretsmanager_secret_version (resource + data source), which is what the
        # enumerated Sid below serves. scripts/checks/iam_tf/_read_coverage.py encodes both as
        # exact ALL-OF refresh-read sets so a wide Describe* here can never stand in for the value
        # read a secret_version genuinely needs.
        Sid    = "SecretsManagerMetadataRead"
        Effect = "Allow"
        Action = [
          "secretsmanager:Describe*",
          "secretsmanager:List*",
          "secretsmanager:GetResourcePolicy"
        ]
        Resource = ["arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:agent-platform-*"]
      },
      {
        # VALUE-CAPABLE half of the READ split -- ENUMERATED, NEVER PREFIXED. secretsmanager:
        # GetSecretValue is the only Secrets Manager read that returns secret material, so it is the
        # read Decision 129 pt 2 (as amended) actually constrains: value-capable reads stay
        # enumerated at exactly the four ARNs below, kept VERBATIM from the pre-split
        # SecretsManagerReadOnly Sid this replaces. The value-free metadata classes that Sid also
        # carried moved to the prefixed SecretsManagerMetadataRead above; the only net narrowing is
        # on neon-api-key-* (the one non-agent-platform-* ARN here), whose sole consumer is
        # scripts/ci/neon_api_key.sh -- a GetSecretValue call, not a metadata one.
        # Each ARN's lifecycle is human-owned / out-of-band
        # (docs/contracts/secret-material-handling.yaml, Decision 175 -- rehomed from the
        # Decision 37 precedent); CI reads these, never writes them. The writable DuckLake Neon
        # DSN secret keeps its own statement above.
        #   neon-api-key-*                              : Neon provider API key (Phase 0 out-of-band).
        #   agent-platform-terraform-personal-tfvars-* : tfvars sourcing at apply time.
        #   agent-platform-deepseek/anthropic-api-key-*: inference credential envelopes (admin-applied).
        Sid    = "SecretsManagerValueReadEnumerated"
        Effect = "Allow"
        Action = ["secretsmanager:GetSecretValue"]
        Resource = [
          "arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:neon-api-key-*",
          "arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:agent-platform-terraform-personal-tfvars-*",
          "arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:agent-platform-deepseek-api-key-*",
          "arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:agent-platform-anthropic-api-key-*",
        ]
      },
      {
        # Decision 237 (secrets-to-ssm-grants): VALUE reads on exactly the three /secret/ parameters the apply role
        # consumes, each by its own literal ARN (never a pattern; validate_secret_parameter_grants). neon-api-key is
        # fetched by scripts/ci/neon_api_key.sh, the tfvars by the plan and apply jobs, and the DSN is the one
        # Terraform-managed SecureString (its refresh decrypts it). The parameters exist only after the later plans.
        Sid    = "SecretParameterValueRead"
        Effect = "Allow"
        Action = ["ssm:GetParameter", "ssm:ListTagsForResource"]
        Resource = [
          "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/secret/neon-api-key",
          "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/secret/agent-platform-terraform-personal-tfvars",
          "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/secret/ducklake-neon-catalog-dsn",
        ]
      },
      {
        # Per-service read-wildcard closure: logs:Describe*/List* on * closes the iterative-discovery
        # anti-pattern for CloudWatch Logs refresh reads. Resource: "*" required (logs:DescribeLogGroups
        # has no resource-level scoping).
        Sid      = "CloudWatchLogsRead"
        Effect   = "Allow"
        Action   = ["logs:Describe*", "logs:List*"]
        Resource = ["*"]
      },
      {
        # Per-service read-wildcard closure: lambda:Get*/List* covers the full refresh-read set
        # incl. GetFunctionConcurrency / GetRuntimeManagementConfig. Do not prune.
        # Resource axis (Decision 129 / T2.43 rec-2702 anti-recurrence): the function
        # ARN is broadened from four enumerated ducklake-* entries to the account-wide
        # function:agent-platform-* prefix so a future agent-platform-* Lambda auto-covers without
        # a bootstrap out-of-band grant edit.
        # P1-3 (gap sweep): the layer axis gets the SAME treatment -- layer:agent-platform-* (and its
        # :* version suffix) is added and agent-platform-* adopted as the layer naming convention,
        # with the three ducklake-* literals RETAINED because the existing layers carry those names.
        # This matters on the READ side too, not just the write side: _literal_or_prefix_match would
        # let a new layer named agent-platform-mylayer match the FUNCTION prefix
        # function:agent-platform-* and pass read coverage, so a new layer would be a SILENT read gap
        # that surfaces only as an apply-time AccessDenied. Deliberately not layer:* (Decision 143).
        Sid    = "LambdaRead"
        Effect = "Allow"
        Action = ["lambda:Get*", "lambda:List*"]
        Resource = [
          "arn:aws:lambda:${var.aws_region}:${var.account_id}:layer:agent-platform-*",
          "arn:aws:lambda:${var.aws_region}:${var.account_id}:layer:agent-platform-*:*",
          "arn:aws:lambda:${var.aws_region}:${var.account_id}:layer:ducklake-pgclient",
          "arn:aws:lambda:${var.aws_region}:${var.account_id}:layer:ducklake-pgclient:*",
          "arn:aws:lambda:${var.aws_region}:${var.account_id}:layer:ducklake-deps",
          "arn:aws:lambda:${var.aws_region}:${var.account_id}:layer:ducklake-deps:*",
          "arn:aws:lambda:${var.aws_region}:${var.account_id}:layer:ducklake-extensions",
          "arn:aws:lambda:${var.aws_region}:${var.account_id}:layer:ducklake-extensions:*",
          "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:agent-platform-*",
        ]
      },
      {
        # Refresh-time reads the provider issues on aws_cloudwatch_event_rule every plan.
        # Per-service read-wildcard closure: events:Describe*/List* closes the anti-pattern.
        # Resource axis (Decision 129 / T2.43 rec-2702 anti-recurrence): broadened from five
        # enumerated ducklake-* rule ARNs to the account-wide rule/agent-platform-* prefix so a
        # future agent-platform-* EventBridge rule auto-covers without a bootstrap grant edit.
        Sid    = "EventBridgeRead"
        Effect = "Allow"
        Action = ["events:Describe*", "events:List*"]
        Resource = [
          "arn:aws:events:${var.aws_region}:${var.account_id}:rule/agent-platform-*",
        ]
      },
      {
        # Refresh-time reads the provider issues on aws_sns_topic every plan.
        # Per-service read-wildcard closure: sns:Get*/List* closes the anti-pattern.
        Sid      = "SNSRead"
        Effect   = "Allow"
        Action   = ["sns:Get*", "sns:List*"]
        Resource = ["arn:aws:sns:${var.aws_region}:${var.account_id}:agent-platform-alerts"]
      },
      {
        # sns:GetSubscriptionAttributes does NOT support resource-level permissions (SNS defines no
        # subscription IAM resource type); Resource: "*" is required. The provider issues it as a
        # refresh-read on aws_sns_topic_subscription every plan. Do not prune.
        Sid      = "SNSSubscriptionRead"
        Effect   = "Allow"
        Action   = ["sns:GetSubscriptionAttributes"]
        Resource = ["*"]
      },
      {
        # cloudwatch:DescribeAlarms has no resource-level scoping; Resource: "*" is required.
        # Per-service read-wildcard closure: cloudwatch:Describe*/List* closes the anti-pattern.
        Sid      = "CloudWatchAlarmsRead"
        Effect   = "Allow"
        Action   = ["cloudwatch:Describe*", "cloudwatch:List*"]
        Resource = ["*"]
      },
      {
        # Refresh-time READ on every agent-platform SSM parameter the provider issues on each plan.
        # Per-service read-wildcard closure + rec-2276 SSM List* completion: Get*/Describe*/List*
        # scoped to /agent-platform/* (not ssm:* and not all parameters).
        Sid      = "SSMParameterRead"
        Effect   = "Allow"
        Action   = ["ssm:Get*", "ssm:Describe*", "ssm:List*"]
        Resource = ["arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/agent-platform/*"]
      },
      {
        # ssm:DescribeParameters has no resource-level scoping -- Resource: "*" is required (a
        # parameter-ARN scope evaluates as implicitDeny). Mirrors the cloudwatch:DescribeAlarms /
        # logs:DescribeLogGroups Resource: "*" convention. Do not prune.
        Sid      = "SSMDescribeParameters"
        Effect   = "Allow"
        Action   = ["ssm:DescribeParameters"]
        Resource = ["*"]
      }
    ]
  })
}

resource "aws_iam_policy" "github_ci_apply_reads" {
  name        = "agent-platform-github-ci-apply-reads"
  description = "Refresh-time read surface for the CD apply role (relocated from the inline policy; reads move, authority stays)"
  policy      = local.github_ci_apply_reads_policy_json

  lifecycle {
    precondition {
      # Managed-policy hard limit is 6,144 B (distinct from the 10,240 B inline-policy limit).
      # A LimitExceeded here is invisible to `terraform plan` and surfaces only at apply.
      condition     = length(jsonencode(jsondecode(local.github_ci_apply_reads_policy_json))) <= 6144
      error_message = "github_ci_apply reads policy exceeds the 6,144 B managed-policy limit."
    }
  }
}

resource "aws_iam_role_policy_attachment" "github_ci_apply_reads" {
  role       = aws_iam_role.github_ci_apply.name
  policy_arn = aws_iam_policy.github_ci_apply_reads.arn
}
