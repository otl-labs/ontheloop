# GitHub Actions OIDC pipeline roles (planner dual-sub speculative-plan/drift + governed
# Lambda deploy) -- split from oidc.tf (Decision 166 terraform-class grandfather drain,
# PLAN-terraform-decompose-oidc-rename).
#
# ---------------------------------------------------------------------------
# Planner role (dual-sub speculative-plan + drift, T2.49 c2 / DEP-12 / Decision 144): merges
# github_ci_plan (PR-sub, CD.35 Wave 2 / T2.21) + github_ci_drift (main-sub, CD.35 Wave 5 /
# T2.24) into ONE identity trusting BOTH subs, partitioned by sts:RoleSessionName so
# convergence-write stays fail-closed and non-spoofable (Decision 92 pt2).
#
# This role is IAM-SENSITIVE -- the deterministic guard (scripts/terraform_apply_guard.py)
# BLOCKS its creation (exit 2) and it lands via the human-gated tf-gated-apply Environment
# (Decision 77/92). The speculative-plan job's assume-role step carries continue-on-error to
# cover any residual bootstrap window; the drift workflow's assume-role step does the same.
#
# Trust partition (the fail-closed discriminator, Decision 92 pt2):
#   - MAIN-sub statement: sub=refs/heads/main AND sts:RoleSessionName StringEquals the
#     RESERVED session-name (local.convergence_writer_session_name) -- ONLY the scheduled
#     drift workflow (which sets this exact role-session-name) can satisfy this statement.
#   - PR-sub statement: sub in {pull_request, ref:refs/pull/*} AND sts:RoleSessionName
#     StringNotEquals the reserved name -- a PR-context assumption can NEVER present the
#     reserved session-name (the ducklake-artifacts job sets a distinct, non-reserved
#     value), so it structurally cannot satisfy the main-sub statement either.
# A PR-context assumption therefore cannot obtain the reserved session-name under ANY
# statement -- the trust half of the fail-closed guarantee. The permission half (below) adds
# the aws:userid condition on ConvergenceRecordWrite so even a hypothetical main-sub session
# that omits the reserved name gains no convergence-write eligibility.
#
# Capability union (plan + drift, unconditioned except ConvergenceRecordWrite):
#   from github_ci_plan  -- DucklakeBuildInputsRead, DucklakeLambdaPackagesWrite (the saved-plan write moved to the
#                            trusted plan role, Decision 238).
#   from github_ci_drift -- TfstateNativeLockFile, DuckLakeWriterInvoke (writer-only, Decision
#                            84 closed boundary), ConvergenceRecordWrite (now FAIL-CLOSED,
#                            aws:userid-conditioned -- see validate_convergence_writer_isolation
#                            for the standing semantic check).
#   plus DriftSessionSecretValueRead -- the only Secrets Manager value read on the Neon key and catalog
#                            DSN, aws:userid-conditioned on the reserved drift session (site 5/6).
# Refresh-read surface: ci_full_refresh_read, composed below (metadata reads plus the tfvars value read).
# ---------------------------------------------------------------------------

resource "aws_iam_role" "github_ci_planner" {
  name                 = "agent-platform-github-ci-planner"
  description          = "GitHub Actions dual-sub planner (T2.49 c2 / DEP-12): merges speculative-plan (PR) + drift (main) via OIDC"
  permissions_boundary = "arn:aws:iam::${var.account_id}:policy/agent-platform-github-ci-apply-boundary"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Main-sub: the scheduled/dispatch drift workflow ONLY. Requires the RESERVED
        # session-name (site 1/6, local.convergence_writer_session_name) -- the sole
        # session-name this role's ConvergenceRecordWrite condition (below) accepts.
        Sid    = "AssumeMainReservedSession"
        Effect = "Allow"
        Principal = {
          Federated = aws_iam_openid_connect_provider.github_actions.arn
        }
        Action = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
            "sts:RoleSessionName"                     = local.convergence_writer_session_name
          }
          StringLike = {
            # Dual-slug transition (Decision 171): both slugs trusted for the reserved-session
            # main-sub statement during the transition window. Same flatten([for ...]) idiom as
            # every other sub site, so an audit can confirm at a glance that none was missed.
            "token.actions.githubusercontent.com:sub" = flatten([
              for repo in local.github_repos : [
                "repo:${repo}:ref:refs/heads/main"
              ]
            ])
          }
        }
      },
      {
        # PR-sub: the speculative-plan job. Trust mirrors github_ci_pr's sub condition, PLUS
        # StringNotEquals the reserved session-name (site 2/6) -- a PR-context assumption can
        # never present the one session-name that satisfies the main-sub statement above or the
        # ConvergenceRecordWrite permission condition below (non-spoofable, Decision 92 pt2).
        Sid    = "AssumePrNonReservedSession"
        Effect = "Allow"
        Principal = {
          Federated = aws_iam_openid_connect_provider.github_actions.arn
        }
        Action = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          }
          StringNotEquals = {
            "sts:RoleSessionName" = local.convergence_writer_session_name
          }
          StringLike = {
            # Dual-slug transition (Decision 171): flatten over local.github_repos so both slugs
            # are trusted for each PR-context sub pattern.
            "token.actions.githubusercontent.com:sub" = flatten([
              for repo in local.github_repos : [
                "repo:${repo}:pull_request",
                "repo:${repo}:ref:refs/pull/*"
              ]
            ])
          }
        }
      }
    ]
  })
}

data "aws_iam_policy_document" "github_ci_planner" {
  # DRY composition (T2.34): the shared 19-statement refresh-read surface, not re-declared
  # inline -- identical to the pre-merge plan/drift roles. ci_full_refresh_read itself
  # composes ci_ssm_refresh_read.
  source_policy_documents = [data.aws_iam_policy_document.ci_full_refresh_read.json]

  statement {
    # From github_ci_plan: vendored DuckLake build inputs (read-only, rec-2512).
    sid     = "DucklakeBuildInputsRead"
    effect  = "Allow"
    actions = ["s3:GetObject"]
    resources = [
      "${aws_s3_bucket.data_lake.arn}/ducklake-pgclient/*",
      "${aws_s3_bucket.data_lake.arn}/ducklake-extensions/*"
    ]
  }

  statement {
    # From github_ci_plan: upload the rebuilt DuckLake zips (rec-2512). No DeleteObject.
    sid       = "DucklakeLambdaPackagesWrite"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.data_lake.arn}/lambda-packages/*"]
  }

  statement {
    # From github_ci_drift: native S3 locking coexistence. Scoped to the EXACT lock object key
    # -- NO write on the state object terraform.tfstate itself.
    sid     = "TfstateNativeLockFile"
    effect  = "Allow"
    actions = ["s3:PutObject", "s3:DeleteObject"]
    resources = [
      "${aws_s3_bucket.data_lake.arn}/tfstate/personal/sandbox/terraform.tfstate.tflock"
    ]
  }

  statement {
    # rec-4335: the PR-time terraform/github plan (terraform-github-plan.yml) reads that root's
    # state. GetObject on the ONE state object only -- no PutObject (the plan runs -lock=false, so
    # no .tflock write) and no other key under tfstate/github/.
    sid       = "TfstateGithubRead"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.data_lake.arn}/tfstate/github/terraform.tfstate"]
  }

  statement {
    # From github_ci_drift: WRITER-only invoke (Decision 84 closed reader/writer boundary) --
    # files the drift rec via the ops portal. The reader is explicitly excluded.
    sid     = "DuckLakeWriterInvoke"
    effect  = "Allow"
    actions = ["lambda:InvokeFunctionUrl", "lambda:GetFunctionUrlConfig"]
    resources = [
      # rec-4685: name Lambdas by built ARN, not resource reference; a reference defers this document read to apply whenever the Lambda changes.
      "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:${local.ducklake_writer_function}",
      "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:${local.ducklake_writer_function}:*",
    ]
  }

  statement {
    # Decision 213 cl.1: lambda:InvokeFunction authorizes only the Function-URL path (the permissions
    # boundary denies a direct invoke); the Function URL needs this action alongside InvokeFunctionUrl.
    sid     = "DuckLakeWriterInvokeViaUrlOnly"
    effect  = "Allow"
    actions = ["lambda:InvokeFunction"]
    resources = [
      "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:${local.ducklake_writer_function}",
      "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:${local.ducklake_writer_function}:*",
    ]
    condition {
      test     = "Bool"
      variable = "lambda:InvokedViaFunctionUrl"
      values   = ["true"]
    }
  }

  statement {
    # c2 FAIL-CLOSED convergence write (Decision 92 pt2, hardening item 1): the SOLE allow on
    # convergence/personal/* in this policy, conditioned on aws:userid matching the RESERVED
    # session (site 3/6, local.convergence_writer_session_name) -- default-deny for every other
    # session identity. Combined with the trust partition above, a PR-context assumption can
    # neither obtain the reserved session-name nor satisfy this condition. Standing semantic
    # check: scripts/checks/iam_tf/validate_convergence_writer_isolation.py asserts this is the
    # ONLY convergence-write Allow and that it carries this exact condition (grep-presence
    # cannot see a second, unconditioned Allow added alongside this one -- the semantic check
    # can). Drift NEVER writes the record green -- green is written solely by a converged apply
    # (T2.20 anti-masking anchor).
    sid       = "ConvergenceRecordWrite"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.data_lake.arn}/convergence/personal/*"]
    condition {
      test     = "StringLike"
      variable = "aws:userid"
      values   = ["*:${local.convergence_writer_session_name}"]
    }
  }

  statement {
    # Neon provider API key and catalog DSN value reads for the reserved drift session only (site 5/6,
    # local.convergence_writer_session_name). PR sessions hold no value read on either secret; the PR-time plan
    # runs under the trusted plan role. Standing check: validate_planner_secret_value_reads.
    sid     = "DriftSessionSecretValueRead"
    effect  = "Allow"
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      "arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:neon-api-key-*",
      "arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:ducklake-neon-catalog-dsn-*",
    ]
    condition {
      test     = "StringLike"
      variable = "aws:userid"
      values   = ["*:${local.convergence_writer_session_name}"]
    }
  }

  statement {
    # Decision 237: the SSM twin of DriftSessionSecretValueRead, for the same reserved drift session only (site 6/6,
    # local.convergence_writer_session_name). Standing check: validate_planner_secret_value_reads.
    sid     = "DriftSessionSecretParameterRead"
    effect  = "Allow"
    actions = ["ssm:GetParameter"]
    resources = [
      "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/secret/neon-api-key",
      "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/secret/ducklake-neon-catalog-dsn",
    ]
    condition {
      test     = "StringLike"
      variable = "aws:userid"
      values   = ["*:${local.convergence_writer_session_name}"]
    }
  }
}

resource "aws_iam_role_policy" "github_ci_planner" {
  name   = "agent-platform-github-ci-planner"
  role   = aws_iam_role.github_ci_planner.id
  policy = data.aws_iam_policy_document.github_ci_planner.json

  lifecycle {
    precondition {
      # rec-2793 (DEP-01 anti-recurrence): AWS excludes whitespace from the 10,240 B inline-
      # policy limit, so measure the WHITESPACE-STRIPPED/minified rendering, not the raw
      # (pretty-printed) data-source .json string.
      condition     = length(jsonencode(jsondecode(data.aws_iam_policy_document.github_ci_planner.json))) <= 10240
      error_message = "github_ci_planner inline policy exceeds the 10,240 B IAM inline-policy limit (whitespace-stripped measure, rec-2793). Move a statement to a customer-managed policy or trim grants."
    }
  }
}

# ---------------------------------------------------------------------------
# Deploy role (governed code-deploy channel, T2.49 c3 / DEP-12 / Decision 144): merges
# github_ci_ducklake_deploy (T2.38) + github_ci_prod_deploy (T2.43) into ONE role scoped to
# lambda:UpdateFunctionCode on the account-wide function:agent-platform-* prefix (Decision
# 129/144 pt2) -- covers all 8 functions (5 ducklake + 3 prod) without per-function
# enumeration, so a future agent-platform-* function auto-covers.
#
# This role is IAM-SENSITIVE -- the deterministic guard (scripts/terraform_apply_guard.py)
# BLOCKS its creation (exit 2) and it lands via the human-gated tf-gated-apply Environment
# (Decision 77/92). Both governed deploy workflows carry continue-on-error on the assume-role
# step to cover any residual bootstrap window.
#
# Capability shape (deliberately narrow -- "UpdateFunctionCode-only" is the literal invariant,
# unchanged from the two roles it merges):
#   - lambda:UpdateFunctionCode on function:agent-platform-* ONLY. No
#     UpdateFunctionConfiguration, no InvokeFunction*, no PublishVersion, no AddPermission, no
#     other lambda: action.
#   - S3: GetObject/PutObject on lambda-packages/* (build + upload), PutObject on BOTH
#     deploy-records/ducklake/* and deploy-records/prod/* (union of the two deploy-record
#     prefixes), GetObject on the two vendored build-input prefixes, ListBucket (head-bucket).
#   - No terraform:*, no iam:* of any kind.
#
# Trust mirrors github_ci_branch: StringEquals aud + StringLike sub refs/heads/main ONLY (no
# agent/*, no pull/*, no environment sub -- this role is never assumed from a PR or a gated
# Environment).
# ---------------------------------------------------------------------------

resource "aws_iam_role" "github_ci_deploy" {
  name                 = "agent-platform-github-ci-deploy"
  description          = "GitHub Actions governed Lambda code deploy (T2.49 c3 / DEP-12): merges ducklake + prod deploy via OIDC"
  permissions_boundary = "arn:aws:iam::${var.account_id}:policy/agent-platform-github-ci-apply-boundary"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Federated = aws_iam_openid_connect_provider.github_actions.arn
        }
        Action = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          }
          StringLike = {
            # Dual-slug transition (Decision 171): both slugs trusted for the deploy role's
            # single main-sub statement during the transition window. Same flatten([for ...])
            # idiom as every other sub site, so an audit can confirm none was missed.
            "token.actions.githubusercontent.com:sub" = flatten([
              for repo in local.github_repos : [
                "repo:${repo}:ref:refs/heads/main"
              ]
            ])
          }
        }
      }
    ]
  })
}

data "aws_iam_policy_document" "github_ci_deploy" {
  statement {
    # The ONLY Lambda action this role grants -- UpdateFunctionCode on the account-wide
    # function:agent-platform-* prefix (Decision 129/144 pt2), covering all 8 functions (the
    # five ducklake + three prod functions) without per-function enumeration. No lambda-config,
    # no invoke, no publish-version/add-permission.
    sid       = "DeployUpdateFunctionCode"
    effect    = "Allow"
    actions   = ["lambda:UpdateFunctionCode"]
    resources = ["arn:aws:lambda:${var.aws_region}:${var.account_id}:function:agent-platform-*"]
  }

  statement {
    # build_lambda's validate_bucket_exists runs `aws s3api head-bucket` before uploading;
    # bucket-level ONLY (no /*). Mirrors github_ci_ducklake_deploy's DataLakeHeadBucket.
    sid       = "DataLakeHeadBucket"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.data_lake.arn]
  }

  statement {
    # Build + upload every function zip (and layer zips, when rebuilt) to lambda-packages/.
    # Union of DucklakeLambdaPackagesReadWrite + ProdLambdaPackagesReadWrite (identical scope).
    sid       = "LambdaPackagesReadWrite"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.data_lake.arn}/lambda-packages/*"]
  }

  statement {
    # Union of the two deploy-record prefixes: write the per-function deployment record
    # (function -> CodeSha256 -> source git SHA) for BOTH the ducklake and prod classes.
    sid     = "DeployRecordWrite"
    effect  = "Allow"
    actions = ["s3:PutObject"]
    resources = [
      "${aws_s3_bucket.data_lake.arn}/deploy-records/ducklake/*",
      "${aws_s3_bucket.data_lake.arn}/deploy-records/prod/*",
    ]
  }

  statement {
    # Vendored build inputs `build_lambda --ducklake-only` reads at build time. Read-only --
    # operator-seeded, never written by CI. Matches github_ci_ducklake_deploy's
    # DucklakeBuildInputsRead (the prod build path does not need this prefix, but sharing one
    # identity means it is granted here too -- read-only, no broader blast radius created).
    sid     = "DucklakeBuildInputsRead"
    effect  = "Allow"
    actions = ["s3:GetObject"]
    resources = [
      "${aws_s3_bucket.data_lake.arn}/ducklake-pgclient/*",
      "${aws_s3_bucket.data_lake.arn}/ducklake-extensions/*"
    ]
  }
}

resource "aws_iam_role_policy" "github_ci_deploy" {
  name   = "agent-platform-github-ci-deploy"
  role   = aws_iam_role.github_ci_deploy.id
  policy = data.aws_iam_policy_document.github_ci_deploy.json

  lifecycle {
    precondition {
      # rec-2793 (DEP-01 anti-recurrence): whitespace-stripped/minified measure (see
      # github_ci_planner's precondition above for the AWS whitespace-exclusion rationale).
      condition     = length(jsonencode(jsondecode(data.aws_iam_policy_document.github_ci_deploy.json))) <= 10240
      error_message = "github_ci_deploy inline policy exceeds the 10,240 B IAM inline-policy limit (whitespace-stripped measure, rec-2793). Move a statement to a customer-managed policy or trim grants."
    }
  }
}
