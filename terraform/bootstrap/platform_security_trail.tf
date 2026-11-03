# Event source for the platform-security-* IAM-change detector (rec-4044, Decision 202).
#
# One multi-region trail homed in var.aws_region. IAM is a global service whose events are
# delivered only to trails that cover IAM's home region; a multi-region trail with global service
# events receives them and forwards every region's events to its one CloudWatch Logs group here, so
# no second-region provider or resource exists (Decision 202 clause 4).
#
# Lives in this admin-applied root so the detector sits outside the reach of the principals it
# watches: CI grants stop at agent-platform-*/ducklake-* names, and PlatformDev holds no
# logs/cloudwatch/cloudtrail/s3 write on these resources. The trail, bucket and log group carry
# prevent_destroy -- destroying the trail is break-glass only (Decision 202 clause 2).
locals {
  platform_security_trail_arn     = "arn:aws:cloudtrail:${var.aws_region}:${var.account_id}:trail/platform-security-trail"
  platform_security_log_group_arn = "arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:platform-security-cloudtrail"

  platform_security_trail_logs_policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "PlatformSecurityTrailLogDelivery"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${local.platform_security_log_group_arn}:log-stream:*"
      },
    ]
  })
}

resource "aws_s3_bucket" "platform_security_trail" {
  bucket        = "platform-security-trail-${var.account_id}-${var.aws_region}"
  force_destroy = false

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "platform_security_trail" {
  bucket = aws_s3_bucket.platform_security_trail.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "platform_security_trail" {
  bucket                  = aws_s3_bucket.platform_security_trail.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "platform_security_trail" {
  bucket = aws_s3_bucket.platform_security_trail.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_ownership_controls" "platform_security_trail" {
  bucket = aws_s3_bucket.platform_security_trail.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# S3 is the 400-day authoritative copy; the log group keeps 30 days for the alarms and queries.
resource "aws_s3_bucket_lifecycle_configuration" "platform_security_trail" {
  bucket = aws_s3_bucket.platform_security_trail.id

  rule {
    id     = "platform-security-trail-retention"
    status = "Enabled"

    filter {}

    expiration {
      days = 400
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

resource "aws_s3_bucket_policy" "platform_security_trail" {
  bucket = aws_s3_bucket.platform_security_trail.id

  # The trail ARN is a template, not a reference: the trail depends on this policy existing.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "PlatformSecurityTrailAclCheck"
        Effect    = "Allow"
        Principal = { Service = "cloudtrail.amazonaws.com" }
        Action    = "s3:GetBucketAcl"
        Resource  = aws_s3_bucket.platform_security_trail.arn
        Condition = {
          StringEquals = { "aws:SourceArn" = local.platform_security_trail_arn }
        }
      },
      {
        Sid       = "PlatformSecurityTrailWrite"
        Effect    = "Allow"
        Principal = { Service = "cloudtrail.amazonaws.com" }
        Action    = "s3:PutObject"
        Resource  = "${aws_s3_bucket.platform_security_trail.arn}/AWSLogs/${var.account_id}/*"
        Condition = {
          StringEquals = {
            "s3:x-amz-acl"  = "bucket-owner-full-control"
            "aws:SourceArn" = local.platform_security_trail_arn
          }
        }
      },
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.platform_security_trail.arn,
          "${aws_s3_bucket.platform_security_trail.arn}/*",
        ]
        Condition = {
          Bool = { "aws:SecureTransport" = "false" }
        }
      },
    ]
  })

  depends_on = [aws_s3_bucket_public_access_block.platform_security_trail]
}

resource "aws_cloudwatch_log_group" "platform_security_trail" {
  name              = "platform-security-cloudtrail"
  retention_in_days = 30

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_iam_role" "platform_security_trail_logs" {
  name        = "platform-security-cloudtrail-logs"
  description = "CloudTrail delivery into the platform-security-cloudtrail log group (Decision 202)"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "cloudtrail.amazonaws.com" }
        Action    = "sts:AssumeRole"
        Condition = {
          StringEquals = { "aws:SourceArn" = local.platform_security_trail_arn }
        }
      },
    ]
  })
}

resource "aws_iam_role_policy" "platform_security_trail_logs" {
  name   = "platform-security-cloudtrail-logs"
  role   = aws_iam_role.platform_security_trail_logs.id
  policy = local.platform_security_trail_logs_policy_json

  lifecycle {
    precondition {
      # Inline-policy hard limit is 10,240 B; a LimitExceeded is invisible to plan, surfaces at apply.
      condition     = length(jsonencode(jsondecode(local.platform_security_trail_logs_policy_json))) <= 10240
      error_message = "platform-security-cloudtrail-logs inline policy exceeds the 10,240 B inline-policy limit."
    }
  }
}

resource "aws_cloudtrail" "platform_security" {
  name                          = "platform-security-trail"
  s3_bucket_name                = aws_s3_bucket.platform_security_trail.id
  is_multi_region_trail         = true
  include_global_service_events = true
  enable_log_file_validation    = true
  enable_logging                = true
  cloud_watch_logs_group_arn    = "${aws_cloudwatch_log_group.platform_security_trail.arn}:*"
  cloud_watch_logs_role_arn     = aws_iam_role.platform_security_trail_logs.arn

  event_selector {
    read_write_type           = "All"
    include_management_events = true
  }

  # Decision 213 cl.4: Lambda data events for the identity-trusting DuckLake functions, so a denied
  # direct invoke leaves a record. CloudTrail data-resource values are exact ARN strings; the telemetry
  # writer, smoke twin and reader are named before their functions exist. The management selector above is
  # unchanged, so the Decision 202 detectors keep their inputs.
  event_selector {
    read_write_type           = "All"
    include_management_events = false

    data_resource {
      type = "AWS::Lambda::Function"
      values = [
        "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:agent-platform-ducklake-writer",
        "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:agent-platform-ducklake-reader",
        "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:agent-platform-ducklake-telemetry-writer",
        "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:agent-platform-ducklake-telemetry-writer-smoke",
        "arn:aws:lambda:${var.aws_region}:${var.account_id}:function:agent-platform-ducklake-telemetry-reader",
      ]
    }
  }

  depends_on = [
    aws_s3_bucket_policy.platform_security_trail,
    aws_iam_role_policy.platform_security_trail_logs,
  ]

  lifecycle {
    prevent_destroy = true
  }
}
