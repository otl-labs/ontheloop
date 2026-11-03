# github_ci_apply identity policy (the write-authority Sids) -- split from github_ci_apply.tf
# (Decision 166 terraform-class grandfather drain, PLAN-terraform-decompose-oidc-rename).
#
# rec-2793 (DEP-01 anti-recurrence): hoisted out of the aws_iam_role_policy resource's inline
# `policy = jsonencode({...})` attribute so the lifecycle precondition below can self-reference
# the rendered JSON (a precondition cannot reference `self` -- that is postcondition-only).
locals {
  github_ci_apply_policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Terraform S3 backend: read/write the sandbox state object + native lock file (use_lockfile
        # writes a sibling .tflock object under the same key prefix). Scoped to the tfstate prefix.
        Sid    = "TerraformStateBackend"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject"
        ]
        Resource = ["arn:aws:s3:::agent-platform-data-lake/tfstate/personal/*"]
      },
      {
        # Data-plane object IO the module's resources require during apply.
        Sid    = "DataLakeObjectIO"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject"
        ]
        Resource = ["arn:aws:s3:::agent-platform-data-lake/*"]
      },
      {
        # CD.35 / T2.20 convergence record (the server-side anti-masking anchor). Among the CI roles
        # the apply identity is A writer of the durable convergence record -- the integrity anchor the
        # design rests on (a commit status alone is spoofable). github_ci_planner's main-sub
        # statement joins the sanctioned writer set at T2.49 / DEP-12 (its own inline
        # ConvergenceRecordWrite in terraform/personal/oidc_pipeline_roles.tf). Enforced at the IAM
        # layer: this grant + the planner main-sub's grant + the explicit DenyConvergenceRecordWrite
        # on github_ci_branch + the PR role's read-only S3ReadConvergenceRecord = the two-member
        # {apply, planner main-sub} writer set among CI roles.
        Sid    = "ConvergenceRecordWrite"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject"
        ]
        Resource = ["arn:aws:s3:::agent-platform-data-lake/convergence/personal/*"]
      },
      {
        # P0-5 (gap sweep): terraform/personal manages the two buckets' CONFIGURATION through six
        # aws_s3_bucket_* resource types (versioning, server_side_encryption_configuration,
        # public_access_block, lifecycle_configuration, policy, notification) plus aws_s3_bucket
        # itself, and the apply role held only the matching refresh READS (DataLakeBucketManage, now
        # in the reads policy) -- every one of these Put verbs AccessDenies today. The write set
        # mirrors, verb for verb, the empirically-validated PlatformAdmin DataLakeBucketManage /
        # CatalogDrBucketManage grants in terraform/personal/platform_roles.tf, which is the identity
        # that has actually applied this module. s3:PutBucketTagging is required because the
        # provider's default_tags block (terraform/personal/main.tf) forces a tag-on-create call on
        # every taggable resource -- the same structural coupling that caused rec-2842 for TagRole.
        # P2 additions: s3:DeleteBucketPolicy (aws_s3_bucket_policy destroy/replace symmetry -- the
        # provider deletes then re-puts on a policy change) and s3:CreateBucket (bucket re-create on
        # a greenfield or replaced bucket). s3:DeleteBucket is DENIED BY DESIGN and stays admin-tier:
        # this bucket holds tfstate, the saved tfplan artifacts AND the convergence record, so a
        # CD-executable bucket delete would let one approved apply destroy the platform's own
        # integrity anchor (recorded in docs/contracts/iam-simulate-fixture.yaml). Scoped to the two
        # literal bucket ARNs -- the same Resource list the read Sid carries (never s3:* on "*").
        Sid    = "DataLakeBucketConfigWrite"
        Effect = "Allow"
        Action = [
          "s3:CreateBucket",
          "s3:PutBucketVersioning",
          "s3:PutEncryptionConfiguration",
          "s3:PutBucketPublicAccessBlock",
          "s3:PutLifecycleConfiguration",
          "s3:PutBucketPolicy",
          "s3:DeleteBucketPolicy",
          "s3:PutBucketNotification",
          "s3:PutBucketTagging"
        ]
        Resource = [
          "arn:aws:s3:::agent-platform-data-lake",
          "arn:aws:s3:::agent-platform-ducklake-catalog-dr",
        ]
      },
      {
        # DescribeContinuousBackups/DescribeTimeToLive are refresh-time reads the provider issues on
        # aws_dynamodb_table every plan (PITR + TTL status); without them `terraform plan` fails
        # AccessDenied before the guard runs. Do not prune as "unused".
        Sid    = "DynamoDBCounters"
        Effect = "Allow"
        Action = [
          "dynamodb:DescribeTable",
          "dynamodb:DescribeContinuousBackups",
          "dynamodb:DescribeTimeToLive",
          "dynamodb:CreateTable",
          "dynamodb:UpdateTable",
          # P2 destroy symmetry (gap sweep): the provider's aws_dynamodb_table destroy calls
          # dynamodb:DeleteTable. CreateTable was granted without it, so a guard-gated counters-table
          # retirement AccessDenies AFTER human approval. Scoped to the existing enumerated table ARN.
          "dynamodb:DeleteTable",
          "dynamodb:TagResource",
          "dynamodb:UntagResource",
          "dynamodb:ListTagsOfResource",
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem"
        ]
        Resource = ["arn:aws:dynamodb:${var.aws_region}:${var.account_id}:table/agent-platform-counters"]
      },
      {
        # TRUST-ONLY (Decision 143 clause 1, worst-verb scoping -- a standing rule that needs no
        # other control's status as a contingency): iam:UpdateAssumeRolePolicy on ENUMERATED roles
        # ONLY -- the Resource is deliberately NOT widened to role/agent-platform-*. A widened trust
        # Resource would silently grant a fleet-wide trust rewrite (any agent-platform-* role's
        # trust policy); Decision 143 clause 1 forbids that for a worst-verb like this one on its
        # own terms, regardless of what other controls exist alongside it. The enforcement primitive
        # is the TestIAMRoleReconcileScope pin test in
        # tests/checks/iam_tf/test_iam_role_reconcile_trust_scope.py, a standing guard pinning this
        # Resource set to exactly {branch, pr} -- a re-widening must be a reviewed edit to that
        # test, never a quiet comment nobody enforces. The non-trust role metadata/lifecycle verbs
        # that used to share this Sid (TagRole/UntagRole) are split OUT into the prefix-scoped
        # IAMRoleMetadataWrite Sid below -- they are non-escalating role-metadata writes, not trust,
        # so they get the wider agent-platform-* prefix while trust stays fleet-narrow at exactly
        # these enumerated roles.
        Sid    = "IAMRoleReconcile"
        Effect = "Allow"
        Action = [
          "iam:UpdateAssumeRolePolicy"
        ]
        Resource = [
          "arn:aws:iam::${var.account_id}:role/agent-platform-github-ci-branch",
          "arn:aws:iam::${var.account_id}:role/agent-platform-github-ci-pr",
        ]
      },
      {
        # DEP-02 (Decision 144, rec-2842): prefix-scoped role metadata/lifecycle risk-class Sid, at
        # the SAME role/agent-platform-* prefix as IAMRoleCreateBounded's iam:CreateRole below (Fable
        # best-practice consult: verb-FAMILY-wildcards-under-boundary -- the identity policy's
        # granularity unit is per-risk-class, not per-verb; Decision 144). TagRole/UntagRole moved
        # here from the (now trust-only) IAMRoleReconcile Sid above: the AWS provider's default_tags
        # block (terraform/personal/main.tf) forces a TagRole call on EVERY taggable resource's
        # create, so iam:CreateRole@role/agent-platform-* structurally REQUIRES iam:TagRole at the
        # SAME prefix, or every new agent-platform-* role's create AccessDenies -- the exact rec-2842
        # recurrence (run 30126122217; the first role ever minted through the gated path). UntagRole
        # covers tag-drift reconcile. UpdateRoleDescription is folded in from the retired
        # IAMRoleDescriptionWrite Sid (DEP-01 / rec-2757) -- same risk class, same prefix, no reason
        # to keep it a separate Sid. UpdateRole is added proactively (Fable's predicted next gap): it
        # covers max_session_duration edits, which AccessDeny at both the identity and boundary layers
        # today with no covering grant. None of these four verbs is trust, inline-policy, or
        # boundary-modifying -- they carry no escalation risk equivalent to IAMRoleReconcile's trust
        # verb, so the prefix-scoped grant is safe (Decision 143: only worst-verb-scoped verbs like
        # iam:UpdateAssumeRolePolicy / iam:PassRole / the boundary-edit verbs stay individually narrow).
        # Decision 225 (rec-4049): the metadata class is role/agent-platform-* only. Once Decision 223 moved
        # PlatformDev to terraform/bootstrap, no CI identity holds an IAM grant on PlatformDev or PlatformAdmin
        # (Decision 225 clause 1; PlatformAdmin's exclusion is Decision 180 clause 3). Pinned by
        # tests/checks/iam_tf/test_platform_role_metadata_admin_scope.py.
        Sid    = "IAMRoleMetadataWrite"
        Effect = "Allow"
        Action = [
          "iam:TagRole",
          "iam:UntagRole",
          "iam:UpdateRole",
          "iam:UpdateRoleDescription"
        ]
        Resource = [
          "arn:aws:iam::${var.account_id}:role/agent-platform-*",
        ]
      },
      {
        # In-budget CreateRole: the pipeline may only create roles that carry the authority budget
        # (T2.23 EC4). iam:PermissionsBoundary propagation condition forces the budget ARN to be
        # specified on every role the pipeline creates. An unbounded role-create is implicitly denied
        # -- no unconditional Allow for iam:CreateRole exists in this policy. DEP-02 / Decision 144:
        # Resource narrowed from role/* to role/agent-platform-* (the pipeline mints only
        # boundary-carrying agent-platform-* roles; PlatformDev/PlatformAdmin are admin-created).
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
        # In-budget PutRolePolicy/AttachRolePolicy. DEP-02 / Decision 144: Resource widened from the
        # two enumerated CI roles (branch + pr) to the boundary-carrying agent-platform-* prefix,
        # extending Decision 129's per-service agent-platform-* read prefix to writes. The apply role's
        # own ARN -- which the widened prefix now matches -- is carved out by the explicit
        # DenySelfInlinePolicyWrite Deny below (retains the T2.23 self-grant break). Condition: target
        # role must carry the authority budget (propagation). The machine-readable mirror of the
        # managed-role prefix + resource types + actions lives in terraform/bootstrap/authority_budget.json
        # v2 (Decision 92 point 5). The guard (scripts/terraform_apply_guard.py) reads that table and
        # auto-applies in-budget inline-policy / attachment CREATE+UPDATE on managed boundary-carrying
        # agent-platform-* roles (self-excluding the apply role); role CREATES, trust diffs, destroys,
        # and out-of-budget changes still route to the gated-apply Environment. Defense-in-depth at the
        # IAM layer (T2.23 EC4).
        Sid    = "IAMRoleWriteBounded"
        Effect = "Allow"
        Action = [
          "iam:PutRolePolicy",
          "iam:AttachRolePolicy",
          "iam:PutRolePermissionsBoundary"
        ]
        Resource = [
          "arn:aws:iam::${var.account_id}:role/agent-platform-*",
        ]
        Condition = {
          StringEquals = {
            "iam:PermissionsBoundary" = "arn:aws:iam::${var.account_id}:policy/agent-platform-github-ci-apply-boundary"
          }
        }
      },
      {
        # HAZARD-4 (Fable, load-bearing for slice F): apply-phase iam:DeleteRole / DetachRolePolicy /
        # DeleteRolePolicy on agent-platform-* roles so slice F's guard-gated role RETIREMENTS execute
        # (not AccessDeny) after human approval. Destroys still ROUTE to gated-apply (the guard is
        # unchanged and always gates a delete); this only gives the apply role the VERB. The apply
        # role's own ARN is carved out of DeleteRolePolicy/DetachRolePolicy by DenySelfInlinePolicyWrite
        # below (self-DoS break). Scoped to role/agent-platform-*; the boundary DataPlaneAllow is
        # extended with these three iam verbs (a grant absent from the ceiling is silently denied).
        Sid    = "IAMRoleDeleteBounded"
        Effect = "Allow"
        Action = [
          "iam:DeleteRole",
          "iam:DetachRolePolicy",
          "iam:DeleteRolePolicy",
          # rec-2882 (P0-1, the THIRD instance of the headline-verb-granted / companion-verb-missing
          # class; it killed the live gated destroy in run 30264239445 and left the sandbox
          # convergence record red at 99eb274): the AWS provider's resourceAwsIamRoleDelete
          # unconditionally calls deleteRoleInstanceProfiles() BEFORE DeleteRole, and that helper
          # calls iam:ListInstanceProfilesForRole and then iam:RemoveRoleFromInstanceProfile for each
          # profile returned. Neither is optional and neither depends on the role actually having an
          # instance profile -- the List call happens unconditionally -- so iam:DeleteRole without
          # BOTH is structurally unusable. Only the List verb belongs in THIS Sid: it authorizes on
          # the ROLE resource type, which is what this statement is scoped to. Its loop partner
          # iam:RemoveRoleFromInstanceProfile authorizes on the INSTANCE-PROFILE resource type and
          # therefore lives in its own Sid (IAMInstanceProfileDetach, immediately below) -- see that
          # comment for the live-simulate proof. Both verbs are also in the boundary DataPlaneAllow
          # ceiling below -- a grant absent from the ceiling is silently denied by the intersection.
          "iam:ListInstanceProfilesForRole"
        ]
        Resource = ["arn:aws:iam::${var.account_id}:role/agent-platform-*"]
      },
      {
        # rec-2882 follow-through -- A SEPARATE Sid BECAUSE THE RESOURCE AXIS DIFFERS, not for
        # readability. iam:RemoveRoleFromInstanceProfile authorizes on the INSTANCE-PROFILE resource
        # type, never on the role, so the grant it shipped with -- inside IAMRoleDeleteBounded at
        # role/agent-platform-* -- was INERT: present in the policy and structurally unable to
        # authorize anything. Live simulate against the APPLIED policy is what caught it (a 63-triple
        # post-apply sweep; 3 mismatches): the same verb granted at instance-profile/agent-platform-*
        # and simulated against an instance-profile ARN returns `allowed`, while the role-scoped
        # grant simulated against a role ARN returns implicitDeny -- with its former Sid siblings
        # iam:DeleteRole and iam:ListInstanceProfilesForRole returning `allowed` on that same role
        # ARN, which is what isolated the axis rather than the verb. EVERY STATIC CHECK PASSED on the
        # inert shape (the verb was literally present, at the marker the companion table declared, in
        # both layers), which is precisely why docs/contracts/iam-simulate-fixture.yaml exists and
        # why "the verb is in the policy" is never evidence that it can authorize. The boundary
        # DataPlaneAllow already carries this verb at Resource ["*"] (a ceiling, not a second copy of
        # the identity-side scoping), so no boundary change accompanies this rescope.
        Sid      = "IAMInstanceProfileDetach"
        Effect   = "Allow"
        Action   = ["iam:RemoveRoleFromInstanceProfile"]
        Resource = ["arn:aws:iam::${var.account_id}:instance-profile/agent-platform-*"]
      },
      {
        # rec-2831 (DEP-01 completion, T2.48 c1, PLAN-t248-passrole-liveproof): AWS REQUIRES
        # iam:PassRole on a Lambda's execution role for lambda:CreateFunction to succeed --
        # LambdaFunctionWrite below grants CreateFunction but the enumerated model never paired it
        # with PassRole, so a new agent-platform-* Lambda auto-applies past the guard and then
        # AccessDenies at CreateFunction (the exact recurrence this Sid closes). Worst-verb scoped
        # (Decision 143): Resource is the execution-role prefix role/agent-platform-* (never role/*
        # or PlatformAdmin/PlatformDev), AND Condition requires iam:PassedToService=lambda.amazonaws.com
        # (this identity may pass an agent-platform-* role ONLY to the lambda service, never to ecs,
        # or any other principal). Passing a PRIVILEGED agent-platform-* role (e.g. this apply
        # role itself, or the planner role) to lambda is ALLOWED by this prefix grant BY DESIGN
        # (Decision 143) -- containment is that the apply role holds no broad lambda:InvokeFunction,
        # so it cannot create-then-invoke such a lambda to escalate in-session (verified via the
        # bootstrap simulate-principal-policy gate). The boundary DataPlaneAllow ceiling is extended
        # with the same verb below (a grant absent from the ceiling is silently denied).
        Sid      = "IAMPassRoleForLambda"
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = ["arn:aws:iam::${var.account_id}:role/agent-platform-*"]
        Condition = {
          StringEquals = {
            "iam:PassedToService" = "lambda.amazonaws.com"
          }
        }
      },
      {
        # T2.23 self-grant break, retained under the widened agent-platform-* prefix (audit Q7). The
        # widened role/agent-platform-* write grants above MATCH the apply role's own ARN
        # (agent-platform-github-ci-apply), so this explicit Deny carves that ARN out of the
        # inline-policy write + attach + boundary-set actions (escalation break) AND -- carry M1 --
        # DeleteRolePolicy/DetachRolePolicy on self (self-DoS break). Explicit Deny in the identity
        # policy overrides the Allow. DeleteRole-on-self is intentionally NOT listed: deleting the apply
        # role is self-destruction (fail-safe, admin-recoverable, and guard-gated), not an escalation.
        # This is the identity-side counterpart to the guard's apply-role self-exclusion
        # (scripts/terraform_apply_guard.py _classify_iam_change) -- two independent layers.
        Sid    = "DenySelfInlinePolicyWrite"
        Effect = "Deny"
        Action = [
          "iam:PutRolePolicy",
          "iam:AttachRolePolicy",
          "iam:PutRolePermissionsBoundary",
          "iam:DeleteRolePolicy",
          "iam:DetachRolePolicy"
        ]
        Resource = ["arn:aws:iam::${var.account_id}:role/agent-platform-github-ci-apply"]
      },
      {
        # P1-4 (gap sweep): iam:TagOpenIDConnectProvider was granted with no Untag counterpart, so a
        # tag-drift reconcile on aws_iam_openid_connect_provider (default_tags removes a tag -> the
        # provider calls iam:UntagOpenIDConnectProvider) AccessDenies. Non-escalating tag metadata at
        # the same single enumerated provider ARN; also added to the boundary DataPlaneAllow ceiling
        # below, since a grant absent from the ceiling is silently denied by the intersection. The
        # three OIDC-provider LIFECYCLE verbs (Create/Delete/RemoveClientIDFrom) stay DENIED BY DESIGN
        # and admin-tier -- destroying or replacing the provider breaks the very pipeline executing
        # the apply (recorded in docs/contracts/iam-simulate-fixture.yaml).
        Sid    = "OIDCProviderReconcile"
        Effect = "Allow"
        Action = [
          "iam:GetOpenIDConnectProvider",
          "iam:UpdateOpenIDConnectProviderThumbprint",
          "iam:AddClientIDToOpenIDConnectProvider",
          "iam:TagOpenIDConnectProvider",
          "iam:UntagOpenIDConnectProvider"
        ]
        Resource = ["arn:aws:iam::${var.account_id}:oidc-provider/token.actions.githubusercontent.com"]
      },
      {
        # DuckLake Neon catalog DSN secret (T2.16b / CD.34): the apply role creates + manages the
        # Secrets Manager secret holding the assembled Neon DSN (neon_ducklake_catalog.tf). NOTE the
        # "-*" suffix -- Secrets Manager appends a random 6-char suffix to every secret ARN.
        # DescribeSecret / GetResourcePolicy are refresh-time reads the AWS provider issues on every
        # plan -- do not prune them as "unused" (dynamodb:Describe* refresh-read convention).
        Sid    = "SecretsManagerDuckLakeNeonDSN"
        Effect = "Allow"
        Action = [
          "secretsmanager:CreateSecret",
          "secretsmanager:PutSecretValue",
          "secretsmanager:UpdateSecret",
          "secretsmanager:DescribeSecret",
          "secretsmanager:GetSecretValue",
          "secretsmanager:GetResourcePolicy",
          "secretsmanager:TagResource",
          "secretsmanager:UntagResource",
          # P2 destroy symmetry (gap sweep): CreateSecret was granted here with no delete counterpart,
          # so a guard-gated retirement or replace of the DSN secret AccessDenies AFTER human
          # approval. DeleteSecret is recoverable by AWS design (7-30 day recovery window), so this
          # is not an irreversible-destroy grant. Same ARN, no widening.
          "secretsmanager:DeleteSecret"
        ]
        Resource = ["arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:ducklake-neon-catalog-dsn-*"]
      },
      {
        # P0-6 (gap sweep), METADATA half of the deliberate two-Sid split. terraform/personal declares
        # five agent-platform-* aws_secretsmanager_secret resources and the apply role could neither
        # create, retire nor tag any of them (only the DSN secret above had a write grant), so any PR
        # adding or retiring a secret AccessDenies. These four verbs are metadata-only: NONE of them
        # can read or overwrite a secret VALUE, which is what makes the agent-platform-* prefix safe
        # here while UpdateSecret below stays enumerated. TagResource is structurally required by the
        # default_tags block (a create with inline tags calls it -- the rec-2842 coupling);
        # UntagResource covers tag-drift reconcile; DeleteSecret has AWS's 7-30 day recovery window.
        # secretsmanager:PutSecretValue is granted NOWHERE new by this change.
        Sid    = "SecretsManagerMetadataWrite"
        Effect = "Allow"
        Action = [
          "secretsmanager:CreateSecret",
          "secretsmanager:DeleteSecret",
          "secretsmanager:TagResource",
          "secretsmanager:UntagResource"
        ]
        Resource = ["arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:agent-platform-*"]
        Condition = {
          # Closes the one value-REPLACEMENT path adjacent to this otherwise metadata-only Sid:
          # CreateSecret accepts a SecretString, so DeleteSecret + same-name CreateSecret would
          # substitute a value inside the prefix without ever calling PutSecretValue or
          # UpdateSecret. Forcing the recovery window to be honoured (ForceDeleteWithoutRecovery
          # false, or absent -- BoolIfExists) keeps the deleted name reserved in the
          # scheduled-for-deletion state for AWS's 7-30 day window, so the same-name recreate
          # fails loudly instead of silently succeeding with a new value. Zero autonomy cost: no
          # terraform/personal secret sets recovery_window_in_days = 0, so the provider never
          # sends ForceDeleteWithoutRecovery=true, and BoolIfExists keeps every request that omits
          # the key (CreateSecret / TagResource / UntagResource) allowed exactly as before.
          BoolIfExists = {
            "secretsmanager:ForceDeleteWithoutRecovery" = "false"
          }
        }
      },
      {
        # P0-6 (gap sweep), VALUE-CAPABLE half -- ENUMERATED, NEVER PREFIXED (S2). secretsmanager:
        # UpdateSecret accepts a SecretString, so it can overwrite a credential; it has no
        # metadata-only variant and no narrowing condition key, which makes it a worst verb under
        # Decision 143 clause 1. Critically there is NO boundary intersection available to narrow it:
        # the boundary DataPlaneAllow already carries secretsmanager:*, so this identity statement is
        # the SOLE control -- a secret:agent-platform-* prefix here would be fully effective over the
        # anthropic + deepseek API keys AND every
        # future agent-platform-* secret, with no review. It is therefore held to exactly the
        # aws_secretsmanager_secret resources terraform/personal actually declares (the DuckLake Neon
        # DSN keeps its own SecretsManagerDuckLakeNeonDSN Sid above). A NEW secret must be added here
        # deliberately. Accepted residual: UpdateSecret can still overwrite a value on these --
        # marginal rather than a new capability class, since SecretsManagerValueReadEnumerated
        # already grants GetSecretValue on them -- and it is now bounded to a named, reviewable two.
        Sid    = "SecretsManagerUpdateEnumerated"
        Effect = "Allow"
        Action = ["secretsmanager:UpdateSecret"]
        Resource = [
          "arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:agent-platform-deepseek-api-key-*",
          "arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:agent-platform-anthropic-api-key-*",
        ]
      },
      {
        # Decision 237 (secrets-to-ssm-grants): the one Terraform-managed secret parameter is the Neon catalog DSN, whose
        # password Terraform creates (a declared value exception). The write verbs are granted on exactly that ARN;
        # every out-of-band /secret/ parameter stays unwritable by CI. The parameter does not exist until
        # secrets-to-ssm-expand, and this grant must be live before that plan merges.
        Sid    = "SecretParameterDsnWrite"
        Effect = "Allow"
        Action = [
          "ssm:PutParameter",
          "ssm:DeleteParameter",
          "ssm:AddTagsToResource",
          "ssm:RemoveTagsFromResource"
        ]
        Resource = ["arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/secret/ducklake-neon-catalog-dsn"]
      },
      {
        # DEP-01 (Decision 144, rec-2757): apply-phase CREATE / MODIFY / DESTROY of the agent-platform-*
        # Lambda log groups. The enumerated model lacked logs:CreateLogGroup entirely (log groups were
        # admin-created via PlatformAdmin's LambdaLogGroupManagement), so a PR adding a NEW agent-platform-*
        # Lambda + its log group AccessDenied on CreateLogGroup. Scoped to the /aws/lambda/agent-platform-*
        # log-group prefix (mirrors PlatformAdmin's LambdaLogGroupManagement). logs:* in the boundary
        # DataPlaneAllow already permits these verbs.
        Sid    = "CloudWatchLogsWrite"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:PutRetentionPolicy",
          "logs:DeleteRetentionPolicy",
          "logs:TagLogGroup",
          "logs:DeleteLogGroup",
          # P0-4 (gap sweep): logs:TagLogGroup was granted with NO untag counterpart in either
          # tagging family, so a default_tags drift reconcile on any agent-platform-* log group
          # AccessDenies. CloudWatch Logs has TWO tagging families -- the legacy log-group-specific
          # TagLogGroup/UntagLogGroup and the newer resource-generic TagResource/UntagResource -- and
          # which one hashicorp/aws 5.100 calls on create could NOT be determined offline
          # (cloudtrail:LookupEvents is AccessDenied to PlatformAdmin). All four are granted
          # deliberately: that is what the empirically-validated PlatformAdmin
          # LambdaLogGroupManagement grant does (terraform/personal/platform_roles.tf), both families
          # are non-escalating at the same /aws/lambda/agent-platform-* prefix, and UntagLogGroup is
          # missing regardless of which family the provider picks. Blast radius if the provider uses
          # TagResource: EVERY new log group create -- hence every new Lambda -- fails, unexercised
          # only because no new Lambda has landed since logs:CreateLogGroup was added at DEP-01. This
          # over-grant is the concrete case for the CloudTrail observability follow-on.
          "logs:TagResource",
          "logs:UntagResource",
          "logs:UntagLogGroup"
        ]
        Resource = ["arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:/aws/lambda/agent-platform-*"]
      },
      {
        # apply-phase MODIFY needs AddPermission on agent-platform-* functions (EventBridge / S3 grant
        # the trigger invocation right on the function resource policy at apply time). DEP-01 / Decision
        # 144: broadened from the enumerated ducklake ARNs to the account-wide function:agent-platform-*
        # prefix so a future agent-platform-* function auto-covers without a bootstrap out-of-band grant
        # edit (mirrors the LambdaRead resource-axis broadening / rec-2702 anti-recurrence).
        Sid    = "LambdaPermissionWrite"
        Effect = "Allow"
        Action = [
          "lambda:AddPermission",
          "lambda:RemovePermission"
        ]
        Resource = [
          "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:agent-platform-*",
        ]
      },
      {
        # DEP-01 (Decision 144, rec-2703): apply-phase CREATE / MODIFY / DESTROY of an agent-platform-*
        # Lambda function. The enumerated model lacked these entirely (functions were admin-created via
        # PlatformAdmin), so a PR adding a NEW agent-platform-* Lambda AccessDenied on CreateFunction.
        # The broad-but-bounded deployer now creates + configures + retires agent-platform-* functions
        # under the MANDATORY boundary. Scoped to function:agent-platform-* (covers agent-platform-*
        # AND the agent-platform-ducklake-* naming). Data-plane wildcard lambda:* in the boundary
        # DataPlaneAllow already permits these verbs (only new iam verbs need adding to the boundary).
        Sid    = "LambdaFunctionWrite"
        Effect = "Allow"
        Action = [
          "lambda:CreateFunction",
          "lambda:UpdateFunctionConfiguration",
          "lambda:DeleteFunction",
          "lambda:TagResource",
          "lambda:UntagResource",
          "lambda:PutFunctionConcurrency",
          "lambda:DeleteFunctionConcurrency",
          # P1-2 (gap sweep): aws_lambda_function's UPDATE path calls lambda:UpdateFunctionCode
          # whenever source_code_hash / s3_key / image_uri changes. The four DuckLake functions
          # currently ignore_changes on source_code_hash (rec-2646/2654) and deploy through the
          # governed code-deploy channel (Decision 125/126), which is the only reason this has not
          # yet AccessDenied -- any function outside that arrangement, or any lift of the
          # ignore_changes, fails at apply. Same function:agent-platform-* prefix, no widening.
          "lambda:UpdateFunctionCode",
          # P0-2 (gap sweep): aws_lambda_function_url is a terraform/personal resource type with NO
          # write grant at all -- the provider calls Create/Update/DeleteFunctionUrlConfig on the
          # FUNCTION resource ARN, so they belong on this prefix rather than a separate Sid. The
          # matching read (lambda:GetFunctionUrlConfig) is already covered by LambdaRead's
          # lambda:Get* wildcard, which is exactly why the gap was invisible until an apply hit it.
          "lambda:CreateFunctionUrlConfig",
          "lambda:UpdateFunctionUrlConfig",
          "lambda:DeleteFunctionUrlConfig"
        ]
        Resource = [
          "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:agent-platform-*",
        ]
      },
      {
        # apply-phase MODIFY needs PublishLayerVersion/DeleteLayerVersion on the three ducklake
        # layers. rec-2646/2654 decoupled the FOUR ducklake Lambda FUNCTIONS' source_code_hash from
        # terraform (lifecycle ignore_changes), but the layer resources were out of that fix's scope
        # and still compute source_code_hash live from a freshly-rebuilt local zip -- any CD run that
        # rebuilds before planning (speculative-plan, apply-the-saved-plan, workflow_dispatch) can see
        # a spurious "must be replaced" diff even with no real dependency change (rec-2755 tracks the
        # durable fix: extending the functions' ignore_changes pattern to these layer resources). This
        # grant lets the apply role actually execute that replace when the guard routes it to
        # gated-apply, instead of failing AccessDenied and requiring an admin bailout every time.
        Sid    = "LambdaLayerVersionWrite"
        Effect = "Allow"
        Action = [
          "lambda:PublishLayerVersion",
          "lambda:DeleteLayerVersion"
        ]
        Resource = [
          # P1-3 (gap sweep): adopt agent-platform-* as the LAYER naming convention and grant it on
          # the write side too, so a new agent-platform-* layer does not need a fresh out-of-band
          # bootstrap grant edit (the rec-2702 resource-axis anti-recurrence, applied to layers). The
          # three ducklake-* literals are RETAINED -- the existing layers are named ducklake-*, not
          # agent-platform-*, so the prefix does not cover them. Deliberately NOT layer:* (Decision
          # 143 worst-verb scoping): PublishLayerVersion/DeleteLayerVersion on any layer in the
          # account is a materially wider grant than this module needs.
          "arn:aws:lambda:${var.aws_region}:${var.account_id}:layer:agent-platform-*",
          "arn:aws:lambda:${var.aws_region}:${var.account_id}:layer:agent-platform-*:*",
          "arn:aws:lambda:${var.aws_region}:${var.account_id}:layer:ducklake-pgclient",
          "arn:aws:lambda:${var.aws_region}:${var.account_id}:layer:ducklake-pgclient:*",
          "arn:aws:lambda:${var.aws_region}:${var.account_id}:layer:ducklake-deps",
          "arn:aws:lambda:${var.aws_region}:${var.account_id}:layer:ducklake-deps:*",
          "arn:aws:lambda:${var.aws_region}:${var.account_id}:layer:ducklake-extensions",
          "arn:aws:lambda:${var.aws_region}:${var.account_id}:layer:ducklake-extensions:*",
        ]
      },
      {
        # apply-phase MODIFY/CREATE/DESTROY needs PutRule/PutTargets on agent-platform-* EventBridge
        # rules. DEP-01 / Decision 144: broadened from the five enumerated ducklake rule ARNs to the
        # account-wide rule/agent-platform-* prefix so a future agent-platform-* rule auto-covers
        # without a bootstrap grant edit (mirrors the EventBridgeRead resource-axis broadening).
        Sid    = "EventBridgeWrite"
        Effect = "Allow"
        Action = [
          "events:PutRule",
          "events:DeleteRule",
          "events:PutTargets",
          "events:RemoveTargets",
          "events:TagResource",
          "events:UntagResource",
          "events:EnableRule",
          "events:DisableRule"
        ]
        Resource = [
          "arn:aws:events:${var.aws_region}:${var.account_id}:rule/agent-platform-*",
        ]
      },
      {
        # apply-phase MODIFY/CREATE/DESTROY needs PutMetricAlarm on the agent-platform alarms.
        # DEP-01 / Decision 144: broadened from the three enumerated ducklake-* alarm ARNs to the
        # agent-platform-* AND ducklake-* alarm namespaces (the current alarms are named ducklake-*,
        # NOT agent-platform-*, so BOTH prefixes are required; a future agent-platform-* alarm
        # auto-covers). cloudwatch:* in the boundary DataPlaneAllow already permits these verbs.
        Sid    = "CloudWatchAlarmsWrite"
        Effect = "Allow"
        Action = [
          "cloudwatch:PutMetricAlarm",
          "cloudwatch:DeleteAlarms",
          "cloudwatch:TagResource",
          "cloudwatch:UntagResource"
        ]
        Resource = [
          "arn:aws:cloudwatch:${var.aws_region}:${var.account_id}:alarm:agent-platform-*",
          "arn:aws:cloudwatch:${var.aws_region}:${var.account_id}:alarm:ducklake-*",
        ]
      },
      {
        # P0-3 (gap sweep): aws_sns_topic and aws_sns_topic_subscription (sns_alerts.tf) had refresh
        # READS only (SNSRead / SNSSubscriptionRead) and no write grant at all, so creating,
        # retiring, re-tagging or re-subscribing the alerts topic AccessDenies at apply. These six
        # verbs are the topic-scopable write half of the empirically-validated PlatformAdmin
        # AlertsTopicManage grant (terraform/personal/platform_roles.tf) -- its four read verbs are
        # already covered by the sns:Get*/List* read closure, and TagResource is forced on create by
        # the default_tags block.
        #
        # SUBSCRIPTION-MUTATION VERBS ARE DENIED BY DESIGN (Decision 143 worst-verb enumeration).
        # The earlier claim here -- that a subscription's `<topic-arn>:<uuid>` ARN made
        # sns:Unsubscribe / sns:SetSubscriptionAttributes authorizable via an agent-platform-alerts:*
        # Resource entry -- is DISPROVEN by live simulate: both actions have NO resource type in
        # SNS's IAM model, so a wildcard grant (Resource ["*"]) simulated against `*` returns
        # `allowed` while the SAME wildcard grant simulated against either the topic ARN or a
        # subscription ARN returns implicitDeny. They are unscopeable: making them work requires an
        # account-wide Resource "*" grant, which would let one approved CD apply unsubscribe or
        # reconfigure ANY subscription in the account -- so they are dropped rather than widened.
        # ACCEPTED CONSEQUENCE, stated so no future auditor reads the denial as a gap: destroying or
        # REPLACING aws_sns_topic_subscription.alerts_email through CD will AccessDeny and needs an
        # admin apply. sns:Subscribe STAYS -- it authorizes on the topic resource type (proven
        # `allowed` at the bare topic ARN), so subscription CREATE works through CD. Recorded as
        # by_design entries in docs/contracts/iam-simulate-fixture.yaml.
        #
        # ONE Resource entry, not two: with Unsubscribe/SetSubscriptionAttributes gone, no remaining
        # verb in this Sid authorizes on anything but the topic itself, so the former
        # agent-platform-alerts:* entry matched nothing and is removed with them.
        Sid    = "SNSTopicWrite"
        Effect = "Allow"
        Action = [
          "sns:CreateTopic",
          "sns:DeleteTopic",
          "sns:SetTopicAttributes",
          "sns:TagResource",
          "sns:UntagResource",
          "sns:Subscribe"
        ]
        Resource = [
          "arn:aws:sns:${var.aws_region}:${var.account_id}:agent-platform-alerts",
        ]
      },
      {
        # P0-7 (gap sweep): RESCOPED from the old SSMFeatureFlagsManage Sid, which was pinned to
        # /agent-platform/feature-flags/* while terraform/personal now declares aws_ssm_parameter
        # resources elsewhere under /agent-platform/ (e.g. the T2.19 DuckLake endpoint-discovery
        # parameters) -- those writes AccessDeny today. The prefix is widened to the SAME
        # parameter/agent-platform/* scope the read Sid (SSMParameterRead) already carries, so read
        # and write scope stay at parity, and the Sid is renamed to match the equivalent
        # empirically-validated PlatformAdmin statement (SSMParameterProvisioning in
        # terraform/personal/platform_roles.tf). ssm:DeleteParameter closes the create/destroy
        # asymmetry: PutParameter was granted with no delete counterpart, so a parameter retirement
        # AccessDenies after human approval. The plural ssm:DeleteParameters is deliberately NOT
        # granted -- the provider uses the singular form for aws_ssm_parameter. The three read verbs
        # are retained from the pre-rescope grant (redundant with SSMParameterRead's wildcards, but
        # this statement keeps the shape of its PlatformAdmin counterpart). Still NOT ssm:* and still
        # not all parameters.
        Sid    = "SSMParameterProvisioning"
        Effect = "Allow"
        Action = [
          "ssm:GetParameter",
          "ssm:GetParameters",
          "ssm:PutParameter",
          "ssm:DeleteParameter",
          "ssm:AddTagsToResource",
          "ssm:RemoveTagsFromResource",
          "ssm:ListTagsForResource"
        ]
        Resource = ["arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/agent-platform/*"]
      },
      {
        # Time-boxed restore for the Decision 178 clause 4 drain (PLAN-glue-delete-database-grant):
        # the 7b67e21d cleanse deleted this Sid and the live aws_glue_catalog_database.ops resource
        # in the same sweep, deleting the HCL before the resource was destroyed in AWS and orphaning
        # it in tfstate. Narrowed to the DESTROY path only (Decision 143 worst-verb scoping) -- no
        # remaining HCL can reach glue:CreateDatabase/UpdateDatabase/CreateTable/UpdateTable, so none
        # is granted. The fourth Resource ARN (userDefinedFunction/agent_platform/*) is required
        # because AWS authorizes glue:DeleteDatabase against the database's child ARNs, not the
        # database ARN alone -- run 33323201848 AccessDenied on exactly this ARN. This grant is
        # TIME-BOXED to the drain: scripts/ops/drain_glue_orphan.py's close phase files a
        # removal-obligation rec once the orphan leaves state (rec-3348, rec-3328).
        Sid    = "GlueCatalog"
        Effect = "Allow"
        Action = [
          "glue:GetDatabase",
          "glue:GetDatabases",
          "glue:GetTable",
          "glue:GetTables",
          "glue:GetPartitions",
          "glue:GetTags",
          "glue:DeleteTable",
          "glue:DeleteDatabase"
        ]
        Resource = [
          "arn:aws:glue:${var.aws_region}:${var.account_id}:catalog",
          "arn:aws:glue:${var.aws_region}:${var.account_id}:database/agent_platform",
          "arn:aws:glue:${var.aws_region}:${var.account_id}:table/agent_platform/*",
          "arn:aws:glue:${var.aws_region}:${var.account_id}:userDefinedFunction/agent_platform/*"
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy" "github_ci_apply" {
  name   = "agent-platform-github-ci-apply"
  role   = aws_iam_role.github_ci_apply.id
  policy = local.github_ci_apply_policy_json

  # FORWARD-APPLY ORDERING (policy-architecture split): the reads policy and this inline policy are
  # ONE logical read surface split across two resources, and terraform has no implicit dependency
  # between them -- both target the same role, neither references the other. Without this edge a
  # forward apply is free to SHRINK the inline policy first; if the create-or-attach then fails, the
  # role holds ZERO refresh-read surface and every subsequent CD plan dies AccessDenied BEFORE the
  # guard runs, including any reconcile. With it, the worst case is the reads being granted twice
  # for one step -- an identical allow-union, harmless. The ROLLBACK direction is the mirror image
  # and is NOT expressible in HCL: restore the inline read Sids FIRST, then detach; never detach the
  # reads policy alone.
  depends_on = [aws_iam_role_policy_attachment.github_ci_apply_reads]

  lifecycle {
    precondition {
      # rec-2793 (DEP-01 anti-recurrence): AWS excludes whitespace from the 10,240 B inline-
      # policy limit, so measure the WHITESPACE-STRIPPED/minified rendering (a raw
      # whitespace-inclusive length() false-fails here: ~10,534 B raw, deploys fine minified).
      condition     = length(jsonencode(jsondecode(local.github_ci_apply_policy_json))) <= 10240
      error_message = "github_ci_apply inline policy exceeds the 10,240 B IAM inline-policy limit (whitespace-stripped measure, rec-2793). Move a statement to a customer-managed policy or trim grants."
    }
  }
}
