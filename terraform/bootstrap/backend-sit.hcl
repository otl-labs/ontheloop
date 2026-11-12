# Partial S3 backend for the SIT bootstrap state (rec-5025, SIT Decision 252). Overrides main.tf's backend block bucket:
#   terraform init -backend-config=backend-sit.hcl -backend-config="region=$(tr -d '[:space:]' < ../../config/aws-region)"
# Every SIT apply also sets -var environment=sit. Holds no account id and no region (Decision 202 cl.4).
bucket       = "agent-platform-bootstrap-tfstate-sit"
key          = "terraform.tfstate"
encrypt      = true
use_lockfile = true
