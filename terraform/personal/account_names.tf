# The one home of this root's globally unique, per-account names (rec-5025, SIT Decision 252). The sandbox
# values equal the names in use today; sit adds a suffix. The map index is the only form: the trusted-plan
# screen refuses a conditional or comparison operator here. The shared locals are byte-identical to
# terraform/bootstrap/account_names.tf (validate_account_portable_names).
locals {
  name_suffix = { sandbox = "", sit = "-sit" }[var.environment]

  data_lake_bucket_name = "agent-platform-data-lake${local.name_suffix}"
  data_lake_bucket_arn  = "arn:aws:s3:::${local.data_lake_bucket_name}"

  ducklake_dr_bucket_name = "agent-platform-ducklake-catalog-dr${local.name_suffix}"
  ducklake_dr_bucket_arn  = "arn:aws:s3:::${local.ducklake_dr_bucket_name}"
}
