# github_ci_apply role + authority budget (CD.35 Wave 4 / T2.23 / Decisions 92, 94).
#
# Migrated from terraform/personal/oidc.tf:
#   - aws_iam_role.github_ci_apply (permissions_boundary now attached)
#   - aws_iam_role_policy.github_ci_apply (self-grant break + rec-2079 consolidation + rec-2305 style)
#
# New in this root:
#   - aws_iam_policy.github_ci_apply_boundary (the authority budget)
#
# The OIDC provider and branch/pr/plan roles stay in terraform/personal/oidc.tf.
# The trust references the OIDC provider as a literal ARN (no cross-root resource reference).

locals {
  # PRE-STAGED STATE (Decision 218 / PLAN-oidc-owner-segment-prestage): mirrors
  # terraform/personal/oidc.tf's local.github_repos -- the current segment plus, additively, the
  # otl-labs/ontheloop target segment. This list MUST stay identical to terraform/personal's; the
  # two roots cannot reference each other, so agreement is enforced by
  # tests/checks/iam_tf/test_oidc_trust_slug_invariants.py and validate_github_org_transfer.
  #
  # CONTRACTED STEADY STATE (history, Decision 172 / PLAN-oidc-trust-contraction-closure): the
  # dual-slug transition (Decision 171 / PLAN-repo-rename-relicense) ended when both pre-rename
  # name-only entries, unmintable post-rename, were removed with live proof.
  #
  # IMMUTABLE-SUBJECT ENTRY (Decision 172): mirrors terraform/personal/oidc.tf's entry -- a repo
  # SEGMENT "OWNER@OWNER-ID/REPO@REPO-ID", never a "repo:"-prefixed literal (this root's two sub
  # sites below already render "repo:${repo}:<suffix>"). GitHub applies the immutable numeric-id
  # subject to any repo renamed/transferred after 2026-07-15; theseus renamed at
  # 2026-08-15T12:55:57Z and now presents ONLY this shape. Decision 172 point 2's pre-stage
  # playbook adds a future name's immutable entry to this list ADDITIVELY, BEFORE the rename
  # lands; never remove an entry without live proof of the replacement first.
  github_repos = [
    "benjamin-blake@217728084/theseus@1252427466",
    "otl-labs@330441623/ontheloop@1252427466",
  ]
}

# Adopt the live role + inline policy without recreate.
import {
  id = "agent-platform-github-ci-apply"
  to = aws_iam_role.github_ci_apply
}

import {
  id = "agent-platform-github-ci-apply:agent-platform-github-ci-apply"
  to = aws_iam_role_policy.github_ci_apply
}

resource "aws_iam_role" "github_ci_apply" {
  name                 = "agent-platform-github-ci-apply"
  description          = "GitHub Actions sandbox apply (Decisions 77, 94): refs/heads/main and environment:tf-gated-apply via OIDC"
  permissions_boundary = aws_iam_policy.github_ci_apply_boundary.arn

  # CD.35 Wave 3 / T2.22 (Decision 92, CORRECTED post-VP9):
  # This role is assumed by TWO apply paths in terraform-apply-sandbox.yml:
  #   1. Routine auto-apply (apply-sandbox job, guard PASS): no job-level environment, so GitHub
  #      mints sub = repo:<OWNER/REPO or OWNER@ID/REPO@ID>:ref:refs/heads/main.
  #   2. Gated apply (gated-apply job, guard fail-closed set: IAM/trust/destroy): the job declares
  #      environment: tf-gated-apply, and GitHub then OVERRIDES the sub to
  #      repo:<OWNER/REPO or OWNER@ID/REPO@ID>:environment:tf-gated-apply (the env claim REPLACES
  #      the ref claim in sub). (Decision 172: post-2026-07-15-rename repos mint ONLY the
  #      immutable OWNER@ID/REPO@ID shape -- the name-only OWNER/REPO shape is legacy and no
  #      longer minted for this repository and is not in the list.)
  # Decision 94 (VP9 regression guard): trust MUST keep BOTH subs or the gated-apply path breaks.
  # The OIDC provider stays in terraform/personal/; trust references its ARN as a literal
  # (no cross-root resource reference).
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
            # Exact-match list (StringEquals with an array = OR of exact values; NOT a wildcard).
            # agent/* and pull/* still cannot assume this role.
            #   - refs/heads/main          : the routine auto-apply path (no job environment).
            #   - environment:tf-gated-apply: the gated-apply job (GitHub overrides sub to the env
            #     claim when a job declares environment:; approval-gated by the required reviewer).
            # Flatten over local.github_repos so BOTH Decision 94 sub forms are trusted for EVERY
            # segment (current and pre-staged target) -- never drop either form for either
            # segment (see the module-level warning above).
            "token.actions.githubusercontent.com:sub" = flatten([
              for repo in local.github_repos : [
                "repo:${repo}:ref:refs/heads/main",
                "repo:${repo}:environment:tf-gated-apply"
              ]
            ])
          }
        }
      }
    ]
  })
}
