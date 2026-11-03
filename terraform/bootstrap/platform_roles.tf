# PlatformDev and PlatformAdmin, adopted from terraform/personal (PLAN-platform-iam-bootstrap-adopt,
# rec-3838 plan 1 of 2). A state holds only what its applier may mutate: both identities are
# CI-unwritable by design (Decision 180 clause 3), so their terraform/personal home was a
# split-apply generator (Decision 190). The sibling plan (PLAN-platform-iam-personal-release)
# releases them from terraform/personal once this import lands.
#
# Zero-diff import: argument-for-argument copies of terraform/personal/platform_roles.tf. Resource-
# level tags reproduce the personal provider's default_tags values this root's default_tags do not
# carry (Account = "personal", ManagedBy = "Terraform"), so tags_all is unchanged by the move.
#
# IMPORT BEFORE APPLY -- both roles pre-exist (imported from terraform/personal's state, which
# pre-existed from breakglass creation). The import {} blocks below do this declaratively as part
# of the presented-plan apply; never run a CLI `terraform import` against this root -- that would
# write state outside the presented-plan gate (Decision 126).
#
# SAFETY -- the adoption plan must show no VALUE change on either trust document (lockout risk:
# this root's apply runs under the PlatformAdmin identity being adopted). A cosmetic "updated
# in-place" on assume_role_policy, with Terraform's own "The value is unchanged" annotation, is
# expected the first time the ExternalId variables transition to sensitive -- see
# docs/contracts/deploy-paths.yaml admin_out_of_band.procedure for the human-gated walk.

import {
  id = "PlatformDev"
  to = aws_iam_role.platform_dev
}

import {
  id = "PlatformAdmin"
  to = aws_iam_role.platform_admin
}

resource "aws_iam_role" "platform_dev" {
  name                 = "PlatformDev"
  permissions_boundary = aws_iam_policy.github_ci_apply_boundary.arn
  max_session_duration = 36000 # 10h; matches duration_seconds in ~/.aws/config so CC-web sessions run unattended
  description          = "Daily agent ops (runtime): S3 read/write on the data lake, DynamoDB counters, DuckLake verb invokes"

  tags = {
    Account   = "personal"
    ManagedBy = "Terraform"
  }

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = "sts:AssumeRole"
        Principal = {
          AWS = "arn:aws:iam::${var.account_id}:user/${var.agent_service_account_user_name}"
        }
        Condition = {
          StringEquals = {
            "sts:ExternalId" = var.platform_dev_external_id
          }
        }
      }
    ]
  })

  lifecycle {
    prevent_destroy = true
  }
}

# PlatformAdmin -- provisioning role (IAM import + admin Lambda/secrets + data-lake provisioning).
# DELIBERATELY EXCLUDED from a permissions_boundary (control identity that must remain able to
# amend the boundary; Decision 144 pt.3 / Decision 113 two-principal split).
resource "aws_iam_role" "platform_admin" {
  name                 = "PlatformAdmin"
  max_session_duration = 3600 # AWS IAM minimum; admin sessions kept short (limit further via duration_seconds in ~/.aws/config)
  description          = "Admin ops (provisioning): iam:* for import, admin Lambda management, secrets, and data-lake provisioning"

  tags = {
    Account   = "personal"
    ManagedBy = "Terraform"
  }

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = "sts:AssumeRole"
        Principal = {
          AWS = "arn:aws:iam::${var.account_id}:user/${var.agent_service_account_user_name}"
        }
        Condition = {
          StringEquals = {
            "sts:ExternalId" = var.platform_admin_external_id
          }
        }
      }
    ]
  })

  lifecycle {
    prevent_destroy = true
  }
}
