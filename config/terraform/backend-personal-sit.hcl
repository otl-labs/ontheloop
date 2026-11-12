# Partial S3 backend for terraform/personal in the SIT account (rec-5025, SIT Decision 252). Kept outside terraform/personal
# because the trusted PR plan refuses a non-.tf file added there. Passed with -backend-config beside -var environment=sit.
# SIT keeps PROD's layout: state, saved plans and convergence records live inside its data-lake bucket. No region (Decision 202).
bucket       = "agent-platform-data-lake-sit"
key          = "tfstate/personal/sit/terraform.tfstate"
encrypt      = true
use_lockfile = true
