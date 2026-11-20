# The Organization, its root features and the two OUs (rec-5026). Member accounts are added by the
# child plans (rec-5064 SIT, rec-5065 PROD). FullAWSAccess stays attached where AWS puts it.
resource "aws_organizations_organization" "this" {
  feature_set                   = "ALL"
  enabled_policy_types          = ["SERVICE_CONTROL_POLICY"]
  aws_service_access_principals = ["iam.amazonaws.com"]

  lifecycle {
    prevent_destroy = true
  }
}

# Centralized root access: member accounts have no root credentials, and a root-only task runs as a
# scoped root session from this account.
resource "aws_iam_organizations_features" "this" {
  enabled_features = ["RootCredentialsManagement", "RootSessions"]

  depends_on = [aws_organizations_organization.this]
}

resource "aws_organizations_organizational_unit" "prod" {
  name      = "prod"
  parent_id = aws_organizations_organization.this.roots[0].id

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_organizations_organizational_unit" "sit" {
  name      = "sit"
  parent_id = aws_organizations_organization.this.roots[0].id

  lifecycle {
    prevent_destroy = true
  }
}
