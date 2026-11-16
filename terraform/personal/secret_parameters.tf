# Decision 237: secret values live as SSM SecureString parameters under /secret/. This file holds the nine
# parameter names, used for env vars only; every IAM grant writes the literal parameter ARN string instead, the one
# form the coverage and planner checks recognise. Only the Neon catalog DSN parameter is Terraform-managed (see
# neon_ducklake_catalog.tf); the other eight are copied in out of band.
# Readers of the two inference keys (deepseek_api_key, anthropic_api_key): PlatformDev
# (InferenceCredentialsParameterRead in terraform/bootstrap/platform_dev_policies.tf, the credential smoke test) and
# agent-platform-github-ci-persona-verdict (InferenceKeyParameterRead in oidc_persona_verdict_role.tf, the verdict
# persona jobs; Decision 263), the two literal-ARN consumers; PlatformAdmin also reads every /secret/ parameter
# through SecretParameterAdmin in terraform/bootstrap/platform_admin_policies.tf, the operator's admin grant
# (Decision 237 cl.2).
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
