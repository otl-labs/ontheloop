# The management account's admin lane (rec-5026, Decision 256 cl.3): a console-only IAM user that can
# only assume role OrganizationAdmin (MFA required). The operator's step-4 root seed creates the four
# entities from the same templates; the blocks below adopt them. The user's password and MFA device
# are set by the operator in the console and never declared, so no secret reaches state (Decision 237).
# No access key, login profile or virtual MFA device resource exists, and none may be added.
#
# A missing template fails closed: the policies become a single Deny of everything, and the trust a
# Deny of sts:AssumeRole to every principal.
import {
  to = aws_iam_user.org_admin_operator
  id = "org-admin-operator"
}

import {
  to = aws_iam_user_policy.assume_organization_admin
  id = "org-admin-operator:AssumeOrganizationAdmin"
}

import {
  to = aws_iam_role.organization_admin
  id = "OrganizationAdmin"
}

import {
  to = aws_iam_role_policy.organization_admin_ops
  id = "OrganizationAdmin:OrganizationAdminOps"
}

locals {
  organization_admin_template_vars = {
    account_id = data.aws_caller_identity.current.account_id
    region     = var.aws_region
  }

  deny_all_policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Deny"
        Action   = "*"
        Resource = "*"
      },
    ]
  })

  deny_assume_trust_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Deny"
        Principal = { AWS = "*" }
        Action    = "sts:AssumeRole"
      },
    ]
  })
}

resource "aws_iam_user" "org_admin_operator" {
  name = "org-admin-operator"
}

resource "aws_iam_user_policy" "assume_organization_admin" {
  name   = "AssumeOrganizationAdmin"
  user   = aws_iam_user.org_admin_operator.name
  policy = try(templatefile("${path.module}/policies/assume_organization_admin.json.tftpl", local.organization_admin_template_vars), local.deny_all_policy_json)
}

resource "aws_iam_role" "organization_admin" {
  name                 = "OrganizationAdmin"
  max_session_duration = 3600
  assume_role_policy   = try(templatefile("${path.module}/policies/organization_admin_trust.json.tftpl", local.organization_admin_template_vars), local.deny_assume_trust_json)
}

resource "aws_iam_role_policy" "organization_admin_ops" {
  name   = "OrganizationAdminOps"
  role   = aws_iam_role.organization_admin.name
  policy = try(templatefile("${path.module}/policies/organization_admin.json.tftpl", local.organization_admin_template_vars), local.deny_all_policy_json)
}
