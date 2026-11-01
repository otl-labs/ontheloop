# DuckLake operational lakehouse -- Neon serverless Postgres catalog (T2.16b / CD.34, pending).
#
# Replaces the T2.16 RDS catalog (rds_ducklake_catalog.tf, retired in Phase 2) with Neon serverless
# Postgres (originally the free tier). Like RDS, this is a catalog-metadata store, NOT a query engine:
# DuckDB performs all computation against S3 Parquet; the catalog holds only DuckLake metadata
# (table/version/snapshot pointers).
#
# ---------------------------------------------------------------------------
# APPLY POSTURE (Decision 77 + the 2026-06-04 posture pivot)
# ---------------------------------------------------------------------------
# These Neon resources + the DSN secret AUTO-APPLY via the sandbox pipeline behind the Neon-aware
# fail-closed guard (scripts/terraform_apply_guard.py) + subagent review -- they are NOT carved out of
# Decision-77. The guard blocks any neon_* update / replace / delete (the mutation surface where
# allow-list widening, credential rotation, or project deletion would happen) and ALLOWS a neon_*
# create. A create is safe on the strength of compensating controls, NOT an IP allow-list: enforced
# TLS (sslmode=require), a scoped non-owner neon_role, and the DSN held in Secrets Manager. Neon
# IP-Allow is a Scale-plan feature (unavailable on the free tier) and egress here is dynamic (CC-web
# containers + GitHub-hosted runners + Lambda), so no static allow-list is set (REPORT R3 / CD.34).
#
# aws_secretsmanager_secret is NOT in the guard's IAM_SENSITIVE_TYPES, so the DSN-secret create also
# returns guard exit 0 and auto-applies.
#
# ---------------------------------------------------------------------------
# PHASE 0 PREREQUISITES (one-time, manual from the admin container, BEFORE Phase 1 merges)
# ---------------------------------------------------------------------------
#   1. Create the Neon provider API key secret out-of-band (plaintext; human-run; NOT Terraform-managed
#      -- Terraform-managing the key the provider reads would create a provider->resource cycle):
#        aws secretsmanager create-secret --name neon-api-key \
#          --secret-string '<neon-api-key>' --region "$(tr -d '[:space:]' < config/aws-region)" --profile agent_platform_admin
#   2. Grant the CI apply role secretsmanager rights on the two secret ARNs (oidc.tf,
#      aws_iam_role_policy.github_ci_apply) and apply JUST that statement manually (IAM-sensitive ->
#      the guard fail-closes, so it cannot auto-apply):
#        terraform apply -target=aws_iam_role_policy.github_ci_apply   # from the admin container
# After Phase 0, Phase 1 auto-applies cleanly: the neon-api-key secret + the apply-role grant already
# exist, and the Neon creates + DSN-secret create return guard exit 0.

# The neon provider and its pin live in providers.tf (required_providers allows one block per module). Its
# checksums are committed in .terraform.lock.hcl (supply-chain control).
#
# Neon provider API key: the ephemeral input variable var.neon_api_key (providers.tf), supplied per run (trusted CI
# fetches it with scripts/ci/neon_api_key.sh; the admin loop follows deploy-paths admin_out_of_band;
# pattern: secret-material-handling provider_credential_ephemeral_input). It is never in state or a saved plan.
# The provider pin stays at 0.13.0: the configure-time ephemeral spike is valid for that version only.

# ---------------------------------------------------------------------------
# Neon project -- the catalog. Free tier: 0.5 GB storage, 100 CU-hours/mo, scale-to-zero. pg_version
# 16 matches the prior RDS engine. org_id is required by Neon's org-based model (every account, incl.
# personal, has a default organization) -- it is an identifier, not a secret. No allowed_ips (free-tier
# posture -- see APPLY POSTURE above). The project auto-creates a default branch + endpoint; the scoped
# role and the catalog database below live on that default branch.
# ---------------------------------------------------------------------------

resource "neon_project" "ducklake_catalog" {
  name       = "ducklake-catalog"
  org_id     = var.neon_org_id
  region_id  = var.neon_region_id
  pg_version = 16

  # 7 days (604800s), the paid Launch plan maximum: the point-in-time window Decisions 88 cl.3 and 107 rely on
  # between weekly dumps. The earlier 21600 (6h) was the free-plan ceiling.
  history_retention_seconds = 604800
}

# Scoped, non-owner role for catalog access. Distinct from the project's auto-created owner role so the
# DuckLake runtime credential is least-privilege (REPORT R3 compensating control).
resource "neon_role" "ducklake_ops" {
  project_id = neon_project.ducklake_catalog.id
  branch_id  = neon_project.ducklake_catalog.default_branch_id
  name       = "ducklake_ops"
}

# Catalog database. The DuckLake metadata schema (META_SCHEMA 'ducklake_ops', created post-provision
# from migrations/ducklake_ops_schema.sql) lives WITHIN this database.
resource "neon_database" "ducklake_ops" {
  project_id = neon_project.ducklake_catalog.id
  branch_id  = neon_project.ducklake_catalog.default_branch_id
  name       = "ducklake_ops"
  owner_name = neon_role.ducklake_ops.name
}

# ---------------------------------------------------------------------------
# Assembled DSN for the catalog, stored in Secrets Manager
# (docs/contracts/secret-material-handling.yaml RUNTIME-FETCH pattern, Decision 175 -- rehomed
# from the Decision 37 precedent).
# Uses the DIRECT (unpooled) endpoint host -- DuckLake commits are multi-statement transactions, which
# PgBouncer transaction-mode pooling can break (the pooled endpoint is host_pooler; used only if proven
# transaction-safe, per the T2.16b connection-churn gate). TLS enforced via sslmode=require.
#
# ROTATION: the Neon role password does NOT auto-rotate like the prior RDS-managed master secret
# (manage_master_user_password). Rotate quarterly, calendar-reminded (repo convention; recorded in the
# secret description).
# ---------------------------------------------------------------------------

locals {
  ducklake_neon_dsn = format(
    "postgresql://%s:%s@%s/%s?sslmode=require", # pragma: allowlist secret -- format template, not a literal credential
    neon_role.ducklake_ops.name,
    neon_role.ducklake_ops.password,
    neon_project.ducklake_catalog.database_host,
    neon_database.ducklake_ops.name,
  )
}

resource "aws_secretsmanager_secret" "ducklake_neon_catalog_dsn" {
  name        = "ducklake-neon-catalog-dsn"
  description = "DuckLake Neon catalog DSN (direct endpoint, sslmode=require). ROTATION: quarterly, calendar-reminded -- the Neon role password does not auto-rotate (CD.34 / Decision 37)."

  tags = {
    Name    = "DuckLake Neon Catalog DSN"
    Purpose = "DuckLake catalog T2.16b CD.34 -- Lambda/smoke-test runtime-fetch"
  }
}

locals {
  ducklake_neon_dsn_document = jsonencode({
    dsn         = local.ducklake_neon_dsn
    host        = neon_project.ducklake_catalog.database_host
    dbname      = neon_database.ducklake_ops.name
    username    = neon_role.ducklake_ops.name
    password    = neon_role.ducklake_ops.password
    sslmode     = "require"
    meta_schema = "ducklake_ops"
  })
}

resource "aws_secretsmanager_secret_version" "ducklake_neon_catalog_dsn" {
  secret_id     = aws_secretsmanager_secret.ducklake_neon_catalog_dsn.id
  secret_string = local.ducklake_neon_dsn_document
}

resource "aws_ssm_parameter" "ducklake_neon_catalog_dsn" {
  name        = "/secret/ducklake-neon-catalog-dsn"
  type        = "SecureString"
  tier        = "Standard"
  value       = local.ducklake_neon_dsn_document
  description = "DuckLake Neon catalog DSN (direct endpoint, sslmode=require). ROTATION: quarterly, calendar-reminded -- the Neon role password does not auto-rotate (CD.34 / Decision 37)."

  tags = {
    Name    = "DuckLake Neon Catalog DSN"
    Purpose = "DuckLake catalog T2.16b CD.34 -- Lambda/smoke-test runtime-fetch"
  }
}

# ---------------------------------------------------------------------------
# Scoped catalog login for the telemetry writer (Decision 213 cl.3) -- the CONTAINER only.
#
# There is deliberately no aws_secretsmanager_secret_version here: the role is created by SQL
# (src/lambdas/ducklake_maintenance/ducklake_telemetry_writer_role.sql), not by the Neon provider, and
# its password is generated and written out of band by the admin under the SECRET-VALUE-OUT-OF-BAND
# pattern of docs/contracts/secret-material-handling.yaml, so it never enters Terraform state. The
# value is {host, dbname, username, password, sslmode}; ducklake_maintenance's provision_telemetry_login
# reads it to set the role's password (deploy-paths.yaml admin_out_of_band.scoped_login_provisioning).
# ---------------------------------------------------------------------------

resource "aws_secretsmanager_secret" "ducklake_telemetry_writer_dsn" {
  name        = "agent-platform-ducklake-telemetry-writer-dsn"
  description = "DuckLake telemetry writer scoped catalog login (role ducklake_telemetry_writer, DML-only on the smoke metadata schema). Value written out of band by the admin. ROTATION: quarterly, manual, via provision_telemetry_login. Migration: src/lambdas/ducklake_maintenance/ducklake_telemetry_writer_role.sql (Decision 213)."

  tags = {
    Name    = "DuckLake Telemetry Writer DSN"
    Purpose = "Decision 213 scoped catalog login - runtime-fetch by the telemetry writer"
  }
}

# ---------------------------------------------------------------------------
# Production telemetry catalog logins (Decision 230) -- CONTAINERS only, no aws_secretsmanager_secret_version,
# for the same SECRET-VALUE-OUT-OF-BAND reason as the writer secret above. Each value is written by the admin and
# applied to its role by provision_telemetry_login.
# ---------------------------------------------------------------------------

resource "aws_secretsmanager_secret" "ducklake_telemetry_writer_prod_dsn" {
  name        = "agent-platform-ducklake-telemetry-writer-prod-dsn"
  description = "DuckLake telemetry writer scoped login for the production telemetry catalog (DML-only on the ducklake_telemetry metadata schema). Value written out of band by the admin. ROTATION: quarterly, manual, via provision_telemetry_login."

  tags = {
    Name    = "DuckLake Telemetry Writer Prod DSN"
    Purpose = "Decision 230 scoped catalog login - runtime-fetch by the telemetry writer after the flip"
  }
}

resource "aws_secretsmanager_secret" "ducklake_telemetry_reader_dsn" {
  name        = "agent-platform-ducklake-telemetry-reader-dsn"
  description = "DuckLake telemetry reader scoped login (SELECT-only on the ducklake_telemetry metadata schema). Value written out of band by the admin. ROTATION: quarterly, manual, via provision_telemetry_login."

  tags = {
    Name    = "DuckLake Telemetry Reader DSN"
    Purpose = "Decision 230 scoped catalog login - runtime-fetch by the telemetry reader"
  }
}

resource "aws_secretsmanager_secret" "ducklake_smoke_maintainer_dsn" {
  name        = "agent-platform-ducklake-smoke-maintainer-dsn"
  description = "DuckLake smoke maintainer scoped login (one owner-role grant for the smoke catalog maintenance function). Value written out of band by the admin. ROTATION: quarterly, manual, via provision_telemetry_login."

  tags = {
    Name    = "DuckLake Smoke Maintainer DSN"
    Purpose = "Decision 230 scoped catalog login - runtime-fetch by the smoke maintenance function"
  }
}

# ---------------------------------------------------------------------------
# Outputs -- consumed by the T2.16b smoke test + the T2.17 Lambda runtime.
# ---------------------------------------------------------------------------

output "ducklake_neon_catalog_dsn_secret_arn" {
  description = "Secrets Manager ARN of the DuckLake Neon catalog DSN JSON. Runtime-fetched (Decision 37) by the smoke test + the T2.17 Lambdas."
  value       = aws_secretsmanager_secret.ducklake_neon_catalog_dsn.arn
}

output "ducklake_neon_catalog_host_direct" {
  description = "Neon DIRECT (unpooled) endpoint host for the DuckLake catalog. Catalog writes use this host (multi-statement DuckLake txns vs transaction-mode pooling)."
  value       = neon_project.ducklake_catalog.database_host
}

output "ducklake_neon_catalog_dsn_parameter_name" {
  description = "SSM SecureString parameter name of the DuckLake Neon catalog DSN JSON (Decision 237); nothing reads it until secrets-to-ssm-cutover."
  value       = aws_ssm_parameter.ducklake_neon_catalog_dsn.name
}
