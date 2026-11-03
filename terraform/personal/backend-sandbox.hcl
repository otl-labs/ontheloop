# Partial S3 backend config for the sandbox PLATFORM environment (Decision 77).
# Account-agnostic: holds no account ID. Consumed via:
#   terraform init -backend-config=backend-sandbox.hcl
# A future SIT/PROD environment is a new backend-<env>.hcl with a different key (single-account
# until the product live_full trigger per docs/contracts/environment-taxonomy.yaml single_account_rule).
# Region comes from AWS_REGION in CI or a -backend-config region from config/aws-region at hand-run init (rec-4067).
bucket       = "agent-platform-data-lake"
key          = "tfstate/personal/sandbox/terraform.tfstate"
encrypt      = true
use_lockfile = true
