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
# These Neon resources + the DSN parameter AUTO-APPLY via the sandbox pipeline behind the Neon-aware
# fail-closed guard (scripts/terraform_apply_guard.py) + subagent review -- they are NOT carved out of
# Decision-77. The guard blocks any neon_* update / replace / delete (the mutation surface where
# allow-list widening, credential rotation, or project deletion would happen) and ALLOWS a neon_*
# create. A create is safe on the strength of compensating controls, NOT an IP allow-list: enforced
# TLS (sslmode=require), a scoped non-owner neon_role, and the DSN held as an SSM SecureString. Neon
# IP-Allow is a Scale-plan feature (unavailable on the free tier) and egress here is dynamic (CC-web
# containers + GitHub-hosted runners + Lambda), so no static allow-list is set (REPORT R3 / CD.34).
#
# aws_ssm_parameter is NOT in the guard's IAM_SENSITIVE_TYPES, so the DSN-parameter create also
# returns guard exit 0 and auto-applies.
#
# ---------------------------------------------------------------------------
# PHASE 0 PREREQUISITES (historical; done once, out of band, before Phase 1 merged)
# ---------------------------------------------------------------------------
# The Neon provider API key is held out of band at the /secret/neon-api-key SSM parameter (Decision 237), never
# Terraform-managed -- Terraform-managing the key the provider reads would create a provider->resource cycle.
# The CI apply role reads it through SecretParameterValueRead (terraform/bootstrap/github_ci_apply_reads.tf).

# The neon provider and its pin live in providers.tf (required_providers allows one block per module). Its
# checksums are committed in .terraform.lock.hcl (supply-chain control).
#
# Neon provider API key: the ephemeral input variable var.neon_api_key (providers.tf), supplied per run (trusted CI
# fetches it with scripts/ci/neon_api_key.sh; the admin loop follows deploy-paths admin_out_of_band;
# pattern: secret-material-handling provider_credential_ephemeral_input). It is never in state or a saved plan.
# The provider pin stays at 0.13.0: the configure-time ephemeral spike is valid for that version only.

# ---------------------------------------------------------------------------
# Neon project -- the catalog. Free tier: 0.5 GB storage, 100 CU-hours/mo, scale-to-zero. pg_version
# 16 matches the prior RDS engine. org_id comes from the tfvars parameter (var.neon_org_id, never
# committed, rec-4543) and is ignored after creation, because the provider makes it ForceNew. No
# allowed_ips (free-tier posture -- see APPLY POSTURE above). The project auto-creates a default branch +
# endpoint; the scoped role and the catalog database below live on that default branch.
# ---------------------------------------------------------------------------

resource "neon_project" "ducklake_catalog" {
  name       = "ducklake-catalog"
  org_id     = var.neon_org_id
  region_id  = var.neon_region_id
  pg_version = 16

  # 7 days (604800s), the paid Launch plan maximum: the point-in-time window Decisions 88 cl.3 and 107 rely on
  # between weekly dumps. The earlier 21600 (6h) was the free-plan ceiling.
  history_retention_seconds = 604800

  # The catalog is the ops tables' sole system of record (Decision 84): no plan may destroy or replace it.
  lifecycle {
    prevent_destroy = true
    ignore_changes  = [org_id]
  }
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

  lifecycle {
    prevent_destroy = true
  }
}

# ---------------------------------------------------------------------------
# Assembled DSN for the catalog, stored as an SSM SecureString parameter
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

# The Secrets Manager version leaves state without an API call (destroy = false); the secret's own removal
# schedules its deletion with the 30-day recovery window (Decision 157).
removed {
  from = aws_secretsmanager_secret_version.ducklake_neon_catalog_dsn

  lifecycle {
    destroy = false
  }
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
# Outputs -- consumed by the T2.16b smoke test + the T2.17 Lambda runtime.
# ---------------------------------------------------------------------------

output "ducklake_neon_catalog_host_direct" {
  description = "Neon DIRECT (unpooled) endpoint host for the DuckLake catalog. Catalog writes use this host (multi-statement DuckLake txns vs transaction-mode pooling)."
  value       = neon_project.ducklake_catalog.database_host
}

output "ducklake_neon_catalog_dsn_parameter_name" {
  description = "SSM SecureString parameter name of the DuckLake Neon catalog DSN JSON (Decision 237); nothing reads it until secrets-to-ssm-cutover."
  value       = aws_ssm_parameter.ducklake_neon_catalog_dsn.name
}
