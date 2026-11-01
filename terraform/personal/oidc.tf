# GitHub Actions OIDC -> personal-account IAM roles (PLAN-public-migration Step 8, CD.21).
#
# Replaces the retired self-hosted EC2 runner (Decision 68 -> CD.21). CI on GitHub-hosted
# ubuntu-latest assumes these roles via OIDC -- no static credentials, no IAM users (Decision 36/37
# scoped to the work account; the personal account has no such SCP, confirmed by the Phase A OIDC
# feasibility probe under agent_platform_admin).
#
# Two roles, split by trust:
#   branch (write) -- refs/heads/main + refs/heads/agent/*  -> main-validate / ci-rca portal writes
#   pr (read-only)  -- refs/pull/*                          -> PR-context read queries
#   trusted-plan    -- tf-trusted-plan Environment + job_workflow_ref -> speculative plan (oidc_trusted_plan_role.tf)
# The account ID in ARNs comes from var.account_id (gitignored tfvars); never a committed literal.

locals {
  # PRE-STAGED STATE (Decision 218 / PLAN-oidc-owner-segment-prestage): the list carries the current
  # segment and, additively beside it, the otl-labs/ontheloop target segment, so the GitHub
  # organisation transfer cannot lock CI out. The current segment is removed only by the contract's
  # predecessor_removal phase, with live proof of the target first. Every sub site below MUST iterate
  # this list -- never re-introduce a scalar `github_repo` local. This list MUST stay identical to
  # terraform/bootstrap's; the two roots cannot reference each other, so agreement is enforced by
  # tests/checks/iam_tf/test_oidc_trust_slug_invariants.py and validate_github_org_transfer.
  #
  # CONTRACTED STEADY STATE (history, Decision 172 / PLAN-oidc-trust-contraction-closure): the
  # dual-slug transition (Decision 171 / PLAN-repo-rename-relicense) ended when both pre-rename
  # name-only entries, unmintable post-rename, were removed with live proof.
  #
  # IMMUTABLE-SUBJECT ENTRY (Decision 172): a repo SEGMENT of the form
  # "OWNER@OWNER-ID/REPO@REPO-ID", never a full "repo:" sub prefix (every sub site below already
  # renders "repo:${repo}:<suffix>"; a "repo:"-prefixed entry here would render "repo:repo:..."
  # and match nothing). GitHub mints this immutable numeric-id subject for any repository renamed
  # or transferred after 2026-07-15 -- benjamin-blake/theseus renamed at 2026-08-15T12:55:57Z, then
  # moved to otl-labs/ontheloop in one transfer-and-rename (Decision 218), and now presents ONLY
  # this shape under the otl-labs segment. The repository id -- not the name -- is the durable
  # identity: a rename changes only the REPO name half and a transfer the OWNER half (login and
  # id), so Decision 172 point 2's pre-stage playbook adds the future identity's immutable entry to
  # this list ADDITIVELY, BEFORE the move lands, which is what keeps a future rename or transfer
  # from repeating the 2026-08-15 outage. Never narrow this list to zero entries, and never remove
  # an entry without live proof of the replacement first -- the exact discipline this contraction
  # itself followed.
  github_repos = [
    "benjamin-blake@217728084/theseus@1252427466",
    "otl-labs@330441623/ontheloop@1252427466",
  ]

  # T2.49 c2 hardening item 3 (single-source, DEP-12 / Decision 144): the RESERVED session-name
  # that discriminates the planner role's fail-closed convergence-write path. Referenced at
  # EXACTLY 6 coupled sites -- changing this value requires updating all six in the same commit:
  #   1. github_ci_planner trust, main-sub statement: sts:RoleSessionName StringEquals
  #      (oidc_pipeline_roles.tf)
  #   2. github_ci_planner trust, pr-sub statement:   sts:RoleSessionName StringNotEquals
  #      (oidc_pipeline_roles.tf)
  #   3. github_ci_planner policy, ConvergenceRecordWrite: aws:userid StringLike "*:<this>"
  #      (oidc_pipeline_roles.tf)
  #   4. .github/workflows/terraform-drift.yml: configure-aws-credentials role-session-name (a
  #      workflow literal outside Terraform's reach -- MUST equal this value verbatim)
  #   5. github_ci_planner policy, DriftSessionSecretValueRead: aws:userid StringLike "*:<this>"
  #      (oidc_pipeline_roles.tf)
  #   6. github_ci_planner policy, DriftSessionSecretParameterRead: aws:userid StringLike "*:<this>"
  #      (oidc_pipeline_roles.tf)
  convergence_writer_session_name = "tf-drift-convergence-writer"
}

resource "aws_iam_openid_connect_provider" "github_actions" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  # GitHub Actions OIDC root CA thumbprint -- a public, well-known value (not a secret).
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"] # pragma: allowlist secret
}

# ---------------------------------------------------------------------------
# Shared refresh-read policy fragments (T2.34 / Decision 104): DRY composition so the
# CI-role refresh-read surface cannot silently drift between peer roles (rec-2363 and
# predecessors rec-2223/2251/2276). Every CI role that invokes the DuckLake reader/writer
# composes ci_ssm_refresh_read via source_policy_documents rather than re-declaring the SSM
# statements inline (validated credential-free by
# scripts/checks/iam_tf/validate_invoke_implies_resolve.py, T2.34:c2); github_ci_planner
# additionally composes the shared 19-statement refresh-read surface via
# ci_full_refresh_read (which itself sources ci_ssm_refresh_read). IAM read statements stay
# enumerated with literal ARNs (Decision 35/98) -- composition relocates statements, it never
# collapses them into a wildcard.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "ci_ssm_refresh_read" {
  statement {
    # SSM parameter refresh-time reads on /agent-platform/*. Sourced by every CI role that
    # invokes the DuckLake reader/writer (branch, pr, plan via ci_full_refresh_read, drift via
    # ci_full_refresh_read).
    sid       = "SSMParameterRead"
    effect    = "Allow"
    actions   = ["ssm:Get*", "ssm:Describe*", "ssm:List*"]
    resources = ["arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/agent-platform/*"]
  }

  statement {
    # ssm:DescribeParameters has no resource-level scoping; Resource: "*" required.
    sid       = "SSMDescribeParameters"
    effect    = "Allow"
    actions   = ["ssm:DescribeParameters"]
    resources = ["*"]
  }
}

data "aws_iam_policy_document" "ci_full_refresh_read" {
  # Composes the shared SSM fragment so plan/drift never re-declare it inline.
  source_policy_documents = [data.aws_iam_policy_document.ci_ssm_refresh_read.json]

  statement {
    # Read tfstate to run a real speculative plan / drift plan. Read-only: NO PutObject /
    # DeleteObject on the state object itself. Byte-identical between plan and drift (verified
    # 2026-06-05); composed here rather than declared per-role.
    sid       = "TfstateRead"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.data_lake.arn}/tfstate/personal/*"]
  }

  statement {
    # Bucket-level access + refresh-time bucket-config reads the AWS provider issues on every
    # plan for all managed aws_s3_bucket resources.
    sid    = "DataLakeBucketRead"
    effect = "Allow"
    actions = [
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
    resources = [
      aws_s3_bucket.data_lake.arn,
      aws_s3_bucket.ducklake_catalog_dr.arn,
    ]
  }

  statement {
    # DynamoDB refresh-time reads the provider issues on aws_dynamodb_table every plan.
    # No Create/Update/Put/Delete (write actions).
    sid    = "DynamoDBRead"
    effect = "Allow"
    actions = [
      "dynamodb:DescribeTable",
      "dynamodb:DescribeContinuousBackups",
      "dynamodb:DescribeTimeToLive",
      "dynamodb:ListTagsOfResource"
    ]
    resources = [aws_dynamodb_table.counters.arn]
  }

  statement {
    # IAM read-quartet the provider issues on each managed aws_iam_role during plan.
    # Scoped to the managed CI roles -- read-only (no PutRolePolicy / UpdateAssumeRolePolicy).
    # Literal ARNs per the IAMPlatformRolesRead convention (refresh-read grants do not create
    # Terraform dependency edges onto the resources they read). Decision 35/98: enumerated,
    # never a service or path wildcard on iam: read actions. T2.49 / DEP-12 (Decision 144): the
    # four retired CI roles (plan, drift, ducklake-deploy, prod-deploy) are replaced by two
    # merged roles -- planner (plan+drift) and deploy (ducklake-deploy+prod-deploy) -- so this
    # list shrinks by two entries (net -2, helps the rec-2793 headroom). planner/deploy are
    # listed so github_ci_apply can refresh-read them once they enter terraform/personal state,
    # the same class of grant the retired roles had (rec-2688; mirrors how github-ci-drift's own
    # ARN was added here when T2.24 landed).
    sid    = "IAMCIRolesRead"
    effect = "Allow"
    actions = [
      "iam:GetRole",
      "iam:GetRolePolicy",
      "iam:ListRolePolicies",
      "iam:ListAttachedRolePolicies"
    ]
    resources = [
      "arn:aws:iam::${var.account_id}:role/agent-platform-github-ci-branch",
      "arn:aws:iam::${var.account_id}:role/agent-platform-github-ci-pr",
      "arn:aws:iam::${var.account_id}:role/agent-platform-github-ci-apply",
      "arn:aws:iam::${var.account_id}:role/agent-platform-github-ci-planner",
      "arn:aws:iam::${var.account_id}:role/agent-platform-github-ci-deploy",
      # rec-4636: the trusted speculative-plan role (oidc_trusted_plan_role.tf).
      "arn:aws:iam::${var.account_id}:role/agent-platform-github-ci-trusted-plan"
    ]
  }

  statement {
    # IAM read-quartet on the agent-platform-* execution roles terraform/personal manages. Decision 35/98:
    # enumerated literal ARNs, never a wildcard. PlatformDev and PlatformAdmin are bootstrap-owned and never
    # CI-readable (Decision 225).
    sid    = "IAMPlatformRolesRead"
    effect = "Allow"
    actions = [
      "iam:GetRole",
      "iam:GetRolePolicy",
      "iam:ListRolePolicies",
      "iam:ListAttachedRolePolicies"
    ]
    resources = [
      "arn:aws:iam::${var.account_id}:role/agent-platform-ducklake-catalog-dr",
      "arn:aws:iam::${var.account_id}:role/agent-platform-ducklake-writer",
      "arn:aws:iam::${var.account_id}:role/agent-platform-ducklake-reader",
      "arn:aws:iam::${var.account_id}:role/agent-platform-ducklake-maintenance",
      # T2.18 c9 split gap (same class as rec-2688 for ducklake-deploy): the smoke exec role must be
      # refresh-readable by github_ci_planner once it enters terraform/personal state, or every
      # subsequent plan against this module fails closed with AccessDenied.
      "arn:aws:iam::${var.account_id}:role/agent-platform-ducklake-maintenance-smoke",
      # Telemetry writer exec role (same refresh-read class as the maintenance-smoke entry above).
      "arn:aws:iam::${var.account_id}:role/agent-platform-ducklake-telemetry-writer",
      # Pre-staged for PLAN-telemetry-reader-and-smoke-twin's creates (the telemetry-writer precedent), so the
      # planner's refresh reads never fail closed.
      "arn:aws:iam::${var.account_id}:role/agent-platform-ducklake-telemetry-writer-smoke",
      "arn:aws:iam::${var.account_id}:role/agent-platform-ducklake-telemetry-reader",
      # T2.43 gap (same class as rec-2688 for ducklake-deploy): these prod-class execution
      # roles must be refresh-readable by github_ci_planner once they enter terraform/personal
      # state, or every subsequent plan against this module fails closed with AccessDenied.
      "arn:aws:iam::${var.account_id}:role/agent-platform-scheduled-agent-dispatcher",
      "arn:aws:iam::${var.account_id}:role/agent-platform-findings-processor"
    ]
  }

  statement {
    # OIDC provider refresh-read.
    sid       = "OIDCProviderRead"
    effect    = "Allow"
    actions   = ["iam:GetOpenIDConnectProvider"]
    resources = ["arn:aws:iam::${var.account_id}:oidc-provider/token.actions.githubusercontent.com"]
  }

  statement {
    # Lambda refresh-time reads. Layer ARNs stay enumerated (mixed ducklake-*/data-pipeline-*
    # naming); function ARNs use the account-wide function:agent-platform-* prefix (Decision 129 /
    # T2.43 rec-2702 anti-recurrence) so a future agent-platform-* function auto-covers -- keeps
    # this role's data-plane read surface identical to github_ci_apply's (the parity the
    # validate_ci_refresh_read_coverage verifier relies on).
    sid     = "LambdaRead"
    effect  = "Allow"
    actions = ["lambda:Get*", "lambda:List*"]
    resources = [
      "arn:aws:lambda:${var.aws_region}:${var.account_id}:layer:ducklake-pgclient",
      "arn:aws:lambda:${var.aws_region}:${var.account_id}:layer:ducklake-pgclient:*",
      "arn:aws:lambda:${var.aws_region}:${var.account_id}:layer:ducklake-deps",
      "arn:aws:lambda:${var.aws_region}:${var.account_id}:layer:ducklake-deps:*",
      "arn:aws:lambda:${var.aws_region}:${var.account_id}:layer:ducklake-extensions",
      "arn:aws:lambda:${var.aws_region}:${var.account_id}:layer:ducklake-extensions:*",
      "arn:aws:lambda:${var.aws_region}:${var.account_id}:layer:data-pipeline-deps",
      "arn:aws:lambda:${var.aws_region}:${var.account_id}:layer:data-pipeline-deps:*",
      "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:agent-platform-*",
    ]
  }

  statement {
    # EventBridge refresh-time reads. Broadened to the account-wide rule/agent-platform-* prefix
    # (Decision 129 / T2.43 rec-2702 anti-recurrence) -- mirrors the LambdaRead broadening above.
    sid     = "EventBridgeRead"
    effect  = "Allow"
    actions = ["events:Describe*", "events:List*"]
    resources = [
      "arn:aws:events:${var.aws_region}:${var.account_id}:rule/agent-platform-*",
    ]
  }

  statement {
    # SNS refresh-time reads.
    sid       = "SNSRead"
    effect    = "Allow"
    actions   = ["sns:Get*", "sns:List*"]
    resources = [aws_sns_topic.alerts.arn]
  }

  statement {
    # sns:GetSubscriptionAttributes has no resource-level scoping; Resource: "*" required.
    sid       = "SNSSubscriptionRead"
    effect    = "Allow"
    actions   = ["sns:GetSubscriptionAttributes"]
    resources = ["*"]
  }

  statement {
    # CloudWatch refresh-time reads; cloudwatch:DescribeAlarms has no resource-level scoping.
    sid       = "CloudWatchAlarmsRead"
    effect    = "Allow"
    actions   = ["cloudwatch:Describe*", "cloudwatch:List*"]
    resources = ["*"]
  }

  statement {
    # CloudWatch Logs refresh-time reads; logs:DescribeLogGroups has no resource-level scoping.
    sid       = "CloudWatchLogsRead"
    effect    = "Allow"
    actions   = ["logs:Describe*", "logs:List*"]
    resources = ["*"]
  }

  statement {
    # Tfvars sourcing: plan/drift fetch this secret to materialise terraform.personal.tfvars.
    # Read-only -- lifecycle is human-owned.
    sid       = "SecretsManagerTfvarsRead"
    effect    = "Allow"
    actions   = ["secretsmanager:Describe*", "secretsmanager:Get*"]
    resources = ["arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:agent-platform-terraform-personal-tfvars-*"]
  }

  statement {
    # DuckLake Neon catalog DSN -- refresh-time metadata only. The DSN name does not match the agent-platform-*
    # metadata prefix, so this statement keeps validate_ci_refresh_read_coverage green. The value read is held
    # by DriftSessionSecretValueRead (oidc_pipeline_roles.tf), conditioned on the reserved drift session.
    sid       = "SecretsManagerDuckLakeNeonDSNRead"
    effect    = "Allow"
    actions   = ["secretsmanager:DescribeSecret", "secretsmanager:GetResourcePolicy"]
    resources = ["arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:ducklake-neon-catalog-dsn-*"]
  }

  statement {
    # Decision 237: the tfvars SecureString parameter, read by every plan to materialise terraform.personal.tfvars.
    # The one value read of a /secret/ parameter that validate_planner_secret_value_reads allows without a session condition.
    sid       = "SecretParameterTfvarsRead"
    effect    = "Allow"
    actions   = ["ssm:GetParameter"]
    resources = ["arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/secret/agent-platform-terraform-personal-tfvars"]
  }

  statement {
    # Decision 237: refresh of aws_ssm_parameter.ducklake_neon_catalog_dsn reads its tags. Metadata only; the value read is
    # DriftSessionSecretParameterRead (oidc_pipeline_roles.tf), conditioned on the reserved drift session.
    sid       = "SecretParameterMetadataRead"
    effect    = "Allow"
    actions   = ["ssm:ListTagsForResource"]
    resources = ["arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/secret/ducklake-neon-catalog-dsn"]
  }

  statement {
    # PLANNER-SIDE MIRROR of the apply role's SecretsManagerMetadataRead
    # (terraform/bootstrap/github_ci_apply.tf). Without it the one-PR autonomy claim fails
    # asymmetrically: a PR adding a new agent-platform-* secret would be write- and read-covered on
    # the apply role but NOT on this plan-capable role, so the speculative plan (and the hourly
    # drift plan) would AccessDeny on the new secret's DescribeSecret while the merge-time apply
    # succeeded -- the per-secret Sids above each cover exactly one existing ARN and cover nothing
    # new. The prefix closes that gap for every future agent-platform-* secret with no further
    # grant edit (the rec-2702 resource-axis anti-recurrence).
    # Value-free BY CONSTRUCTION and therefore NOT a widening of Decision 129 pt 2: within Secrets
    # Manager only GetSecretValue returns secret material (AWS named the batch form
    # BatchGetSecretValue, outside the Get* metadata pattern), so Describe*/List*/GetResourcePolicy
    # cannot read a value. This role's value-capable reads stay in the tfvars Sid above and
    # DriftSessionSecretValueRead (oidc_pipeline_roles.tf) -- deliberately NOT restated here. hashicorp/aws v5.100.0 grounding:
    # aws_secretsmanager_secret refreshes with DescribeSecret + GetResourcePolicy only.
    sid     = "SecretsManagerMetadataRead"
    effect  = "Allow"
    actions = ["secretsmanager:Describe*", "secretsmanager:List*", "secretsmanager:GetResourcePolicy"]
    resources = [
      "arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:agent-platform-*",
    ]
  }
}
