# Personal-account platform infrastructure (isolated root module) -- PLAN-public-migration Phase B.
#
# WHY a separate root module: the work-account root (terraform/) points its DEFAULT aws provider
# at the work account and only ~8 of ~137 resources use the aws.platform alias. Applying that root
# against the personal account would try to CREATE ~120 work-account resources. This module holds
# ALL personal-account infra with its OWN provider + state and is the ONLY root applied post-CD.21.
#
# Provisioning profile is agent_platform_admin (PlatformAdmin) -- creates IAM + OIDC, which the
# permissionless agent_platform (PlatformDev) runtime role cannot. Runtime stays agent_platform.
# The account ID is supplied at apply time via the gitignored terraform.personal.tfvars; it is
# never a committed literal (PLAN Step 11b parameterisation invariant).
# Retired-resource state reconciliation for this root is governed by Decision 178 clause 4 (docs/DECISIONS.md).
#
# Draining a retired address never uses `terraform state rm`: a destroy always routes the
# deterministic guard to terraform-apply-sandbox's gated-apply job (the tf-gated-apply human
# approval). Two heal verbs reach it (Decision 183): the push-triggered run from merging a PR that
# touches this root applies the saved, digest-verified plan.bin; a workflow_dispatch
# acknowledge-and-retry run plans fresh at main HEAD and hands that plan.bin to gated-apply as the
# sha256-verified SANDBOX_FRESH_PLAN_ARTIFACT -- the only path that also clears a red record.
# Reconcile cannot substitute: it replays the saved plan.bin for the red commit, which stops
# rendering once config drops a provider that plan still references.

# Provider, backend and terraform {} blocks live in providers.tf (main-only, Decision 238 cl.3).

# ---------------------------------------------------------------------------
# Data-lake S3 bucket (platform object storage: tfstate, tfplan, convergence
# records, provider mirror, logs)
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "data_lake" {
  bucket = "agent-platform-data-lake"

  tags = {
    Name    = "Platform Data Lake"
    Purpose = "Platform object storage - tfstate / plans / convergence records / logs"
  }
}

resource "aws_s3_bucket_versioning" "data_lake" {
  bucket = aws_s3_bucket.data_lake.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data_lake" {
  bucket = aws_s3_bucket.data_lake.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "data_lake" {
  bucket = aws_s3_bucket.data_lake.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_policy" "data_lake_https_only" {
  bucket = aws_s3_bucket.data_lake.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyNonHTTPS"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.data_lake.arn,
          "${aws_s3_bucket.data_lake.arn}/*"
        ]
        Condition = {
          Bool = {
            "aws:SecureTransport" = "false"
          }
        }
      }
    ]
  })
}

# ---------------------------------------------------------------------------
# DuckLake data-lake lifecycle (T2.18, PLAN-ducklake-noncurrent-version-reclaim /
# PLAN-smoke-prefix-lifecycle-coverage): sweep noncurrent versions + their now-expired delete
# markers so billed storage reconverges on the listing GcDebtRatio is computed from. Both rules
# are prefix-scoped (trailing slash, from the declared local -- never a retyped literal, so a
# rename of the local cannot silently drop the sweep). The trailing slash is load-bearing:
# local.ducklake_prod_data_prefix is the bare string "ducklake", which also prefix-matches
# "ducklake-neon-smoke/" without it -- the two prefixes must stay separately declared and never
# collapse into one rule. rec-3892's unexplained-deleter RCA (smoke prefix) is resolved (no
# unmodeled deleter; all deletions trace to the smoke gc cron), so the smoke prefix is now
# POSITIVELY covered by a second rule rather than a declared exclusion (rec-3945; see
# TestDataLakeLifecycleGoverned in the companion test module). NO current-version expiration rule
# on either: DuckLake addresses live Parquet by key, never by version id, so an age-based rule
# here would delete data the catalog still references.
#
# A third rule (telemetry-blobs-noncurrent-reclaim) extends the Decision 192 noncurrent-reclaim family to a
# non-DuckLake prefix, the telemetry blob store (T2.36 slice 2a-2). The same justification holds:
# telemetry_transcripts.content_uri addresses a blob by key, never by version id, so noncurrent versions sit
# outside the referenced set and the rule touches no referenced blob. Current blobs never expire, because
# telemetry_transcripts rows reference them by content_uri; orphans are rec-4033's. The window moves with
# SNAPSHOT_RETAIN_DAYS if rec-3870 changes it.
# ---------------------------------------------------------------------------

resource "aws_s3_bucket_lifecycle_configuration" "data_lake" {
  bucket = aws_s3_bucket.data_lake.id

  rule {
    id     = "ducklake-prod-noncurrent-reclaim"
    status = "Enabled"

    filter {
      prefix = "${local.ducklake_prod_data_prefix}/"
    }

    noncurrent_version_expiration {
      # Bound to SNAPSHOT_RETAIN_DAYS (src/common/ducklake_maintenance.py) -- an operator
      # manual-recovery window, not catalog time travel: gc_ops runs after expire_snapshots, so
      # every file it deletes is already outside the snapshot horizon and no read path pins a
      # version id. Cross-checked against the Python constant by TestDataLakeLifecycleGoverned.
      noncurrent_days = 30
    }

    expiration {
      expired_object_delete_marker = true
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  rule {
    id     = "ducklake-smoke-noncurrent-reclaim"
    status = "Enabled"

    filter {
      prefix = "${local.ducklake_smoke_data_prefix}/"
    }

    noncurrent_version_expiration {
      # Same shape and window as the production rule above -- noncurrent_days stays bound to
      # SNAPSHOT_RETAIN_DAYS for BOTH rules; revisiting the retention window is rec-3870's
      # business, not this one's.
      noncurrent_days = 30
    }

    expiration {
      expired_object_delete_marker = true
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  rule {
    id     = "telemetry-blobs-noncurrent-reclaim"
    status = "Enabled"

    filter {
      prefix = "${local.telemetry_blob_data_prefix}/"
    }

    noncurrent_version_expiration {
      # Same window as the DuckLake rules, bound to SNAPSHOT_RETAIN_DAYS and cross-checked by
      # TestDataLakeLifecycleGoverned. No current-version expiration: telemetry_transcripts rows reference blobs by key.
      noncurrent_days = 30
    }

    expiration {
      expired_object_delete_marker = true
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  # Production telemetry catalog data (Decision 230). A sibling of ducklake/, not inside it: the ops catalog's
  # catalog-wide orphan cleanup must never see these files, and the trailing slash keeps this prefix apart from
  # ducklake/ and ducklake-neon-smoke/. Same noncurrent-only shape: DuckLake addresses Parquet by key.
  rule {
    id     = "ducklake-telemetry-noncurrent-reclaim"
    status = "Enabled"

    filter {
      prefix = "${local.ducklake_telemetry_data_prefix}/"
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }

    expiration {
      expired_object_delete_marker = true
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

# ---------------------------------------------------------------------------
# DynamoDB atomic counters (rec/decision ID allocation, Decision 36/37: SSO, no IAM users)
# Seeded ONCE at greenfield ABOVE the work-account max + 1000 margin (Decision 50 collision guard;
# work maxes 2026-05-28: recommendations=944, decisions=81 -> floors 1944/1081). The counter VALUES
# are app-owned runtime state (atomic UpdateItem ADD), deliberately NOT Terraform-managed -- see the
# note below the table resource.
# ---------------------------------------------------------------------------

resource "aws_dynamodb_table" "counters" {
  name         = "agent-platform-counters"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "counter_name"

  attribute {
    name = "counter_name"
    type = "S"
  }

  tags = {
    Purpose = "Atomic sequential counter allocation for agents and executor"
  }
}

# Counter seed items are intentionally NOT Terraform-managed (removed during the PLAN-public-migration
# S3-backend bootstrap, 2026-05-30). aws_dynamodb_table_item manages a row's VALUE -- but current_value
# is mutable runtime state the ops portal increments via atomic UpdateItem ADD on every ID allocation.
# Terraform must not own a value another system mutates: state here is ephemeral (CD applies run from
# fresh containers), so a fresh apply would treat the seed as "to create" and PutItem the stale floor
# over the live counter, resetting it and reusing already-issued IDs. The greenfield seed has done its
# job (counters are live, well past the 1944/1081 floors). The table stays Terraform-managed; the rows
# are app-owned. A NEW environment seeds its floor once out-of-band (e.g. `aws dynamodb put-item`)
# during that environment's bootstrap, not via Terraform.
