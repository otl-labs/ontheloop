# Decision 237: secret values live as SSM SecureString parameters under /secret/. This file holds the nine
# parameter names, used for env vars only; every IAM grant writes the literal parameter ARN string instead, the one
# form the coverage and planner checks recognise. Only the Neon catalog DSN parameter is Terraform-managed (see
# neon_ducklake_catalog.tf); the other eight are copied in out of band and nothing reads any of them until
# secrets-to-ssm-cutover.
locals {
  secret_parameter_names = {
    neon_api_key              = "/secret/neon-api-key"
    terraform_personal_tfvars = "/secret/agent-platform-terraform-personal-tfvars"
    ducklake_neon_catalog_dsn = "/secret/ducklake-neon-catalog-dsn"
    deepseek_api_key          = "/secret/agent-platform-deepseek-api-key"
    anthropic_api_key         = "/secret/agent-platform-anthropic-api-key"
    telemetry_writer_dsn      = "/secret/agent-platform-ducklake-telemetry-writer-dsn"
    telemetry_writer_prod_dsn = "/secret/agent-platform-ducklake-telemetry-writer-prod-dsn"
    telemetry_reader_dsn      = "/secret/agent-platform-ducklake-telemetry-reader-dsn"
    smoke_maintainer_dsn      = "/secret/agent-platform-ducklake-smoke-maintainer-dsn"
  }
}
