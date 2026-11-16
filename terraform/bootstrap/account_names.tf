# The one home of this root's globally unique, per-account names (rec-5025, SIT Decision 252). The sandbox
# values equal the names in use today, so PROD renders byte-identical policies; sit adds a suffix. The map index
# is the only form (the trusted-plan screen refuses a conditional or comparison operator in the personal root,
# and this file mirrors terraform/personal/account_names.tf). Account ids, regions and e-mails never appear here.
locals {
  name_suffix = { sandbox = "", sit = "-sit" }[var.environment]

  data_lake_bucket_name = "agent-platform-data-lake${local.name_suffix}"
  data_lake_bucket_arn  = "arn:aws:s3:::${local.data_lake_bucket_name}"

  ducklake_dr_bucket_name = "agent-platform-ducklake-catalog-dr${local.name_suffix}"
  ducklake_dr_bucket_arn  = "arn:aws:s3:::${local.ducklake_dr_bucket_name}"

  bootstrap_state_bucket_name = "agent-platform-bootstrap-tfstate${local.name_suffix}"
  bootstrap_state_bucket_arn  = "arn:aws:s3:::${local.bootstrap_state_bucket_name}"
}
